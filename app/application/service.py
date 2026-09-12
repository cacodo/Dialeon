"""
`CouncilExecutionService` -- Etapa 10.

A boundary entre `CouncilRunner` (lógica pura de pipeline, Etapa 8) e
`CouncilRepository` (persistência, Etapa 10) -- nenhum dos dois conhece o
outro. Não se chama "Coordinator" de propósito: `CouncilRunner` já é quem
coordena/sequencia as etapas do pipeline; este objeto faz uma coisa
diferente e mais externa -- executar e then registrar o resultado (ou a
falha) para auditoria.

Fluxo (Decision Delta §5):

    caller
      -> captura started_at
      -> chama CouncilRunner.run()
      -> sucesso: repo.save_success(...)
      -> quorum failure: repo.save_quorum_failure(...)
      -> retorna o resultado, ou relança a exceção (sempre relança aqui —
         ver docstring de `run()`)

Não minta `run_id` antes de chamar `CouncilRunner.run()` (Decision Delta
§5, explícito) -- para sucesso, o id É `CouncilRunResult.id` (já mintado
por `CouncilRunner`); para falha de quórum, o id só passa a existir dentro
de `repository.save_quorum_failure()`, no momento em que o registro de
falha é criado.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.errors import UnknownProviderError
from app.council.result import CouncilRunResult
from app.council.runner import CouncilRunner
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.storage.repository import CouncilRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CouncilExecutionService:
    """Executa um `CouncilRunner` e persiste o desfecho -- sucesso ou
    falha de quórum -- numa única chamada. `CouncilRunner`/`Orchestrator`
    nunca precisam saber que isso existe.

    `known_providers` (Etapa 14, T19A.1): o conjunto de nomes de provider
    realmente construídos (mesma fonte que `AppComponents.providers`) --
    é contra ele que `run_config.enabled_providers` é validado, ANTES de
    qualquer chamada ao `CouncilRunner`. Essa checagem morava só em
    `app/api/routes.py` até esta etapa; movida pra cá porque é a boundary
    reutilizável de verdade -- API e CLI (e qualquer cliente futuro)
    compartilham a MESMA regra por construção, em vez de cada um
    reimplementá-la (achado do Repo Evidence Pack de T19A.1)."""

    def __init__(
        self,
        runner: CouncilRunner,
        repository: CouncilRepository,
        known_providers: frozenset[str] | set[str],
    ):
        self._runner = runner
        self._repository = repository
        self._known_providers = frozenset(known_providers)

    async def run(self, run_config: RunConfig) -> CouncilRunResult:
        """Executa e persiste. Em sucesso, devolve o `CouncilRunResult`
        normalmente (já persistido). Em `InsufficientQuorumError`, persiste
        o registro de falha auditável, anexa o id do registro recém-criado
        em `exc.persisted_failure_id` (campo formal, Etapa 11 — não um
        atributo dinâmico) e RELANÇA a MESMA instância — o contrato de
        erro do chamador não muda por causa da persistência existir; ela
        só passa a deixar rastro recuperável. Qualquer outra exceção
        (config inválida, bug) propaga sem ser tocada — não é
        responsabilidade desta camada (Decision Delta §4, fora de
        escopo).

        `UnknownProviderError` (Etapa 14): levantada ANTES de qualquer
        chamada ao runner/persistência -- nenhum `started_at` é
        capturado, nenhuma tentativa é feita, exatamente como o
        comportamento HTTP anterior (rejeitado antes do service ser
        chamado) exceto que agora a checagem em si mora aqui, não em
        `routes.py`."""
        unknown = [p for p in run_config.enabled_providers if p not in self._known_providers]
        if unknown:
            raise UnknownProviderError(
                unknown_providers=unknown, known_providers=sorted(self._known_providers)
            )

        started_at = _now()
        try:
            result = await self._runner.run(run_config)
        except InsufficientQuorumError as exc:
            failed_at = _now()
            failure_id = await self._repository.save_quorum_failure(
                exc, run_config=run_config, started_at=started_at, failed_at=failed_at
            )
            exc.persisted_failure_id = failure_id
            raise

        await self._repository.save_success(result)
        return result
