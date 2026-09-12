from __future__ import annotations

from app.structured_output import strip_single_json_code_fence


def test_raw_json_passes_through_unchanged():
    raw = '{"groups": [], "ungrouped_claim_ids": []}'
    assert strip_single_json_code_fence(raw) == raw


def test_fenced_json_with_language_tag_is_stripped():
    raw = '```json\n{"groups": [], "ungrouped_claim_ids": []}\n```'
    assert strip_single_json_code_fence(raw) == '{"groups": [], "ungrouped_claim_ids": []}'


def test_fenced_json_without_language_tag_is_stripped():
    raw = '```\n{"groups": [], "ungrouped_claim_ids": []}\n```'
    assert strip_single_json_code_fence(raw) == '{"groups": [], "ungrouped_claim_ids": []}'


def test_real_run_shape_from_production_grouping_output():
    raw = '```json\n{"groups": [], "ungrouped_claim_ids": ["c1", "c2"]}\n```'
    assert (
        strip_single_json_code_fence(raw)
        == '{"groups": [], "ungrouped_claim_ids": ["c1", "c2"]}'
    )


def test_surrounding_whitespace_outside_fence_tolerated():
    raw = '  \n```json\n{"a": 1}\n```\n  '
    assert strip_single_json_code_fence(raw) == '{"a": 1}'


def test_prose_before_fence_is_not_stripped():
    """Prosa arbitrária + JSON permanece rejeitada -- nunca busca
    substring dentro de texto maior."""
    raw = 'Aqui está o resultado:\n```json\n{"a": 1}\n```'
    assert strip_single_json_code_fence(raw) == raw


def test_prose_after_fence_is_not_stripped():
    raw = '```json\n{"a": 1}\n```\nEspero que ajude!'
    assert strip_single_json_code_fence(raw) == raw


def test_truncated_unclosed_fence_is_not_stripped():
    """JSON truncado (cerca aberta, nunca fechada) permanece rejeitado
    -- nunca repara JSON incompleto."""
    raw = '```json\n{"a": 1, "b": [1, 2, 3'
    assert strip_single_json_code_fence(raw) == raw


def test_multiple_fenced_blocks_not_stripped():
    raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
    assert strip_single_json_code_fence(raw) == raw


def test_plain_text_without_any_fence_passes_through():
    raw = "isto não é JSON nem tem cerca nenhuma"
    assert strip_single_json_code_fence(raw) == raw


def test_empty_string_passes_through():
    assert strip_single_json_code_fence("") == ""
