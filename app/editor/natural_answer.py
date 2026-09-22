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

Repair (closure repair sobre 9464fdf, Blocker 4 -- "trusted/untrusted
presentation boundary"): `rendered_text` é uma ÚNICA string plana que
mistura andaimes de aplicação (confiáveis) com `claim_text`/`limitations`
(NUNCA confiáveis -- texto de participante/Judge). O frontend
(`NaturalAnswerView`, frontend/src/components/FinalAnswerView.tsx) separa
essa string em parágrafos de apresentação SÓ por linha em branco
(`splitAnswerParagraphs`, igual a `answer_text`) -- uma claim selecionada
contendo `"\n\n"` podia portanto forjar uma fronteira de parágrafo
aplicação-nível e visualmente destacar o rótulo de veredito que a segue
da própria claim, sem que este renderizador de domínio jamais reescrevesse
nada (`PrimaryAnswer`, a apresentação TIPADA por trás, nunca foi vulnerável
a isso -- claim_text e veredito são nós de texto React SEPARADOS lá, nunca
uma string recombinada/re-parseada por padrão visual).

Reparo escolhido -- a SOLUÇÃO CONSERVADORA DE PRIMEIRA FATIA (não a
extensão pra segmentos tipados): quando o texto de uma claim selecionada OU
de uma limitação contém um controle estrutural de apresentação não seguro
(quebra de parágrafo forjada, caractere de controle bidirecional, ou
caractere de controle não imprimível -- ver `_has_unsafe_structural_presentation_controls`
abaixo), `natural_answer_v1` é RECUSADO inteiro (`NaturalAnswerUnsafePresentationError`,
nunca uma sanitização/reescrita silenciosa do texto autoritativo) -- o
chamador (`app/editor/compose.py::_render_natural_answer_or_fallback`) cai
pra `PrimaryAnswer` estruturado (que nunca teve este problema) com um
motivo verdadeiro e limitado (`natural_answer_declined_unsafe_presentation`).
Por que esta é a repair MENOR/mais segura: nenhum schema novo, nenhuma
mudança de contrato em `NaturalAnswer`/`rendered_text`, nenhuma mudança de
frontend -- o fallback pra `PrimaryAnswer` já existe, já é exercitado, e já
é a apresentação SEGURA (tipada) pro mesmo conteúdo; introduzir segmentos
tipados pra `NaturalAnswer` duplicaria exatamente essa segurança que
`PrimaryAnswer` já garante, só que com uma superfície nova (schema
aditivo, versionamento, renderização de frontend) que este repair não
precisa pra fechar a vulnerabilidade -- complexidade especulativa que o
mandato desta repair pede pra evitar. O texto autoritativo NUNCA é
sanitizado/reescrito/normalizado em nenhum dos dois casos (aceito ou
recusado) -- só a DECISÃO de renderizar muda.
"""

from __future__ import annotations

import re
import unicodedata
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


class NaturalAnswerUnsafePresentationError(ValueError):
    """Levantada quando o texto de uma claim selecionada ou de uma
    limitação contém um controle estrutural de apresentação não seguro
    (ver `_has_unsafe_structural_presentation_controls`) -- NUNCA
    sanitizado/reescrito aqui (fronteira de autoridade não-negociável
    deste módulo, ver docstring acima): a única resposta seguro é
    RECUSAR a renderização inteira e deixar o chamador cair pro
    `PrimaryAnswer` estruturado, cuja apresentação (claim_text/veredito
    como nós de texto React separados, nunca uma string recombinada) já
    é segura pro MESMO conteúdo. Subclasse de `ValueError` de propósito:
    propaga pelos MESMOS caminhos de fail-closed que qualquer outra
    versão desconhecida/malformação já usava
    (`expected_rendered_text`/`validate_natural_answer_coherence`), sem
    precisar de um segundo mecanismo de erro."""


# Repair (closure repair, Blocker 4) -- caractere de controle bidirecional
# ou formatação estrutural: podem alterar a ORDEM visual do texto que os
# cerca (ex. RLO/LRO) sem que nenhum byte do texto em si mude -- uma
# claim/limitação com um desses poderia visualmente reordenar o rótulo de
# veredito adjacente. Conjunto FECHADO e EXPLÍCITO (nunca a categoria
# Unicode "Cf" inteira, que inclui caracteres benignos/comuns como ZWJ/ZWNJ
# usados em texto legítimo) -- só os marcadores/controles direcionais
# reais.
_BIDI_STRUCTURAL_CONTROLS = frozenset(
    "؜"  # ARABIC LETTER MARK
    "‎"  # LEFT-TO-RIGHT MARK
    "‏"  # RIGHT-TO-LEFT MARK
    "‪"  # LEFT-TO-RIGHT EMBEDDING
    "‫"  # RIGHT-TO-LEFT EMBEDDING
    "‬"  # POP DIRECTIONAL FORMATTING
    "‭"  # LEFT-TO-RIGHT OVERRIDE
    "‮"  # RIGHT-TO-LEFT OVERRIDE
    "⁦"  # LEFT-TO-RIGHT ISOLATE
    "⁧"  # RIGHT-TO-LEFT ISOLATE
    "⁨"  # FIRST STRONG ISOLATE
    "⁩"  # POP DIRECTIONAL ISOLATE
)

# Repair (closure repair, Blocker 4) -- a AMEAÇA identificada pela revisão:
# duas (ou mais) quebras de linha consecutivas (com só espaço/tab/NBSP
# entre elas) é exatamente o padrão que `splitAnswerParagraphs`
# (frontend/src/api/formatting.ts, `/\n{2,}/`) usa pra forjar uma fronteira
# de parágrafo de APLICAÇÃO a partir de uma string plana -- uma claim/
# limitação contendo isso poderia visualmente destacar o rótulo de
# veredito que a segue. Levemente mais amplo que o regex exato do
# frontend (tolera espaço/tab/NBSP entre as duas quebras) -- defesa em
# profundidade, não uma tentativa de espelhar byte-a-byte uma
# implementação de apresentação que pode mudar.
_FORGED_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t ]*\n")


def _has_unsafe_structural_presentation_controls(text: str) -> bool:
    """Único critério FECHADO (nunca um heurístico difuso) de "controle
    estrutural de apresentação não seguro": quebra de parágrafo forjada
    (ver `_FORGED_PARAGRAPH_BREAK_RE`), caractere de controle
    bidirecional (`_BIDI_STRUCTURAL_CONTROLS`), separador de linha/
    parágrafo Unicode (categorias "Zl"/"Zp" -- U+2028/U+2029, que alguma
    apresentação/CSS pode tratar como quebra estrutural), ou qualquer
    outro caractere de controle não imprimível (categoria "Cc") ALÉM de
    tab/newline isolados (que sozinhos, sem o padrão de quebra dupla
    acima, são conteúdo textual benigno)."""
    if _FORGED_PARAGRAPH_BREAK_RE.search(text):
        return True
    for ch in text:
        if ch in _BIDI_STRUCTURAL_CONTROLS:
            return True
        category = unicodedata.category(ch)
        if category in ("Zl", "Zp"):
            return True
        if category == "Cc" and ch not in ("\t", "\n"):
            return True
    return False


def _reject_if_unsafe_structural_presentation(primary: PrimaryAnswer) -> None:
    """Varre só o texto NÃO CONFIÁVEL que `natural_answer_v1` embutiria
    numa string plana: `claim_text` de cada item SELECIONADO e cada
    `limitations` -- nunca `lead_in`/`heading`/`scope_note` (sempre
    app-autorados, já validados em `PrimaryAnswer`, nunca a fonte da
    ameaça). Mensagem BOUNDED -- nunca ecoa o texto não confiável em si
    (mesma disciplina de `InvalidPrimaryAnswerPlanError.to_feedback`,
    app/editor/primary_answer.py)."""
    texts = [item.claim_text for section in primary.sections for item in section.items]
    texts.extend(primary.limitations)
    if any(_has_unsafe_structural_presentation_controls(text) for text in texts):
        raise NaturalAnswerUnsafePresentationError(
            "natural_answer_v1 recusado: uma claim selecionada ou limitação contém um "
            "controle estrutural de apresentação não seguro (quebra de parágrafo forjada, "
            "caractere bidirecional, ou caractere de controle não imprimível) -- o texto "
            "autoritativo nunca é sanitizado/reescrito; PrimaryAnswer estruturado (cuja "
            "apresentação tipada nunca foi vulnerável a isso) continua disponível"
        )

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


def _render_natural_answer_text_v1(primary: PrimaryAnswer) -> str:
    """Repair (closure repair sobre 9464fdf, Blocker 5 -- "freeze
    natural_answer_v1"): implementação PURA, CONGELADA e SÓ-ADITIVA do
    contrato `natural_answer_v1` -- o corpo EXATO que produz os bytes que
    um `NaturalAnswer` v1 já persistido precisa continuar recomputando
    pra sempre. A partir deste repair, esta função NUNCA é editada de
    novo: qualquer mudança de redação/layout/andaime exige um contrato
    NOVO (`natural_answer_v2`), registrado como uma entrada NOVA em
    `_RENDERERS` (ver abaixo), nunca uma edição aqui. Fixada por um
    fixture golden byte-exato em tests/editor/test_natural_answer.py
    (literal/estático, nunca gerado chamando esta própria função --
    existe só pra detectar deriva futura). Chamada só através de
    `render_natural_answer_text` (que aplica a fronteira de segurança de
    apresentação ANTES de despachar pra cá, ver Blocker 4 acima) --
    nunca diretamente pelo caminho de composição/coerência.

    Nunca lê nada além de `primary` (que já foi validado internamente e
    contra os registros da execução em outro lugar, ver
    app/editor/primary_answer_coherence.py) -- nenhuma ampliação de
    superfície de confiança.

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


def render_natural_answer_text(primary: PrimaryAnswer) -> str:
    """Ponto de entrada PÚBLICO da versão `natural_answer_v1`: aplica a
    fronteira de segurança de apresentação (Blocker 4 --
    `_reject_if_unsafe_structural_presentation`, levanta
    `NaturalAnswerUnsafePresentationError` pra claim/limitação com
    controle estrutural não seguro) e despacha pro renderizador puro
    CONGELADO (`_render_natural_answer_text_v1`, Blocker 5). A fronteira
    de segurança é uma política de apresentação INDEPENDENTE de versão
    (nunca reescreve texto, só decide se renderiza) -- fica aqui, fora do
    corpo congelado, pra que o corpo em si permaneça puramente a
    formatação, byte-exata, sem nenhuma lógica de decisão a preservar
    junto com ela."""
    _reject_if_unsafe_structural_presentation(primary)
    return _render_natural_answer_text_v1(primary)


# Registro FECHADO {versão do contrato -> renderizador}. Cresce só quando
# uma nova versão de renderizador for adicionada explicitamente aqui --
# nunca implicitamente aceito por um "else" permissivo. `natural_answer_v1`
# despacha especificamente pra `render_natural_answer_text` (que por sua
# vez despacha pro corpo CONGELADO `_render_natural_answer_text_v1`, ver
# Blocker 5) -- uma v2 futura precisaria de sua PRÓPRIA entrada aqui,
# nunca reusar/editar esta.
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
    propaga como `ValueError`/`pydantic.ValidationError` -- o CHAMADOR
    (nunca este módulo) decide tratar isso como fallback opcional.
    Inclui `NaturalAnswerUnsafePresentationError` (Blocker 4, closure
    repair) -- uma RECUSA intencional/verdadeira, distinta de uma falha
    defensiva genérica (o `primary` em si continua perfeitamente válido;
    só este renderizador declina apresentá-lo como texto plano)."""
    text = render_natural_answer_text(primary)
    return NaturalAnswer(
        renderer_contract_version=NATURAL_ANSWER_CONTRACT_VERSION,
        based_on_verdict_id=primary.based_on_verdict_id,
        rendered_text=text,
    )
