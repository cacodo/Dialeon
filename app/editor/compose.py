"""
`Editor` — compõe a `FinalAnswer` a partir do `DebateResult`+`JudgeResult`
(Etapa 7; arquitetura de apresentação reescrita na Etapa 17B).

Contrato: `compose(debate_result, judge_result, run_config) -> EditorResult`
— nunca levanta exceção por falha de composição (transporte/output
inválido/budget viram `fallback_reason` explícito em `EditorResult`); só
levanta `ValueError` se `run_config.editor_provider` não existir entre os
providers injetados, e SÓ quando uma chamada LLM realmente vai ser
iniciada (ver ordem de validação abaixo — um `editor_provider` mal
configurado nunca impede uma resposta que não precisava dele).

Ordem de execução (obrigatória, arquitetura fechada):
    1. judge_result.verdict is None? -> fallback sem veredito, editor_provider
       NUNCA checado.
    2. current_claims = get_current_claims(...)
    3. budget (debate + judge) já esgotado? -> fallback a partir do veredito,
       editor_provider NUNCA checado.
    4. SÓ AGORA valida editor_provider.
    5. tenta LLM (retry local, máx. 2).

Retry: `_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2` LOCAL a este módulo — não
importado do Judge nem do claim processor (camadas independentes). Erro
de transporte não é retentado nesta camada (o `LLMProvider` já esgotou o
retry dele).

Etapa 17B — vulnerabilidade B4 e sua correção: o `Editor` costumava pedir
à LLM que escrevesse prosa livre por claim (`narrative`) e de síntese
(`synthesis_intro`/`synthesis_conclusion`), validando mecanicamente só o
RÓTULO declarado (`verdict_reflected`) contra o veredito real — nunca o
conteúdo em linguagem natural, que podia contradizer esse mesmo rótulo
sem ser pego por validação nenhuma (ver histórico deste módulo). A
correção NÃO foi adicionar mais validação em cima de texto livre — foi
remover o canal de texto livre inteiramente: `EditorPlan` (ver
app/editor/schemas.py) só tem dois enums finitos
(`opening_style`/`closing_style`); TODA palavra que o usuário lê em
`FinalAnswer.answer_text` é escrita por `_compose_answer` abaixo
(`_render_final_answer_text`, mantida por compatibilidade de assinatura
com chamadores/testes existentes, é hoje uma projeção fina dela -- ver
UI Slice 3 abaixo), o ÚNICO compositor epistêmico deste módulo, usado
tanto no sucesso do plano da LLM quanto em qualquer fallback (mesma
função, nunca dois textos possíveis pro mesmo estado epistêmico
dependendo de qual caminho de execução foi seguido). A LLM Editor nunca
mais vê nem escreve claim_text/explanation/debate_limitations -- só os
usa a aplicação, que os inclui verbatim.

UI Slice 3 (Structured Final Answer) -- `_compose_answer` agora também
devolve `answer_blocks` (ver app/editor/answer_blocks.py), uma
representação ADITIVA e tipada dos mesmos dados, produzida NO MESMO
loop que produz `answer_text` -- nunca por parsing de `answer_text`
(que mistura texto app-autorado com texto NÃO CONFIÁVEL sem delimitador
reversível, ver app/text_safety.py), nunca uma segunda derivação
independente. `answer_blocks` é `None` pra runs históricos, pro caminho
sem veredito (`_no_verdict_result`, escopo desta slice) e pro status
histórico `llm_composed` -- o frontend trata todos esses casos da MESMA
forma (fallback pra divisão de parágrafos sobre `answer_text`).

Isso NÃO resolve (fora de escopo, ver docstring de app/editor/schemas.py):
se `ClaimAssessment.explanation` (texto livre do PRÓPRIO Judge) é
internamente coerente com `ClaimAssessment.verdict` -- essa é uma
garantia sobre o contrato do Judge (app/judge/schemas.py,
app/judge/single_judge.py), uma camada acima desta. A Etapa 17B garante
que a camada de apresentação não ESCALA/reinterpreta o Judge; não garante
que o próprio Judge é internamente consistente.

Cross-Channel Reconciliation V1 (substitui o patch de apresentação com
fonte anterior) -- CHANNEL RELATIONSHIP != JUDGE VERDICT != TRUTH:
`_render_final_answer_text` agora recebe (opcionalmente) um
`SourceJudgeReconciliationResult` JÁ RESOLVIDO por
`app/reconciliation/reconcile.py` (chamado uma única vez em
`app/council/runner.py`, depois do Judge, antes do Editor) -- este
módulo NUNCA re-deriva a classificação de relacionamento por conta
própria, só lê `outcome.channel_relationship`/`outcome.source_state` já
prontos e escolhe o TEMPLATE fixo correspondente (ver
`_render_reconciliation_suffix` abaixo). Quando o relacionamento é
"materialmente útil" (ver docstring daquela função pra quais estados são
intencionalmente audit-only), uma linha ADICIONAL é anexada logo abaixo
da avaliação do Judge -- nunca substituindo, nunca alterando o
rótulo/explicação do Judge. `Judge` continua inteiramente cego a Source
Analysis (nenhuma mudança em `app/judge/`); `EditorPlan` continua sem
nenhum campo relacionado a fonte/reconciliação (a LLM Editor nunca vê
nem escolhe nada sobre isso); a linha de relacionamento é template FIXO
desta função, igual a todo o resto do renderizador -- não introduz
nenhuma autoridade epistêmica nova, só comunica um segundo fato JÁ
CLASSIFICADO (como os dois canais se relacionam) ao lado do veredito do
Judge (ver caso real: claim sobre ano de fundação, Judge diz
"indeterminável pelo debate", Source Analysis diz "a fonte contradiz" --
`SOURCE_ADDS_DIRECTION` -- as duas coexistem no texto final, nenhuma
reescreve a outra). `source_analysis_result` continua recebido só pra
resolver o EXCERTO exato de um `source_claim_result_id` já referenciado
pelo outcome -- nunca pra reclassificar nada.

Bounded contextual opening (patch de legibilidade pós-diagnóstico de run
real) -- `EditorPlan.opening_style="contextual"` antepunha a `question`
ORIGINAL inteira, verbatim, sem nenhum limite de apresentação (Accepted
Question Size Boundary V1, app/orchestrator/config.py: `validate_question`
já rejeita, na fronteira de aceite -- `CreateRunRequest`/
`CouncilExecutionService.run()` --, qualquer `question` acima de
`MAX_QUESTION_CHARACTERS` [20_000 caracteres]; mas `RunConfig.question`
em si continua só `min_length=1`, `RunConfig` NUNCA reaplica essa regra
como field_validator próprio -- ver docstring de `CreateRunRequest.question`,
app/presentation/schemas.py -- então `RunConfig` é diretamente
construível, fora da fronteira de aceite, com uma `question` de qualquer
tamanho). Pra uma pergunta curta de uma linha isso é perfeitamente
legível; pra uma pergunta longa (mesmo dentro do teto de 20_000
caracteres da fronteira de aceite), um documento de requisitos colado,
ou um prompt multi-linha/com bullets, a abertura passava a dominar
`answer_text` inteiro -- um problema de LEGIBILIDADE de apresentação,
nunca epistêmico: o excerto limitado usado nesta abertura contextual
(via `_bounded_question_excerpt`) NUNCA altera claims/veredito -- ambos
já foram computados por etapas anteriores do pipeline (debate/extração/
Judge) antes do Editor sequer rodar; aqui `question` só é ecoada de
volta pro usuário como enquadramento de apresentação -- e este patch
permanece necessário mesmo com o teto de aceite existente, tanto pra
questions dentro desse teto quanto pra chamadores diretos de
`RunConfig` que o contornam.

A correção fica inteiramente em `_bounded_question_excerpt` abaixo,
puramente determinística, sem nenhuma autoridade nova pra LLM nenhuma
(`EditorPlan` continua com exatamente os mesmos dois enums finitos;
`build_editor_request`/o prompt do Editor não mudam, ver
app/editor/context.py) -- a LLM Editor escolhe `opening_style`, nunca o
CONTEÚDO do enquadramento, exatamente como antes. Deliberadamente NÃO é
um resumo semântico (nenhum heurístico "entende" a pergunta pra
sintetizar do que ela trata -- isso reintroduziria exatamente o tipo de
autoridade interpretativa que a Etapa 17B removeu da LLM, só que agora
via heurística de aplicação em vez de LLM): é achatamento de whitespace
(inclusive newlines -- resolve o caso multi-linha/bullets) seguido de
corte por tamanho de caractere com reticência visível quando algo é
omitido -- nunca um corte silencioso que pareça a pergunta inteira.

Isso NÃO toca em segurança de terminal: nenhum escaping de controle é
introduzido aqui (isso seguiria pertencendo só a
app/cli/output.py::human_run_result, ver app/text_safety.py) -- este
patch é sobre TAMANHO/achatamento de whitespace do excerto, uma
preocupação de legibilidade de apresentação, ortogonal a caracteres de
controle/terminal.

Deterministic verdict-bucket final answer (patch de legibilidade
pós-investigação de "claim amplification") -- antes deste patch, TODA
`ClaimAssessment` virava uma linha solta, na ordem bruta em que o Judge
LLM listou `claim_assessments` em seu JSON, sem nenhuma organização
visual -- um debate com 17 claims avaliadas virava 17 blocos de texto
indistinguíveis entre si, misturando conclusões sustentadas com
ressalvas/conflitos/rejeições sem nenhuma separação.

Escopo estrito do que este patch resolve -- e do que NÃO resolve:
resolve SOMENTE a apresentação plana e indiferenciada de um conjunto de
avaliações que o Judge já produziu. NÃO resolve, e não tenta resolver,
NENHUM dos seguintes (investigação de "claim amplification", fora de
escopo aqui por decisão explícita): quantidade excessiva de claims
extraídas (`app/debate/claim_extraction.py::extract_claims`),
agrupamento insuficiente de claims semanticamente próximas
(o agrupamento foi removido da execução corrente), ou relevância de uma claim específica
para a pergunta do usuário -- não existe, e este patch não introduz,
NENHUM sinal de saliência/relevância/importância no domínio (ver
app/models/domain.py: `Claim`/`ClaimAssessment` não têm nenhum campo
desse tipo). Uma claim narrow-mas-correta continua tão presente e tão
"correta" quanto antes -- só passa a aparecer ao lado de outras do MESMO
tipo de veredito, nunca reordenada/filtrada por importância percebida.

Mecanismo -- `_bucket_for_verdict` particiona `ClaimAssessment.verdict`
(um Literal de 5 valores fechado, já validado pelo Judge, ver
app/judge/schemas.py) em exatamente 2 grupos FIXOS, nunca decididos por
conteúdo/similaridade/texto: BUCKET A (`supported`,
`partially_supported`) e BUCKET B (`conflicting`, `unresolved`,
`rejected`). Isso é uma projeção determinística e total de um campo que
já existe -- não deriva, infere, nem reinterpreta veredito nenhum
(`EDITOR != SECOND JUDGE` continua valendo: nenhuma decisão nova é
tomada sobre o CONTEÚDO da claim, só sobre em qual das 2 seções fixas
ela é impressa, a partir do rótulo que o Judge já atribuiu). A ORDEM
dentro de cada bucket continua sendo exatamente a ordem em que o Judge
listou `claim_assessments` -- este patch nunca ordena por claim_id,
texto, supporting_model_ratio, nem qualquer outra chave; só particiona,
preservando ordem relativa. `supporting_model_ratio`/`Claim.status`
(consenso entre modelos) NUNCA são consultados aqui -- consenso não é
usado como proxy de bucket, exatamente porque CONSENSUS != TRUTH (e, por
extensão, CONSENSUS != RELEVANCE) -- só `ClaimAssessment.verdict`, o
único campo que este patch olha. O mapeamento (`_VERDICT_TO_BUCKET`) é
EXAUSTIVO, não um `if/else` com fallback -- um veredito fora dos 5
valores mapeados faz `_bucket_for_verdict` levantar `ValueError`, nunca
cair silenciosamente em BUCKET B (hardening pós-revisão independente:
schema drift futuro no Judge precisa ser uma decisão explícita aqui,
nunca um acidente de apresentação).

Os cabeçalhos de seção (`_BUCKET_A_HEADING`/`_BUCKET_B_HEADING`) são
templates FIXOS desta função, no mesmo espírito de
`_OPENING_DIRECT`/`_CLOSING_LIMITATIONS_LEAD_IN` -- nunca escritos ou
escolhidos pela LLM Editor (`EditorPlan` continua com exatamente os
mesmos dois enums finitos, sem nenhum campo novo). Redação deliberadamente
neutra quanto a categoria epistêmica, nunca quanto a importância: descrevem
"o Judge considerou isto sustentado" / "o Judge não deu isto por
estabelecido" -- nunca "principal"/"secundário", "confirmado"/"correto",
"mais relevante". Um cabeçalho de bucket VAZIO nunca é impresso (ver
`_render_final_answer_text`) -- não existe frase fixa alternativa tipo
"nenhuma conclusão sustentada" (isso sugeriria uma leitura sobre o
CONTEÚDO do debate que este patch não tem autoridade pra fazer; contraste
com `_CLOSING_LIMITATIONS_NONE_REGISTERED`, que é sobre limitações, um
campo à parte, com sua própria semântica de fallback pré-existente e
inalterada).

O fallback sem veredito (`Editor._no_verdict_result`) continua
INALTERADO por este patch -- ele nunca teve `ClaimAssessment.verdict`
nenhum pra particionar (o Judge nunca chegou a rodar), então continua
listando as claims brutas levantadas como uma lista simples, sem buckets
inventados sobre dado que não existe."""

from __future__ import annotations

import json
import unicodedata
from typing import Literal, NamedTuple

from pydantic import ValidationError

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.editor.answer_blocks import AnswerBlock, AnswerClaimItem, AnswerClaimSectionBlock, AnswerParagraphBlock
from app.editor.attempt import EditorAttempt
from app.editor.context import EDITOR_CONTRACT_VERSION, build_editor_request
from app.editor.errors import MalformedEditorOutputError
from app.editor.result import EditorResult, FinalAnswer
from app.editor.schemas import EditorPlan
from app.judge.result import JudgeResult
from app.models.domain import Claim, JudgeVerdict
from app.models.provider_models import ProviderResponse
from app.models.request_provenance import RequestProvenance, build_request_provenance
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.providers.base import LLMProvider, transport_error_common_fields
from app.reconciliation.errors import ReconciliationError
from app.reconciliation.models import (
    ChannelRelationship,
    ClaimReconciliationOutcome,
    SourceChannelState,
    SourceJudgeReconciliationResult,
)
from app.source_analysis.models import SourceClaimAnalysisResult, ValidSourceRelation
from app.source_analysis.result import SourceAnalysisResult
from app.structured_output import strip_single_json_code_fence

_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2

_VERDICT_LABELS: dict[str, str] = {
    "supported": "sustentada pelo debate",
    "partially_supported": "parcialmente sustentada, com ressalvas",
    "rejected": "rejeitada pelo juiz com base no debate disponível",
    "conflicting": "com posições conflitantes, não resolvida",
    "unresolved": "sem informação suficiente para decidir",
}

# Deterministic verdict-bucket final answer -- ver docstring do módulo.
# Partição FIXA e TOTAL de `ClaimAssessment.verdict` (Literal fechado de 5
# valores, app/judge/schemas.py) em exatamente 2 grupos -- nunca 3+, nunca
# decidido por conteúdo. BUCKET A = veredito que o Judge considerou
# sustentado (mesmo que parcialmente); BUCKET B = o Judge não deu por
# estabelecido (rejeitado, conflitante, ou insuficiente pra decidir --
# tratados como uma ÚNICA categoria de apresentação aqui, sem hierarquia
# entre eles; a distinção ENTRE os três continua inteiramente visível no
# rótulo por claim, via `_VERDICT_LABELS`, nunca perdida). Isso NÃO é uma
# escala de confiança/importância.
#
# EXAUSTIVO de propósito -- mapeia os 5 valores um a um, nunca um
# `if/else` com fallback implícito: um SEXTO valor de `verdict` (schema
# do Judge estendido no futuro sem que este renderizador seja
# atualizado) precisa ser uma decisão explícita de bucket ANTES de
# qualquer texto ser gerado, nunca um acidente de "cai em B por não bater
# com A" -- ver `_bucket_for_verdict` abaixo, que falha ao invés de
# degradar quando a chave não existe aqui. Deliberadamente MAIS estrito
# que `_VERDICT_LABELS.get(..., assessment.verdict)` acima (que é só um
# RÓTULO de exibição, nunca decide entre 2 seções fixas -- um rótulo
# desconhecido exibido verbatim não tem o mesmo risco de "silenciosamente
# incorreto" que um veredito novo caindo na seção errada teria).
_VERDICT_TO_BUCKET: dict[str, Literal["a", "b"]] = {
    "supported": "a",
    "partially_supported": "a",
    "conflicting": "b",
    "unresolved": "b",
    "rejected": "b",
}

# Cabeçalhos FIXOS desta função (nunca escritos/escolhidos pela LLM
# Editor) -- redigidos pra descrever a CATEGORIA epistêmica do Judge,
# nunca importância/prioridade/verdade/certeza além do próprio veredito.
# "sustentadas pelo debate" ecoa deliberadamente o vocabulário já usado
# em `_VERDICT_LABELS["supported"]` ("sustentada pelo debate") -- mesma
# terminologia, nunca um sinônimo novo que pudesse ler como uma
# afirmação mais forte. "não estabelecidos" descreve com precisão as 3
# categorias de BUCKET B sem promover nenhuma leitura de falsidade: uma
# claim rejeitada não foi "provada falsa" (ver `_VERDICT_LABELS`), uma
# conflitante não foi resolvida, uma insuficiente não foi decidida --
# "não estabelecido" é o denominador comum verdadeiro das três, sem
# inventar uma palavra mais forte que nenhuma delas sozinha sustentaria.
_BUCKET_A_HEADING = "Conclusões sustentadas pelo debate:"
_BUCKET_B_HEADING = "Pontos não estabelecidos pelo debate:"


def _bucket_for_verdict(verdict_value: str) -> Literal["a", "b"]:
    """Lookup EXAUSTIVO em `_VERDICT_TO_BUCKET` -- nunca um fallback
    implícito. Um `verdict_value` fora dos 5 mapeados (estruturalmente
    impossível hoje: `ClaimAssessment.verdict` é um `Literal` fechado
    validado pelo Judge antes de chegar aqui, ver app/judge/schemas.py/
    app/models/domain.py) levanta `ValueError` ao invés de cair
    silenciosamente em BUCKET B -- fail-closed: nenhum `FinalAnswer` é
    produzido a partir de um veredito que este renderizador não sabe
    onde colocar."""
    try:
        return _VERDICT_TO_BUCKET[verdict_value]
    except KeyError:
        raise ValueError(
            "veredito sem bucket de apresentação definido: "
            f"{verdict_value!r} -- _VERDICT_TO_BUCKET precisa ser "
            "atualizado explicitamente antes que este veredito possa "
            "ser renderizado"
        ) from None

_JUDGE_REASON_LABELS: dict[str, str] = {
    "budget_exhausted_before_judge": "o orçamento se esgotou antes da avaliação final",
    "no_claims_to_judge": "nenhuma informação avaliável foi produzida no debate",
    "judge_transport_failed": "houve uma falha de comunicação durante a avaliação final",
    "judge_output_invalid": "a avaliação final não pôde ser interpretada corretamente",
    # Repair (Run02 claim-extraction exhaustion) -- deliberadamente
    # DIFERENTE de "no_claims_to_judge": afirma que os participantes
    # responderam (nunca que nenhuma informação avaliável existia), e
    # atribui a ausência de claims à extração ESTRUTURADA ter falhado,
    # nunca ao conteúdo do debate em si.
    "claim_extraction_failed": (
        "os participantes responderam, mas a extração estruturada das "
        "afirmações feitas por eles falhou"
    ),
    # Repair (adversarial review, Finding A) -- distinto de
    # "claim_extraction_failed" (falha TOTAL da rodada 1): aqui a
    # extração ocorreu parcialmente (alguma resposta teve extração
    # aceita em algum ponto do debate), mas cobertura ficou incompleta e
    # nenhuma claim sobreviveu pra avaliação -- nunca afirma que nenhuma
    # informação avaliável existia, nunca afirma falha total.
    "claim_extraction_incomplete": (
        "algumas respostas dos participantes não puderam ter suas "
        "afirmações extraídas, e nenhuma afirmação sobrevivente ficou "
        "disponível para avaliação"
    ),
}

# Cross-Channel Reconciliation V1 -- rótulos de RELACIONAMENTO ENTRE
# CANAIS, nunca de verdade externa (mesma disciplina de app/cli/output.py
# e do frontend, ver app/reconciliation/models.py): descrevem COMO o
# Judge e a fonte se relacionam -- nunca se a claim é
# verdadeira/falsa/provada, nunca qual dos dois canais está "certo".
#
# Estados intencionalmente AUDIT-ONLY nesta função (nenhuma linha em
# `answer_text`, mas continuam expostos no resultado estruturado/audit,
# ver app/presentation/schemas.py::SourceJudgeReconciliationResultPublic):
# - SOURCE_UNRESOLVED: sinal nulo -- a fonte também não decidiu nada,
#   renderizar uma linha extra pra "a fonte não decide" deixaria toda
#   claim sem relação clara ainda mais verbosa sem acrescentar
#   informação real (mesma disciplina do antigo "unresolved omitido").
# - NOT_COMPARABLE: sem fonte utilizável -- o caso mais comum (nenhuma
#   fonte fornecida); imprimir uma linha aqui pra TODA claim seria ruído
#   puro pro caso mais frequente do produto.
#
# Repair (revisão adversarial, achado 1) -- estes templates NUNCA levam o
# prefixo de concatenação `"\n  "` embutido (diferente de antes desta
# correção): o prefixo é só uma decisão de FORMATAÇÃO do renderizador de
# `answer_text` (ver `_render_reconciliation_suffix`), nunca parte do
# CONTEÚDO app-autorado. Manter os dois separados aqui é o que permite ao
# bloco tipado (`AnswerClaimItem.source_relationship_note`) nunca precisar
# desfazer uma concatenação por substring depois -- ele usa o MESMO texto
# app-autorado, só que formatado à parte, sem jamais tocar no excerto
# NÃO CONFIÁVEL que pode acompanhá-lo (ver `_reconciliation_note_parts`).
_RELATIONSHIP_RENDER_TEMPLATES: dict[ChannelRelationship, str] = {
    ChannelRelationship.DIRECTIONALLY_ALIGNED: (
        "Relação com a fonte fornecida: a fonte aponta na MESMA direção "
        "da avaliação do debate."
    ),
    ChannelRelationship.IN_TENSION: (
        "Relação com a fonte fornecida: a fonte aponta na direção OPOSTA "
        "à avaliação do debate."
    ),
    # Repair #5 (revisão adversarial) -- "mixed"/SOURCE_CHANNEL_CONFLICT
    # cobre QUALQUER combinação de entradas canônicas do lado da fonte
    # que não seja redutível a um único estado coerente -- não SÓ
    # supports+contradicts (inclui supports+unresolved,
    # direcional+rejected, unresolved+rejected, etc., ver
    # app/reconciliation/reconcile.py::_reduce_source_group). A redação
    # anterior ("a fonte contém resultados conflitantes") superespecifica
    # pra um conflito direcional que pode nem existir, e atribui a
    # anomalia à FONTE em si -- a atribuição correta é à ANÁLISE DE
    # FONTE (o processo que produziu entradas incoerentes), nunca ao
    # texto da fonte, que pode ser perfeitamente coerente. Wording
    # NEUTRO exigido: nunca "conflito interno na fonte", nunca "resultados
    # conflitantes para esta afirmação" atribuído à fonte.
    ChannelRelationship.SOURCE_CHANNEL_CONFLICT: (
        "Relação com a fonte fornecida: a análise da fonte produziu "
        "entradas que não puderam ser reduzidas a um único estado coerente "
        "para esta afirmação, e não pôde ser usada para comparação aqui."
    ),
}

# SOURCE_ADDS_DIRECTION só ocorre quando source_state é SUPPORTS/CONTRADICTS
# (ver ChannelRelationship/VALID_RELATIONSHIPS_WHEN_COMPLETE) -- o Judge não
# deu direção nenhuma (conflicting/unresolved), então a frase PRECISA dizer
# qual direção a fonte acrescenta (nunca "mesma"/"oposta", que pressupõem
# uma direção do Judge que não existe aqui).
_SOURCE_ADDS_DIRECTION_LABELS: dict[SourceChannelState, str] = {
    SourceChannelState.SUPPORTS: "apoiada pela fonte fornecida",
    SourceChannelState.CONTRADICTS: "contradita pela fonte fornecida",
}

_OPENING_DIRECT = "Resultado da avaliação do debate:"
_OPENING_CONTEXTUAL_TEMPLATE = (
    'Em resposta à pergunta "{question}", segue o resultado da avaliação do debate:'
)

# Bounded contextual opening -- ver docstring do módulo. 100 caracteres
# mantém a abertura como uma frase curta/legível (ordem de grandeza de um
# título/assunto, não de um parágrafo colado inteiro) -- é um teto de
# APRESENTAÇÃO desta única frase, deliberadamente independente de
# `MAX_SOURCE_TEXT_CHARACTERS` (app/orchestrator/config.py, 20_000), que
# bounda armazenamento/prompt de `source_text`, um problema de tamanho
# completamente diferente (documento inteiro vs. uma cláusula de abertura).
_CONTEXTUAL_OPENING_QUESTION_MAX_CHARS = 100
# Reticência única (U+2026), não "...": um caractere textual normal (não
# controle/formatação), sinaliza omissão sem exigir nenhum escaping
# terminal-específico (ortogonal a `terminal_safe_text`, ver
# app/text_safety.py).
_CONTEXTUAL_OPENING_TRUNCATION_MARKER = "…"
_CONTEXTUAL_OPENING_EMPTY_QUESTION_PLACEHOLDER = "(pergunta vazia)"

_CLOSING_LIMITATIONS_LEAD_IN = (
    "Antes de considerar esta resposta, observe especificamente as seguintes "
    "limitações do debate:"
)
_CLOSING_LIMITATIONS_NONE_REGISTERED = (
    "Nenhuma limitação foi registrada pelo juiz para este debate."
)
_CLOSING_LIMITATIONS_CONCISE_HEADER = "Limitações do debate:"

# Etapa 17B -- plano usado em QUALQUER caminho de fallback (transporte/
# parse/budget), sempre através do MESMO `_render_final_answer_text` do
# caminho de sucesso -- nunca um renderizador de fallback separado (ver
# docstring do módulo). As escolhas mais neutras/simples do vocabulário
# finito: "direct" (sem enquadramento adicional) + "concise" (limitações
# listadas sem destaque extra) -- reproduz o texto que
# `_deterministic_from_verdict_answer` já produzia antes da Etapa 17B.
_DEFAULT_PLAN = EditorPlan(opening_style="direct", closing_style="concise")


def _extraction_coverage_note(debate_result: DebateResult) -> str | None:
    """Repair (Run02 claim-extraction exhaustion) -- disclosure
    DETERMINÍSTICA de cobertura de extração incompleta: quando ao menos
    uma resposta bem-sucedida de participante não teve suas claims
    extraídas (ver `DebateResult.claim_extraction_missing_response_count`),
    a resposta final NUNCA deve dar a entender cobertura completa. `None`
    quando a cobertura é completa (`missing_response_count == 0`) -- nenhuma
    nota é anexada nesse caso, byte-idêntico ao comportamento de antes
    desta repair.

    Usada pelos 3 caminhos que produzem uma `FinalAnswer` com veredito
    presente (sucesso da LLM Editor, fallback por budget, fallback por
    transporte/parse do Editor) E, desde o repair da revisão adversarial
    (recheck Finding A), TAMBÉM por `Editor._no_verdict_result` -- os
    dois fatos (Judge sem veredito / cobertura de extração incompleta)
    são INDEPENDENTES e podem coexistir (ex.: algumas respostas
    elegíveis falharam extração, outras produziram claims sobreviventes,
    e o Judge não chegou a avaliá-las por budget/transporte -- motivo
    TOTALMENTE alheio à extração). A ÚNICA exceção continua sendo o caso
    de falha TOTAL de extração da rodada 1
    (`verdict_unavailable_reason=="claim_extraction_failed"`), onde
    `missing_response_count == eligible_response_count` e
    `current_claims` é sempre vazio -- `_no_verdict_result` chama esta
    função ali também, mas ela naturalmente devolve a MESMA disclosure
    (nunca `None`), consistente com o resto do texto daquele branch."""
    missing = debate_result.claim_extraction_missing_response_count
    if missing <= 0:
        return None
    eligible = debate_result.claim_extraction_eligible_response_count
    response_phrase = "resposta bem-sucedida" if eligible == 1 else "respostas bem-sucedidas"
    verb = "não pôde" if missing == 1 else "não puderam"
    return (
        f"Cobertura de extração de afirmações incompleta: {missing} de {eligible} "
        f"{response_phrase} dos participantes {verb} ter suas afirmações extraídas "
        "para avaliação -- o resultado acima considera só as afirmações que puderam "
        "ser extraídas."
    )


def _limitations_with_coverage_note(
    base_limitations: list[str], coverage_note: str | None
) -> list[str]:
    """Repair (adversarial review, Finding B) -- ÚNICO ponto que combina
    as limitações VERBATIM do Judge (`base_limitations`) com a
    disclosure DETERMINÍSTICA e APP-AUTORADA de cobertura de extração
    (`coverage_note`, ver `_extraction_coverage_note`) -- usado pelos 2
    caminhos que produzem `FinalAnswer` com veredito presente (sucesso
    da LLM Editor e fallback determinístico a partir do veredito).

    `coverage_note is None` (cobertura completa) devolve
    `base_limitations` inalterada -- NUNCA adiciona uma entrada vazia/
    duplicada quando não há nada a dizer sobre cobertura. Quando
    presente, é SEMPRE a ÚLTIMA entrada da lista -- as limitações do
    Judge (sobre o CONTEÚDO do debate) vêm primeiro, a limitação
    OPERACIONAL (sobre o PROCESSAMENTO de extração, nunca escrita/
    escolhida pela LLM) vem depois, nunca misturada/intercalada."""
    if coverage_note is None:
        return base_limitations
    return base_limitations + [coverage_note]


class Editor:
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    async def compose(
        self,
        debate_result: DebateResult,
        judge_result: JudgeResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int,
        prior_output_tokens: int,
        prior_cost_usd: float,
        source_analysis_result: SourceAnalysisResult | None = None,
        reconciliation: SourceJudgeReconciliationResult | None = None,
    ) -> EditorResult:
        # `prior_*` (patch de revisão do Stage 16): consumo REAL acumulado
        # de Debate + Source Analysis (se houve) + Judge -- já inclui
        # Judge aqui (diferente do que Judge recebe, que é só
        # Debate+Source Analysis).
        #
        # Cross-Channel Reconciliation V1 -- `source_analysis_result`/
        # `reconciliation` são NOVOS aqui (default None preserva toda
        # chamada existente sem fonte/reconciliação), mas o Editor LLM/
        # `EditorPlan` continuam TÃO cegos a Source Analysis/reconciliação
        # quanto antes: só o RENDERIZADOR determinístico
        # (`_render_final_answer_text`) os recebe -- nunca entra em
        # `build_editor_request`/no prompt da LLM.
        if judge_result.verdict is None:
            return self._no_verdict_result(
                debate_result,
                judge_result,
                run_config,
                prior_input_tokens=prior_input_tokens,
                prior_output_tokens=prior_output_tokens,
                prior_cost_usd=prior_cost_usd,
                reconciliation=reconciliation,
            )

        verdict = judge_result.verdict
        current_claims = get_current_claims(debate_result.claims)
        source_results_by_id = _source_results_by_id(source_analysis_result)

        input_before, output_before, cost_before = (
            prior_input_tokens,
            prior_output_tokens,
            prior_cost_usd,
        )

        if compute_budget_exceeded(input_before, output_before, cost_before, run_config):
            return EditorResult(
                final_answer=self._deterministic_from_verdict_answer(
                    run_config.question,
                    verdict,
                    current_claims,
                    reconciliation,
                    source_results_by_id,
                    debate_result,
                ),
                attempts=[],
                fallback_reason="budget_exhausted_before_editor",
                editor_provider=run_config.editor_provider,
                cumulative_budget_exceeded=True,
            )

        # SÓ AGORA validamos editor_provider — uma dependência que não vai
        # ser usada não pode bloquear uma resposta que já teria sido
        # determinística de qualquer forma.
        if run_config.editor_provider not in self._providers:
            raise ValueError(f"editor_provider desconhecido: {run_config.editor_provider!r}")
        editor_llm = self._providers[run_config.editor_provider]

        request = build_editor_request(
            run_config.question, verdict, run_config.max_output_tokens_per_call
        )
        request_provenance = build_request_provenance(EDITOR_CONTRACT_VERSION, request)

        attempts: list[EditorAttempt] = []
        parsed: EditorPlan | None = None
        accepted_response: ProviderResponse | None = None

        for attempt_number in range(1, _MAX_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            if attempt_number > 1:
                so_far_input, so_far_output, so_far_cost, _ = sum_usage_and_cost(attempts)
                if compute_budget_exceeded(
                    input_before + so_far_input,
                    output_before + so_far_output,
                    cost_before + so_far_cost,
                    run_config,
                ):
                    break  # budget já esgotado -- não inicia o retry
            provider_response = await editor_llm.complete(request)

            if provider_response.status == "error":
                attempts.append(
                    _transport_error_attempt(attempt_number, provider_response, request_provenance)
                )
                break  # sem retry desta camada pra erro de transporte

            try:
                parsed = _parse_and_validate(provider_response.text)
            except MalformedEditorOutputError as exc:
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number,
                        provider_response,
                        "malformed",
                        str(exc),
                        request_provenance,
                    )
                )
                continue

            attempts.append(
                _accepted_attempt(attempt_number, provider_response, request_provenance)
            )
            accepted_response = provider_response
            break

        editor_input, editor_output, editor_cost, _editor_has_unknown = sum_usage_and_cost(
            attempts
        )
        cumulative_budget_exceeded = compute_budget_exceeded(
            input_before + editor_input,
            output_before + editor_output,
            cost_before + editor_cost,
            run_config,
        )

        if parsed is None or accepted_response is None:
            reason: Literal["editor_transport_failed", "editor_output_invalid"] = (
                "editor_transport_failed"
                if attempts[-1].transport_status == "error"
                else "editor_output_invalid"
            )
            return EditorResult(
                final_answer=self._deterministic_from_verdict_answer(
                    run_config.question,
                    verdict,
                    current_claims,
                    reconciliation,
                    source_results_by_id,
                    debate_result,
                ),
                attempts=attempts,
                fallback_reason=reason,
                editor_provider=run_config.editor_provider,
                cumulative_budget_exceeded=cumulative_budget_exceeded,
            )

        answer_text, answer_blocks = _compose_answer(
            run_config.question,
            verdict,
            current_claims,
            parsed,
            reconciliation,
            source_results_by_id,
        )
        # Repair (Run02 claim-extraction exhaustion; Finding B da revisão
        # adversarial) -- disclosure de cobertura de extração incompleta,
        # anexada AQUI (fora de `_compose_answer`, que preserva sua
        # assinatura/comportamento existente byte-a-byte pros
        # chamadores/testes que já a invocam diretamente) -- ver
        # `_extraction_coverage_note`. Em DOIS lugares, nunca só um:
        # `answer_text` (compatibilidade texto-puro/histórico) E
        # `FinalAnswer.limitations` (canal ESTRUTURADO -- ver docstring
        # de `FinalAnswer.limitations`, app/editor/result.py -- é o que
        # `FinalAnswerView` no frontend realmente renderiza quando
        # `answer_blocks` está presente, já que `answer_blocks` em si
        # segue uma forma FECHADA que nunca aceita um parágrafo de
        # fechamento solto, ver
        # `FinalAnswer._answer_blocks_follow_the_fixed_document_shape`).
        coverage_note = _extraction_coverage_note(debate_result)
        if coverage_note is not None:
            answer_text += f"\n\n{coverage_note}"
        final_answer = FinalAnswer(
            answer_text=answer_text,
            answer_blocks=answer_blocks,
            limitations=_limitations_with_coverage_note(
                list(verdict.debate_limitations), coverage_note
            ),
            status="llm_planned",
            editor_model=accepted_response.model,
            editor_model_identity_source=accepted_response.model_identity_source,
            based_on_verdict_id=verdict.id,
            judge_confidence=verdict.confidence,
        )

        return EditorResult(
            final_answer=final_answer,
            attempts=attempts,
            fallback_reason=None,
            editor_provider=run_config.editor_provider,
            cumulative_budget_exceeded=cumulative_budget_exceeded,
        )

    def _no_verdict_result(
        self,
        debate_result: DebateResult,
        judge_result: JudgeResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int,
        prior_output_tokens: int,
        prior_cost_usd: float,
        reconciliation: SourceJudgeReconciliationResult | None = None,
    ) -> EditorResult:
        current_claims = get_current_claims(debate_result.claims)
        reason_text = _JUDGE_REASON_LABELS.get(
            judge_result.verdict_unavailable_reason or "", "a avaliação final não foi concluída"
        )
        # Repair pós-revisão independente (seção 15 do contrato desta
        # slice) -- este branch costumava SUPRIMIR qualquer relação de
        # fonte válida só porque o Judge falhou; `reconciliation` agora
        # torna isso explícito, e o comportamento muda: uma claim com
        # source_state direcional (supports/contradicts) ganha uma nota
        # ESCOPADA À ANÁLISE DE FONTE, nunca apresentada como substituto
        # de um veredito do Judge (ver `_render_source_only_note`).
        outcomes_by_claim_id = (
            {o.claim_id: o for o in reconciliation.claim_outcomes}
            if reconciliation is not None
            else {}
        )

        # Repair (adversarial review -- Structured Unevaluated Claims) --
        # DERIVAÇÃO ÚNICA: a sequência ordenada de strings de exibição
        # (uma por claim corrente, ver `get_current_claims` acima) é
        # computada UMA VEZ aqui, sem o prefixo sintético `"- "` -- nunca
        # duas derivações independentes pra `answer_text` (prosa legada)
        # e `unevaluated_claims` (campo estruturado novo). `answer_text`
        # acrescenta o prefixo `"- "` só na hora de montar a prosa,
        # preservando o texto legado byte-a-byte idêntico ao
        # comportamento anterior a este repair.
        claim_display_texts: list[str] = []
        for claim in current_claims:
            text = claim.text
            outcome = outcomes_by_claim_id.get(claim.id)
            if outcome is not None:
                note = _render_source_only_note(outcome.source_state)
                if note is not None:
                    text += f"\n  {note}"
            claim_display_texts.append(text)

        if claim_display_texts:
            answer_text = (
                f"A avaliação final não pôde ser concluída: {reason_text}. As seguintes "
                "afirmações foram levantadas pelos modelos participantes, mas não foram "
                "avaliadas:\n" + "\n".join(f"- {t}" for t in claim_display_texts)
            )
            unevaluated_claims: tuple[str, ...] | None = tuple(claim_display_texts)
        else:
            answer_text = (
                f"Não foi possível produzir uma resposta avaliável para esta pergunta: "
                f"{reason_text}."
            )
            unevaluated_claims = None

        # Repair (adversarial review, recheck Finding A) -- este caminho
        # (sem veredito -- budget/transporte/falha do Judge, NUNCA a
        # falha TOTAL de extração da rodada 1, que já tem sua própria
        # razão dedicada e nunca chega a ter claims sobreviventes) pode
        # genuinamente coexistir com cobertura de extração INCOMPLETA:
        # ex. algumas respostas elegíveis falharam/nunca foram tentadas
        # na extração, MAS outras produziram claims sobreviventes, e o
        # Judge não chegou a avaliá-las por um motivo TOTALMENTE
        # independente (budget esgotado antes do Judge, falha de
        # transporte do Judge, etc.). Reusa a MESMA derivação/helper
        # determinística já usada nos caminhos COM veredito
        # (`_extraction_coverage_note`) -- nunca uma segunda fonte de
        # verdade nem uma segunda redação. `None` (cobertura completa)
        # nunca adiciona nada, byte-idêntico ao comportamento anterior.
        coverage_note = _extraction_coverage_note(debate_result)
        if coverage_note is not None:
            answer_text += f"\n\n{coverage_note}"

        final_answer = FinalAnswer(
            answer_text=answer_text,
            # `answer_blocks` fica no default (`None`) de propósito -- UI
            # Slice 3 escopa deliberadamente este caminho (sem veredito)
            # fora da representação estruturada; ver docstring de
            # app/editor/answer_blocks.py. O frontend já trata `None`
            # graciosamente (fallback de divisão de parágrafos). A
            # disclosure de cobertura, mesmo aqui, vai SÓ pra
            # `answer_text`/`limitations`, nunca pra um bloco -- não há
            # `answer_blocks` nenhum pra ela entrar.
            limitations=_limitations_with_coverage_note(
                [f"Avaliação final não realizada: {reason_text}."], coverage_note
            ),
            # Mesma sequência computada acima, sem prefixo `"- "` -- ver
            # comentário na derivação única (`claim_display_texts`).
            unevaluated_claims=unevaluated_claims,
            status="deterministic_no_verdict",
        )

        cumulative_budget_exceeded = compute_budget_exceeded(
            prior_input_tokens,
            prior_output_tokens,
            prior_cost_usd,
            run_config,
        )

        return EditorResult(
            final_answer=final_answer,
            attempts=[],
            fallback_reason="judge_verdict_unavailable",
            editor_provider=run_config.editor_provider,
            cumulative_budget_exceeded=cumulative_budget_exceeded,
        )

    @staticmethod
    def _deterministic_from_verdict_answer(
        question: str,
        verdict: JudgeVerdict,
        current_claims: list[Claim],
        reconciliation: SourceJudgeReconciliationResult | None,
        source_results_by_id: dict[str, SourceClaimAnalysisResult],
        debate_result: DebateResult,
    ) -> FinalAnswer:
        """Fallback (transporte/parse/budget) -- usa o MESMO
        `_compose_answer` do caminho de sucesso, com `_DEFAULT_PLAN`,
        nunca um compositor próprio (Etapa 17B): não pode haver dois
        textos/conjuntos-de-blocos possíveis pro mesmo veredito
        dependendo só de qual caminho de execução foi seguido.
        `reconciliation` segue o mesmo princípio (Cross-Channel
        Reconciliation V1): o fallback mostra exatamente o mesmo
        relacionamento entre canais que o caminho de sucesso mostraria
        pro mesmo veredito. `debate_result` (Run02 claim-extraction
        exhaustion repair; Finding B): mesma disciplina -- a disclosure
        de cobertura de extração (`_extraction_coverage_note`) nunca
        pode depender de qual caminho de execução produziu o veredito,
        e vai tanto em `answer_text` quanto em `limitations` -- ver
        comentário equivalente no caminho de sucesso, dentro de
        `Editor.compose()`."""
        answer_text, answer_blocks = _compose_answer(
            question, verdict, current_claims, _DEFAULT_PLAN, reconciliation, source_results_by_id
        )
        coverage_note = _extraction_coverage_note(debate_result)
        if coverage_note is not None:
            answer_text += f"\n\n{coverage_note}"
        return FinalAnswer(
            answer_text=answer_text,
            answer_blocks=answer_blocks,
            limitations=_limitations_with_coverage_note(
                list(verdict.debate_limitations), coverage_note
            ),
            status="deterministic_from_verdict",
            based_on_verdict_id=verdict.id,
            judge_confidence=verdict.confidence,
        )


def _source_results_by_id(
    source_analysis_result: SourceAnalysisResult | None,
) -> dict[str, SourceClaimAnalysisResult]:
    """Lookup CRU por id -- usado só pra resolver o EXCERTO exato de um
    `source_claim_result_id` já referenciado por um
    `ClaimReconciliationOutcome` (ver `_first_excerpt` abaixo). NUNCA usado
    pra (re)classificar relacionamento -- isso é responsabilidade exclusiva
    de `app/reconciliation/reconcile.py`, já resolvida antes deste módulo
    ser chamado. `{}` quando não há análise de fonte nenhuma."""
    if source_analysis_result is None:
        return {}
    return {r.id: r for r in source_analysis_result.claim_results}


_EXPECTED_RELATION_FOR_SOURCE_STATE: dict[SourceChannelState, str] = {
    SourceChannelState.SUPPORTS: "supports",
    SourceChannelState.CONTRADICTS: "contradicts",
}


def _first_excerpt(
    outcome: ClaimReconciliationOutcome,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> str | None:
    """Primeiro excerto REAL entre os ids referenciados pelo outcome --
    suficiente pros estados coerentes (supports/contradicts unânimes,
    onde duplicatas retidas compartilham o mesmo tipo de relação); nunca
    chamado pra SOURCE_CHANNEL_CONFLICT/UNRESOLVED/NOT_COMPARABLE (ver
    `_render_reconciliation_suffix`) -- `expected_relation is None`
    cobre esses (e qualquer outro) estado não-direcional defensivamente.

    Repair #3 (revisão adversarial) -- FRONTEIRA DE PROVENANCE PRÓPRIA do
    Editor: mesmo que `validate_reconciliation_coherence`
    (app/reconciliation/reconcile.py) já devesse ter rejeitado, antes
    deste ponto, qualquer `SourceJudgeReconciliationResult` incoerente,
    este renderizador NUNCA confia cegamente no que recebe -- pra CADA
    id referenciado, exige que o resultado exista, seja uma
    `ValidSourceRelation`, pertença EXATAMENTE à mesma claim do outcome
    (`relation.claim_id == outcome.claim_id`) e que sua direção seja
    compatível com `outcome.source_state` (supports só aceita
    `relation="supports"`, contradicts só aceita `relation="contradicts"`).

    FALHA FECHADO (`ReconciliationError`) se QUALQUER id referenciado
    violar qualquer uma dessas condições -- NUNCA pula um id incoerente
    silenciosamente pra tentar um "substituto conveniente" mais adiante.

    Repair da revisão focada F3 -- a validação NUNCA retorna cedo: TODOS
    os ids referenciados são validados, na ordem, ATÉ O FIM, mesmo depois
    de um excerto usável já ter sido encontrado. Um excerto válido nos
    primeiros ids nunca pode "escapar" antes de uma referência malformada
    mais adiante ser examinada -- do contrário um outcome com
    (relação válida da claim A, referência malformada/cross-claim
    posterior) renderizaria o excerto válido e esconderia a corrupção de
    provenance no resto da lista. Só avança pro próximo id sem guardar
    excerto quando o atual É coerente mas simplesmente não tem excerto
    (`excerpt is None`) -- isso não é uma violação de provenance, é
    apenas ausência de trecho pra aquela entrada específica; o PRIMEIRO
    excerto coerente encontrado (na ordem de `source_claim_result_ids`)
    é o que acaba sendo devolvido, nunca um posterior."""
    expected_relation = _EXPECTED_RELATION_FOR_SOURCE_STATE.get(outcome.source_state)
    if expected_relation is None:
        return None
    first_excerpt: str | None = None
    for result_id in outcome.source_claim_result_ids:
        result = source_results_by_id.get(result_id)
        if result is None:
            raise ReconciliationError(
                f"outcome de claim_id={outcome.claim_id!r} referencia "
                f"source_claim_result_id={result_id!r} que não existe nos "
                "resultados de análise de fonte desta execução"
            )
        if not isinstance(result, ValidSourceRelation):
            raise ReconciliationError(
                f"outcome de claim_id={outcome.claim_id!r} (source_state="
                f"{outcome.source_state!r}) referencia source_claim_result_id="
                f"{result_id!r}, que não é uma ValidSourceRelation -- estado "
                "canônico incoerente"
            )
        if result.claim_id != outcome.claim_id:
            raise ReconciliationError(
                f"outcome de claim_id={outcome.claim_id!r} referencia "
                f"source_claim_result_id={result_id!r}, que pertence à claim "
                f"{result.claim_id!r} -- provenance cruzada entre claims nunca "
                "é aceita"
            )
        if result.relation != expected_relation:
            raise ReconciliationError(
                f"outcome de claim_id={outcome.claim_id!r} (source_state="
                f"{outcome.source_state!r}) referencia source_claim_result_id="
                f"{result_id!r} cuja relação real é {result.relation!r} -- "
                "direção incompatível"
            )
        if first_excerpt is None and result.excerpt is not None:
            first_excerpt = result.excerpt
    return first_excerpt
    return None


class _ReconciliationNoteParts(NamedTuple):
    """Texto app-autorado e excerto NÃO CONFIÁVEL mantidos SEPARADOS --
    nunca uma string já concatenada. Ver `_reconciliation_note_parts`."""

    relationship_text: str
    excerpt: str | None


def _reconciliation_note_parts(
    outcome: ClaimReconciliationOutcome,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> _ReconciliationNoteParts | None:
    """Repair (revisão adversarial, achado 1) -- ÚNICA fonte de verdade
    da nota de reconciliação, devolvendo `relationship_text` (template
    FIXO, sempre app-autorado, NUNCA contém o excerto embutido) e
    `excerpt` (BYTE-FIEL ao valor original de `ValidSourceRelation.excerpt`,
    ou `None`) como dois campos INDEPENDENTES -- nunca uma única string
    já formatada. `_render_reconciliation_suffix` (forma string, pra
    `answer_text`) e o bloco tipado
    (`AnswerClaimItem.source_relationship_note`, pra `answer_blocks`)
    formatam esses dois pedaços cada um à sua própria maneira, mas NENHUM
    dos dois volta a fazer parsing/substituição sobre uma string que já
    misturasse os dois -- eliminando por construção o risco (achado da
    revisão) de um `"\\n  "` dentro do excerto verbatim ser confundido
    com o prefixo de formatação e apagado por engano.

    A classificação vem inteiramente de `outcome.channel_relationship`
    (já resolvida pela ÚNICA implementação de reconciliação,
    `app/reconciliation/reconcile.py`) -- esta função NUNCA re-deriva o
    relacionamento a partir de judge_verdict/source_state por conta
    própria, só escolhe o TEMPLATE fixo correspondente. Ver comentário de
    `_RELATIONSHIP_RENDER_TEMPLATES` acima pra quais estados são
    intencionalmente audit-only (`None` aqui).

    Correção arquitetural (auditoria de terminal-safety pós-CLI): o
    excerto entra aqui BYTE-FIEL, sem nenhum escaping -- ambos os
    consumidores (string/bloco) são dados CANÔNICOS (persistidos/API/
    frontend), nunca artefatos de terminal, e escaping terminal-
    específico NUNCA pertence a esta camada de domínio (a mesma razão
    pela qual `Claim.text`/`ClaimAssessment.explanation` também nunca
    passam por `terminal_safe_text` aqui). A neutralização de controle de
    terminal acontece uma única vez, no limite de apresentação real --
    `app/cli/output.py::human_run_result` -- nunca dentro do compositor
    de domínio. Ver docstring de `app/text_safety.py`."""
    relationship = outcome.channel_relationship
    if relationship in (ChannelRelationship.SOURCE_UNRESOLVED, ChannelRelationship.NOT_COMPARABLE):
        return None

    if relationship == ChannelRelationship.SOURCE_ADDS_DIRECTION:
        label = _SOURCE_ADDS_DIRECTION_LABELS[outcome.source_state]
        relationship_text = (
            "Relação com a fonte fornecida: o debate não decidiu esta "
            f"afirmação, mas ela é {label}."
        )
    else:
        relationship_text = _RELATIONSHIP_RENDER_TEMPLATES[relationship]

    excerpt: str | None = None
    if relationship != ChannelRelationship.SOURCE_CHANNEL_CONFLICT:
        excerpt = _first_excerpt(outcome, source_results_by_id)

    return _ReconciliationNoteParts(relationship_text=relationship_text, excerpt=excerpt)


def _render_reconciliation_suffix(
    outcome: ClaimReconciliationOutcome,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> str | None:
    """Forma STRING, pra concatenar em `answer_text` -- linha ADICIONAL,
    nunca substituta, da avaliação do Judge (CHANNEL RELATIONSHIP !=
    JUDGE VERDICT != TRUTH). Byte-idêntica ao comportamento desta função
    antes do repair -- só a CONSTRUÇÃO mudou (via `_reconciliation_note_parts`
    compartilhada, nunca uma segunda derivação), nunca a saída pra
    `answer_text`."""
    parts = _reconciliation_note_parts(outcome, source_results_by_id)
    if parts is None:
        return None
    suffix = f"\n  {parts.relationship_text}"
    if parts.excerpt is not None:
        suffix += f'\n  Trecho da fonte: "{parts.excerpt}"'
    return suffix


def _reconciliation_note_for_block(
    outcome: ClaimReconciliationOutcome,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> str | None:
    """Forma BLOCO, pra `AnswerClaimItem.source_relationship_note` --
    MESMA fonte de verdade (`_reconciliation_note_parts`) que
    `_render_reconciliation_suffix`, nunca derivada a partir da string já
    concatenada por aquela função (repair da revisão adversarial, achado
    1: o código anterior fazia `suffix.replace("\\n  ", "\\n")` sobre a
    string JÁ CONCATENADA, o que podia corromper um `"\\n  "` genuíno
    dentro do excerto verbatim -- indentação/blank lines/qualquer
    ocorrência coincidente da sequência de formatação dentro do trecho
    da fonte real). Aqui o excerto nunca é tocado: ele é interpolado
    exatamente como veio de `ValidSourceRelation.excerpt`, sem passar por
    NENHUM parsing/substituição de string."""
    parts = _reconciliation_note_parts(outcome, source_results_by_id)
    if parts is None:
        return None
    note = parts.relationship_text
    if parts.excerpt is not None:
        note += f'\nTrecho da fonte: "{parts.excerpt}"'
    return note


# Repair pós-revisão independente (seção 15 do contrato) -- usado SÓ em
# `Editor._no_verdict_result` (Judge indisponível, `channel_relationship`
# é SEMPRE not_comparable ali -- ver ClaimReconciliationOutcome, então a
# nota aqui é escolhida por `source_state` diretamente, nunca por
# relationship). NUNCA finge ser um veredito substituto -- a redação
# deixa explícito que é a ANÁLISE DE FONTE, nunca "avaliação"/"veredito".
# Estados sem nota (omissão concisa aceitável, ver contrato seção 15):
# UNRESOLVED/ENTRY_REJECTED/ANALYSIS_UNAVAILABLE/NOT_SUPPLIED.
_NO_VERDICT_SOURCE_STATE_LABELS: dict[SourceChannelState, str] = {
    SourceChannelState.SUPPORTS: "apoiada pela fonte fornecida",
    SourceChannelState.CONTRADICTS: "contradita pela fonte fornecida",
}


def _render_source_only_note(source_state: SourceChannelState) -> str | None:
    # Repair #5 (revisão adversarial) -- wording neutro: "mixed" cobre
    # qualquer combinação de entradas canônicas incoerentes entre si
    # (não só supports+contradicts), e a anomalia é da ANÁLISE DE FONTE
    # (processo), nunca do texto da fonte em si (ver comentário de
    # `_RELATIONSHIP_RENDER_TEMPLATES` acima).
    if source_state == SourceChannelState.MIXED:
        return (
            "A análise da fonte fornecida produziu entradas que não puderam "
            "ser reduzidas a um único estado coerente para esta afirmação."
        )
    label = _NO_VERDICT_SOURCE_STATE_LABELS.get(source_state)
    if label is None:
        return None
    return f"A análise da fonte fornecida classificou esta afirmação como {label}."


def _bounded_question_excerpt(question: str) -> str:
    """Excerto determinístico de `question` pra abertura "contextual" --
    NUNCA a pergunta inteira quando ela é longa/multi-linha/estruturada
    (ver "Bounded contextual opening" na docstring do módulo). Duas
    operações puramente sintáticas, nenhuma delas tenta entender do que a
    pergunta trata:

    1. `question.split()` colapsa qualquer run de whitespace -- espaço,
       tab, e newline (Python trata `\\n`/`\\r` como whitespace pra
       `.split()`) -- num único espaço, descartando bordas. É assim que
       uma pergunta multi-linha ou uma lista de bullets vira uma única
       linha ANTES de qualquer corte por tamanho -- sem inspecionar
       estrutura (sem tratamento especial de bullets/parágrafos, sem
       decidir que uma linha é "mais importante" que outra).
    2. Se o resultado colapsado já cabe no teto
       (`_CONTEXTUAL_OPENING_QUESTION_MAX_CHARS`), é devolvido intacto --
       uma pergunta curta comum (o caso mais frequente) nunca é afetada
       por este patch, byte-a-byte igual ao comportamento anterior.
       Caso contrário, corta exatamente nesse tanto de caracteres
       (code points Unicode -- nunca bytes, nunca depende de encoding) e
       acrescenta `_CONTEXTUAL_OPENING_TRUNCATION_MARKER`, sinalizando
       explicitamente que algo foi omitido -- nunca um corte silencioso
       que possa ser lido como a pergunta inteira.

    Não corta em fronteira de palavra de propósito -- a regra mais simples
    permanece igualmente determinística, e uma palavra cortada ao meio
    antes da reticência é uma degradação cosmética aceitável (não
    ambígua sobre o fato de que houve corte); evita introduzir qualquer
    lógica adicional de "encontrar o espaço mais próximo" pra este patch
    de legibilidade.

    Um único cuidado de fronteira: se o corte caísse bem entre um
    caractere-base e uma marca combinante que o modifica (ex.: acento em
    forma NFD decomposta), o `while` abaixo recua o corte pra excluir
    também a base -- evita devolver um caractere base "nu" (sem seu
    acento) logo antes da reticência. Isso NÃO é um framework de
    normalização Unicode -- é uma checagem de um caractere por vez com
    `unicodedata.combining`, já suficiente porque só precisa evitar essa
    única fronteira, nunca reprocessar o texto inteiro.

    Pergunta vazia/só-espaço (não deveria alcançar aqui em uso normal --
    `CreateRunRequest` já rejeita isso na borda da API antes de
    `RunConfig` existir, ver app/presentation/schemas.py -- mas
    `RunConfig` é diretamente construível com `question=" "`, que passa
    em `min_length=1` sem passar por aquele validador, ver
    app/orchestrator/config.py): devolve um placeholder fixo neutro em
    vez de uma abertura com aspas vazias ('""') que pareceria um bug de
    apresentação."""
    collapsed = " ".join(question.split())
    if not collapsed:
        return _CONTEXTUAL_OPENING_EMPTY_QUESTION_PLACEHOLDER
    if len(collapsed) <= _CONTEXTUAL_OPENING_QUESTION_MAX_CHARS:
        return collapsed
    cut_len = _CONTEXTUAL_OPENING_QUESTION_MAX_CHARS
    while cut_len > 0 and unicodedata.combining(collapsed[cut_len]):
        cut_len -= 1
    return collapsed[:cut_len].rstrip() + _CONTEXTUAL_OPENING_TRUNCATION_MARKER


def _compose_answer(
    question: str,
    verdict: JudgeVerdict,
    current_claims: list[Claim],
    plan: EditorPlan,
    reconciliation: SourceJudgeReconciliationResult | None,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> tuple[str, list[AnswerBlock]]:
    """Único compositor epistêmico do módulo (Etapa 17B; ganhou a saída
    estruturada na UI Slice 3) -- usado tanto quando a LLM Editor produz
    um `EditorPlan` aceito quanto em qualquer fallback (com
    `_DEFAULT_PLAN`, ver `Editor._deterministic_from_verdict_answer`).
    Nenhuma palavra aqui se origina da LLM: `claim_text` (via
    `current_claims`) e `verdict.claim_assessments`
    (verdict/explanation)/`verdict.debate_limitations` são sempre dados
    do Judge/debate, incluídos verbatim; todo o resto — rótulo por
    veredito, frases de abertura/fechamento — é template FIXO desta
    função, só selecionado (nunca escrito) pelos dois enums finitos do
    plano.

    UI Slice 3 -- `answer_text` (string plana) e `answer_blocks`
    (sequência tipada, ver app/editor/answer_blocks.py) são produzidos
    NUM ÚNICO LOOP sobre `verdict.claim_assessments`, nunca por duas
    passagens/derivações independentes: cada avaliação vira, na MESMA
    iteração, tanto uma linha de `bucket_lines` (pro string) quanto um
    `AnswerClaimItem` (pro bloco) -- garantindo por construção que as
    duas formas nunca podem divergir sobre QUAIS claims aparecem, em que
    ORDEM, ou com que rótulo/explicação/nota de reconciliação.
    `answer_blocks` NUNCA inclui o fechamento de limitações (ver
    docstring de app/editor/answer_blocks.py, regra de autoridade única)
    -- só `answer_text` continua ecoando-as textualmente, byte-idêntico
    ao comportamento desta função antes desta slice (a Ordem/regras de
    bucket abaixo são inalteradas em relação à versão anterior desta
    função, que se chamava `_render_final_answer_text` e devolvia só a
    string; essa função agora é uma projeção fina desta).

    Ordem: `verdict.claim_assessments` é sempre percorrido uma única vez,
    na ordem do próprio Judge (nunca controlável pela LLM -- `EditorPlan`
    nem tem campo de referência a claim) -- cada avaliação é ANEXADA ao
    bucket A ou B (`_bucket_for_verdict`, projeção FIXA e determinística
    de `assessment.verdict`, ver docstring do módulo, "Deterministic
    verdict-bucket final answer") -- nunca reordenada, nunca filtrada:
    a ordem RELATIVA dentro de cada bucket é exatamente a ordem em que o
    Judge listou aquelas avaliações. Toda avaliação do Judge aparece
    exatamente uma vez, em exatamente um bucket -- não há como o plano
    omitir/reordenar/duplicar uma claim, porque o plano não sabe que
    claims (nem buckets) existem. Um bucket vazio nunca imprime seu
    cabeçalho (nenhuma frase "nenhuma conclusão sustentada" inventada) --
    ver `_BUCKET_A_HEADING`/`_BUCKET_B_HEADING`; pela mesma razão, uma
    `AnswerClaimSectionBlock` vazia nunca é emitida.

    `reconciliation` (Cross-Channel Reconciliation V1): quando existe um
    `ClaimReconciliationOutcome` pra `assessment.claim_id`, uma linha
    ADICIONAL (`_render_reconciliation_suffix`) é anexada DEPOIS da linha
    "Avaliação: ..." do Judge -- nunca a substitui, nunca muda
    `label`/`assessment.explanation`. As duas coexistem sempre que ambas
    existem (CHANNEL RELATIONSHIP != JUDGE VERDICT). A relação viaja
    PRESA ao bloco da claim -- nunca ao bucket -- então nunca muda de
    claim nem desaparece por causa do particionamento (o bucket de uma
    claim é decidido SÓ por `assessment.verdict`, nunca pela
    presença/ausência de um outcome de reconciliação). Este compositor
    NUNCA re-deriva `channel_relationship` a partir de
    `assessment.verdict`/`source_state` por conta própria -- só lê o
    campo já resolvido por `app/reconciliation/reconcile.py`. No bloco
    tipado, a MESMA nota vira `AnswerClaimItem.source_relationship_note`
    -- mesma chamada de `_render_reconciliation_suffix`, só sem o
    prefixo `"\\n  "` que fazia sentido pra concatenação em string (aqui
    vira um campo próprio, nunca concatenado a mais nada)."""
    claims_by_id = {c.id: c for c in current_claims}
    outcomes_by_claim_id = (
        {o.claim_id: o for o in reconciliation.claim_outcomes} if reconciliation is not None else {}
    )
    bucket_lines: dict[Literal["a", "b"], list[str]] = {"a": [], "b": []}
    bucket_items: dict[Literal["a", "b"], list[AnswerClaimItem]] = {"a": [], "b": []}
    for assessment in verdict.claim_assessments:
        claim = claims_by_id.get(assessment.claim_id)
        claim_text = claim.text if claim is not None else "(claim não encontrada)"
        label = _VERDICT_LABELS.get(assessment.verdict, assessment.verdict)
        line = f"- {claim_text}\n  Avaliação: {label}. {assessment.explanation}"
        note: str | None = None
        outcome = outcomes_by_claim_id.get(assessment.claim_id)
        if outcome is not None:
            suffix = _render_reconciliation_suffix(outcome, source_results_by_id)
            if suffix is not None:
                line += suffix
            # Repair (revisão adversarial, achado 1) -- `note` vem da
            # MESMA fonte de verdade que `suffix` (`_reconciliation_note_parts`,
            # via `_reconciliation_note_for_block`), nunca por
            # parsing/substituição sobre `suffix` já concatenada -- um
            # excerto verbatim que contivesse a sequência `"\n  "`
            # (indentação, blank lines, texto formatado) nunca é
            # corrompido, porque o bloco nunca desfaz uma string, ele lê
            # o excerto ainda separado do template.
            note = _reconciliation_note_for_block(outcome, source_results_by_id)
        bucket = _bucket_for_verdict(assessment.verdict)
        bucket_lines[bucket].append(line)
        bucket_items[bucket].append(
            AnswerClaimItem(
                claim_text=claim_text,
                verdict_label=label,
                explanation=assessment.explanation,
                source_relationship_note=note,
            )
        )

    sections = []
    answer_blocks: list[AnswerBlock] = []
    if bucket_lines["a"]:
        sections.append(_BUCKET_A_HEADING + "\n" + "\n".join(bucket_lines["a"]))
        answer_blocks.append(AnswerClaimSectionBlock(heading=_BUCKET_A_HEADING, items=bucket_items["a"]))
    if bucket_lines["b"]:
        sections.append(_BUCKET_B_HEADING + "\n" + "\n".join(bucket_lines["b"]))
        answer_blocks.append(AnswerClaimSectionBlock(heading=_BUCKET_B_HEADING, items=bucket_items["b"]))

    opening = (
        _OPENING_CONTEXTUAL_TEMPLATE.format(question=_bounded_question_excerpt(question))
        if plan.opening_style == "contextual"
        else _OPENING_DIRECT
    )
    answer_text = opening + "\n\n" + "\n\n".join(sections)
    answer_blocks.insert(0, AnswerParagraphBlock(text=opening))

    limitation_lines = (
        "\n".join(f"- {lim}" for lim in verdict.debate_limitations)
        if verdict.debate_limitations
        else None
    )
    if plan.closing_style == "limitations_focused":
        if limitation_lines is not None:
            answer_text += f"\n\n{_CLOSING_LIMITATIONS_LEAD_IN}\n{limitation_lines}"
        else:
            answer_text += f"\n\n{_CLOSING_LIMITATIONS_NONE_REGISTERED}"
    else:  # "concise"
        if limitation_lines is not None:
            answer_text += f"\n\n{_CLOSING_LIMITATIONS_CONCISE_HEADER}\n{limitation_lines}"
    # `answer_blocks` PARA AQUI, de propósito -- nunca ganha um bloco de
    # limitações (ver docstring de app/editor/answer_blocks.py). Só
    # `answer_text`, acima, continua ecoando-as textualmente.

    return answer_text, answer_blocks


def _render_final_answer_text(
    question: str,
    verdict: JudgeVerdict,
    current_claims: list[Claim],
    plan: EditorPlan,
    reconciliation: SourceJudgeReconciliationResult | None,
    source_results_by_id: dict[str, SourceClaimAnalysisResult],
) -> str:
    """Projeção fina de `_compose_answer` -- mantida com esta assinatura/
    tipo de retorno (`str`) exatamente pra preservar todo chamador
    existente (inclusive testes que já chamavam esta função diretamente
    antes da UI Slice 3) byte-a-byte inalterado. Nunca recomputa nada de
    forma independente -- delega inteiramente pra `_compose_answer` e
    descarta os blocos."""
    answer_text, _ = _compose_answer(
        question, verdict, current_claims, plan, reconciliation, source_results_by_id
    )
    return answer_text


def _parse_and_validate(raw_text: str) -> EditorPlan:
    """Etapa 17B -- validação inteira se resume a "é JSON e bate com o
    schema fechado (`EditorPlan`, extra=forbid, dois Literal)". Não há
    mais checagem de referência a claim_id (nenhuma existe no schema) --
    a classe de erro `InconsistentEditorReferenceError` foi removida
    (ver app/editor/errors.py), não apenas deixada sem uso."""
    try:
        data = json.loads(strip_single_json_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise MalformedEditorOutputError(f"JSON inválido: {exc}") from exc
    try:
        return EditorPlan.model_validate(data)
    except ValidationError as exc:
        raise MalformedEditorOutputError(f"JSON não bate com o schema esperado: {exc}") from exc


def _transport_error_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    request_provenance: RequestProvenance | None = None,
) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number,
        request_provenance=request_provenance,
        **transport_error_common_fields(provider_response),
    )


def _parse_rejected_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    parse_status: Literal["malformed"],
    message: str,
    request_provenance: RequestProvenance | None = None,
) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
        model_identity_source=provider_response.model_identity_source,
        transport_status="success",
        transport_error=None,
        transport_attempts=provider_response.attempts,
        raw_output_text=provider_response.text,
        parse_status=parse_status,
        parse_error_message=message,
        usage=provider_response.usage,
        cost_usd=provider_response.cost_usd,
        pricing_provenance=provider_response.pricing_provenance,
        latency_ms=provider_response.latency_ms,
        had_uncertain_prior_attempts=provider_response.had_uncertain_prior_attempts,
        provider_finish_reason=provider_response.provider_finish_reason,
        request_provenance=request_provenance,
    )


def _accepted_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    request_provenance: RequestProvenance | None = None,
) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
        model_identity_source=provider_response.model_identity_source,
        transport_status="success",
        transport_error=None,
        transport_attempts=provider_response.attempts,
        raw_output_text=provider_response.text,
        parse_status="accepted",
        parse_error_message=None,
        usage=provider_response.usage,
        cost_usd=provider_response.cost_usd,
        pricing_provenance=provider_response.pricing_provenance,
        latency_ms=provider_response.latency_ms,
        had_uncertain_prior_attempts=provider_response.had_uncertain_prior_attempts,
        provider_finish_reason=provider_response.provider_finish_reason,
        request_provenance=request_provenance,
    )
