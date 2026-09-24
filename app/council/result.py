"""
`CouncilRunResult` — Etapa 8.

Amarra os 3 resultados de fase (`DebateResult`/`JudgeResult`/`EditorResult`) mais
identidade/timestamps da execução top-level. Não duplica nada que já exista em algum
desses três.

Fronteira `@property` vs `@computed_field` (verificada contra o comportamento REAL do
Pydantic instalado — 2.13.5, `pyproject.toml` pin `>=2.7` — não presumida):
`@computed_field` participa de `model_dump()`/`model_dump_json()`; `@property` comum não.

- `final_answer`/`status`/`cumulative_budget_exceeded`: `@property` comum. Cada um já
  existe verbatim em algum lugar acessível a partir de `editor_result` — usar
  `@computed_field` aqui duplicaria o payload serializado (no caso de `final_answer`,
  duplicaria o objeto `FinalAnswer` inteiro). São conveniências Python, nunca
  re-serializadas.
- `total_input_tokens`/`total_output_tokens`/`total_cost_usd`: `@computed_field`. Estes
  SÃO informação genuinamente nova — nenhum dos três resultados de fase, sozinho, expõe a
  soma combinada — então cabem na serialização sem duplicar nenhum objeto existente.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.debate.result import DebateResult
from app.editor.natural_answer_coherence import (
    NaturalAnswerCoherenceError,
    validate_natural_answer_coherence,
)
from app.editor.linguistic_realization_coherence import (
    LinguisticRealizationCoherenceError,
    validate_linguistic_realization_coherence,
)
from app.editor.primary_answer_coherence import (
    PrimaryAnswerCoherenceError,
    validate_primary_answer_coherence,
)
from app.editor.result import EditorResult, FinalAnswer
from app.judge.result import JudgeResult
from app.orchestrator.config import RunConfig
from app.reconciliation.models import SourceJudgeReconciliationResult
from app.source_analysis.result import SourceAnalysisResult

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CouncilRunResult(BaseModel):
    """Resultado de uma execução completa do Council. Só existe pra execuções que
    chegaram até o `Editor` e ele retornou um `EditorResult` — se qualquer estágio
    anterior levantar exceção, nenhum `CouncilRunResult` chega a ser construído (ver
    `app/council/runner.py`)."""

    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    # A MESMA instância de RunConfig usada na execução — não é um "snapshot"
    # persistente (RunConfig.enabled_providers é list[str] mutável mesmo com
    # frozen=True no model — frozen bloqueia reatribuir o CAMPO, não mutar a
    # lista em si; verificado contra o comportamento real do Pydantic). Uma
    # cópia congelada de verdade fica pra quando a Etapa 10 (persistência)
    # existir; nomear isso "snapshot" agora seria uma promessa que o tipo
    # não cumpre.
    run_config: RunConfig
    debate_result: DebateResult
    # Etapa 16 -- None SÓ quando run_config.source_text is None (nenhuma
    # fonte fornecida: a análise nem existe). Ver
    # app/source_analysis/result.py pra semântica completa.
    source_analysis_result: SourceAnalysisResult | None
    judge_result: JudgeResult
    editor_result: EditorResult
    # Cross-Channel Reconciliation V1 -- classificação DETERMINÍSTICA de
    # como o canal Judge e o canal Source Analysis se relacionam, por
    # claim corrente (ver app/reconciliation/models.py). Custa ZERO
    # tokens/dólares (função pura, sem chamada de provider) -- por isso
    # nunca entra nos `@computed_field` de accounting abaixo.
    #
    # `None` aqui significa EXCLUSIVAMENTE "execução persistida antes
    # deste slice existir" -- toda execução NOVA que chega a este ponto
    # do pipeline (depois do Judge, ver app/council/runner.py) sempre tem
    # um `SourceJudgeReconciliationResult` concreto, mesmo sem fonte e/ou
    # sem veredito do Judge (`status="judge_unavailable"` nesse caso,
    # nunca `None`). NUNCA reinterpretado como "not_comparable" -- ver
    # docstring de `SourceJudgeReconciliationResult`.
    reconciliation: SourceJudgeReconciliationResult | None = None
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def _primary_answer_is_coherent_with_this_run(self) -> CouncilRunResult:
        """Um Primary Answer não-nulo nunca reivindica autoridade que os
        registros DESTA execução (claims atuais, avaliações do Judge,
        limitações, tentativa de planejamento) não sustentam -- vale na
        construção e na reconstrução a partir do storage (fail-closed)."""
        try:
            validate_primary_answer_coherence(
                self.debate_result, self.judge_result, self.editor_result
            )
        except PrimaryAnswerCoherenceError as exc:
            raise ValueError(str(exc)) from exc
        # Natural Answer -- depende do primary_answer JÁ coerente (checado
        # acima, mesma ordem que app/storage/repository.py::save_success
        # segue) -- confere `natural_answer` contra ele, nunca contra
        # debate/judge diretamente (ver docstring do módulo).
        try:
            validate_natural_answer_coherence(self.editor_result.final_answer)
        except NaturalAnswerCoherenceError as exc:
            raise ValueError(str(exc)) from exc
        try:
            validate_linguistic_realization_coherence(
                self.editor_result.final_answer,
                self.editor_result,
                question=self.run_config.question,
            )
        except LinguisticRealizationCoherenceError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_input_tokens(self) -> int:
        """Soma de 4 parcelas NÃO SOBREPOSTAS (verificado lendo cada
        fonte): DebateResult.cumulative_* cobre só rodada inicial+crítica
        +claim processing; SourceAnalysisResult (Etapa 16) cobre só
        chamadas reais de análise de fonte -- toda chamada real de LLM
        conta pro orçamento agregado, mesmo sendo audit-only nesta etapa;
        JudgeResult.judge_* cobre só attempts do Judge; EditorResult.editor_*
        cobre só attempts do Editor."""
        source_tokens = (
            self.source_analysis_result.source_analysis_input_tokens
            if self.source_analysis_result is not None
            else 0
        )
        return (
            self.debate_result.cumulative_input_tokens
            + source_tokens
            + self.judge_result.judge_input_tokens
            + self.editor_result.editor_input_tokens
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_output_tokens(self) -> int:
        source_tokens = (
            self.source_analysis_result.source_analysis_output_tokens
            if self.source_analysis_result is not None
            else 0
        )
        return (
            self.debate_result.cumulative_output_tokens
            + source_tokens
            + self.judge_result.judge_output_tokens
            + self.editor_result.editor_output_tokens
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cost_usd(self) -> float:
        source_cost = (
            self.source_analysis_result.source_analysis_cost_usd
            if self.source_analysis_result is not None
            else 0.0
        )
        return (
            self.debate_result.cumulative_cost_usd
            + source_cost
            + self.judge_result.judge_cost_usd
            + self.editor_result.editor_cost_usd
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        """OR dos 4 estágios (Etapa 16 adiciona source_analysis_result) —
        agregação genuinamente nova, por isso `@computed_field`."""
        source_unknown = (
            self.source_analysis_result.has_unknown_accounting_components
            if self.source_analysis_result is not None
            else False
        )
        return (
            self.debate_result.has_unknown_accounting_components
            or source_unknown
            or self.judge_result.has_unknown_accounting_components
            or self.editor_result.has_unknown_accounting_components
        )

    @property
    def final_answer(self) -> FinalAnswer:
        """Conveniência — mesma instância de editor_result.final_answer,
        nunca uma segunda cópia. Deliberadamente `@property` comum (não
        `@computed_field`) pra não duplicar o objeto inteiro em
        model_dump()."""
        return self.editor_result.final_answer

    @property
    def status(self) -> Literal[
        "llm_planned", "llm_composed", "deterministic_from_verdict", "deterministic_no_verdict"
    ]:
        """Passthrough de editor_result.final_answer.status — nunca um
        valor armazenado independente que poderia divergir dele."""
        return self.editor_result.final_answer.status

    @property
    def cumulative_budget_exceeded(self) -> bool:
        """Passthrough de editor_result.cumulative_budget_exceeded.

        Continua correto após o Stage 16 (patch de revisão): Editor
        recebe `prior_input_tokens`/`prior_output_tokens`/`prior_cost_usd`
        de `CouncilRunner.run()` já incluindo Debate + Source Analysis
        (se houve) + Judge -- então `editor_result.cumulative_budget_exceeded`
        já é o total dos 4 estágios reais corretamente calculado em
        qualquer branch do Editor, nunca recalculado aqui."""
        return self.editor_result.cumulative_budget_exceeded
