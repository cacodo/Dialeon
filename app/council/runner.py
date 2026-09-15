"""
`CouncilRunner` — Etapa 8 (Stage 16 adicionou um 4º estágio real: `SourceAnalyzer`).

Coordena UMA execução completa do Council. Depende de `DebateEngine`, `SourceAnalyzer`
(Etapa 16), `JudgeStrategy` e `Editor` — **não** de `Orchestrator`: `DebateEngine` já
constrói e chama seu próprio `Orchestrator` internamente (ver `app/debate/debate_engine.py`),
então esse estágio específico do pipeline não duplica a rodada inicial. Injetar
`Orchestrator` aqui e chamá-lo separadamente chamaria todos os providers da pergunta
original duas vezes -- essa é a única duplicação que a composição evita, não uma contagem
fixa de "3 estágios" (que já não reflete a realidade desde que `SourceAnalyzer` foi
adicionado entre `DebateEngine` e `Judge`).

`CouncilRunner.run()` não tem nenhum `try`/`except`. Toda exceção que as camadas internas
já decidiram propagar (`InsufficientQuorumError` de quórum zero, `ValueError` de provider
mal configurado, qualquer erro de programação inesperado) continua propagando sem
interceptação — é o primeiro princípio desta camada: ela SEQUENCIA, nunca REINTERPRETA
falha. Toda falha que os componentes já decidiram representar como DADO (não exceção) —
`JudgeResult.verdict=None`, `EditorResult.fallback_reason` — chega aqui como dado, e o
Coordinator só repassa pro `CouncilRunResult` final, sem examinar nem decidir nada a
partir disso.

Sem `from_providers()`/factory de conveniência: `CouncilRunner` depende deliberadamente
da abstração `JudgeStrategy`, não de `SingleJudge` — uma factory que construísse
`SingleJudge(providers)` internamente reintroduziria exatamente o acoplamento que a
abstração existe pra evitar (`MultiJudgeConsensus` já está no roadmap). Composição/wiring
conveniente fica pra uma futura composition root (CLI/API), fora do escopo desta etapa.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.council.result import CouncilRunResult
from app.debate.claims import get_current_claims
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.strategy import JudgeStrategy
from app.orchestrator.config import RunConfig
from app.reconciliation.reconcile import (
    reconcile_source_and_judge,
    validate_reconciliation_coherence,
)
from app.source_analysis.analyzer import SourceAnalyzer


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CouncilRunner:
    def __init__(
        self,
        debate_engine: DebateEngine,
        source_analyzer: SourceAnalyzer,
        judge: JudgeStrategy,
        editor: Editor,
    ):
        self._debate_engine = debate_engine
        self._source_analyzer = source_analyzer
        self._judge = judge
        self._editor = editor

    async def run(
        self,
        run_config: RunConfig,
        *,
        run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> CouncilRunResult:
        """`run_config` é repassado, IDÊNTICO (mesma instância, nunca copiado ou
        mutado), a todas as chamadas abaixo. Não promete idempotência — cada chamada
        real produz timestamps novos e chamadas reais e não-determinísticas a LLMs.

        `run_id`/`started_at` (T02.4, aditivo/opcional): quando um chamador
        autoritativo (`CouncilExecutionService`) já mintou/persistiu uma
        identidade de aceite ANTES desta chamada, ele passa essa MESMA
        identidade aqui -- `CouncilRunner` nunca minta um substituto
        nesse caso (principio 3 do contrato: autoridade única de run-id).
        Quando omitidos (chamada direta, sem lifecycle de aceite -- ex.:
        testes de `CouncilRunner` isolado, scripts), o comportamento é
        exatamente o de antes: `CouncilRunResult.id` usa seu próprio
        `default_factory`, e `started_at` é capturado aqui mesmo.

        Etapa 16: `SourceAnalyzer` roda DEPOIS do `DebateEngine` (opera sobre
        claims CORRENTES pós-crítica, via `get_current_claims` internamente) e
        ANTES do `Judge` — `Judge` NUNCA recebe `source_analysis_result`
        (audit-only, ver `app/judge/context.py`, inalterado). `Editor`
        recebe (patch de apresentação com fonte, pós-diagnóstico de run
        real) -- mas só o `Editor.compose()` de app, nunca a LLM Editor
        (`build_editor_request`/`EditorPlan` continuam tão cegos a Source
        Analysis quanto antes, ver app/editor/context.py/schemas.py): o
        renderizador determinístico usa `source_analysis_result` pra
        anexar a relação com a fonte (quando existe) ao lado da avaliação
        do Judge, nunca pra mudar veredito/composição de plano.

        Cross-Channel Reconciliation V1: um QUINTO estágio roda DEPOIS do
        `Judge` e ANTES do `Editor` -- `reconcile_source_and_judge`
        (app/reconciliation/reconcile.py), função PURA (sem chamada de
        provider, sem custo) que classifica DETERMINISTICAMENTE como o
        canal Judge e o canal Source Analysis se relacionam, por claim
        corrente. Nem `Judge` nem `SourceAnalyzer` sabem que isto existe
        -- só `Editor.compose()` recebe o resultado (`reconciliation=`),
        pro renderizador determinístico consumir em vez de re-derivar a
        relação por conta própria (ver app/editor/compose.py). Não
        adiciona nenhuma chamada de provider, não afeta accounting.

        Patch de revisão do Stage 16 (blocker de accounting): `Judge`
        continua cego ao CONTEÚDO de Source Analysis, mas precisa saber
        quanto ela REALMENTE consumiu — `prior_*` carrega só os 3 números
        (tokens/custo), calculado aqui, no único lugar que já vê
        `debate_result` E `source_analysis_result` ao mesmo tempo. ALL
        REAL LLM CALLS COUNT BUDGET permanece verdadeiro sem que Judge
        precise saber que Source Analysis existe como conceito — só
        quanto ela custou."""
        started_at = started_at if started_at is not None else _now()

        debate_result = await self._debate_engine.run(run_config)
        source_analysis_result = await self._source_analyzer.analyze(debate_result, run_config)

        sa_input = (
            source_analysis_result.source_analysis_input_tokens
            if source_analysis_result is not None
            else 0
        )
        sa_output = (
            source_analysis_result.source_analysis_output_tokens
            if source_analysis_result is not None
            else 0
        )
        sa_cost = (
            source_analysis_result.source_analysis_cost_usd
            if source_analysis_result is not None
            else 0.0
        )
        prior_input_for_judge = debate_result.cumulative_input_tokens + sa_input
        prior_output_for_judge = debate_result.cumulative_output_tokens + sa_output
        prior_cost_for_judge = debate_result.cumulative_cost_usd + sa_cost

        judge_result = await self._judge.judge(
            debate_result,
            run_config,
            prior_input_tokens=prior_input_for_judge,
            prior_output_tokens=prior_output_for_judge,
            prior_cost_usd=prior_cost_for_judge,
        )

        prior_input_for_editor = prior_input_for_judge + judge_result.judge_input_tokens
        prior_output_for_editor = prior_output_for_judge + judge_result.judge_output_tokens
        prior_cost_for_editor = prior_cost_for_judge + judge_result.judge_cost_usd

        # Cross-Channel Reconciliation V1 -- roda DEPOIS do Judge e ANTES
        # do Editor (ver app/reconciliation/reconcile.py). Função PURA:
        # nenhuma chamada de provider, nenhum prompt, nenhum custo -- só
        # classifica fatos que os dois canais anteriores já produziram.
        # `current_claims` é o MESMO conjunto que Judge/Editor já usam
        # (`get_current_claims(debate_result.claims)`), recalculado aqui
        # porque nenhum estágio anterior devolve essa lista pronta.
        current_claims = get_current_claims(debate_result.claims)
        reconciliation = reconcile_source_and_judge(
            current_claims, judge_result, source_analysis_result
        )
        # Repair #2B (revisão adversarial) -- valida o resultado que
        # ACABOU de ser construído contra os EXATOS inputs desta
        # execução, ANTES do Editor consumi-lo. Redundante por
        # construção neste ponto específico (o mesmo
        # `reconcile_source_and_judge` que acabou de rodar é a própria
        # implementação de referência), mas é a MESMA chamada usada na
        # fronteira de persistência (`CouncilRepository.save_success`) --
        # protege ambas as fronteiras com um único validador, nunca duas
        # implementações que podem divergir.
        validate_reconciliation_coherence(
            reconciliation, current_claims, judge_result, source_analysis_result
        )

        editor_result = await self._editor.compose(
            debate_result,
            judge_result,
            run_config,
            prior_input_tokens=prior_input_for_editor,
            prior_output_tokens=prior_output_for_editor,
            prior_cost_usd=prior_cost_for_editor,
            source_analysis_result=source_analysis_result,
            reconciliation=reconciliation,
        )

        completed_at = _now()

        return CouncilRunResult(
            **({"id": run_id} if run_id is not None else {}),
            run_config=run_config,
            debate_result=debate_result,
            source_analysis_result=source_analysis_result,
            judge_result=judge_result,
            editor_result=editor_result,
            reconciliation=reconciliation,
            started_at=started_at,
            completed_at=completed_at,
        )
