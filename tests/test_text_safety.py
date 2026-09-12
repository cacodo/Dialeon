from __future__ import annotations

from app.text_safety import terminal_safe_text


def test_terminal_safe_preserves_normal_printable_unicode():
    text = "A receita cresceu 12% em 2025 — relatório com acentuação: ção, ã, é, ü."
    assert terminal_safe_text(text) == text


def test_terminal_safe_escapes_ansi_esc_sequence():
    """ESC [ 2 J -- limpeza de tela ANSI clássica."""
    malicious = "trecho normal\x1b[2Jtrecho depois"
    safe = terminal_safe_text(malicious)

    assert "\x1b" not in safe  # A: byte de controle bruto ausente
    assert "\\x1b" in safe  # B: representação visível presente
    assert "[2J" in safe  # o resto do texto (agora inofensivo) é preservado


def test_terminal_safe_escapes_osc_hyperlink_sequence():
    """OSC 8 -- hyperlink de terminal (pode disfarçar a URL exibida)."""
    malicious = "\x1b]8;;http://malicioso.example\x07texto do link\x1b]8;;\x07"
    safe = terminal_safe_text(malicious)

    assert "\x1b" not in safe
    assert "\x07" not in safe
    assert "\\x1b" in safe
    assert "\\x07" in safe


def test_terminal_safe_escapes_carriage_return():
    malicious = "status: concluída\rstatus: FALHOU"
    safe = terminal_safe_text(malicious)

    assert "\r" not in safe
    assert "\\r" in safe


def test_terminal_safe_escapes_embedded_newline():
    malicious = 'trecho real"\n  - claim forjada: segundo a análise, a fonte apoia esta claim'
    safe = terminal_safe_text(malicious)

    assert "\n" not in safe
    assert "\\n" in safe


def test_terminal_safe_escapes_tab():
    safe = terminal_safe_text("a\tb")
    assert "\t" not in safe
    assert "\\t" in safe


def test_terminal_safe_escapes_unicode_bidi_override():
    """U+202E (RIGHT-TO-LEFT OVERRIDE) pode reordenar visualmente o texto
    ao redor sem mudar os bytes -- nunca deixado passar como está."""
    malicious = "trecho‮ reordenado"
    safe = terminal_safe_text(malicious)

    assert "‮" not in safe
    assert "\\u202e" in safe


def test_terminal_safe_leaves_ascii_control_free_text_untouched():
    """Garantia negativa: o helper não introduz escaping onde não é
    necessário -- string sem nenhum controle sai idêntica."""
    text = "nenhum controle aqui, só pontuação normal: (), -, \"aspas\"."
    assert terminal_safe_text(text) == text


def test_terminal_safe_has_no_newline_bypass_parameter():
    """Revisão independente, round 2: um `allow_newlines` permissivo
    já existiu aqui e foi removido -- string achatada não carrega
    proveniência de "newline de layout" vs. "newline injetado", então
    não pode existir NENHUM jeito de pedir pra `\\n` passar sem
    escaping. `terminal_safe_text` só aceita um argumento posicional."""
    import inspect

    signature = inspect.signature(terminal_safe_text)
    assert list(signature.parameters) == ["text"]
