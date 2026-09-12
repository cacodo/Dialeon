"""
`terminal_safe_text` -- extraído de `app/cli/output.py` (patch de
segurança de terminal) para um módulo neutro de topo, no mesmo espírito
de `app/structured_output.py` (utilitário pequeno e único, importado
por mais de uma camada sem criar dependência de uma camada de
apresentação específica pra outra).

ONDE isto se aplica -- invariante fechado por auditoria (patch de
terminal-safety do FinalAnswer da CLI, round 2): escaping terminal-
específico pertence SOMENTE ao limite real de apresentação em terminal
-- `app/cli/output.py::human_run_result` (e `_human_source_analysis_lines`,
no bloco de auditoria) -- NUNCA em código de domínio/aplicação
(`app/editor/compose.py`, `app/source_analysis/`, etc.). `Claim.text`,
`ClaimAssessment.explanation`, `JudgeVerdict.debate_limitations`,
`editor_model` (metadado reportado pelo provider, também tratado como
não confiável) e qualquer excerpt de `SourceAnalysisResult` entram em
`FinalAnswer.answer_text` sempre BYTE-FIÉIS, verbatim -- `answer_text`
é o dado CANÔNICO (persistido, devolvido por `--json`/API, consumido
pelo frontend), nunca um artefato de terminal; ele não pode carregar
uma transformação que só faz sentido pra um terminal ANSI. A CLI
humana é quem de fato escreve num terminal real -- é ali, e só ali, que
`terminal_safe_text(...)` deve envolver qualquer texto que possa
conter `Claim.text`/`explanation`/`limitations`/`editor_model`/excerpt
de fonte antes do `print()`.

`terminal_safe_text` é SEMPRE estrito (sem parâmetro de "permitir
newline") -- ver docstring da função pra por que um modo permissivo já
existiu e foi removido.

Isso evita dois erros: (1) mutar/perder o byte-fidelity do dado
canônico só pra satisfazer um consumidor específico (a CLI); (2)
duplicar a lógica de escaping em vários pontos -- há exatamente UM
lugar por saída humana (`human_run_result` pro caminho principal,
`_human_source_analysis_lines` pro bloco de auditoria de fonte).

Nunca usada pra `--json`/persistência, que permanecem byte-fiéis ao
valor original em `FinalAnswer.answer_text`/`SourceAnalysisResult`/
`SourceClaimAnalysisResultPublic.excerpt` -- ver docstring de
`terminal_safe_text`.
"""

from __future__ import annotations

# Notação visível fixa pros controles mais comuns/perigosos, pra ficar
# legível ("\x1b" em vez do fallback genérico "") quando aparecem
# sozinhos. Qualquer OUTRO caractere não imprimível (control C0/C1,
# formatação/bidi Unicode como RLO/PDF, separadores de linha/parágrafo,
# etc.) cai no fallback genérico de `terminal_safe_text` -- nunca uma
# lista fechada de "casos conhecidos", porque o objetivo é nunca deixar
# NENHUM caractere não imprimível passar, conhecido ou não.
_NAMED_CONTROL_ESCAPES: dict[str, str] = {
    "\x1b": "\\x1b",  # ESC -- início de toda sequência ANSI/OSC
    "\r": "\\r",  # carriage return -- sobrescrita de linha
    "\n": "\\n",  # newline embutido -- forjaria uma linha nova
    "\t": "\\t",
}


def terminal_safe_text(text: str) -> str:
    """Neutraliza texto NÃO CONFIÁVEL (dado de usuário/fonte externa, ou
    texto produzido por LLM/provider que a aplicação nunca controla
    totalmente -- `Claim.text`, `ClaimAssessment.explanation`,
    `debate_limitations`, `editor_model`) antes de interpolar numa
    linha de terminal HUMANA -- nunca usado pra `--json`/persistência,
    que continuam byte-fiéis ao valor original. Preserva verbatim todo
    caractere Unicode IMPRIMÍVEL (inclusive acentuação/pontuação normal
    em português) -- só o que `str.isprintable()` já classifica como
    não-imprimível (controles C0/C1 -- ESC/CR/LF/TAB/etc., formatação/
    bidi Unicode como RLO U+202E, separadores de linha/parágrafo
    U+2028/U+2029, substitutos, área de uso privado) vira notação
    visível `\\xNN`/`\\uNNNN`/`\\UNNNNNNNN` -- nunca interpretado pelo
    terminal como controle real, nunca reordena visualmente nem
    sobrescreve/forja outras linhas já impressas. Escapa caractere a
    caractere (não detecta "sequências") de propósito -- mais forte que
    reconhecer padrões ANSI/OSC específicos, que sempre podem ter uma
    variante não prevista; remover o ESC/controle em si já neutraliza
    qualquer sequência que dependesse dele.

    SEMPRE estrito -- inclusive `\\n` (SEM exceção pra newlines "reais"
    de layout): a proveniência de cada `\\n` dentro de uma string já
    achatada (ex.: `FinalAnswer.answer_text`, que mistura template
    autorado pela aplicação com `Claim.text`/`explanation`/excerpt de
    fonte NÃO CONFIÁVEIS) se perde antes de chegar aqui -- não há como
    distinguir "newline de layout" de "newline injetado por conteúdo
    não confiável" só olhando pro caractere. Um `allow_newlines`
    permissivo já existiu aqui e foi removido (revisão independente,
    round 2) por permitir que texto não confiável embutido em
    `answer_text` forjasse uma linha de terminal nova e indistinguível
    (ex.: `"...\\nStatus: FORGED"`). Corretude do limite de confiança
    de terminal tem prioridade sobre apresentação multi-linha bonita --
    ver app/cli/output.py::human_run_result."""
    out: list[str] = []
    for ch in text:
        if ch in _NAMED_CONTROL_ESCAPES:
            out.append(_NAMED_CONTROL_ESCAPES[ch])
        elif ch.isprintable():
            out.append(ch)
        else:
            code_point = ord(ch)
            if code_point <= 0xFF:
                out.append(f"\\x{code_point:02x}")
            elif code_point <= 0xFFFF:
                out.append(f"\\u{code_point:04x}")
            else:
                out.append(f"\\U{code_point:08x}")
    return "".join(out)
