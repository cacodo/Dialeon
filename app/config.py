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
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API keys dos providers (Fase 1 do MVP: OpenAI, Anthropic, Gemini) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- Modelo padrão por provider, usado quando a requisição não especifica ---
    # Definidos aqui (não hardcoded nos providers) para facilitar atualização
    # quando os fornecedores lançarem novas versões.
    openai_default_model: str = "gpt-5.5"
    anthropic_default_model: str = "claude-sonnet-5"
    gemini_default_model: str = "gemini-3.7-flash"

    # --- Banco de dados ---
    database_url: str = "sqlite+aiosqlite:///./llm_council.db"

    # --- Limites padrão de execução (podem ser sobrescritos por request) ---
    # Dois conceitos distintos de "tokens", nunca confundidos entre si:
    # default_max_total_tokens = orçamento AGREGADO da execução inteira
    #   (soma de input+output de todos os providers), verificado DEPOIS
    #   que as respostas chegam — não limita nenhuma chamada individual.
    # default_max_output_tokens_per_call = teto de output de UMA chamada
    #   a UM provider, repassado para CompletionRequest.max_tokens antes
    #   da chamada — é o que de fato limita quanto texto cada resposta
    #   pode ter.
    default_max_cost_usd: float = 1.00
    default_max_total_tokens: int = 50_000
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
    default_max_output_tokens_per_call: int = 4096

    # Etapa 17A.2 -- investigação confirmou amplificação estrutural de
    # escala: agrupamento e Judge têm schema de output com cobertura
    # OBRIGATÓRIA (uma entrada por claim bruta/atual), então o tamanho
    # mínimo exigido de output cresce com a contagem de claims, nunca
    # limitado por nada além do teto de tokens -- ao contrário de
    # extração/crítica, cujo output é por natureza O(1) por chamada
    # (uma resposta de cada vez). Tetos PRÓPRIOS, mais generosos,
    # continuam um único valor fixo cada (nenhum cálculo dinâmico por
    # contagem de claims, nenhuma alocação adaptativa) -- só reconhece
    # que agrupamento/Judge não deveriam compartilhar o mesmo teto de
    # extração/crítica/participante.
    default_max_output_tokens_grouping: int = 8192
    default_max_output_tokens_judge: int = 8192

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
    orchestrator_round_dispatch_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices(
            "ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS",
            "ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS",
        ),
    )

    # --- App ---
    log_level: str = "INFO"
