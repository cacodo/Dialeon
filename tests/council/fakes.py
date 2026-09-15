from __future__ import annotations

from app.debate.result import DebateResult
from app.editor.result import EditorResult
from app.judge.result import JudgeResult
from app.judge.strategy import JudgeStrategy
from app.orchestrator.config import RunConfig
from app.reconciliation.models import SourceJudgeReconciliationResult
from app.source_analysis.result import SourceAnalysisResult


class FakeDebateEngine:
    """Fake de DebateEngine — não subclassa nada (duck typing), prova que
    CouncilRunner não exige herança formal."""

    def __init__(self, result: DebateResult | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.calls: list[RunConfig] = []

    async def run(self, run_config: RunConfig) -> DebateResult:
        self.calls.append(run_config)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


class FakeSourceAnalyzer:
    """Etapa 16 — duck typing, mesmo padrão de FakeDebateEngine.
    `result=None` (default) é o caso mais comum nos testes de
    CouncilRunner que não exercitam Stage 16 -- equivale a "nenhuma
    fonte fornecida", exatamente o contrato real de
    SourceAnalyzer.analyze()."""

    def __init__(
        self, result: SourceAnalysisResult | None = None, exc: Exception | None = None
    ):
        self._result = result
        self._exc = exc
        self.calls: list[tuple[DebateResult, RunConfig]] = []

    async def analyze(
        self, debate_result: DebateResult, run_config: RunConfig
    ) -> SourceAnalysisResult | None:
        self.calls.append((debate_result, run_config))
        if self._exc is not None:
            raise self._exc
        return self._result


class FakeJudge(JudgeStrategy):
    """Fake formalmente subclassando JudgeStrategy — prova que o
    CouncilRunner funciona com qualquer implementação compatível da
    abstração, não com SingleJudge especificamente."""

    def __init__(self, result: JudgeResult | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.calls: list[tuple[DebateResult, RunConfig]] = []
        # Patch de revisão do Stage 16 -- registrado separado de `calls`
        # (que preserva a assinatura original de antes do patch, usada
        # por testes já existentes) pra quem quiser inspecionar o
        # accounting recebido especificamente.
        self.prior_accounting_calls: list[tuple[int, int, float]] = []

    async def judge(
        self,
        debate_result: DebateResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int = 0,
        prior_output_tokens: int = 0,
        prior_cost_usd: float = 0.0,
    ) -> JudgeResult:
        self.calls.append((debate_result, run_config))
        self.prior_accounting_calls.append(
            (prior_input_tokens, prior_output_tokens, prior_cost_usd)
        )
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


class FakeEditor:
    def __init__(self, result: EditorResult | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.calls: list[tuple[DebateResult, JudgeResult, RunConfig]] = []
        self.prior_accounting_calls: list[tuple[int, int, float]] = []
        # Patch de apresentação com fonte -- registra o que o CouncilRunner
        # realmente passou, pra provar (tests/council) que
        # source_analysis_result chega ao Editor sem precisar duplicar
        # lógica de renderização aqui.
        self.source_analysis_result_calls: list[SourceAnalysisResult | None] = []
        # Cross-Channel Reconciliation V1 -- idem, pra
        # `reconciliation` (ver tests/council/test_runner.py).
        self.reconciliation_calls: list[SourceJudgeReconciliationResult | None] = []

    async def compose(
        self,
        debate_result: DebateResult,
        judge_result: JudgeResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int = 0,
        prior_output_tokens: int = 0,
        prior_cost_usd: float = 0.0,
        source_analysis_result: SourceAnalysisResult | None = None,
        reconciliation: SourceJudgeReconciliationResult | None = None,
    ) -> EditorResult:
        self.calls.append((debate_result, judge_result, run_config))
        self.prior_accounting_calls.append(
            (prior_input_tokens, prior_output_tokens, prior_cost_usd)
        )
        self.source_analysis_result_calls.append(source_analysis_result)
        self.reconciliation_calls.append(reconciliation)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result
