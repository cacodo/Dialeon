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
