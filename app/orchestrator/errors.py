"""Exceções específicas do Orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestrator.result import RoundResult


class InsufficientQuorumError(Exception):
    """Levantada quando o número de respostas bem-sucedidas fica abaixo de
    `QuorumPolicy.min_to_return` — nem uma execução "insuficiente mas
    utilizável" é possível, então a execução aborta em vez de devolver
    um InitialResponsesResult.

    Contrato de Orchestrator.run(): RunConfig -> InitialResponsesResult
    | raise InsufficientQuorumError. Como um futuro chamador
    multi-rodada (Debate Engine) deve reagir a esta exceção — abortar
    tudo, tentar de novo, outra política — NÃO é definido aqui
    (correção pós-Etapa-4, revisão item 3); fica para quando o Debate
    Engine for desenhado. A semântica de quórum de uma execução isolada
    não muda por causa disso.

    `round_result` (Etapa 10, mudança aditiva): a rodada inicial JÁ
    ESTÁ completa no momento em que esta exceção é levantada (auditoria
    do repo real — `_apply_quorum_and_budget` recebe `round_result` já
    construído, com todas as `ModelResponse` reais, incluindo as que já
    tiveram custo real incorrido, ANTES de checar o quórum) — sem isso,
    essa informação era descartada junto com a pilha, e uma execução que
    já gerou custo real (ex.: 1 de 3 providers respondeu, e foi cobrado)
    desaparecia sem deixar rastro nenhum pra auditoria. `InitialResponsesResult`
    nunca chega a ser construído quando o quórum falha — `round_result`
    é o objeto mais primitivo, e já contém tudo que `InitialResponsesResult`
    teria derivado dele.

    `persisted_failure_id` (Etapa 11, mudança aditiva): campo formal,
    não um atributo dinâmico via setattr — nasce sempre `None` aqui (o
    Orchestrator não sabe nada de persistência), e é preenchido por
    `CouncilExecutionService.run()` DEPOIS de `repository.save_quorum_failure(...)`
    retornar o id do registro recém-criado, na MESMA instância antes do
    `raise` (nunca uma exceção nova/wrapper — ver app/application/service.py).
    Antes da persistência (ex.: dentro do próprio Orchestrator/DebateEngine),
    vale sempre `None`."""

    def __init__(
        self,
        successful_count: int,
        total_providers: int,
        min_to_return: int,
        round_result: "RoundResult",
    ):
        self.successful_count = successful_count
        self.total_providers = total_providers
        self.min_to_return = min_to_return
        self.round_result = round_result
        self.persisted_failure_id: str | None = None
        super().__init__(
            f"Quórum insuficiente: {successful_count}/{total_providers} providers "
            f"responderam com sucesso, abaixo do mínimo de {min_to_return} "
            "necessário para retornar qualquer resultado."
        )
