from __future__ import annotations

import pytest

from app.cli.main import _build_parser, _parse_providers


def test_run_parses_question_positional():
    args = _build_parser().parse_args(["run", "Qual a capital do Brasil?"])
    assert args.command == "run"
    assert args.question == "Qual a capital do Brasil?"
    assert args.providers is None
    assert args.as_json is False


def test_run_parses_providers_and_json_flags():
    args = _build_parser().parse_args(
        ["run", "pergunta", "--providers", "openai,anthropic", "--json"]
    )
    assert args.providers == "openai,anthropic"
    assert args.as_json is True


def test_run_parses_source_flag():
    args = _build_parser().parse_args(["run", "pergunta", "--source", "texto da fonte"])
    assert args.source == "texto da fonte"


def test_run_without_source_flag_defaults_to_none():
    args = _build_parser().parse_args(["run", "pergunta"])
    assert args.source is None


def test_run_help_has_no_stale_stage_terminology_or_audit_only_claim():
    """Repair de revisão -- `--source` já participa da composição
    determinística da resposta final via reconciliação Source↔Judge
    (ver app/editor/compose.py), então o help nunca pode mais descrever
    o efeito da fonte como "audit-only", nem carregar um rótulo de
    etapa histórica ("Etapa N") que fica stale a cada nova mudança."""
    run_subparser = _build_parser()._subparsers._group_actions[0].choices["run"]
    run_help = run_subparser.format_help()

    assert "Etapa" not in run_help
    assert "audit-only" not in run_help
    assert "Source Analysis" in run_help


def test_top_level_run_summary_names_council_default_and_direct_mode():
    """v1.3.0: `run` executa pelo Conselho por padrão OU uma resposta direta
    (`--direct`) -- o resumo de topo nunca pode voltar a dizer que toda run é
    do Conselho, nem sugerir que a direta é o padrão."""
    top_help = " ".join(_build_parser().format_help().split())
    assert (
        "Executa uma pergunta: pelo Conselho (padrão) ou, com --direct, resposta direta "
        "de um provider." in top_help
    )
    assert "através do Council" not in top_help


def test_audit_help_describes_summary_and_retains_json_option():
    parser = _build_parser()
    assert "Mostra um resumo da auditoria de uma execução." in parser.format_help()
    audit_subparser = parser._subparsers._group_actions[0].choices["audit"]
    assert "--json" in audit_subparser.format_help()


def test_run_without_question_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["run"])
    assert exc_info.value.code == 2


def test_no_subcommand_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args([])
    assert exc_info.value.code == 2


def test_unknown_command_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["nonexistent"])
    assert exc_info.value.code == 2


def test_list_defaults_and_overrides():
    default_args = _build_parser().parse_args(["list"])
    assert default_args.limit == 50
    assert default_args.offset == 0

    overridden = _build_parser().parse_args(["list", "--limit", "10", "--offset", "5"])
    assert overridden.limit == 10
    assert overridden.offset == 5


def test_get_and_audit_parse_run_id():
    get_args = _build_parser().parse_args(["get", "run-123"])
    assert get_args.run_id == "run-123"

    audit_args = _build_parser().parse_args(["audit", "run-123", "--json"])
    assert audit_args.run_id == "run-123"
    assert audit_args.as_json is True


def test_providers_command_parses():
    args = _build_parser().parse_args(["providers"])
    assert args.command == "providers"


# ---------------------------------------------------------------------------
# _parse_providers
# ---------------------------------------------------------------------------


def test_parse_providers_none_stays_none():
    assert _parse_providers(None) is None


def test_parse_providers_splits_and_strips():
    assert _parse_providers("openai, anthropic ,gemini") == ["openai", "anthropic", "gemini"]


def test_parse_providers_drops_empty_tokens():
    assert _parse_providers("openai,,anthropic") == ["openai", "anthropic"]


# ---------------------------------------------------------------------------
# list --limit/--offset -- Blocker 2 (patch de revisão): espelha
# exatamente os limites de GET /runs (Query(ge=1, le=100)/Query(ge=0)).
# ---------------------------------------------------------------------------


def test_list_limit_zero_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["list", "--limit", "0"])
    assert exc_info.value.code == 2


def test_list_limit_101_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["list", "--limit", "101"])
    assert exc_info.value.code == 2


def test_list_offset_negative_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["list", "--offset", "-1"])
    assert exc_info.value.code == 2


def test_list_limit_non_integer_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["list", "--limit", "abc"])
    assert exc_info.value.code == 2


def test_list_limit_boundaries_are_accepted():
    args_min = _build_parser().parse_args(["list", "--limit", "1"])
    assert args_min.limit == 1
    args_max = _build_parser().parse_args(["list", "--limit", "100"])
    assert args_max.limit == 100


def test_list_offset_zero_is_accepted():
    args = _build_parser().parse_args(["list", "--offset", "0"])
    assert args.offset == 0


def test_list_defaults_still_valid_after_fix():
    args = _build_parser().parse_args(["list"])
    assert args.limit == 50
    assert args.offset == 0
