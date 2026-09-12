"""
`JudgeStrategy` — interface pluggable do Judge (Etapa 6).

Permite, no futuro (NÃO implementado nesta etapa), `MultiJudgeConsensus`
coexistir com `SingleJudge` sem que quem chama `.judge(...)` precise saber
qual implementação está por trás — mesmo princípio já usado em
`LLMProvider`/providers concretos.

`prior_input_tokens`/`prior_output_tokens`/`prior_cost_usd` (patch de
revisão do Stage 16): consumo REAL acumulado de TODAS as fases anteriores
do mesmo Run (Debate + Source Analysis, se houver) -- só os 3 números,
nunca o `SourceAnalysisResult` em si. `JudgeStrategy` continua
inteiramente cego à existência de Source Analysis como conceito --
recebe só accounting, nunca conteúdo epistêmico (ALL REAL LLM CALLS
COUNT BUDGET e SOURCE ANALYSIS remains AUDIT-ONLY são dois invariantes
diferentes, preservados ao mesmo tempo por este desenho).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.debate.result import DebateResult
from app.judge.result import JudgeResult
from app.orchestrator.config import RunConfig


class JudgeStrategy(ABC):
    @abstractmethod
    async def judge(
        self,
        debate_result: DebateResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int,
        prior_output_tokens: int,
        prior_cost_usd: float,
    ) -> JudgeResult: ...
