"""
Etapa 17B — compatibilidade retroativa da mudança de contrato do Editor.

`FinalAnswerRow.status`/`EditorAttemptRow.raw_output_text` são colunas de
texto sem verificação de schema em leitura (ver
app/storage/serializers.py: `final_answer_from_row`/`editor_attempt_from_row`
fazem só (de)serialização direta, nunca reparseiam `raw_output_text` como
JSON) -- então um registro histórico (pré-Etapa-17B, com
status="llm_composed" e raw_output_text no formato antigo de
claim_narratives/synthesis_intro) precisa continuar sendo lido sem erro,
mesmo que esse formato não seja mais um `EditorPlan` válido hoje. Nenhuma
migração de banco foi feita; nenhuma run histórica é reescrita.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.storage.models import EditorAttemptRow, FinalAnswerRow
from app.storage.serializers import editor_attempt_from_row, final_answer_from_row

_NAIVE_NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def test_final_answer_with_historical_llm_composed_status_still_deserializes():
    row = FinalAnswerRow(
        id="fa-historico-1",
        council_run_id="run-historico-1",
        answer_text="Resposta histórica composta pelo editor livre (pré-Etapa-17B).",
        limitations_json=["limitação histórica registrada pelo juiz"],
        status="llm_composed",
        editor_model="claude-legacy",
        based_on_verdict_id="verdict-historico-1",
        judge_confidence=0.6,
        created_at=_NAIVE_NOW,
    )

    final_answer = final_answer_from_row(row)

    assert final_answer.status == "llm_composed"
    assert final_answer.answer_text == "Resposta histórica composta pelo editor livre (pré-Etapa-17B)."
    assert final_answer.limitations == ["limitação histórica registrada pelo juiz"]
    assert final_answer.editor_model == "claude-legacy"


def test_editor_attempt_with_old_narrative_json_in_raw_output_text_still_deserializes():
    """`raw_output_text` é sempre string opaca -- nunca reparseada contra
    `EditorPlan`/`EditorOutput`. Um registro histórico com
    claim_narratives/synthesis_intro no JSON bruto continua legível como
    dado de auditoria, mesmo não sendo mais um EditorPlan válido hoje."""
    old_raw_output = (
        '{"claim_narratives": [{"claim_id": "c1", "verdict_reflected": "supported", '
        '"narrative": "texto histórico de antes da Etapa 17B"}], '
        '"synthesis_intro": "", "synthesis_conclusion": ""}'
    )
    row = EditorAttemptRow(
        id="att-historico-1",
        council_run_id="run-historico-1",
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-legacy",
        model="claude-legacy",
        transport_status="success",
        transport_error_json=None,
        transport_attempts=1,
        had_uncertain_prior_attempts=False,
        provider_finish_reason=None,
        raw_output_text=old_raw_output,
        parse_status="accepted",
        parse_error_message=None,
        usage_present=True,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        pricing_provenance_json=None,
        latency_ms=200,
        created_at=_NAIVE_NOW,
    )

    attempt = editor_attempt_from_row(row)

    assert attempt.raw_output_text == old_raw_output
    assert attempt.parse_status == "accepted"


def test_editor_attempt_with_historical_inconsistent_references_parse_status_still_deserializes():
    """`inconsistent_references` foi mantido no Literal de
    `EditorAttempt.parse_status` só por compatibilidade de leitura
    (ver app/editor/errors.py) -- nenhum código novo o produz, mas um
    registro histórico com esse valor precisa continuar legível."""
    row = EditorAttemptRow(
        id="att-historico-2",
        council_run_id="run-historico-1",
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-legacy",
        model="claude-legacy",
        transport_status="success",
        transport_error_json=None,
        transport_attempts=1,
        had_uncertain_prior_attempts=False,
        provider_finish_reason=None,
        raw_output_text='{"claim_narratives": [{"claim_id": "id-desconhecido", '
        '"verdict_reflected": "supported", "narrative": "x"}]}',
        parse_status="inconsistent_references",
        parse_error_message="claim_narratives referencia claim_id(s) desconhecido(s)",
        usage_present=True,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        pricing_provenance_json=None,
        latency_ms=200,
        created_at=_NAIVE_NOW,
    )

    attempt = editor_attempt_from_row(row)

    assert attempt.parse_status == "inconsistent_references"
