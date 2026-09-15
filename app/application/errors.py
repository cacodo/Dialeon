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
