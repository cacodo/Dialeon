"""
Contrato estruturado de I/O do Editor (Etapa 7; substituído na Etapa 17B).

Deliberadamente NÃO é `FinalAnswer`/`EditorResult` de domínio
(app/editor/result.py) — é o que a LLM tem permissão de produzir.

Etapa 17B — reescrita completa do contrato (vulnerabilidade B4): o
contrato anterior (`ClaimNarrativeDraft`/`EditorOutput`, removidos deste
módulo) permitia à LLM escrever prosa livre por claim (`narrative`) e
prosa de síntese livre (`synthesis_intro`/`synthesis_conclusion`).
Pydantic só validava o RÓTULO declarado (`verdict_reflected`) contra o
veredito real — nunca o CONTEÚDO em linguagem natural que o acompanhava.
Isso permitia um payload estruturalmente "correto" (rótulo batendo
exatamente) cujo texto livre CONTRADIZIA esse mesmo rótulo (ex.:
`verdict_reflected="rejected"` com `narrative="Isto está correto e
deve ser tratado como estabelecido."`) — nada detectava isso, porque não
havia nada ali além do rótulo pra validar.

`EditorPlan` fecha essa vulnerabilidade por construção, não por mais
validação: não existe NENHUM campo de string livre neste schema. Os
únicos dois campos são `Literal` finitos (um vocabulário pequeno e
explícito de escolhas de apresentação, ver docstrings de cada campo) —
não há onde uma frase contradizendo um veredito possa sequer ser
escrita. A LLM Editor deixa de ser autora de prosa e passa a ser
PLANEJADORA de apresentação: escolhe, entre opções finitas, COMO a
resposta determinística (montada por app/editor/compose.py a partir de
`ClaimAssessment.verdict`/`explanation`/`debate_limitations`, sempre
dados do Judge, nunca da LLM) deve ser introduzida/encerrada. Nenhuma
palavra voltada ao usuário se origina aqui.

Isso NÃO resolve (fora de escopo da Etapa 17B, ver app/editor/compose.py):
se `ClaimAssessment.explanation` (texto livre do PRÓPRIO Judge) é
internamente coerente com `ClaimAssessment.verdict` — essa é uma
garantia sobre o contrato do Judge, uma camada acima desta, que a Etapa
17B não tenta prover.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_IO_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EditorPlan(BaseModel):
    """Único artefato que a LLM Editor tem permissão de produzir (Etapa
    17B) — um plano de apresentação puramente estrutural, sem nenhum
    campo de texto livre. `extra="forbid"` (via `_IO_CONFIG`) rejeita
    explicitamente qualquer tentativa de reintroduzir os campos do
    contrato antigo (`narrative`, `synthesis_intro`,
    `synthesis_conclusion`, `claim_narratives`) — um payload que tente
    fornecê-los nunca é um `EditorPlan` válido, vira
    `MalformedEditorOutputError` em app/editor/compose.py, tratado como
    qualquer outra saída malformada (fallback determinístico).

    Não há referência a `claim_id` aqui: a cobertura/ordem das claims
    NUNCA dependeu da LLM (o renderizador determinístico sempre itera
    `JudgeVerdict.claim_assessments` na ordem do próprio Judge) — omitir
    esse campo inteiramente, em vez de validá-lo, é mais forte que
    validação: não sobra canal algum pela LLM tentar omitir/reordenar
    uma claim."""

    model_config = _IO_CONFIG

    opening_style: Literal["direct", "contextual"] = Field(
        description=(
            "Como a resposta abre. 'direct': vai direto ao resultado da avaliação, "
            "sem preâmbulo. 'contextual': antepõe uma frase FIXA (autorada pela "
            "aplicação, nunca pela LLM) que reconecta a resposta à pergunta original "
            "antes do resultado. Nenhuma das duas opções introduz uma afirmação "
            "factual nova, resume claims, ou é sensível à distribuição de "
            "veredictos -- ambas são seguras para qualquer combinação de "
            "supported/partially_supported/rejected/conflicting/unresolved (ver "
            "app/editor/compose.py, _render_final_answer_text)."
        )
    )
    closing_style: Literal["concise", "limitations_focused"] = Field(
        description=(
            "Como a resposta fecha. 'concise': as limitações do debate (se houver) "
            "aparecem listadas, sem destaque adicional -- mesmo comportamento do "
            "fallback determinístico pré-Etapa-17B. 'limitations_focused': as mesmas "
            "limitações (VERBATIM, nunca reescritas/resumidas) recebem uma frase de "
            "introdução FIXA que chama atenção explícita pra elas antes de "
            "listá-las; se não houver nenhuma limitação registrada, uma frase FIXA "
            "alternativa (nunca 'sem limitações' apresentado como garantia de "
            "qualidade) cobre esse caso -- nenhuma das duas variantes esconde, "
            "resume ou remove limitações existentes. Nenhuma das duas opções "
            "fortalece confiança, resolve conflito, ou depende do conteúdo das "
            "claims -- ambas são seguras pra qualquer distribuição de veredictos."
        )
    )
