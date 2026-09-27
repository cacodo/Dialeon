"""
Erros da camada de aplicação -- Etapa 14 (T19A.1).

Domain/application-neutral: nunca importam FastAPI nem qualquer coisa de
`app.api`. API e CLI traduzem estes erros pra sua própria representação
(HTTP 422 + `error.code`, ou exit code 2) -- mas a REGRA em si (o que
conta como provider desconhecido) mora aqui, uma única vez, checada por
`CouncilExecutionService.run()` antes de qualquer chamada ao
`CouncilRunner`. Antes desta etapa essa checagem só existia em
`app/api/routes.py` -- um cliente embarcado (CLI) que chamasse o service
direto teria essa validação pulada, caindo num `ValueError` cru vindo de
`Orchestrator.run_round()` bem mais fundo no pipeline (achado do Repo
Evidence Pack de T19A.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.council.readiness import CouncilReadiness


class UnknownProviderError(Exception):
    """Alguma autoridade de provider do `RunConfig`
    (`RunConfig.all_provider_authorities` -- `enabled_providers` OU
    qualquer um dos 4 papéis internos: claim processor, judge, editor,
    source analyzer -- ver T02.4 repair) contém nome(s) que não existem
    no registry de providers construído (`AppComponents.providers`)."""

    def __init__(self, unknown_providers: list[str], known_providers: list[str]):
        self.unknown_providers = unknown_providers
        self.known_providers = known_providers
        super().__init__(
            f"provider(s) desconhecido(s): {unknown_providers}; "
            f"providers disponíveis: {known_providers}"
        )


class InvalidQuestionError(Exception):
    """Accepted Question Size Boundary V1 -- `RunConfig.question`
    viola a regra canônica de aceite de execuções NOVAS (vazia/só
    espaço em branco, ou excede
    `app.orchestrator.config.MAX_QUESTION_CHARACTERS`). Levantada por
    `CouncilExecutionService.run()` ANTES de qualquer mintagem de
    run_id/`save_accepted`/chamada ao `CouncilRunner` -- protege
    chamadores diretos do service que construíram um `RunConfig` sem
    passar pela validação antecipada de `CreateRunRequest` (mesma
    disciplina de `UnknownProviderError` acima). A regra em si mora só
    em `validate_question` (app/orchestrator/config.py) -- esta
    exceção nunca reimplementa a checagem, só a traduz pro vocabulário
    de erro desta camada."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class InvalidQuorumConfigurationError(Exception):
    """Accepted Quorum Feasibility Boundary V1 -- `RunConfig.quorum.min_to_return`
    excede `len(RunConfig.enabled_providers)`: uma execução NOVA que
    NUNCA poderia satisfazer seu próprio quórum de retorno, mesmo que
    TODO participante selecionado tenha sucesso. Levantada por
    `CouncilExecutionService.run()` ANTES de qualquer mintagem de
    run_id/`save_accepted`/chamada ao `CouncilRunner` -- mesma
    disciplina de `InvalidQuestionError`/`UnknownProviderError` acima.

    A regra em si mora só em `validate_quorum_feasibility`
    (app/orchestrator/config.py) -- esta exceção nunca reimplementa a
    comparação, só a traduz pro vocabulário de erro desta camada.
    Distinta de `InsufficientQuorumError`
    (app/orchestrator/errors.py): aquela é uma falha de EXECUÇÃO real
    (uma configuração FACTÍVEL foi de fato despachada, mas o número
    OBSERVADO de sucessos ficou abaixo de `min_to_return`); esta é uma
    falha de CONFIGURAÇÃO detectada ANTES de qualquer dispatch --
    nenhuma chamada de provider chega a ocorrer.

    Expõe só fatos não sensíveis (contagem de participantes
    selecionados e o `min_to_return` exigido) -- nunca configuração
    interna/segredos/papéis internos de provider."""

    def __init__(self, min_to_return: int, participant_count: int):
        self.min_to_return = min_to_return
        self.participant_count = participant_count
        super().__init__(
            f"quorum.min_to_return ({min_to_return}) excede o número de "
            f"providers selecionados ({participant_count})"
        )


class InvalidExecutionLimitsError(Exception):
    """Finite RunConfig New-Execution Boundary V1 -- `RunConfig.max_cost_usd`
    e/ou `RunConfig.round_dispatch_timeout_seconds` não são positivos e
    finitos. Levantada por `CouncilExecutionService.run()` ANTES de
    qualquer mintagem de run_id/`save_accepted`/chamada ao
    `CouncilRunner` -- mesma disciplina de `InvalidQuestionError`/
    `InvalidQuorumConfigurationError` acima.

    A regra em si mora só em `validate_execution_limits_for_new_execution`
    (app/orchestrator/config.py) -- esta exceção nunca reimplementa a
    checagem, só a traduz pro vocabulário de erro desta camada. Um
    `RunConfig` com um destes campos em `+inf` continua CONSTRUÍVEL
    (reconstrução histórica, `RunConfig(**dados_persistidos)`) -- só
    ACEITAR essa configuração como execução NOVA é que é rejeitado
    aqui."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LocalPrerequisitesMissingError(Exception):
    """Direct Answer Execution V1 -- o provider escolhido para uma run
    direta está `missing` (`LLMProvider.local_prerequisite_state()`): falta
    um pré-requisito local conhecido, então a chamada nunca poderia sair do
    processo. Levantada ANTES do aceite durável -- nenhum registro é criado,
    nenhuma chamada é tentada. Nunca carrega valor, tamanho ou nome de
    credencial -- só o identificador do provider."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"o provider {provider!r} não tem a configuração local necessária")


class CouncilPrerequisitesMissingError(Exception):
    """Council Local Execution Readiness & Admission V1 -- admissão ESTRITA
    pedida e alguma dependência do caminho pedido do Conselho (participante
    selecionado ou papel interno que o caminho pode alcançar) tem ausência
    local CONHECIDA (`strict_admission_blockers`). Levantada ANTES do aceite
    durável -- nenhum registro é criado, nenhuma chamada é tentada. Carrega a
    avaliação usada na decisão (só identificadores, estados e modelos
    configurados -- nunca valor, tamanho ou nome de credencial)."""

    def __init__(self, readiness: CouncilReadiness):
        self.readiness = readiness
        blockers = ", ".join(f"{d.role}={d.provider}" for d in readiness.known_missing)
        super().__init__(f"dependências do Conselho sem configuração local necessária: {blockers}")


class CouncilDegradationChangedError(Exception):
    """Council Local Execution Readiness & Admission V1 -- o cliente
    reconheceu uma degradação local (`acknowledged_degradation_fingerprint`),
    mas a avaliação feita no aceite tem OUTRA identidade de degradação (outro
    conjunto de ausências conhecidas, ou nenhuma): o reconhecimento não vale
    pro que seria executado. Levantada ANTES do aceite durável -- nenhum
    registro é criado, nenhuma chamada é tentada. Carrega a avaliação nova,
    pra que o cliente mostre o que mudou e peça um reconhecimento novo."""

    def __init__(self, readiness: CouncilReadiness, acknowledged_fingerprint: str | None):
        self.readiness = readiness
        self.acknowledged_fingerprint = acknowledged_fingerprint
        super().__init__("a degradação local reconhecida não é a avaliada no aceite")


class InvalidParticipantModelOverrideError(Exception):
    """Council Accepted Effective Participant Model Choice V1 -- escolha
    explícita de modelo inválida: para um provider que não é participante
    selecionado, ou com identificador mal formado (ver
    `app/orchestrator/participant_models.py`). Levantada ANTES do aceite
    durável -- nenhum registro é criado, nenhuma chamada é tentada. Só forma
    e seleção: nunca "o modelo não existe no fornecedor" (isso não é
    verificável localmente)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class InvalidDirectModelError(Exception):
    """Direct Accepted Effective Model Choice V1 -- o modelo pedido
    explicitamente numa run direta tem identificador mal formado (mesma regra
    de forma da escolha dos participantes do Conselho,
    `validate_model_override_identifier`). Levantada ANTES do aceite durável
    -- nenhum registro é criado, nenhuma chamada é tentada. Só forma: nunca
    "o modelo não existe no fornecedor" (isso não é verificável localmente).
    `reason` nunca ecoa cru um caractere invisível (a regra o nomeia como
    U+XXXX)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
