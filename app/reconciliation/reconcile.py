"""
`reconcile_source_and_judge` -- ÚNICA implementação de classificação
determinística entre o canal Judge e o canal Source Analysis, Cross-Channel
Reconciliation V1.

PURA: sem chamada de provider, sem prompt, sem LLM, sem rede, sem
aleatoriedade -- só reorganiza/classifica fatos que os dois canais JÁ
produziram, de forma inteiramente determinística e testável (ver
tests/reconciliation/test_reconcile.py). Chamada UMA VEZ por execução, em
`app/council/runner.py`, depois do Judge e antes do Editor -- nunca
dentro de `app/judge/`/`app/source_analysis/` (camadas independentes,
nenhuma sabe da outra) nem re-derivada independentemente em
`app/editor/compose.py` (que consome só o RESULTADO já resolvido aqui,
nunca recalcula a classificação por conta própria)."""

from __future__ import annotations

from typing import Literal

from app.judge.result import JudgeResult
from app.models.domain import Claim
from app.reconciliation.errors import ReconciliationError
from app.reconciliation.models import (
    CONTRACT_VERSION,
    ChannelRelationship,
    ClaimReconciliationOutcome,
    SourceChannelState,
    SourceJudgeReconciliationResult,
)
from app.source_analysis.models import (
    RejectedSourceEntry,
    SourceClaimAnalysisResult,
    ValidSourceRelation,
)
from app.source_analysis.result import SourceAnalysisResult

# SOURCE supports -- ver contrato da tarefa, seção 5.
_SUPPORTS_MAPPING: dict[str, ChannelRelationship] = {
    "supported": ChannelRelationship.DIRECTIONALLY_ALIGNED,
    "partially_supported": ChannelRelationship.DIRECTIONALLY_ALIGNED,
    "rejected": ChannelRelationship.IN_TENSION,
    "conflicting": ChannelRelationship.SOURCE_ADDS_DIRECTION,
    "unresolved": ChannelRelationship.SOURCE_ADDS_DIRECTION,
}

# SOURCE contradicts -- ver contrato da tarefa, seção 5.
_CONTRADICTS_MAPPING: dict[str, ChannelRelationship] = {
    "supported": ChannelRelationship.IN_TENSION,
    "partially_supported": ChannelRelationship.IN_TENSION,
    "rejected": ChannelRelationship.DIRECTIONALLY_ALIGNED,
    "conflicting": ChannelRelationship.SOURCE_ADDS_DIRECTION,
    "unresolved": ChannelRelationship.SOURCE_ADDS_DIRECTION,
}

_RELATION_TO_STATE: dict[str, SourceChannelState] = {
    "supports": SourceChannelState.SUPPORTS,
    "contradicts": SourceChannelState.CONTRADICTS,
    "unresolved": SourceChannelState.UNRESOLVED,
}


def _relationship_for_directional_source(
    source_state: SourceChannelState, judge_verdict: str
) -> ChannelRelationship:
    """Lookup EXAUSTIVO -- nunca um fallback implícito (mesmo espírito de
    `app/editor/compose.py::_bucket_for_verdict`). Um `judge_verdict` fora
    dos 5 valores mapeados (estruturalmente impossível hoje --
    `ClaimAssessment.verdict` é um `Literal` fechado, ver
    app/models/domain.py/app/judge/schemas.py) levanta `ReconciliationError`
    ao invés de escolher um relacionamento arbitrário: schema drift futuro
    no Judge precisa ser uma decisão explícita aqui, nunca um acidente de
    reconciliação.

    Hardening (revisão focada) -- `source_state` também é validado
    EXPLICITAMENTE contra os dois únicos valores direcionais (SUPPORTS/
    CONTRADICTS), nunca um `else` implícito que trataria qualquer
    `SourceChannelState` futuro não-SUPPORTS como CONTRADICTS por
    acidente. Só é chamada hoje com SUPPORTS/CONTRADICTS (ver
    `reconcile_source_and_judge`, que despacha os 5 estados restantes
    antes de chegar aqui) -- mas um `SourceChannelState` novo que algum
    dia alcançasse esta função incorretamente precisa falhar fechado,
    nunca ser silenciosamente reconciliado como se fosse uma
    contradição."""
    if source_state == SourceChannelState.SUPPORTS:
        mapping = _SUPPORTS_MAPPING
    elif source_state == SourceChannelState.CONTRADICTS:
        mapping = _CONTRADICTS_MAPPING
    else:
        raise ReconciliationError(
            f"_relationship_for_directional_source só aceita SUPPORTS/CONTRADICTS, "
            f"recebeu source_state={source_state!r} -- estado não-direcional nunca "
            "pode ser tratado implicitamente como contradição"
        )
    try:
        return mapping[judge_verdict]
    except KeyError:
        raise ReconciliationError(
            f"veredito do Judge sem mapeamento de reconciliação definido: {judge_verdict!r} "
            "-- a tabela precisa ser atualizada explicitamente antes que este veredito "
            "possa ser reconciliado"
        ) from None


def _group_source_results_by_claim(
    source_analysis_result: SourceAnalysisResult | None,
) -> dict[str, list[SourceClaimAnalysisResult]]:
    """Agrupa TODOS os `SourceClaimAnalysisResult` persistidos/presentes
    por `claim_id`, preservando ordem -- nunca um dict `{claim_id:
    relation}` de último-a-escrever-vence (ver seção 8 do contrato).
    Entradas com `claim_id=None` (RejectedSourceEntry sem claim corrente
    correspondente -- ver app/source_analysis/analyzer.py,
    `unknown_entries`) são estruturalmente irrelevantes aqui: nunca
    podem ser atribuídas a nenhuma claim corrente, então nunca entram em
    nenhum grupo."""
    grouped: dict[str, list[SourceClaimAnalysisResult]] = {}
    if source_analysis_result is None or source_analysis_result.skipped_reason is not None:
        return grouped
    for result in source_analysis_result.claim_results:
        if result.claim_id is None:
            continue
        grouped.setdefault(result.claim_id, []).append(result)
    return grouped


def _reduce_source_group(
    group: list[SourceClaimAnalysisResult],
) -> tuple[SourceChannelState, tuple[str, ...]]:
    """Reduz TODOS os resultados de fonte de uma claim a um único
    (source_state, ids-usados-em-ordem) -- NUNCA last-write-wins, NUNCA
    descarta um id silenciosamente. Tabela de redução exata (documentada e
    testada, ver tests/reconciliation/test_reconcile.py):

    - só rejected(s)                          -> ENTRY_REJECTED, retém todos
    - exatamente 1 relação válida distinta,
      sem nenhum rejected coexistindo         -> SUPPORTS/CONTRADICTS/UNRESOLVED,
                                                  retém todos os ids dessa relação
    - >=2 relações válidas distintas, OU
      relação válida + rejected coexistindo   -> MIXED, retém TODOS os ids
                                                  (nunca escolhe um vencedor por ordem)

    O produtor canônico (`app/source_analysis/analyzer.py::_build_claim_results`)
    garante hoje EXATAMENTE um resultado por claim corrente -- esta função
    é defensiva pra dado manual/persistido anômalo (claim_outcomes
    construídos à mão, ou uma linha de banco editada fora do fluxo normal),
    nunca alcançada com múltiplos resultados pelo pipeline real."""
    valid = [r for r in group if isinstance(r, ValidSourceRelation)]
    rejected = [r for r in group if isinstance(r, RejectedSourceEntry)]
    distinct_relations = {r.relation for r in valid}

    if not distinct_relations:
        # só rejected(s) -- NÃO epistêmico, nunca vira supports/contradicts/unresolved.
        return SourceChannelState.ENTRY_REJECTED, tuple(r.id for r in rejected)

    if len(distinct_relations) == 1 and not rejected:
        (relation,) = distinct_relations
        return _RELATION_TO_STATE[relation], tuple(r.id for r in valid)

    # >=2 relações distintas, OU relação válida coexistindo com rejeição
    # anômala -- não redutível a um único estado coerente (seções 3/8/9 do
    # contrato). Retém TODOS os ids na ordem original, nunca escolhe um
    # vencedor por ordem/última escrita.
    return SourceChannelState.MIXED, tuple(r.id for r in group)


def reconcile_source_and_judge(
    current_claims: list[Claim],
    judge_result: JudgeResult,
    source_analysis_result: SourceAnalysisResult | None,
) -> SourceJudgeReconciliationResult:
    """Produz exatamente UM `ClaimReconciliationOutcome` por claim em
    `current_claims`, na MESMA ordem -- nunca aceita/inventa um claim_id
    que não esteja em `current_claims` (seção 7/20 do contrato).

    Falha fechado (`ReconciliationError`) em vez de escolher
    arbitrariamente quando:
    - o Judge está disponível mas `JudgeVerdict.claim_assessments`
      referencia um claim_id que não é mais corrente (dado
      manual/persistido violando a garantia de `SingleJudge._parse_and_validate`);
    - o Judge está disponível mas NÃO tem nenhuma avaliação pra uma claim
      corrente (cobertura incompleta -- nunca selecionada arbitrariamente);
    - a análise de fonte está "concluída" (sem skipped_reason) mas não tem
      NENHUM resultado pra uma claim corrente (violação da garantia de
      `SourceAnalyzer._build_claim_results`, que cobre toda claim
      corrente quando bem-sucedida)."""
    verdict = judge_result.verdict
    status: Literal["complete", "judge_unavailable"] = (
        "complete" if verdict is not None else "judge_unavailable"
    )
    current_claim_ids = {c.id for c in current_claims}

    assessments_by_claim_id: dict[str, str] = {}
    if verdict is not None:
        assessments_by_claim_id = {a.claim_id: a.verdict for a in verdict.claim_assessments}
        unknown = set(assessments_by_claim_id) - current_claim_ids
        if unknown:
            raise ReconciliationError(
                "JudgeVerdict.claim_assessments referencia claim_id(s) que não são "
                f"claims correntes: {sorted(unknown)} -- reconciliação recusa inventar "
                "uma comparação pra uma claim desconhecida"
            )

    source_groups = _group_source_results_by_claim(source_analysis_result)

    # Repair #1 (revisão adversarial) -- um resultado de fonte que
    # referencia um claim_id que não é mais corrente (ex.: claim
    # superseded/revisada, ou dado manual/persistido anômalo) NUNCA pode
    # simplesmente desaparecer por não ser visitado no loop abaixo (que
    # só itera `current_claims`) -- reconciliação é lossless/fail-closed
    # por contrato: ou o resultado é atribuído corretamente a uma claim
    # corrente, ou a reconciliação recusa produzir um resultado
    # "complete" que silenciosamente descartaria um registro real.
    # `RejectedSourceEntry(claim_id=None)` nunca entra em `source_groups`
    # (ver `_group_source_results_by_claim`) -- não é um claim_id
    # desconhecido, é uma entrada estruturalmente não-atribuível por
    # design, tratada pela semântica existente de source-analysis, nunca
    # confundida com este caso.
    unknown_source_claims = set(source_groups) - current_claim_ids
    if unknown_source_claims:
        raise ReconciliationError(
            "resultado(s) de análise de fonte referenciam claim_id(s) que não são "
            f"claims correntes: {sorted(unknown_source_claims)} -- reconciliação "
            "recusa descartar silenciosamente um resultado de fonte real"
        )

    outcomes: list[ClaimReconciliationOutcome] = []
    for claim in current_claims:
        group = source_groups.get(claim.id, [])

        if source_analysis_result is None:
            source_state = SourceChannelState.NOT_SUPPLIED
            source_result_ids: tuple[str, ...] = ()
        elif source_analysis_result.skipped_reason is not None:
            source_state = SourceChannelState.ANALYSIS_UNAVAILABLE
            source_result_ids = ()
        elif not group:
            raise ReconciliationError(
                "análise de fonte concluída sem skipped_reason, mas nenhum "
                f"SourceClaimAnalysisResult existe pra claim corrente {claim.id!r} -- "
                "o produtor canônico garante cobertura completa; dado "
                "manual/persistido violou essa premissa"
            )
        else:
            source_state, source_result_ids = _reduce_source_group(group)

        if status == "judge_unavailable":
            channel_relationship = ChannelRelationship.NOT_COMPARABLE
            judge_verdict_id: str | None = None
        else:
            judge_verdict_id = verdict.id  # type: ignore[union-attr]
            judge_verdict_value = assessments_by_claim_id.get(claim.id)
            if judge_verdict_value is None:
                raise ReconciliationError(
                    "JudgeVerdict não tem claim_assessments pra claim corrente "
                    f"{claim.id!r} -- reconciliação recusa selecionar arbitrariamente"
                )
            if source_state in (
                SourceChannelState.NOT_SUPPLIED,
                SourceChannelState.ANALYSIS_UNAVAILABLE,
                SourceChannelState.ENTRY_REJECTED,
            ):
                channel_relationship = ChannelRelationship.NOT_COMPARABLE
            elif source_state == SourceChannelState.UNRESOLVED:
                channel_relationship = ChannelRelationship.SOURCE_UNRESOLVED
            elif source_state == SourceChannelState.MIXED:
                channel_relationship = ChannelRelationship.SOURCE_CHANNEL_CONFLICT
            else:
                channel_relationship = _relationship_for_directional_source(
                    source_state, judge_verdict_value
                )

        outcomes.append(
            ClaimReconciliationOutcome(
                claim_id=claim.id,
                judge_verdict_id=judge_verdict_id,
                source_claim_result_ids=source_result_ids,
                source_state=source_state,
                channel_relationship=channel_relationship,
            )
        )

    return SourceJudgeReconciliationResult(status=status, claim_outcomes=outcomes)


def validate_reconciliation_coherence(
    reconciliation: SourceJudgeReconciliationResult,
    current_claims: list[Claim],
    judge_result: JudgeResult,
    source_analysis_result: SourceAnalysisResult | None,
) -> None:
    """Repair #2B (revisão adversarial) -- valida um
    `SourceJudgeReconciliationResult` JÁ CONSTRUÍDO contra os EXATOS
    inputs dos quais ele alega derivar. Protege tanto a renderização
    (Editor, chamada logo após a construção em `app/council/runner.py`)
    quanto a persistência (`CouncilRepository.save_success`, ANTES de
    gravar) contra um `CouncilRunResult` montado manualmente/incoerente
    (ex.: `judge_result` trocado depois da reconciliação já ter sido
    calculada contra um veredito diferente) que de outra forma se
    tornaria estado canônico persistido.

    NÃO reimplementa a semântica de reconciliação -- recomputa o
    resultado ESPERADO chamando a MESMA `reconcile_source_and_judge`
    (que por sua vez usa os MESMOS helpers puros:
    `_group_source_results_by_claim`/`_reduce_source_group`/
    `_relationship_for_directional_source`) sobre os inputs reais, e
    compara contra o que foi de fato fornecido. Isto é validação por
    diferença determinística, nunca uma segunda tabela de mapeamento
    (evita exatamente o risco que motivou este repair: duas
    implementações divergentes da mesma regra).

    Levanta `ReconciliationError` -- fail closed -- em QUALQUER
    divergência: cobertura de claim corrente (faltante/extra/fora de
    ordem), judge_verdict_id/channel_relationship/source_state/
    source_claim_result_ids incorretos, contract_version inesperado, ou
    status incompatível com a disponibilidade real do Judge. Nunca
    aceita silenciosamente um `SourceJudgeReconciliationResult`
    incoerente."""
    if reconciliation.contract_version != CONTRACT_VERSION:
        raise ReconciliationError(
            f"reconciliation.contract_version inesperado: "
            f"{reconciliation.contract_version!r} (esperado {CONTRACT_VERSION!r})"
        )

    expected = reconcile_source_and_judge(current_claims, judge_result, source_analysis_result)

    if reconciliation.status != expected.status:
        raise ReconciliationError(
            f"reconciliation.status={reconciliation.status!r} não corresponde ao "
            f"status esperado a partir dos dados reais desta execução: "
            f"{expected.status!r}"
        )

    expected_by_claim_id = {o.claim_id: o for o in expected.claim_outcomes}
    actual_by_claim_id = {o.claim_id: o for o in reconciliation.claim_outcomes}
    expected_ids_in_order = [o.claim_id for o in expected.claim_outcomes]
    actual_ids_in_order = [o.claim_id for o in reconciliation.claim_outcomes]

    missing = [cid for cid in expected_ids_in_order if cid not in actual_by_claim_id]
    if missing:
        raise ReconciliationError(
            f"reconciliation não cobre claim(s) corrente(s): {missing!r}"
        )
    extra = [cid for cid in actual_ids_in_order if cid not in expected_by_claim_id]
    if extra:
        raise ReconciliationError(
            f"reconciliation contém outcome(s) pra claim(s) que não são correntes: "
            f"{extra!r}"
        )
    if actual_ids_in_order != expected_ids_in_order:
        raise ReconciliationError(
            f"ordem de reconciliation.claim_outcomes ({actual_ids_in_order!r}) não "
            f"corresponde à ordem das claims correntes ({expected_ids_in_order!r})"
        )

    for claim_id in expected_ids_in_order:
        expected_outcome = expected_by_claim_id[claim_id]
        actual_outcome = actual_by_claim_id[claim_id]
        if actual_outcome != expected_outcome:
            raise ReconciliationError(
                f"outcome de claim_id={claim_id!r} diverge do esperado a partir dos "
                f"dados reais desta execução: obtido {actual_outcome!r}, esperado "
                f"{expected_outcome!r}"
            )
