"""
Configuração central do LLM Council.

Nada aqui contém lógica de LLM — só definição de settings, lidos de
variáveis de ambiente (via .env em desenvolvimento). API keys nunca
têm valor default nem ficam hardcoded.

Deliberadamente SEM instância module-level (`settings = Settings()`):
importar este módulo nunca deve ler `.env`, validar variáveis de
ambiente, nem falhar como efeito colateral de um `import app.config`
em algum lugar da cadeia de imports transitiva (achado do Stage 14,
patch de revisão final -- uma env malformada, ex.
`DEFAULT_MAX_COST_USD=not-a-number`, derrubava até `import app.cli.main`
com um `ValidationError` cru, ANTES do error boundary da CLI existir
operacionalmente). Cada composition root (`app/bootstrap.py`,
`app/cli/main.py`) instancia `Settings()` explicitamente, dentro do seu
próprio tratamento de erro -- nunca um singleton global reaproveitado.

Deployment Execution Configuration Boundary V1 (F1, repair pós-revisão
independente -- MEDIUM) -- `frozen=True`: uma vez construída com
sucesso, uma instância de `Settings` é um SNAPSHOT imutável de
deployment -- nenhuma atribuição posterior de campo é permitida, nem
mesmo pra outro valor igualmente válido. Sem isso, um deployment
programático podia construir um `Settings` válido e depois mutá-lo
(`settings.default_max_cost_usd = float("inf")`) ANTES de
`build_app_components(settings)` -- as validações de campo desta slice
(seção 6/7/8 do contrato) só rodam na CONSTRUÇÃO, então qualquer
atribuição posterior contornava inteiramente o boundary de deployment
que esta slice existe pra fechar. Auditoria confirmou ZERO mutação de
`Settings` pós-construção em todo o repositório (produção ou testes) --
nenhum código legítimo depende de reatribuir um campo depois de
`Settings()` retornar, então congelar não quebra nenhum uso real."""

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- API keys dos providers (Fase 1 do MVP: OpenAI, Anthropic, Gemini) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- Modelo padrão por provider, usado quando a requisição não especifica ---
    # Definidos aqui (não hardcoded nos providers) para facilitar atualização
    # quando os fornecedores lançarem novas versões.
    #
    # Deployment Execution Configuration Boundary V1 -- os três validados
    # por `_default_model_identifier_is_well_formed` abaixo (não-vazio,
    # não só espaço em branco, sem espaço líder/final): um identificador
    # com esses defeitos nunca corresponde a um modelo real de nenhum
    # provider, e sobrevivia até hoje a todo o processo de bootstrap
    # (engine/DB/registry/service construídos, Run aceito e mintado)
    # antes de falhar só na primeira chamada real, de forma obscura.
    openai_default_model: str = "gpt-5.5"
    anthropic_default_model: str = "claude-sonnet-5"
    gemini_default_model: str = "gemini-3.7-flash"

    # --- Banco de dados ---
    database_url: str = "sqlite+aiosqlite:///./llm_council.db"

    # --- Limites padrão de execução ---
    # Aplicados a toda execução via `RunConfig.from_settings` -- as
    # superfícies de criação atualmente suportadas (`POST /runs` via
    # `CreateRunRequest`, `dialeon run`) não expõem nenhum campo pra
    # sobrescrever estes valores por request; só question/enabled_providers/
    # source_text são aceitos (ver app/presentation/schemas.py:CreateRunRequest).
    # `RunConfig` continua diretamente construível com valores distintos por
    # chamadores internos (ex.: testes), mas isso não é uma superfície de
    # request suportada.
    # Dois conceitos distintos de "tokens", nunca confundidos entre si:
    # default_max_total_tokens = orçamento AGREGADO da execução inteira
    #   (soma de input+output de todos os providers), verificado DEPOIS
    #   que as respostas chegam — não limita nenhuma chamada individual.
    # default_max_output_tokens_per_call = teto de output de UMA chamada
    #   a UM provider, repassado para CompletionRequest.max_tokens antes
    #   da chamada — é o que de fato limita quanto texto cada resposta
    #   pode ter.
    # Deployment Execution Configuration Boundary V1 -- `gt=0` sozinho
    # aceitaria `+inf` (`float('inf') > 0` é True) -- `allow_inf_nan=False`
    # fecha essa lacuna explicitamente pros dois floats de execução
    # abaixo (`default_max_cost_usd` aqui, `orchestrator_round_dispatch_timeout_seconds`
    # mais abaixo): um teto de custo/timeout "infinito" nunca é um
    # limite de verdade, e sobrevivia hoje ao bootstrap inteiro (Run
    # aceito/persistido, trabalho real incorrido) só pra falhar depois,
    # ao serializar a resposta pública em JSON estrito
    # (`ValueError: Out of range float values are not JSON compliant`).
    default_max_cost_usd: float = Field(default=1.00, gt=0, allow_inf_nan=False)
    # `gt=0` sozinho -- zero/negativo aqui sobrevivia ao bootstrap
    # inteiro e só falhava na primeira `RunConfig.from_settings()` (por
    # request), não na composição do deployment.
    default_max_total_tokens: int = Field(default=50_000, gt=0)
    # Etapa 17A.1 -- 1024 provou-se insuficiente em Runs reais de
    # produção: respostas de participante (Gemini) truncadas visivelmente
    # incompletas a ~1020 tokens, e o próprio Judge batendo o teto
    # (1024) duas vezes seguidas, ambas produzindo JSON cortado no meio.
    # 4096 é o menor ajuste que reduz drasticamente a chance de repetir
    # esse cenário sem introduzir um subsistema de alocação de recursos
    # por fase -- continua um único valor provider-neutro, repassado
    # igual a toda chamada (participante, extração, agrupamento,
    # crítica, Judge, Editor, SourceAnalyzer), configurável via
    # RunConfig por execução como já era.
    default_max_output_tokens_per_call: int = Field(default=4096, gt=0)

    # Etapa 17A.2 -- investigação confirmou amplificação estrutural de
    # escala: agrupamento e Judge têm schema de output com cobertura
    # OBRIGATÓRIA (uma entrada por claim bruta/atual), então o tamanho
    # mínimo exigido de output cresce com a contagem de claims, nunca
    # limitado por nada além do teto de tokens -- ao contrário de
    # extração/crítica (uma resposta de cada vez, sem cobertura
    # obrigatória de N claims). Tetos PRÓPRIOS, mais generosos,
    # continuam um único valor fixo cada (nenhum cálculo dinâmico por
    # contagem de claims, nenhuma alocação adaptativa) -- só reconhece
    # que agrupamento/Judge não deveriam compartilhar o mesmo teto de
    # extração/crítica/participante.
    #
    # Repair (Run02 claim-extraction exhaustion) -- correção de
    # caracterização: extração NUNCA foi genuinamente O(1)/"tamanho
    # constante" -- esse rótulo descrevia só a AUSÊNCIA de cobertura
    # obrigatória de N claims (verdade, e ainda verdade), mas escondia
    # que a cardinalidade de claims por resposta era semanticamente
    # ILIMITADA (nenhum teto no contrato/schema) -- uma única resposta
    # "rica" podia sozinha gerar uma lista de claims arbitrariamente
    # grande, consumindo o mesmo `default_max_output_tokens_per_call`
    # compartilhado com participante/crítica. A extração agora tem um
    # teto EXPLÍCITO e finito (`MAX_EXTRACTED_CLAIMS`, ver
    # app/debate/schemas.py/claim_extraction.py) -- BOUNDED, nunca mais
    # tratada como se fosse trivialmente pequena por natureza. Este
    # patch não muda `default_max_output_tokens_per_call` (permanece
    # 4096, ver Field abaixo) -- só corrige a premissa que o justificava.
    default_max_output_tokens_grouping: int = Field(default=8192, gt=0)
    default_max_output_tokens_judge: int = Field(default=8192, gt=0)

    # --- Claim processor (Etapa 5) ---
    # Mesmo provider realiza extração de claims E agrupamento semântico.
    # Não precisa obrigatoriamente estar em enabled_providers — só precisa
    # existir entre os providers que o DebateEngine recebeu por injeção.
    # Sem fallback automático: se este provider falhar, o processamento
    # falha explicitamente (auditável via ClaimProcessingAttempt), nunca
    # troca silenciosamente de provider.
    default_claim_processor_provider: str = "anthropic"

    # --- Judge (Etapa 6) ---
    # Mesma disciplina do claim processor: sem fallback automático — se
    # esse provider falhar, falha explicitamente (auditável via
    # JudgeAttempt), nunca troca de provider silenciosamente.
    default_judge_provider: str = "anthropic"

    # --- Editor (Etapa 7) ---
    # Mesma disciplina do Judge/claim processor: sem fallback automático.
    default_editor_provider: str = "anthropic"
    # Etapa 16 — quarto papel de execução real, mesma disciplina dos 3
    # acima: não precisa pertencer a enabled_providers, validação de
    # existência real acontece em runtime (SourceAnalyzer.analyze()).
    default_source_analyzer_provider: str = "anthropic"

    # --- Política de quórum (ver Etapa 4: QuorumPolicy em app/orchestrator/config.py) ---
    # Estes são os ÚNICOS valores canônicos dos limiares de quórum — o
    # app/orchestrator/config.py:QuorumPolicy é sempre construído a
    # partir daqui (QuorumPolicy.from_settings), nunca com defaults
    # próprios independentes, pra não existirem duas fontes de verdade
    # que possam divergir.
    # min_for_debate: nº mínimo de respostas bem-sucedidas pra seguir
    #   normalmente (comparação/debate/juiz, quando existirem).
    # min_to_return: nº mínimo pra ainda retornar algo (sem debate),
    #   marcado com insufficient_data_for_consensus=True. Abaixo disso,
    #   a execução aborta com erro.
    #
    # Deployment Execution Configuration Boundary V1 -- a FORMA destes
    # dois valores (positividade + `min_to_return <= min_for_debate`) é
    # validada em `app/bootstrap.py`, construindo o `QuorumPolicy` REAL
    # (`QuorumPolicy.from_settings`) durante a composição do deployment
    # -- NUNCA reimplementada aqui como uma segunda comparação
    # independente que pudesse divergir da regra canônica de
    # `QuorumPolicy` (app/orchestrator/config.py). `app/config.py` não
    # pode importar `QuorumPolicy` diretamente (importaria
    # `app.orchestrator.config`, que já importa `Settings` daqui --
    # ciclo) -- por isso a validação de forma mora no composition root,
    # não num field_validator local.
    quorum_min_for_debate: int = 2
    quorum_min_to_return: int = 1

    # --- Timeouts e retry para chamadas a providers ---
    provider_timeout_seconds: int = 60
    provider_max_retries: int = 2

    # --- Timeout de dispatch de UMA rodada de providers em paralelo ---
    # Renomeado de `orchestrator_overall_timeout_seconds` (clarificação de
    # contrato de execução, pós-run real): o nome antigo dava a entender
    # um prazo pra EXECUÇÃO INTEIRA do Council (extração, agrupamento,
    # Source Analysis, Judge, Editor incluídos) -- não é isso. Cada
    # RODADA de dispatch paralelo (rodada inicial, rodada de crítica)
    # aplica este mesmo valor de forma INDEPENDENTE (reinicia por
    # rodada, nunca um orçamento compartilhado/decrescente) -- ver
    # `app/orchestrator/orchestrator.py::Orchestrator._execute_all`.
    # Salvaguarda ADICIONAL ao timeout por provider (que já existe em
    # provider_timeout_seconds e é responsabilidade do LLMProvider). Se
    # estourar, o Orchestrator cancela explicitamente as tasks ainda
    # pendentes DAQUELA rodada.
    #
    # Compatibilidade: aceita a variável de ambiente legada
    # ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS como alias de entrada (um
    # .env já em uso não pode silenciosamente parar de funcionar) --
    # ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS é a canônica; se as
    # duas estiverem definidas, a canônica vence (ordem em AliasChoices).
    # Deployment Execution Configuration Boundary V1 -- `allow_inf_nan=False`
    # ver comentário de `default_max_cost_usd` acima pro mesmo motivo:
    # `+inf` sobreviveria a `gt=0` sozinho e só quebraria depois, na
    # serialização JSON estrita da resposta pública.
    orchestrator_round_dispatch_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices(
            "ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS",
            "ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS",
        ),
    )

    # --- App ---
    log_level: str = "INFO"

    @field_validator(
        "openai_default_model", "anthropic_default_model", "gemini_default_model"
    )
    @classmethod
    def _default_model_identifier_is_well_formed(cls, value: str, info: ValidationInfo) -> str:
        """Deployment Execution Configuration Boundary V1 -- rejeita
        (nunca trima silenciosamente) um identificador de modelo padrão
        vazio, só espaço em branco, ou com espaço em branco líder/final.
        Um valor válido é preservado byte/string-equivalente ao input
        configurado -- esta função nunca reescreve, só aceita ou
        rejeita."""
        if value.strip() == "":
            raise ValueError(
                f"{info.field_name} não pode ser vazio ou conter só espaço em branco"
            )
        if value != value.strip():
            raise ValueError(
                f"{info.field_name} não pode ter espaço em branco líder/final: {value!r}"
            )
        return value
