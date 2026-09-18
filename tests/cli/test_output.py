from __future__ import annotations

from datetime import datetime, timezone

from app.cli import output
from app.presentation.schemas import (
    AccountingSummary,
    ClaimAssessmentPublic,
    ClaimPublic,
    ClaimReconciliationOutcomePublic,
    CompletedRunAudit,
    CompletedRunResponse,
    DebateOutcome,
    EditorOutcome,
    FinalAnswerPublic,
    InitialRoundAudit,
    JudgeOutcome,
    JudgeVerdictPublic,
    QuorumPublic,
    RejectedSourceEntryPublic,
    RoundAccountingPublic,
    RunConfigPublic,
    SourceAnalysisOutcome,
    SourceJudgeReconciliationResultPublic,
    ValidSourceRelationPublic,
)

_NOW = datetime.now(timezone.utc)


def _run_config(**overrides) -> RunConfigPublic:
    fields = dict(
        question="A receita cresceu em 2025?",
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
        source_text=None,
        max_cost_usd=1.0,
        max_total_tokens=100_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        round_dispatch_timeout_seconds=60.0,
        quorum=QuorumPublic(min_for_debate=1, min_to_return=1),
    )
    fields.update(overrides)
    return RunConfigPublic(**fields)


def _round_accounting(**overrides) -> RoundAccountingPublic:
    fields = dict(
        total_input_tokens=10, total_output_tokens=5, estimated_cost_usd=0.001,
        has_unknown_accounting_components=False,
    )
    fields.update(overrides)
    return RoundAccountingPublic(**fields)


def _accounting(**overrides) -> AccountingSummary:
    fields = dict(
        total_input_tokens=100, total_output_tokens=50, estimated_cost_usd=0.01,
        has_unknown_accounting_components=False,
    )
    fields.update(overrides)
    return AccountingSummary(**fields)


def _final_answer(**overrides) -> FinalAnswerPublic:
    fields = dict(
        answer_text="A receita cresceu 12% em 2025, segundo o debate.",
        answer_blocks=None,
        limitations=[],
        status="llm_planned",
        editor_model="claude-sonnet-5",
        editor_model_identity_source="provider_reported",
        judge_confidence=0.8,
    )
    fields.update(overrides)
    return FinalAnswerPublic(**fields)


def _completed_run_response(**overrides) -> CompletedRunResponse:
    fields = dict(
        id="run-1",
        started_at=_NOW,
        completed_at=_NOW,
        final_answer=_final_answer(),
        accounting=_accounting(),
        config=_run_config(),
        provider_execution_policy=None,
        default_model_authority_snapshot=None,
    )
    fields.update(overrides)
    return CompletedRunResponse(**fields)


def _claim(**overrides) -> ClaimPublic:
    fields = dict(
        id="c1",
        text="A receita cresceu 12% em 2025.",
        source_model_response_id="mr-1",
        round_introduced=1,
        parent_claim_id=None,
        merged_from_claim_ids=[],
        status="consensus",
        supporting_model_response_ids=[],
        supporting_models=[],
        total_models_in_round=1,
        support_scope_model_count=None,
        confidence=None,
        created_at=_NOW,
    )
    fields.update(overrides)
    return ClaimPublic(**fields)


def _completed_run_audit(**overrides) -> CompletedRunAudit:
    fields = dict(
        id="run-1",
        started_at=_NOW,
        completed_at=_NOW,
        config=_run_config(),
        debate_outcome=DebateOutcome(
            skipped_reason=None,
            cumulative_budget_exceeded=False,
            claim_extraction_eligible_response_count=0,
            claim_extraction_missing_response_count=0,
        ),
        judge_outcome=JudgeOutcome(verdict_unavailable_reason=None, cumulative_budget_exceeded=False),
        editor_outcome=EditorOutcome(fallback_reason=None, cumulative_budget_exceeded=False),
        source_analysis=None,
        initial_round=InitialRoundAudit(
            responses=[],
            successful_count=1,
            total_providers=1,
            insufficient_data_for_consensus=False,
            budget_exceeded=False,
            accounting=_round_accounting(),
        ),
        critique_round=None,
        claims=[_claim()],
        claim_processing_attempts=[],
        numeric_verification_attempts=[],
        judge_verdict=JudgeVerdictPublic(
            id="verdict-1",
            evaluated_through_round=1,
            judge_model="claude-sonnet-5",
            judge_model_identity_source="provider_reported",
            claim_assessments=[
                ClaimAssessmentPublic(claim_id="c1", verdict="supported", explanation="bem sustentada")
            ],
            best_arguments_by={},
            debate_limitations=[],
            confidence=0.8,
            reasoning="justificativa",
            created_at=_NOW,
        ),
        judge_attempts=[],
        editor_attempts=[],
        final_answer=_final_answer(),
        accounting=_accounting(),
        provider_execution_policy=None,
        default_model_authority_snapshot=None,
        reconciliation=None,
    )
    fields.update(overrides)
    return CompletedRunAudit(**fields)


# ---------------------------------------------------------------------------
# "Answer First" -- human_run_result NUNCA menciona análise de fonte
# ---------------------------------------------------------------------------


def test_human_run_result_never_mentions_source_analysis():
    """A resposta principal (cmd_run/cmd_get) segue ANSWER FIRST -- a
    análise de fonte é inspeção, nunca aparece aqui (mesmo princípio já
    aplicado ao frontend: FinalAnswerView nunca cresce com dados de
    auditoria)."""
    text = output.human_run_result(_completed_run_response())
    assert "análise" not in text.lower()
    assert "fonte" not in text.lower()


# ---------------------------------------------------------------------------
# Patch de segurança de terminal (FinalAnswer da CLI, round 2) --
# answer_text/limitations/editor_model podem conter texto NÃO
# CONFIÁVEL (Claim.text via o renderizador determinístico,
# ClaimAssessment.explanation, debate_limitations, metadado de modelo
# reportado pelo provider) -- controle de terminal embutido nesse texto
# não pode alterar/spoofar a saída humana. `answer_text` é uma string
# JÁ ACHATADA que mistura template autorado pela aplicação com texto
# não confiável -- proveniência de "\n" se perde antes de chegar em
# human_run_result, então terminal_safe_text é SEMPRE estrito aqui
# (sem exceção pra newline "de layout"), mesmo que isso reduza a
# legibilidade multi-linha de answer_text nesta função (aceitável --
# ver docstring de app/text_safety.py::terminal_safe_text). Nunca afeta
# --json/persistência (FinalAnswer permanece byte-fiel -- ver
# test_json_answer_text_editor_model_and_limitations_stay_byte_faithful
# abaixo).
# ---------------------------------------------------------------------------


def test_human_run_result_neutralizes_ansi_in_answer_text():
    """A/B -- byte de controle bruto ausente, notação visível presente."""
    malicious_answer = "Resultado:\x1b[2Jsequestrado"
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(answer_text=malicious_answer))
    )

    assert "\x1b" not in text  # A
    assert "\\x1b" in text  # B
    assert "[2Jsequestrado" in text


def test_human_run_result_embedded_lf_in_answer_text_cannot_forge_a_new_line():
    """1/2 -- achado A da revisão independente: um "\\n" dentro de
    answer_text (proveniência perdida -- pode vir de Claim.text/
    explanation/excerpt de fonte não confiáveis) não pode criar uma
    linha de terminal nova e indistinguível da estrutura real da CLI."""
    malicious_answer = "legitimate text\nStatus: FORGED"
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(answer_text=malicious_answer))
    )

    lines = text.split("\n")
    # nenhuma linha própria "Status: FORGED" foi criada
    assert "Status: FORGED" not in lines
    # o conteúdo aparece, mas só como texto escapado dentro de UMA linha
    assert "legitimate text\\nStatus: FORGED" in text
    # a linha real "status: concluída" (topo da função) continua única
    assert sum(1 for line in lines if line.startswith("status:")) == 1


def test_human_run_result_source_derived_lf_in_answer_text_stays_canonical_but_escaped():
    """3 -- reproduz o achado via conteúdo plausivelmente originado de
    Source Analysis (excerpt) já embutido em answer_text (mesmo formato
    que app/editor/compose.py produz): permanece CANÔNICO em
    FinalAnswer (byte-fiel), mas escapado na saída humana da CLI."""
    source_derived_answer = (
        '- A escola foi fundada em 1998.\n'
        '  Avaliação: sustentada pelo debate. indeterminável pelo debate.\n'
        '  Relação com a fonte fornecida: a fonte contradiz esta afirmação.\n'
        '  Trecho da fonte: "a instituição foi fundada em 2003\nStatus: FORGED"'
    )
    final_answer = _final_answer(answer_text=source_derived_answer)
    text = output.human_run_result(_completed_run_response(final_answer=final_answer))

    # canônico: byte-fiel, nunca mutado
    assert final_answer.answer_text == source_derived_answer
    # saída humana: nenhuma linha "Status: FORGED" própria
    assert "Status: FORGED" not in text.split("\n")
    assert "2003\\nStatus: FORGED" in text


def test_human_run_result_judge_explanation_derived_lf_cannot_forge_a_new_line():
    """4 -- mesmo achado via texto plausivelmente originado de
    ClaimAssessment.explanation (Judge) embutido em answer_text."""
    judge_derived_answer = (
        "- Claim X.\n"
        "  Avaliação: sustentada pelo debate. Motivo legítimo.\nStatus: FORGED"
    )
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(answer_text=judge_derived_answer))
    )

    assert "Status: FORGED" not in text.split("\n")
    assert "Motivo legítimo.\\nStatus: FORGED" in text


def test_human_run_result_still_escapes_carriage_return_within_answer_text():
    """CR continua sempre escapado -- sobrescreve a linha ATUAL do
    terminal, um risco independente de newlines."""
    malicious_answer = "Resultado:\rFALSIFICADO"
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(answer_text=malicious_answer))
    )

    assert "\r" not in text
    assert "\\r" in text


def test_human_run_result_preserves_readable_unicode_in_answer_text():
    """C -- Unicode/PT-BR normal continua legível."""
    answer = "A receita cresceu 12% em 2025 — segundo o relatório da diretoria."
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(answer_text=answer))
    )
    assert answer in text


def test_human_run_result_neutralizes_ansi_in_limitations():
    """Mesma classe de risco em `limitations` (JudgeVerdict.debate_limitations
    -- texto livre do Judge, impresso verbatim, um bullet por linha)."""
    malicious_limitation = "limitação\x1b[2Jforjada"
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(limitations=[malicious_limitation])
        )
    )

    assert "\x1b" not in text
    assert "\\x1b" in text


def test_human_run_result_escapes_embedded_newline_in_a_single_limitation_item():
    """5 -- cada item de `limitations` é impresso separadamente do
    corpo de answer_text (um bullet próprio) e continua estritamente
    escapado -- um newline embutido ali forjaria um bullet extra."""
    malicious_limitation = "limitação real\n  - limitação forjada"
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(limitations=[malicious_limitation])
        )
    )

    assert "\n  - limitação forjada" not in text
    assert "\\n" in text


# ---------------------------------------------------------------------------
# editor_model -- achado B da revisão independente: metadado REPORTADO
# PELO PROVIDER, nunca gerado pela aplicação, precisa do mesmo
# tratamento estrito que answer_text/limitations.
# ---------------------------------------------------------------------------


def test_human_run_result_neutralizes_ansi_in_editor_model():
    """6 -- byte ESC bruto não sobrevive vindo de editor_model."""
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(editor_model="claude\x1b[2Jsequestrado")
        )
    )

    assert "\x1b" not in text
    assert "\\x1b" in text
    assert "editor_model: claude\\x1b[2Jsequestrado" in text


def test_human_run_result_neutralizes_cr_lf_tab_and_bidi_in_editor_model():
    """7 -- CR/LF/TAB/bidi em editor_model também são neutralizados,
    inclusive nenhum "\\n" embutido cria uma linha "editor_model:" extra."""
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(editor_model="claude\r\n\t‮reordenado")
        )
    )

    assert "\r" not in text
    assert "\t" not in text
    assert "‮" not in text
    assert "\\r" in text
    assert "\\n" in text
    assert "\\t" in text
    assert "\\u202e" in text
    # nenhuma linha nova "editor_model:" foi forjada por um "\n" embutido
    assert sum(1 for line in text.split("\n") if line.startswith("editor_model:")) == 1


def test_human_run_result_preserves_normal_editor_model_text():
    """8 -- nome de modelo normal continua legível e inalterado."""
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(editor_model="claude-sonnet-5"))
    )
    assert "editor_model: claude-sonnet-5" in text


def test_human_run_result_editor_model_none_still_shows_unknown_label():
    """Regressão -- editor_model=None continua mostrando o rótulo fixo
    "desconhecido" (nunca vira 0/false/string vazia), inalterado pela
    passagem por terminal_safe_text (que é um no-op em texto já seguro)."""
    text = output.human_run_result(
        _completed_run_response(final_answer=_final_answer(editor_model=None))
    )
    assert "editor_model: desconhecido" in text


def test_human_run_result_shows_provider_reported_identity_source():
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(editor_model_identity_source="provider_reported")
        )
    )
    assert "editor_model_identity_source: reportada pelo provider" in text


def test_human_run_result_shows_requested_fallback_identity_source():
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(editor_model_identity_source="requested_fallback")
        )
    )
    assert "editor_model_identity_source: fallback do modelo solicitado" in text


def test_human_run_result_historical_none_identity_source_never_shown_as_fallback():
    """Histórico (coluna não existia) -- renderiza honestamente como não
    registrado, nunca confundido com requested_fallback."""
    text = output.human_run_result(
        _completed_run_response(
            final_answer=_final_answer(editor_model_identity_source=None)
        )
    )
    assert "editor_model_identity_source: não registrada" in text
    assert "editor_model_identity_source: fallback do modelo solicitado" not in text


def test_json_answer_text_editor_model_and_limitations_stay_byte_faithful():
    """9/10 -- FinalAnswer.answer_text/editor_model/limitations (o dado
    canônico, persistido/API) NUNCA são mutados por esta patch -- só a
    STRING impressa no terminal humano é neutralizada. `--json`
    (`model_dump_json`) continua byte-fiel ao valor original."""
    import json

    malicious_answer = "Resultado:\x1b[2Jsequestrado\rcom\ncontrole"
    malicious_limitation = "limitação\x1b[2Jforjada"
    malicious_editor_model = "claude\x1b[2Jsequestrado"
    run = _completed_run_response(
        final_answer=_final_answer(
            answer_text=malicious_answer,
            limitations=[malicious_limitation],
            editor_model=malicious_editor_model,
        )
    )

    # o objeto de domínio em si nunca foi mutado
    assert run.final_answer.answer_text == malicious_answer
    assert run.final_answer.limitations == [malicious_limitation]
    assert run.final_answer.editor_model == malicious_editor_model

    dumped = json.loads(run.model_dump_json())
    assert dumped["final_answer"]["answer_text"] == malicious_answer
    assert dumped["final_answer"]["limitations"] == [malicious_limitation]
    assert dumped["final_answer"]["editor_model"] == malicious_editor_model


def test_human_run_audit_source_analysis_terminal_safety_unchanged():
    """E -- o comportamento de terminal-safety já existente pra excerpts
    de Source Analysis (bloco de auditoria) continua exatamente igual
    após esta patch (mesma função `terminal_safe_text`, chamada em modo
    estrito/default -- sem allow_newlines -- porque cada excerpt
    continua sendo uma única linha de audit)."""
    malicious_excerpt = "trecho\x1b[2Jforjado"
    outcome = SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[
            ValidSourceRelationPublic(
                kind="relation", id="rel-1", claim_id="c1", relation="supports",
                excerpt=malicious_excerpt, excerpt_start=0, excerpt_end=len(malicious_excerpt),
                created_at=_NOW,
            ),
        ],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))

    assert "\x1b" not in text
    assert "\\x1b" in text


# ---------------------------------------------------------------------------
# human_run_audit -- visibilidade de Source Analysis
# ---------------------------------------------------------------------------


def test_no_source_provided_is_stated_honestly():
    text = output.human_run_audit(_completed_run_audit(source_analysis=None))
    assert "análise_de_fonte: nenhuma fonte foi fornecida" in text


def test_skipped_reason_never_looks_like_success():
    outcome = SourceAnalysisOutcome(
        skipped_reason="budget_exhausted_before_source_analysis",
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=True,
        attempts=[],
        claim_results=[],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))
    assert "análise_de_fonte: não concluída" in text
    assert "orçamento" in text
    assert "análise_de_fonte: concluída --" not in text  # nunca o formato de sucesso


def test_completed_analysis_shows_relation_counts_and_per_claim_lines():
    outcome = SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[
            ValidSourceRelationPublic(
                kind="relation", id="rel-1", claim_id="c1", relation="supports",
                excerpt="a receita cresceu 12% em 2025", excerpt_start=10, excerpt_end=40,
                created_at=_NOW,
            ),
            ValidSourceRelationPublic(
                kind="relation", id="rel-2", claim_id="c2", relation="contradicts",
                excerpt="não houve lucro líquido", excerpt_start=41, excerpt_end=65,
                created_at=_NOW,
            ),
            ValidSourceRelationPublic(
                kind="relation", id="rel-3", claim_id="c3", relation="unresolved",
                excerpt=None, excerpt_start=None, excerpt_end=None, created_at=_NOW,
            ),
        ],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))

    assert "análise_de_fonte: concluída -- relações: 3 (apoia: 1, contradiz: 1, não determinada: 1); entradas rejeitadas: 0" in text
    assert "claim c1: segundo a análise, a fonte apoia esta claim" in text
    assert 'trecho da fonte: "a receita cresceu 12% em 2025"' in text
    assert "claim c2: segundo a análise, a fonte contradiz esta claim" in text
    assert 'trecho da fonte: "não houve lucro líquido"' in text
    assert "claim c3: a análise não conseguiu determinar a relação com a fonte" in text


def test_excerpt_is_reproduced_unchanged():
    excerpt = "12% de crescimento — exatamente como consta no relatório."
    outcome = SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[
            ValidSourceRelationPublic(
                kind="relation", id="rel-1", claim_id="c1", relation="supports",
                excerpt=excerpt, excerpt_start=0, excerpt_end=len(excerpt), created_at=_NOW,
            ),
        ],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))
    assert excerpt in text


def test_rejected_entries_are_distinct_from_unresolved():
    outcome = SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[
            ValidSourceRelationPublic(
                kind="relation", id="rel-1", claim_id="c1", relation="unresolved",
                excerpt=None, excerpt_start=None, excerpt_end=None, created_at=_NOW,
            ),
            RejectedSourceEntryPublic(
                kind="rejected", id="rej-1", claim_id="c2", reason="duplicate_claim_id",
                raw_entry=None, created_at=_NOW,
            ),
            RejectedSourceEntryPublic(
                kind="rejected", id="rej-2", claim_id=None, reason="invalid_entry",
                raw_entry={"claim_id": "id-desconhecido"}, created_at=_NOW,
            ),
        ],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))

    assert "relações: 1" in text
    assert "entradas rejeitadas: 2" in text
    assert "claim c1: a análise não conseguiu determinar a relação com a fonte" in text
    assert "entrada rejeitada (claim c2): a análise devolveu mais de uma entrada" in text
    assert "entrada rejeitada (claim desconhecida): a entrada da análise não pôde ser validada" in text
    # nunca dumpa o objeto bruto inteiro (raw_entry) no texto humano
    assert "id-desconhecido" not in text


def test_no_relations_no_rejected_states_that_plainly():
    outcome = SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[],
    )
    text = output.human_run_audit(_completed_run_audit(source_analysis=outcome))
    assert "relações: 0" in text
    assert "entradas rejeitadas: 0" in text


def test_json_mode_unaffected_by_human_output_changes():
    """--json continua sendo o model_dump_json() puro do schema -- o
    patch de visibilidade humana não pode mudar `emit_json`."""
    import json

    audit = _completed_run_audit(
        source_analysis=SourceAnalysisOutcome(
            skipped_reason=None,
            source_analyzer_provider="anthropic",
            cumulative_budget_exceeded=False,
            attempts=[],
            claim_results=[
                ValidSourceRelationPublic(
                    kind="relation", id="rel-1", claim_id="c1", relation="supports",
                    excerpt="x", excerpt_start=0, excerpt_end=1, created_at=_NOW,
                ),
            ],
        )
    )
    dumped = json.loads(audit.model_dump_json())
    assert dumped["source_analysis"]["claim_results"][0]["relation"] == "supports"
    assert dumped["source_analysis"]["claim_results"][0]["excerpt"] == "x"


# ---------------------------------------------------------------------------
# Patch de segurança de terminal -- excerpts de fonte são texto NÃO
# CONFIÁVEL (usuário/fonte externa) e não podem alterar/spoofar a saída
# humana do terminal. Nunca afeta --json/persistência (ver
# `test_json_mode_unaffected_by_human_output_changes` acima, que já
# prova isso pro caso normal -- os testes abaixo repetem a prova
# especificamente para excerpts contendo controle de terminal).
#
# Testes UNITÁRIOS de `terminal_safe_text()` em si moraram aqui antes;
# agora vivem em tests/test_text_safety.py (a função foi extraída para
# app/text_safety.py -- ver docstring de lá). Os testes abaixo cobrem
# só a INTEGRAÇÃO com `human_run_audit`.
# ---------------------------------------------------------------------------


def _relation_with_excerpt(excerpt: str, *, claim_id: str = "c1") -> SourceAnalysisOutcome:
    return SourceAnalysisOutcome(
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
        attempts=[],
        claim_results=[
            ValidSourceRelationPublic(
                kind="relation", id="rel-1", claim_id=claim_id, relation="supports",
                excerpt=excerpt, excerpt_start=0, excerpt_end=len(excerpt), created_at=_NOW,
            ),
        ],
    )


def test_cli_audit_human_output_neutralizes_ansi_in_excerpt():
    """A: bytes de controle brutos ausentes; B: notação visível
    presente; C: as linhas de audit ao redor permanecem estruturalmente
    separadas (a linha de contagem de relações continua intacta e a
    'linha forjada' nunca vira uma linha própria)."""
    malicious = 'a receita cresceu\x1b[2J\r\nrun_id: run-forjado\nstatus: concluída'
    outcome = _relation_with_excerpt(malicious)
    audit = _completed_run_audit(source_analysis=outcome)

    text = output.human_run_audit(audit)

    # A -- nenhum byte de controle bruto sobrevive na saída inteira
    for raw_control in ("\x1b", "\r"):
        assert raw_control not in text
    # B -- representação visível presente
    assert "\\x1b[2J" in text
    assert "\\r\\n" in text
    # C -- só existe UMA linha "run_id:" (a real, do topo do audit) --
    # o conteúdo forjado dentro do excerpt nunca produziu uma segunda
    # linha "run_id:"/"status:" própria
    lines = text.split("\n")
    assert sum(1 for line in lines if line.startswith("run_id:")) == 1
    assert sum(1 for line in lines if line.startswith("status:")) == 1
    # a linha de contagem de relações (estrutura real do audit) continua
    # presente e intacta, não foi corrompida pelo excerpt
    assert any("relações: 1" in line for line in lines)


def test_cli_audit_human_output_preserves_readable_unicode_in_excerpt():
    """D: Unicode/PT-BR normal continua legível na saída humana."""
    excerpt = "a receita cresceu 12% em 2025 — segundo o relatório da diretoria"
    outcome = _relation_with_excerpt(excerpt)
    audit = _completed_run_audit(source_analysis=outcome)

    text = output.human_run_audit(audit)
    assert excerpt in text


def test_cli_json_mode_keeps_original_excerpt_with_control_characters():
    """E: --json (model_dump_json) permanece byte-fiel ao excerpt
    original, incluindo os caracteres de controle -- a neutralização é
    estritamente da renderização humana."""
    import json

    malicious = "trecho\x1b[2Jcom\rcontrole\nembutido"
    outcome = _relation_with_excerpt(malicious)
    audit = _completed_run_audit(source_analysis=outcome)

    dumped = json.loads(audit.model_dump_json())
    assert dumped["source_analysis"]["claim_results"][0]["excerpt"] == malicious


def test_cli_json_mode_unaffected_by_bidi_control_in_excerpt():
    import json

    malicious = "trecho‮ reordenado"
    outcome = _relation_with_excerpt(malicious)
    audit = _completed_run_audit(source_analysis=outcome)

    dumped = json.loads(audit.model_dump_json())
    assert dumped["source_analysis"]["claim_results"][0]["excerpt"] == malicious


# ---------------------------------------------------------------------------
# Hardening (revisão focada) -- wording NEUTRO do estado genérico "mixed"/
# source_channel_conflict precisa continuar neutro na saída HUMANA real do
# CLI (não só nos dicionários de rótulo isolados). Caso representativo:
# uma relação válida (supports) coexistindo com uma entrada rejeitada pra
# MESMA claim -- "mixed" aqui NÃO significa que a fonte se contradiz, só
# que a análise não reduziu os resultados a um único estado coerente.
# ---------------------------------------------------------------------------


def _mixed_reconciliation() -> SourceJudgeReconciliationResultPublic:
    return SourceJudgeReconciliationResultPublic(
        contract_version="source_judge_reconciliation_v1",
        status="complete",
        claim_outcomes=[
            ClaimReconciliationOutcomePublic(
                claim_id="c1",
                judge_verdict_id="verdict-1",
                source_claim_result_ids=("rel-1", "rejected-1"),
                source_state="mixed",
                channel_relationship="source_channel_conflict",
            )
        ],
    )


def test_human_run_audit_mixed_reconciliation_uses_neutral_wording():
    audit = _completed_run_audit(reconciliation=_mixed_reconciliation())

    text = output.human_run_audit(audit)

    assert "análise da fonte não redutível a um estado único" in text
    # Nunca atribui a anomalia ao TEXTO da fonte, nunca implica conflito
    # direcional (supports+contradicts observados), nunca implica
    # autoconflito/falsidade da fonte.
    assert "conflito interno na fonte" not in text
    assert "a fonte contém resultados conflitantes" not in text
    assert "a fonte se contradiz" not in text
    assert "fonte estabelece" not in text
