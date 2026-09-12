from __future__ import annotations

import json

from app.editor.context import build_editor_request
from app.models.domain import ClaimAssessment, JudgeVerdict


def _verdict(assessments, **overrides) -> JudgeVerdict:
    fields = dict(
        evaluated_through_round=2,
        judge_model="claude-test",
        claim_assessments=assessments,
        debate_limitations=["cobertura de crítica parcial"],
        confidence=0.65,
        reasoning="ESTE TEXTO NUNCA DEVE APARECER NO CONTEXTO DO PLANEJADOR",
    )
    fields.update(overrides)
    return JudgeVerdict(**fields)


def test_question_present():
    verdict = _verdict([ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")])
    request = build_editor_request("Qual a capital?", verdict, 1024)
    assert "Qual a capital?" in request.messages[0].content


def test_confidence_present_for_tone_calibration():
    verdict = _verdict(
        [ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")], confidence=0.42
    )
    request = build_editor_request("pergunta", verdict, 1024)
    assert "0.42" in request.messages[0].content


def test_verdict_counts_present_as_structured_summary():
    verdict = _verdict(
        [
            ClaimAssessment(claim_id="c1", verdict="rejected", explanation="x"),
            ClaimAssessment(claim_id="c2", verdict="rejected", explanation="y"),
            ClaimAssessment(claim_id="c3", verdict="supported", explanation="z"),
        ]
    )
    request = build_editor_request("pergunta", verdict, 1024)
    body = request.messages[0].content
    counts = json.loads(body.split("CONTAGEM_DE_VEREDITOS_POR_TIPO:", 1)[1].split("\n", 1)[0])
    assert counts == {"rejected": 2, "supported": 1}


def test_limitations_presence_signal_without_content():
    verdict = _verdict(
        [ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")],
        debate_limitations=["SEGREDO: apenas 1 de 3 modelos participaram da crítica"],
    )
    request = build_editor_request("pergunta", verdict, 1024)
    body = request.messages[0].content

    assert "SEGREDO" not in body
    assert "HA_LIMITACOES_DE_DEBATE_REGISTRADAS: true" in body


def test_no_limitations_signals_false():
    verdict = _verdict(
        [ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")],
        debate_limitations=[],
    )
    request = build_editor_request("pergunta", verdict, 1024)
    assert "HA_LIMITACOES_DE_DEBATE_REGISTRADAS: false" in request.messages[0].content


# ---------------------------------------------------------------------------
# Ausências (mínimo contexto, Etapa 17B — bem mais restrito que a Etapa 7)
# ---------------------------------------------------------------------------


def test_judge_reasoning_is_explicitly_absent():
    verdict = _verdict([ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")])
    request = build_editor_request("pergunta", verdict, 1024)

    assert "ESTE TEXTO NUNCA DEVE APARECER NO CONTEXTO DO PLANEJADOR" not in request.messages[0].content
    assert "ESTE TEXTO NUNCA DEVE APARECER NO CONTEXTO DO PLANEJADOR" not in request.system_prompt


def test_claim_text_explanation_and_claim_id_are_not_sent():
    """Etapa 17B: o planejador não recebe NENHUM conteúdo factual — só
    contagens/presença estruturais. Diferente da Etapa 7, nem claim_id
    é enviado (o plano não referencia claims)."""
    verdict = _verdict(
        [
            ClaimAssessment(
                claim_id="claim-id-especifico",
                verdict="rejected",
                explanation="EXPLICACAO_QUE_NAO_DEVE_VAZAR",
            )
        ]
    )
    request = build_editor_request("pergunta", verdict, 1024)
    body = request.messages[0].content

    assert "EXPLICACAO_QUE_NAO_DEVE_VAZAR" not in body
    assert "claim-id-especifico" not in body


def test_no_response_text_or_supporting_ratio_or_lineage():
    verdict = _verdict([ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")])
    request = build_editor_request("pergunta", verdict, 1024)
    body = request.messages[0].content

    assert "supporting_model_ratio" not in body
    assert "lineage" not in body
    assert "response_text" not in body


# ---------------------------------------------------------------------------
# Conteúdo do prompt reflete o novo papel (planejador, não autor de prosa)
# ---------------------------------------------------------------------------


def test_system_prompt_describes_planning_not_prose_authorship():
    verdict = _verdict([ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")])
    request = build_editor_request("pergunta", verdict, 1024)

    assert "opening_style" in request.system_prompt
    assert "closing_style" in request.system_prompt
    assert "narrative" not in request.system_prompt.lower()
    assert "synthesis" not in request.system_prompt.lower()
    assert "julga" in request.system_prompt.lower()  # reforça que não julga/reavalia


def test_max_output_tokens_per_call_is_forwarded():
    verdict = _verdict([ClaimAssessment(claim_id="c1", verdict="supported", explanation="ok")])
    request = build_editor_request("pergunta", verdict, 4096)
    assert request.max_tokens == 4096
