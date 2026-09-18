"""
Configuração de UMA execução do Orchestrator (Etapa 4).

Separado de `app/config.py:Settings` deliberadamente: Settings é
configuração global do app (lida do .env, uma vez, no processo inteiro);
RunConfig é imutável e específico de uma execução — a API monta um
RunConfig a partir de Settings + input do usuário, e o Orchestrator só
enxerga o RunConfig. Ele nunca lê nem escreve Settings diretamente, e
nada vindo de uma resposta de LLM chega perto de nenhum dos dois: RunConfig
é construído inteiramente ANTES de qualquer chamada a provider.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Etapa 16 — limite canônico ÚNICO de source_text, em CARACTERES (nunca
# tokens de um tokenizer específico). Reusado por RunConfig (autoritativo)
# e CreateRunRequest (app/presentation/schemas.py, validação antecipada
# pra erro HTTP/CLI limpo) -- nunca duplicado como um segundo número que
# pudesse divergir. 20_000 é conservador o bastante pra caber com folga
# numa única chamada síncrona de análise (fonte + claims correntes), e
# generoso o bastante pra um excerpt de documento real colado pelo
# usuário -- não é garantia de contexto de nenhum provider específico, é
# só um teto de aplicação.
MAX_SOURCE_TEXT_CHARACTERS = 20_000


# Accepted Question Size Boundary V1 -- limite canônico ÚNICO de
# `question`, em CARACTERES Python (`len(str)` -- code points, nunca
# bytes UTF-8/grapheme clusters/tokens de um tokenizer específico).
# Reusado por `CreateRunRequest` (app/presentation/schemas.py,
# validação antecipada pra erro HTTP/CLI limpo e cedo) e
# `CouncilExecutionService.run()` (app/application/service.py, boundary
# de aceite autoritativa pra chamadores diretos do service) -- nunca
# duplicado como um segundo número que pudesse divergir. Semanticamente
# INDEPENDENTE de `MAX_SOURCE_TEXT_CHARACTERS` acima -- mesmo que os
# dois valores coincidam hoje, são limites de campos diferentes com
# motivações diferentes, e nunca devem ser aliasados um ao outro.
#
# Deliberadamente NUNCA aplicado como field_validator de
# `RunConfig.question` (ver classe abaixo): esse campo é reconstruído
# diretamente a partir de dado histórico persistido
# (`RunConfig(**run_config_json)`, app/storage/repository.py), e uma
# execução legítima de ANTES deste limite existir pode ter uma
# `question` mais longa que este teto -- carregar essa execução
# histórica pra auditoria precisa continuar funcionando, sem
# reinterpretar aceite passado à luz de uma regra que não existia
# quando ela foi aceita. A validação de aceite de execuções NOVAS mora
# inteiramente fora de `RunConfig`, nas duas boundaries citadas acima.
MAX_QUESTION_CHARACTERS = 20_000


def validate_question(value: str) -> str:
    """Única função de validação de `question` pra ACEITE DE EXECUÇÕES
    NOVAS -- nunca usada para reconstrução de dado histórico (ver
    comentário de `MAX_QUESTION_CHARACTERS` acima). Preserva o valor
    VERBATIM (nunca trima/normaliza/reescreve) -- só rejeita vazio/
    só-espaço-em-branco e valores que excedem o limite. Espaço em
    branco significativo ao REDOR de conteúdo real (ex.: "  pergunta
    válida  ") nunca é removido -- só o valor INTEIRO sendo em branco é
    rejeitado."""
    if not value.strip():
        raise ValueError("question não pode ser vazia ou só espaços em branco")
    if len(value) > MAX_QUESTION_CHARACTERS:
        raise ValueError(f"question excede o máximo de {MAX_QUESTION_CHARACTERS} caracteres")
    return value


def _normalize_and_validate_source_text(value: str | None) -> str | None:
    """Única função de normalização/validação de `source_text` do
    projeto -- RunConfig e CreateRunRequest delegam pra ela, nunca
    reimplementam a regra. None permanece None; vazio/só-espaço vira
    None (nenhuma fonte, não um erro -- é campo opcional, diferente de
    `question`); conteúdo não-vazio é preservado VERBATIM (nunca
    trimado/normalizado) e só rejeitado se exceder o limite."""
    if value is None:
        return None
    if not value.strip():
        return None
    if len(value) > MAX_SOURCE_TEXT_CHARACTERS:
        raise ValueError(
            f"source_text excede o máximo de {MAX_SOURCE_TEXT_CHARACTERS} caracteres"
        )
    return value


class QuorumPolicy(BaseModel):
    """Os dois limiares que definem os três desfechos possíveis da Fase 1.

    Substitui o antigo `Settings.min_successful_responses` (um único
    inteiro, incapaz de representar "2 seguimos normal / 1 retorna sem
    debate / 0 aborta" corretamente — misturava os dois limiares num só
    valor). Ver proposta da Etapa 4, item 2.

    Única fonte de verdade dos valores canônicos: `Settings.quorum_min_for_debate`
    e `Settings.quorum_min_to_return` (app/config.py). Este schema não
    tem valores default próprios — é sempre construído via
    `QuorumPolicy.from_settings(...)` ou com valores explícitos (em
    teste), nunca com um default hardcoded aqui que pudesse divergir do
    `Settings`.
    """

    model_config = _CONFIG

    min_for_debate: int = Field(ge=1)
    min_to_return: int = Field(ge=1)

    @model_validator(mode="after")
    def _thresholds_are_ordered(self) -> "QuorumPolicy":
        if self.min_to_return > self.min_for_debate:
            raise ValueError(
                "min_to_return não pode ser maior que min_for_debate"
            )
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> "QuorumPolicy":
        return cls(
            min_for_debate=settings.quorum_min_for_debate,
            min_to_return=settings.quorum_min_to_return,
        )


class RunConfig(BaseModel):
    """Configuração imutável de uma execução da Fase 1.

    `enabled_providers` é o único lugar que define quantos/quais
    providers participam — o "3" nunca é hardcoded em lugar nenhum do
    Orchestrator; é sempre `len(enabled_providers)`.

    `round_dispatch_timeout_seconds` (renomeado de `overall_timeout_seconds`
    -- clarificação de contrato, pós-run real onde a duração total
    excedeu o valor configurado) limita o DISPATCH PARALELO DE UMA
    RODADA -- a janela em que `Orchestrator._execute_all()` aguarda
    todas as respostas de UMA rodada (a rodada inicial da Fase 1, OU a
    rodada de crítica do Debate Engine). NÃO é um prazo pra execução
    inteira do Council: reinicia de forma INDEPENDENTE a cada rodada
    (mesmo valor aplicado de novo, nunca um orçamento compartilhado ou
    decrescente entre rodadas) e não cobre NENHUMA das fases fora do
    dispatch de rodada -- extração de claims, agrupamento, Source
    Analysis, Judge, Editor rodam depois/entre rodadas e usam só o
    timeout por chamada de `provider_timeout_seconds`
    (`app/config.py`), nunca este campo. Se estourar, o Orchestrator
    cancela explicitamente as tasks pendentes DAQUELA rodada — ver
    `Orchestrator._execute_all()`. Um verdadeiro prazo fim-a-fim pra
    execução inteira do Council permanece fora de escopo (não
    implementado nesta correção).

    Dois conceitos de "tokens", deliberadamente com nomes distintos pra
    nunca serem confundidos (correção pós-Etapa-4, revisão item 1):

    - `max_output_tokens_per_call`: teto de output de UMA chamada a UM
      provider. O Orchestrator passa este valor para
      `CompletionRequest.max_tokens` ao montar cada requisição — é o que
      de fato limita quanto texto uma resposta pode ter. Usado por
      dispatch de participante, extração de claims e crítica — todas
      chamadas SEM cobertura obrigatória de N claims (uma resposta por
      vez), mas isso NUNCA significou "tamanho de output trivialmente
      pequeno/constante" (Repair, Run02 claim-extraction exhaustion): a
      extração especificamente tem, desde este repair, um teto EXPLÍCITO
      e finito de cardinalidade de claims por resposta
      (`MAX_EXTRACTED_CLAIMS`, ver app/debate/schemas.py) -- antes disso,
      uma única resposta "rica" podia sozinha gerar uma lista de claims
      semanticamente ilimitada, consumindo o mesmo teto compartilhado com
      participante/crítica. `max_output_tokens_per_call` continua 4096
      (inalterado por este repair) -- a correção foi limitar
      CARDINALIDADE + raciocínio da chamada, nunca aumentar o teto.
    - `max_output_tokens_grouping`/`max_output_tokens_judge` (Etapa
      17A.2): tetos PRÓPRIOS, separados de `max_output_tokens_per_call`,
      usados respectivamente pela chamada de agrupamento
      (`app/debate/claim_extraction.py:group_claims`) e pela chamada do
      Judge (`app/judge/single_judge.py`). Investigação confirmou que
      essas duas operações têm schema de output com cobertura
      OBRIGATÓRIA (uma entrada por claim bruta/atual) — o tamanho mínimo
      exigido do output cresce com a contagem de claims, então
      compartilhar o mesmo teto fixo de uma chamada sem essa cobertura as
      deixava estruturalmente mais propensas a truncamento. Continuam
      valores FIXOS e config-driven (nenhum cálculo dinâmico por
      contagem de claims, nenhuma alocação adaptativa) — só reconhecem
      que são operações de natureza diferente.
    - `max_total_tokens`: orçamento AGREGADO da execução inteira (soma
      de input+output de TODOS os providers), comparado com o total
      DEPOIS que todas as respostas da Fase 1 chegam. Não limita
      nenhuma chamada individual — só sinaliza `budget_exceeded` no
      `InitialResponsesResult` depois do fato.

    Budget (`max_cost_usd`, `max_total_tokens`) é verificado DEPOIS que
    todas as respostas da Fase 1 chegam, nunca no meio — nesta fase
    todas as chamadas partem em paralelo de uma vez só, então não
    existe "meio" de onde economizar; parar tasks já em voo
    desperdiçaria trabalho já pago sem economizar nada. Isso é
    documentado aqui para não passar a falsa impressão de que o budget
    consegue interromper chamadas já em andamento. Um gate de budget
    ENTRE rodadas (parar antes de iniciar a próxima) é responsabilidade
    do futuro Debate Engine, não desta etapa.
    """

    model_config = _CONFIG

    # Accepted Question Size Boundary V1 -- deliberadamente SEM
    # field_validator de blank/tamanho máximo aqui (só o `min_length=1`
    # sintático de sempre) -- ver comentário de `validate_question`/
    # `MAX_QUESTION_CHARACTERS` acima pra por que RunConfig precisa
    # continuar diretamente reconstruível a partir de dado histórico
    # que pode legitimamente exceder o limite novo. A validação de
    # aceite de execuções NOVAS mora em `CreateRunRequest`
    # (app/presentation/schemas.py) e `CouncilExecutionService.run()`
    # (app/application/service.py), nunca aqui.
    question: str = Field(min_length=1)
    # Etapa 10: tuple, não list — era a única coleção mutável de
    # RunConfig/QuorumPolicy (confirmado por auditoria: único uso real é
    # iteração em Orchestrator.run_round, nenhuma mutação/indexação em
    # lugar nenhum). Pydantic aceita input como lista normalmente e
    # normaliza pra tuple — nenhum call site precisa mudar. Fecha
    # estruturalmente a brecha "CouncilRunResult.run_config guarda a
    # mesma instância, mas alguém pode mutar depois" (Etapa 8/9): um
    # tuple não pode ser mutado in-place por ninguém, então a mesma
    # instância pode continuar sendo guardada (sem deep copy) com
    # segurança.
    enabled_providers: tuple[str, ...] = Field(min_length=1)
    max_cost_usd: float = Field(gt=0)
    max_total_tokens: int = Field(gt=0)
    max_output_tokens_per_call: int = Field(gt=0)
    # Etapa 17A.2 -- ver docstring da classe. Sem default próprio aqui
    # (mesma disciplina de max_cost_usd/max_total_tokens/
    # max_output_tokens_per_call): a única fonte de um valor "padrão" é
    # `Settings` via `from_settings`, nunca um número solto que possa
    # divergir.
    max_output_tokens_grouping: int = Field(gt=0)
    max_output_tokens_judge: int = Field(gt=0)
    quorum: QuorumPolicy
    round_dispatch_timeout_seconds: float = Field(gt=0)
    # Provider que realiza extração de claims E agrupamento semântico
    # (Etapa 5). Não precisa pertencer a enabled_providers — pode ser um
    # provider dedicado, só processando, nunca respondendo à pergunta
    # original. A única validação possível aqui é sintática (não vazio);
    # confirmar que o nome corresponde a um provider real acontece em
    # runtime, quando o DebateEngine recebe os providers injetados (mesmo
    # padrão que Orchestrator.run_round já usa pra enabled_providers
    # desconhecidos).
    claim_processor_provider: str = Field(min_length=1)
    # Provider que realiza o julgamento final (Etapa 6). Mesma disciplina
    # de claim_processor_provider: não precisa pertencer a
    # enabled_providers, validação de existência real acontece em
    # runtime (SingleJudge.judge()), não aqui.
    judge_provider: str = Field(min_length=1)
    # Provider que compõe a resposta final (Etapa 7). Mesma disciplina de
    # judge_provider/claim_processor_provider: não precisa ser igual ao
    # Judge, não precisa participar do debate. Só precisa existir entre
    # os providers injetados QUANDO uma chamada LLM realmente for
    # iniciada — se não houver veredito, ou se o budget já estiver
    # fechado antes do Editor, esse provider nunca chega a ser
    # consultado (ver app/editor/compose.py, ordem de validação).
    editor_provider: str = Field(min_length=1)
    # Etapa 16 — provider dedicado da análise de fonte. Mesma disciplina
    # de judge_provider/editor_provider: não precisa participar do
    # debate, validação de existência real acontece em runtime
    # (SourceAnalyzer.analyze()). Sempre presente mesmo quando
    # source_text é None -- é config de papel de execução, não input
    # condicional (mesmo raciocínio de editor_provider existir mesmo
    # quando não há veredito pra compor).
    source_analyzer_provider: str = Field(min_length=1)
    # Fonte textual delimitada, opcional, fornecida pelo usuário no
    # momento da criação do Run -- imutável, existe ANTES de qualquer
    # execução (Etapa 16). None = nenhuma fonte fornecida. Normalizado/
    # validado por `_normalize_and_validate_source_text` -- AUTORITATIVO
    # aqui (RunConfig é diretamente construível, não pode depender de
    # CreateRunRequest ter rodado antes pra garantir o limite).
    source_text: str | None = None

    @field_validator("source_text")
    @classmethod
    def _source_text_normalized_and_bounded(cls, value: str | None) -> str | None:
        return _normalize_and_validate_source_text(value)

    @model_validator(mode="after")
    def _enabled_providers_has_no_duplicates(self) -> "RunConfig":
        if len(set(self.enabled_providers)) != len(self.enabled_providers):
            raise ValueError("enabled_providers não pode conter duplicatas")
        return self

    @property
    def all_provider_authorities(self) -> frozenset[str]:
        """T02.4 (repair pós-revisão independente, MEDIUM) -- enumeração
        CANÔNICA de toda autoridade de provider que este RunConfig
        autoriza o pipeline a chamar: os 4 papéis internos
        (`claim_processor_provider`/`judge_provider`/`editor_provider`/
        `source_analyzer_provider`) SOMADOS a `enabled_providers`.

        Único lugar que soma os 5 campos -- qualquer validação de
        "provider desconhecido" (`CouncilExecutionService.run`, e
        futuras) deve iterar isto, nunca reimplementar a lista à mão,
        pra que um campo de autoridade futuro não escape silenciosamente
        de validação (achado da revisão independente: `bootstrap.py`
        validava só 3 dos 4 papéis internos, esquecendo
        `source_analyzer_provider` -- ver `_validate_internal_provider_config`).

        `@property`, nunca `@computed_field`: este valor NUNCA pode
        aparecer em `model_dump(mode="json")` -- `run_config_json` é
        persistido e reconstruído via `RunConfig(**data)`
        (`extra="forbid"`); um `computed_field` extra quebraria essa
        reconstrução (mesmo raciocínio de `CouncilRunResult.status`/
        `final_answer`, ver docstring daquela classe)."""
        return frozenset(self.enabled_providers) | {
            self.claim_processor_provider,
            self.judge_provider,
            self.editor_provider,
            self.source_analyzer_provider,
        }

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        question: str,
        enabled_providers: list[str],
        source_text: str | None = None,
    ) -> "RunConfig":
        """Constrói um RunConfig a partir dos defaults globais + input do
        usuário. Único ponto que traduz Settings -> RunConfig."""
        return cls(
            question=question,
            enabled_providers=enabled_providers,
            max_cost_usd=settings.default_max_cost_usd,
            max_total_tokens=settings.default_max_total_tokens,
            max_output_tokens_per_call=settings.default_max_output_tokens_per_call,
            max_output_tokens_grouping=settings.default_max_output_tokens_grouping,
            max_output_tokens_judge=settings.default_max_output_tokens_judge,
            quorum=QuorumPolicy.from_settings(settings),
            round_dispatch_timeout_seconds=settings.orchestrator_round_dispatch_timeout_seconds,
            claim_processor_provider=settings.default_claim_processor_provider,
            judge_provider=settings.default_judge_provider,
            editor_provider=settings.default_editor_provider,
            source_analyzer_provider=settings.default_source_analyzer_provider,
            source_text=source_text,
        )


def validate_quorum_feasibility(run_config: RunConfig) -> None:
    """Accepted Quorum Feasibility Boundary V1 -- ÚNICA função de
    validação de FACTIBILIDADE de quórum de retorno pra ACEITE DE
    EXECUÇÕES NOVAS (mesma disciplina de `validate_question` acima):
    rejeita configurações onde

        run_config.quorum.min_to_return > len(run_config.enabled_providers)

    -- uma execução assim NUNCA pode satisfazer seu próprio quórum de
    retorno, mesmo que TODO participante selecionado tenha sucesso
    (ex.: `enabled_providers=["openai"]` com `min_to_return=2`).
    Igualdade é válida (`min_to_return == len(enabled_providers)`);
    só o caso estritamente maior é rejeitado.

    Deliberadamente NÃO compara contra `min_for_debate`:
    `run_config.quorum.min_for_debate` pode legitimamente exceder
    `len(enabled_providers)` -- isso só significa que a crítica/debate
    nunca roda (`insufficient_initial_quorum`), não que a execução seja
    infactível -- a rodada inicial ainda pode produzir um resultado
    RETORNÁVEL válido sob a semântica atual de `QuorumPolicy`
    (`_apply_quorum_and_budget`, app/orchestrator/orchestrator.py). Não
    compara contra `all_provider_authorities` nem contra os 4 papéis
    internos (claim processor/judge/editor/source analyzer) -- nenhum
    deles participa da rodada inicial cujo `successful_count` alimenta
    `min_to_return`; esta é estritamente uma checagem de cardinalidade
    de PARTICIPANTES da rodada inicial.

    Reconstrução histórica NUNCA passa por aqui -- esta é uma boundary
    de ACEITE DE EXECUÇÃO NOVA, nunca um `field_validator`/
    `model_validator` de `RunConfig`/`QuorumPolicy`: um `RunConfig`
    persistido ANTES desta regra existir pode legitimamente violá-la
    (ex.: participantes perdidos por uma configuração antiga, ou uma
    Settings global mudada depois), e precisa continuar
    reconstruível/carregável pra auditoria
    (`RunConfig(**run_config_json)`) mesmo assim -- só EXECUTAR essa
    configuração através das boundaries de execução atuais
    (`CouncilExecutionService.run()`, `CouncilRunner.run()`,
    `Orchestrator.run()`) é que é rejeitado.

    ÚNICO ponto de comparação numérica desta regra no repositório --
    Service/Runner/Orchestrator/API/CLI delegam todos aqui, nunca
    reimplementam a comparação."""
    participant_count = len(run_config.enabled_providers)
    if run_config.quorum.min_to_return > participant_count:
        raise ValueError(
            "quorum.min_to_return "
            f"({run_config.quorum.min_to_return}) não pode exceder o número de "
            f"providers selecionados ({participant_count}) -- esta execução nunca "
            "poderia satisfazer seu próprio quórum de retorno, mesmo que todo "
            "participante selecionado tenha sucesso."
        )


# Etapa 17A.2 -- os dois nomes de campo que esta boundary verifica,
# reusado tanto pela função de validação abaixo quanto por qualquer
# teste que precise iterar sobre eles sem hardcodar a lista duas vezes.
_FINITE_EXECUTION_LIMIT_FIELDS = ("max_cost_usd", "round_dispatch_timeout_seconds")


def validate_execution_limits_for_new_execution(run_config: RunConfig) -> None:
    """Finite RunConfig New-Execution Boundary V1 -- ÚNICA função de
    validação de FINITUDE de `RunConfig.max_cost_usd`/
    `RunConfig.round_dispatch_timeout_seconds` pra ACEITE DE EXECUÇÕES
    NOVAS (mesma disciplina de `validate_question`/
    `validate_quorum_feasibility` acima, e de
    `validate_provider_execution_policy_for_new_execution`,
    app/models/provider_models.py):

        math.isfinite(value) and value > 0

    para cada um dos dois campos.

    `RunConfig` (`Field(gt=0)`, sem `allow_inf_nan=False`) continua
    CONSTRUÍVEL diretamente com `max_cost_usd=+inf`/
    `round_dispatch_timeout_seconds=+inf` -- deliberadamente: um
    `RunConfig` aceito ANTES de "Deployment Execution Configuration
    Boundary V1" existir pode ter sido persistido com um desses campos
    em `+inf` (`gt=0` sozinho nunca excluiu `+inf`, só `<=0`/`-inf`/
    NaN), e a reconstrução histórica
    (`RunConfig(**run_config_json)`, `app/storage/repository.py`)
    precisa continuar aceitando/preservando esse valor VERBATIM pra
    auditoria -- nunca clampado/reescrito/inferido/migrado. A
    representação pública histórica (`"positive_infinity"`, ver
    `app/presentation/mappers.py:_positive_execution_limit_public`)
    também permanece inalterada -- esta função nunca participa dessa
    conversão de apresentação, só da decisão de ACEITAR execução nova.

    CONSTRUÍVEL != AUTORIZADO PRA EXECUÇÃO NOVA: esta função é a
    boundary separada que aplica a segunda metade dessa distinção,
    chamada por TODA raiz de execução independente identificada por
    auditoria (`CouncilExecutionService.run()`, `CouncilRunner.run()`,
    `Orchestrator.run()`) -- nunca pela reconstrução histórica
    (`RunConfig(**dados_persistidos)`, carregamento de detail/audit).

    ÚNICO ponto de comparação numérica desta regra no repositório --
    qualquer chamador delega aqui, nunca reimplementa a checagem."""
    invalid = [
        (field_name, value)
        for field_name in _FINITE_EXECUTION_LIMIT_FIELDS
        if not (math.isfinite(value := getattr(run_config, field_name)) and value > 0)
    ]
    if invalid:
        details = ", ".join(f"{field_name}={value!r}" for field_name, value in invalid)
        raise ValueError(
            "RunConfig contém limite(s) de execução inválido(s) pra execução nova "
            f"(precisa ser positivo e finito): {details}"
        )
