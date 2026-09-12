"""
Normalização MÍNIMA e conservadora de output estruturado — Etapa 17A.1.

Achado real de produção: um output de agrupamento genuinamente válido
(`{"groups": [], "ungrouped_claim_ids": [...]}`) foi rejeitado
inteiramente só por vir envolto numa cerca de código Markdown
(` ```json ... ``` `) — um hábito comum de formatação de LLM que não
tem nada a ver com a validade do JSON em si.

`strip_single_json_code_fence` é a ÚNICA normalização feita: se
`raw_text` for EXATAMENTE um bloco de código cercando JSON (nada de
prosa antes/depois, cerca de abertura e fechamento presentes), devolve
só o conteúdo interno. Em QUALQUER outro caso — JSON cru sem cerca,
prosa com JSON embutido, múltiplos blocos, cerca aberta mas nunca
fechada (JSON truncado) — devolve `raw_text` inalterado, deixando o
`json.loads()` de quem chama decidir e falhar normalmente.

Deliberadamente NÃO faz:
- busca de substring JSON dentro de prosa arbitrária;
- reparo de JSON truncado/incompleto;
- remoção de texto arbitrário;
- qualquer heurística de recuperação de JSON malformado.

Compartilhado por extração, agrupamento, Judge, Editor e SourceAnalyzer
-- a normalização em si é pura e não tem opinião sobre qual exceção
cada consumidor levanta em caso de falha (cada um continua com seu
próprio contrato de erro, inalterado); só o texto de entrada pro
`json.loads()` de cada um passa por aqui primeiro.
"""

from __future__ import annotations

import re

# Âncorada em ambas as pontas (^...$, com re.DOTALL pra "." cruzar
# linhas no conteúdo interno) -- só casa quando a string INTEIRA é um
# único bloco cercado, nunca um trecho dentro de texto maior. O rótulo
# de linguagem ("json") é opcional; espaço em branco extra fora da
# cerca é tolerado, mas nada além disso. O conteúdo capturado nunca
# pode conter "```" ele mesmo ((?!```).)* -- garante que uma segunda
# cerca no meio do texto faz o casamento inteiro falhar (múltiplos
# blocos permanecem rejeitados), em vez de backtracking greedy
# engolir a cerca do meio.
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n((?:(?!```).)*)\n\s*```\s*$", re.DOTALL)


def strip_single_json_code_fence(raw_text: str) -> str:
    match = _JSON_FENCE_RE.match(raw_text)
    if match is None:
        return raw_text
    return match.group(1)
