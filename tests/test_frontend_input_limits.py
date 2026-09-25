"""Guarda de deriva: os limites de entrada espelhados no frontend
(`frontend/src/lib/inputLimits.ts`) precisam bater com os limites canônicos do
backend. O backend segue a autoridade; o frontend só dá feedback antecipado."""

from __future__ import annotations

import re
from pathlib import Path

from app.orchestrator.config import MAX_QUESTION_CHARACTERS, MAX_SOURCE_TEXT_CHARACTERS

LIMITS_FILE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "inputLimits.ts"


def _frontend_constant(name: str) -> int:
    match = re.search(rf"export const {name} = ([0-9_]+)", LIMITS_FILE.read_text(encoding="utf-8"))
    assert match, f"{name} não encontrado em {LIMITS_FILE}"
    return int(match.group(1).replace("_", ""))


def test_frontend_question_limit_matches_backend():
    assert _frontend_constant("MAX_QUESTION_CHARACTERS") == MAX_QUESTION_CHARACTERS


def test_frontend_source_limit_matches_backend():
    assert _frontend_constant("MAX_SOURCE_TEXT_CHARACTERS") == MAX_SOURCE_TEXT_CHARACTERS


def test_frontend_blank_source_detection_uses_exactly_the_backend_whitespace_set():
    """A UI decide "fonte vazia -> ausente" pelo MESMO critério do backend
    (`not value.strip()`, ou seja, só code points de `str.isspace()`)."""
    match = re.search(
        r"export const BACKEND_WHITESPACE_CODE_POINTS = \[([^\]]*)\]",
        LIMITS_FILE.read_text(encoding="utf-8"),
    )
    assert match, "BACKEND_WHITESPACE_CODE_POINTS não encontrado"
    frontend = {int(token, 16) for token in re.findall(r"0x[0-9a-fA-F]+", match.group(1))}
    backend = {code for code in range(0x110000) if chr(code).isspace()}
    assert frontend == backend
