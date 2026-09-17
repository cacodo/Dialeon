"""
Blocos estruturados do corpo da resposta final -- UI Slice 3 (Structured
Final Answer).

Representação ADITIVA e opcional de `FinalAnswer.answer_text`: os MESMOS
dados de composição que já produzem `answer_text` (via `_compose_answer`,
app/editor/compose.py) também produzem esta sequência de blocos tipados,
na MESMA chamada -- nunca duas fontes de verdade independentes, e nunca
por parsing de `answer_text` (que mistura texto app-autorado com texto
NÃO CONFIÁVEL de Claim/Judge/fonte sem nenhum delimitador reversível --
ver docstring de app/text_safety.py). A ESTRUTURA (quantos blocos
existem, em que ordem, o que é heading vs. item de lista) é decidida
INTEIRAMENTE pela aplicação, a partir de dados JÁ estruturados
(`JudgeVerdict.claim_assessments`, `Claim.text`, reconciliação) -- as
strings-folha de cada bloco (`claim_text`, `explanation`,
`source_relationship_note`) continuam exatamente tão NÃO CONFIÁVEIS
quanto sempre foram: nunca reinterpretadas, e nunca podem, por si
mesmas, criar um bloco novo. Um `explanation` contendo literalmente
`"\\n\\n# Heading\\n- item"` continua sendo o valor de UM campo de
string de UM `AnswerClaimItem` -- nunca vira um heading ou item de lista
novo, porque nada aqui faz `.split()`/regex/scan sobre essas strings
pra decidir estrutura.

Deliberadamente NÃO cobre limitações (`debate_limitations`) -- ver
docstring de `_compose_answer` (app/editor/compose.py) sobre a regra de
autoridade única: `FinalAnswer.limitations` já é a única fonte
RENDERIZADA de limitações no frontend (lista dedicada, sempre visível
quando não vazia); `answer_blocks` nunca duplica esse conteúdo. Só
`answer_text` (string plana, byte-idêntica à de antes desta slice)
continua ecoando limitações textualmente, para compatibilidade com
consumidores existentes (CLI, `--json`, runs históricos).

Deliberadamente NÃO cobre o caminho sem veredito (`Editor._no_verdict_result`)
-- decisão de escopo explícita desta slice, não uma lacuna acidental: a
lista de claims-não-avaliadas daquele caminho é estruturalmente mais
simples (uma introdução + uma lista simples, sem buckets/veredito/
reconciliação) e o frontend já lida com esse caso corretamente hoje via
`splitAnswerParagraphs`. `FinalAnswer.answer_blocks` fica `None` pra
`status="deterministic_no_verdict"`, exatamente como fica pra qualquer
run histórico anterior a esta slice -- nunca um caso especial novo pro
frontend distinguir.

Deliberadamente NÃO um framework de rich-content/evidence genérico:
apenas os três tipos de bloco que o contrato de resposta final ATUAL
realmente produz hoje (parágrafo de abertura, seção de claims
avaliadas). Nenhum bloco de "citação"/"tabela"/"código"/link -- nenhum
desses existe na composição atual, e nenhum é inventado aqui
especulativamente.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Repair (revisão adversarial, achado 3) -- vocabulário FECHADO de
# headings/rótulos que este módulo (o "contrato" do documento
# estruturado) está autorizado a aceitar. Os valores são os MESMOS,
# byte-a-byte, de `_BUCKET_A_HEADING`/`_BUCKET_B_HEADING`/`_VERDICT_LABELS`
# em app/editor/compose.py (o único lugar que hoje PRODUZ esses valores)
# -- duplicados aqui de propósito, nunca importados de lá: importar
# criaria um ciclo (compose.py já importa `AnswerBlock` deste módulo), e
# este módulo é o contrato de dados que compose.py deve satisfazer, não
# o inverso. A consistência entre as duas cópias é garantida por um
# teste dedicado (tests/editor/test_answer_blocks.py), não por um
# mecanismo de import -- qualquer divergência futura falha CI alto e
# claro, em vez de silenciosamente aceitar um heading/rótulo novo que a
# composição real nunca produziu.
#
# Sem este fechamento, `heading`/`verdict_label` seriam `str` livres --
# um heading ou rótulo FORJADO (por um bug de composição futuro, ou por
# JSON persistido malformado sendo reconstruído, ver
# app/storage/serializers.py) passaria a validação Pydantic normalmente,
# violando a garantia central desta representação estruturada: que toda
# estrutura (headings, rótulos) é sempre app-autorada, nunca arbitrária.
AnswerSectionHeading = Literal[
    "Conclusões sustentadas pelo debate:",
    "Pontos não estabelecidos pelo debate:",
]

AnswerVerdictLabel = Literal[
    "sustentada pelo debate",
    "parcialmente sustentada, com ressalvas",
    "rejeitada pelo juiz com base no debate disponível",
    "com posições conflitantes, não resolvida",
    "sem informação suficiente para decidir",
]


class AnswerParagraphBlock(BaseModel):
    """Uma frase única, inteiramente app-autorada -- a abertura
    ("direct"/"contextual", ver `_OPENING_DIRECT`/`_OPENING_CONTEXTUAL_TEMPLATE`,
    app/editor/compose.py). `text` nunca contém conteúdo NÃO CONFIÁVEL
    bruto por si só -- a única exceção é o excerto JÁ LIMITADO/achatado
    da pergunta original dentro da abertura "contextual"
    (`_bounded_question_excerpt`), que é a MESMA garantia que
    `answer_text` já tinha antes desta slice."""

    model_config = _CONFIG

    kind: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)


class AnswerClaimItem(BaseModel):
    """Uma avaliação de claim dentro de uma `AnswerClaimSectionBlock`.

    `claim_text` (de um participante do debate), `explanation` (do
    Judge) e `source_relationship_note` (derivado de reconciliação, que
    por sua vez pode incluir um excerto verbatim da fonte fornecida pelo
    usuário) são NÃO CONFIÁVEIS -- sempre strings-folha, tratadas como
    DADO puro, nunca reinterpretadas ou re-escaneadas por este ou
    qualquer bloco. `verdict_label` é sempre um dos rótulos fixos de
    `_VERDICT_LABELS` (app/editor/compose.py) -- nunca texto de LLM."""

    model_config = _CONFIG

    claim_text: str = Field(min_length=1)
    verdict_label: AnswerVerdictLabel
    explanation: str = Field(min_length=1)
    source_relationship_note: str | None = None


class AnswerClaimSectionBlock(BaseModel):
    """Uma das duas seções fixas de veredito (ver `_BUCKET_A_HEADING`/
    `_BUCKET_B_HEADING`, app/editor/compose.py) -- `heading` é sempre um
    dos dois textos fixos correspondentes, `items` é a projeção
    EXAUSTIVA e ORDENADA (mesma ordem em que o Judge listou
    `claim_assessments`) das avaliações daquele bucket. Nunca vazio --
    uma seção com zero avaliações simplesmente não é emitida (mesma
    disciplina de `answer_text`, que nunca imprime um cabeçalho de
    bucket vazio).

    Repair (revisão adversarial, achado 2) -- `items` é `tuple[...]`,
    nunca `list[...]`: `ConfigDict(frozen=True)` só bloqueia REATRIBUIR
    o campo (`block.items = outra_coisa`), nunca impede mutação IN-PLACE
    de uma lista referenciada por um campo "congelado"
    (`block.items.append(...)` mutaria silenciosamente o snapshot já
    construído). Pydantic v2 sempre VALIDA um `tuple[X, ...]` construindo
    uma tupla nova a partir do que for passado -- uma lista mutável
    fornecida pelo chamador (ou reconstruída de JSON persistido) nunca
    fica referenciada diretamente pelo modelo; e a tupla resultante, por
    ser imutável no nível da linguagem, não aceita `.append`/`[i] =` de
    jeito nenhum. `AnswerClaimItem` (o elemento) já é integralmente
    imutável por si só (frozen + só campos escalares `str`/`str | None`,
    nenhuma coleção aninhada) -- então a imutabilidade daqui pra baixo é
    genuína e recursiva, sem precisar de cópia defensiva manual."""

    model_config = _CONFIG

    kind: Literal["claim_section"] = "claim_section"
    heading: AnswerSectionHeading
    items: tuple[AnswerClaimItem, ...] = Field(min_length=1)


AnswerBlock = Annotated[
    Union[AnswerParagraphBlock, AnswerClaimSectionBlock],
    Field(discriminator="kind"),
]

# Repair (revisão adversarial, achado 3) -- a ÚNICA ordem de heading que
# a composição real produz (ver `_compose_answer`, app/editor/compose.py):
# bucket A antes de bucket B, quando ambos existem. Usado por
# `FinalAnswer._answer_blocks_follow_the_fixed_document_shape`
# (app/editor/result.py) pra rejeitar uma sequência de blocos fora de
# ordem, duplicada, ou com uma seção que não seja uma destas duas --
# nunca um framework de documento genérico, só a validação concreta da
# ÚNICA forma que este contrato hoje permite.
ANSWER_SECTION_HEADING_ORDER: tuple[AnswerSectionHeading, ...] = (
    "Conclusões sustentadas pelo debate:",
    "Pontos não estabelecidos pelo debate:",
)

# Repair (fechamento do contrato estruturado) -- mapeamento FECHADO de
# heading -> conjunto de `verdict_label` que aquele heading pode
# legitimamente conter, espelhando byte-a-byte a partição de
# `_VERDICT_TO_BUCKET`/`_VERDICT_LABELS` em app/editor/compose.py (BUCKET
# A = "supported"/"partially_supported"; BUCKET B = "conflicting"/
# "unresolved"/"rejected") -- duplicado aqui pela MESMA razão que
# `AnswerSectionHeading`/`AnswerVerdictLabel` acima são duplicados (evitar
# o ciclo de import com compose.py; consistência garantida por
# tests/editor/test_answer_blocks.py, não por import).
#
# Sem este fechamento, um `AnswerClaimItem` com `verdict_label="rejeitada
# pelo juiz com base no debate disponível"` dentro da seção "Conclusões
# sustentadas pelo debate:" passaria a validação Pydantic normalmente --
# cada campo, isoladamente, é um Literal válido, mas a COMBINAÇÃO é
# semanticamente impossível (nenhuma composição real jamais produz isso,
# ver `_compose_answer`) e nunca deveria ser aceita como um documento
# estruturado genuíno.
ANSWER_SECTION_HEADING_VALID_VERDICT_LABELS: dict[AnswerSectionHeading, frozenset[AnswerVerdictLabel]] = {
    "Conclusões sustentadas pelo debate:": frozenset(
        {
            "sustentada pelo debate",
            "parcialmente sustentada, com ressalvas",
        }
    ),
    "Pontos não estabelecidos pelo debate:": frozenset(
        {
            "com posições conflitantes, não resolvida",
            "sem informação suficiente para decidir",
            "rejeitada pelo juiz com base no debate disponível",
        }
    ),
}
