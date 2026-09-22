"""
Natural Answer -- renderização conversacional, OPCIONAL, VERSIONADA e
DETERMINÍSTICA de um `PrimaryAnswer` já validado (app/editor/primary_answer.py).

Implementa a passagem de arquitetura "Natural Primary Answer": o objetivo é
fazer a resposta voltada ao usuário parecer mais uma resposta de assistente
concisa e menos um relatório em seções (CONCLUSÃO CENTRAL / PRINCIPAIS
RAZÕES / CONTRAPONTOS / ...), SEM introduzir prosa factual escrita por
modelo.

Cadeia de autoridade (fechada, nesta ordem):

    Judge -> plano de seleção validado (`PrimaryAnswerPlan`/`validate_plan`)
    -> `PrimaryAnswer` estruturado e validado
    -> este renderizador determinístico, puro, SEM chamada de LLM
    -> `NaturalAnswer`

`PrimaryAnswer` continua sendo o registro de apresentação AUTORITATIVO e o
fallback seguro -- `NaturalAnswer` nunca o substitui, só o realiza numa
forma de leitura diferente.

FRONTEIRA DE AUTORIDADE (não-negociável) -- este módulo NUNCA:

- chama uma LLM;
- parafraseia claims;
- resume claims numa proposição nova;
- combina claims numa proposição nova;
- infere causalidade/comparação;
- fortalece recomendação ou enfraquece qualificação;
- move condição/exceção pra longe da claim a que pertence;
- adiciona exemplo ou conhecimento externo;
- consome prosa de participante, explicação do Judge, Source Analysis ou
  texto de fonte bruto.

O texto factual de cada claim (`PrimaryAnswerItem.claim_text`) é sempre
usado INTACTO (nenhuma substituição de sinônimo, nenhuma reescrita de
pronome, nenhuma pontuação inserida DENTRO dele). Toda a linguagem
conectiva ("moldura") vem de um vocabulário FECHADO, pequeno,
autorado pela aplicação, indexado só pelo PAPEL (já decidido pelo plano
validado) -- nunca pelo conteúdo/texto da claim, nunca pelo veredito
sozinho decidindo qual moldura usar (evita qualquer canal onde texto
hostil de claim pudesse influenciar qual frase de aplicação é escolhida).

Por que os rótulos de veredito continuam anexados a cada claim (em vez de
uma frase única por grupo que os "resumisse"): "parcialmente sustentada,
com ressalvas" e "sustentada pelo debate" são status epistêmicos
DIFERENTES: colapsá-los sob uma única moldura de grupo enfraqueceria a
ressalva de uma claim parcialmente sustentada sempre que ela aparecesse
ao lado de uma claim totalmente sustentada no mesmo papel -- exatamente o
tipo de enfraquecimento de qualificação que esta camada tem proibição
explícita de fazer. A troca (mais repetição do rótulo, correção
preservada) é intencional: nunca "melhorar" estilo às custas de garantia
semântica.

Contrato de renderizador: VERSIONADO (`renderer_contract_version`) --
`_RENDERERS` é um registro fechado {versão: função pura}. Uma versão
desconhecida nunca é silenciosamente aceita/re-renderizada com a versão
mais nova: falha fechado (ver `expected_rendered_text`).
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from app.editor.primary_answer import (
    PRIMARY_ANSWER_LIMITATIONS_HEADING,
    PrimaryAnswer,
    PrimaryAnswerItem,
    PrimaryAnswerRole,
    PrimaryAnswerSection,
)

NATURAL_ANSWER_CONTRACT_VERSION = "natural_answer_v1"

_DOMAIN_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Molduras conversacionais FIXAS, por PAPEL -- vocabulário fechado,
# nunca escrito por LLM, nunca escolhido a partir de conteúdo de claim.
# Cada frase descreve só a FUNÇÃO estrutural que o papel já tinha (o
# mesmo papel que já origina os headings de app/editor/primary_answer.py)
# -- nunca uma relação causal/comparativa NOVA entre claims específicas.
# `None` = primeiro parágrafo (conclusão central), sem moldura: é a
# resposta direta, não uma "seção" à parte.
_ROLE_LEAD: dict[PrimaryAnswerRole, str | None] = {
    "central_conclusion": None,
    "supporting_reasons": "Isso se apoia no seguinte, avaliado no debate:",
    "tradeoffs": "Também foram registrados contrapontos relevantes:",
    "conditions": "A resposta pode mudar dependendo do seguinte:",
    "uncertainties": "Ficam registradas as seguintes incertezas e ressalvas:",
}


def _render_item_sentence(item: PrimaryAnswerItem) -> str:
    """Claim INTACTA + rótulo de veredito (vocabulário fechado, já
    validado em `PrimaryAnswer`) -- pontuação (espaço, parênteses, ponto
    final) é sempre acrescentada FORA do texto da claim, nunca inserida
    dentro dele."""
    return f"{item.claim_text} ({item.verdict_label})."


def _render_role_paragraph(section: PrimaryAnswerSection) -> str:
    sentences = " ".join(_render_item_sentence(item) for item in section.items)
    lead = _ROLE_LEAD[section.role]
    return f"{lead} {sentences}" if lead else sentences


def render_natural_answer_text(primary: PrimaryAnswer) -> str:
    """Renderizador PURO da versão `natural_answer_v1` -- única função de
    fato deste contrato. Nunca lê nada além de `primary` (que já foi
    validado internamente e contra os registros da execução em outro
    lugar, ver app/editor/primary_answer_coherence.py) -- nenhuma
    ampliação de superfície de confiança.

    COBERTURA: toda claim selecionada aparece (via `primary.sections`,
    que `PrimaryAnswer` já garante não-vazio e completo); toda limitação
    registrada aparece (`primary.limitations`, nunca encurtada); o aviso
    de escopo/divulgação (`primary.scope_note`, já determinístico e
    validado) é preservado verbatim ao final -- nada é omitido em
    silêncio."""
    paragraphs = [_render_role_paragraph(section) for section in primary.sections]
    if primary.limitations:
        limitation_lines = "\n".join(f"- {text}" for text in primary.limitations)
        paragraphs.append(f"{PRIMARY_ANSWER_LIMITATIONS_HEADING}\n{limitation_lines}")
    paragraphs.append(primary.scope_note)
    return "\n\n".join(paragraphs)


# Registro FECHADO {versão do contrato -> renderizador puro}. Cresce só
# quando uma nova versão de renderizador for adicionada explicitamente
# aqui -- nunca implicitamente aceito por um "else" permissivo.
_RENDERERS: dict[str, Callable[[PrimaryAnswer], str]] = {
    NATURAL_ANSWER_CONTRACT_VERSION: render_natural_answer_text,
}


def known_renderer_contract_version(version: str) -> bool:
    return version in _RENDERERS


def expected_rendered_text(renderer_contract_version: str, primary: PrimaryAnswer) -> str:
    """Recomputa o texto que a versão declarada produziria a partir do
    `PrimaryAnswer` real -- usado tanto pela auto-validação de
    `NaturalAnswer` quanto pela verificação de coerência entre registros
    (app/editor/natural_answer_coherence.py). Levanta `ValueError` pra
    uma versão não reconhecida -- fail-closed, nunca cai pra versão mais
    recente silenciosamente."""
    try:
        renderer = _RENDERERS[renderer_contract_version]
    except KeyError:
        raise ValueError(
            f"renderer_contract_version desconhecida: {renderer_contract_version!r}"
        ) from None
    return renderer(primary)


class NaturalAnswer(BaseModel):
    """Representação conversacional, ADITIVA e OPCIONAL, de um
    `PrimaryAnswer` já validado. NUNCA reinterpreta
    `PrimaryAnswer.rendered_text`, nunca muda o significado de
    `FinalAnswer.answer_text`/`answer_blocks` -- é uma terceira leitura
    determinística do MESMO conteúdo autoritativo.

    Contrato mínimo, aditivo por design: versão do renderizador, o
    veredito em que se baseia (mesmo valor de
    `PrimaryAnswer.based_on_verdict_id`) e o texto renderizado exato que
    o usuário vê / que "Copiar resposta" copia quando presente."""

    model_config = _DOMAIN_CONFIG

    renderer_contract_version: str = Field(min_length=1)
    based_on_verdict_id: str = Field(min_length=1)
    rendered_text: str = Field(min_length=1)


def render_natural_answer(primary: PrimaryAnswer) -> NaturalAnswer:
    """Ponto de entrada usado pela composição (app/editor/compose.py) --
    função PURA, sem chamada de LLM, sem estado, sem I/O. Qualquer falha
    (defensiva: um `PrimaryAnswer` coerente sempre deveria produzir um
    `NaturalAnswer` válido daqui) propaga como `ValueError`/
    `pydantic.ValidationError` -- o CHAMADOR (nunca este módulo) decide
    tratar isso como fallback opcional."""
    text = render_natural_answer_text(primary)
    return NaturalAnswer(
        renderer_contract_version=NATURAL_ANSWER_CONTRACT_VERSION,
        based_on_verdict_id=primary.based_on_verdict_id,
        rendered_text=text,
    )
