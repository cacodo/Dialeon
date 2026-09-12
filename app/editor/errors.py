"""
Exceções da camada de "structured output" do Editor (Etapa 7; ajustado na
Etapa 17B). LOCAIS a este módulo — não importadas do Judge nem do claim
processor, mesma disciplina de camadas independentes já estabelecida na
Etapa 5/6.

- EditorError: base.
- MalformedEditorOutputError: a chamada teve sucesso de transporte, mas o
  texto retornado não é JSON válido, ou é JSON válido que não bate com o
  schema esperado (`EditorPlan`) -- inclui tanto um valor de enum
  desconhecido (`opening_style`/`closing_style` fora do `Literal`) quanto
  qualquer campo extra (`extra="forbid"`), o que cobre em particular uma
  tentativa de reintroduzir os campos do contrato pré-Etapa-17B
  (`narrative`, `synthesis_intro`, `synthesis_conclusion`,
  `claim_narratives`) -- esse payload nunca é um `EditorPlan` válido.

Etapa 17B -- `InconsistentEditorReferenceError` foi REMOVIDA (não apenas
deixou de ser levantada): existia só pra validar referências a `claim_id`
dentro do output da LLM (cobertura exata, sem duplicata, rótulo fiel ao
veredito real) -- `EditorPlan` não tem NENHUM campo que referencie uma
claim, então essa classe inteira de erro ficou estruturalmente
inexpressível, não apenas sem uso. `EditorAttempt.parse_status` mantém
`"inconsistent_references"` no `Literal` só por compatibilidade de
leitura de registros históricos (Etapa 7) que usaram esse valor -- nenhum
código novo o produz.
"""

from __future__ import annotations


class EditorError(Exception):
    """Base de todas as exceções desta camada."""


class MalformedEditorOutputError(EditorError):
    """JSON inválido, ou JSON válido que não bate com o schema esperado
    (`EditorPlan`) -- inclui enum inválido e qualquer campo extra/antigo."""
