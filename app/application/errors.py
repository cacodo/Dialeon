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
    """`RunConfig.enabled_providers` contém nome(s) que não existem no
    registry de providers construído (`AppComponents.providers`)."""

    def __init__(self, unknown_providers: list[str], known_providers: list[str]):
        self.unknown_providers = unknown_providers
        self.known_providers = known_providers
        super().__init__(
            f"provider(s) desconhecido(s): {unknown_providers}; "
            f"providers disponíveis: {known_providers}"
        )
