"""Resultado agregado da Fase 1 (respostas independentes) do Orchestrator."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import ModelResponse


class RoundResult(BaseModel):
    """Dado CRU de uma rodada de execução paralela — produzido por
    `Orchestrator.run_round()`. Sem quórum, sem budget, sem semântica de
    debate: só o que aconteceu (respostas normalizadas + contagens/totais
    brutos). Quem decide o que fazer com isso é o chamador —
    `Orchestrator.run()` aplica QuorumPolicy e produz `InitialResponsesResult`;
    o Debate Engine aplica sua própria política (sem exceção de quórum) e
    produz `CritiqueResult` (em app/debate/). `RoundResult` em si não sabe
    qual dessas duas interpretações vai receber — é reusado sem alteração
    pelas duas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_number: int = Field(ge=1)
    responses: list[ModelResponse] = Field(min_length=1)
    successful_count: int = Field(ge=0)
    total_participants: int = Field(ge=1)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    # Soma de tudo que TEM cost_usd conhecido — nunca confundir com "custo
    # total real": ver has_unknown_accounting_components ao lado (Etapa 9).
    total_cost_usd: float = Field(ge=0.0)
    # True se qualquer response desta rodada tinha cost_usd=None — nunca
    # apagado nem tratado como zero; convive com total_cost_usd sem
    # alterar seu valor numérico.
    has_unknown_accounting_components: bool


class InitialResponsesResult(BaseModel):
    """Agregado devolvido por `Orchestrator.run()` — escopado deliberadamente
    à Fase 1 (respostas independentes, sem debate). Renomeado de
    `ExecutionResult` (correção pós-Etapa-4, revisão item 2): o nome
    anterior era genérico o bastante para convidar futuras etapas a
    colar `claims`, `debate_rounds`, `judge_verdict` etc. aqui dentro,
    misturando o resultado de uma fase com o de todas.

    Quando o Debate Engine, Judge etc. existirem, cada um terá seu
    próprio agregado (ex.: um futuro `DebateResult`), e um objeto de
    nível mais alto (ex.: `RunResult`) os compõe — NÃO este objeto
    crescendo com campos de outras fases. Essa composição ainda não
    existe nesta etapa; só a intenção fica registrada aqui.

    Contém os `ModelResponse` de todos os providers habilitados (sucesso
    ou erro — nada é descartado, tudo fica auditável) mais os totais e
    flags calculados sobre eles. Não decide nada sozinho: quem lê este
    objeto (a API, e futuramente o Debate Engine) é quem decide o que
    fazer com `insufficient_data_for_consensus` ou `budget_exceeded`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    responses: list[ModelResponse] = Field(min_length=1)
    successful_count: int = Field(ge=0)
    total_providers: int = Field(ge=1)
    insufficient_data_for_consensus: bool
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    has_unknown_accounting_components: bool
    budget_exceeded: bool

