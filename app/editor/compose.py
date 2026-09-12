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
`FinalAnswer.answer_text` é escrita por `_render_final_answer_text`
abaixo, o ÚNICO renderizador epistêmico deste módulo, usado tanto no
sucesso do plano da LLM quanto em qualquer fallback (mesma função, nunca
dois textos possíveis pro mesmo estado epistêmico dependendo de qual
caminho de execução foi seguido). A LLM Editor nunca mais vê nem escreve
claim_text/explanation/debate_limitations -- só os usa a aplicação, que
os inclui verbatim.

Isso NÃO resolve (fora de escopo, ver docstring de app/editor/schemas.py):
se `ClaimAssessment.explanation` (texto livre do PRÓPRIO Judge) é
internamente coerente com `ClaimAssessment.verdict` -- essa é uma
garantia sobre o contrato do Judge (app/judge/schemas.py,
app/judge/single_judge.py), uma camada acima desta. A Etapa 17B garante
que a camada de apresentação não ESCALA/reinterpreta o Judge; não garante
que o próprio Judge é internamente consistente.

Patch de apresentação com fonte (pós-diagnóstico de run real) -- SOURCE
RELATION != JUDGE VERDICT != TRUTH: `_render_final_answer_text` agora
também recebe (opcionalmente) `SourceAnalysisResult.claim_results` e,
quando existe uma `ValidSourceRelation` VÁLIDA (nunca uma
`RejectedSourceEntry`, ver `_source_relations_by_claim_id` abaixo) pra
uma claim que o Judge avaliou, renderiza uma linha ADICIONAL logo
abaixo da avaliação do Judge -- nunca substituindo, nunca alterando o
rótulo/explicação do Judge. `Judge` continua inteiramente cego a Source
Analysis (nenhuma mudança em `app/judge/`); `EditorPlan` continua sem
nenhum campo relacionado a fonte (a LLM Editor nunca vê nem escolhe
nada sobre isso); a linha de relação é template FIXO desta função,
igual a todo o resto do renderizador -- não introduz nenhuma autoridade
epistêmica nova, só comunica um segundo fato JÁ COMPUTADO (a relação
entre a claim e a fonte fornecida) ao lado do veredito do Judge, que o
usuário explicitamente pode ter pedido pra diferenciar (ver caso real:
claim sobre ano de fundação, Judge diz "indeterminável pelo debate",
Source Analysis diz "a fonte contradiz" -- as duas coexistem no texto
final, nenhuma reescreve a outra).

Bounded contextual opening (patch de legibilidade pós-diagnóstico de run
real) -- `EditorPlan.opening_style="contextual"` antepunha a `question`
ORIGINAL inteira, verbatim, sem nenhum limite (`RunConfig.question` só
valida `min_length=1`, ver app/orchestrator/config.py -- nenhum teto de
tamanho, nenhuma normalização de whitespace). Pra uma pergunta curta de
uma linha isso é perfeitamente legível; pra uma pergunta longa, um
documento de requisitos colado, ou um prompt multi-linha/com bullets, a
abertura passava a dominar `answer_text` inteiro -- um problema de
LEGIBILIDADE de apresentação, nunca epistêmico (`question` nunca
influencia claim/veredito, só é ecoada de volta pro usuário como
enquadramento).

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
"""

from __future__ import annotations

import json
import unicodedata
from typing import Literal

from pydantic import ValidationError

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.editor.attempt import EditorAttempt
from app.editor.context import build_editor_request
from app.editor.errors import MalformedEditorOutputError
from app.editor.result import EditorResult, FinalAnswer
from app.editor.schemas import EditorPlan
from app.judge.result import JudgeResult
from app.models.domain import Claim, JudgeVerdict
from app.models.provider_models import ProviderResponse
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.providers.base import LLMProvider, transport_error_common_fields
from app.source_analysis.models import ValidSourceRelation
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

_JUDGE_REASON_LABELS: dict[str, str] = {
    "budget_exhausted_before_judge": "o orçamento se esgotou antes da avaliação final",
    "no_claims_to_judge": "nenhuma informação avaliável foi produzida no debate",
    "judge_transport_failed": "houve uma falha de comunicação durante a avaliação final",
    "judge_output_invalid": "a avaliação final não pôde ser interpretada corretamente",
}

# Patch de apresentação com fonte -- rótulos de RELAÇÃO com a fonte,
# nunca de verdade externa (mesma disciplina de app/cli/output.py e do
# frontend, ver app/source_analysis/models.py): "apoia"/"contradiz"
# descrevem o que a análise encontrou entre a claim e o texto da fonte
# fornecida pelo usuário -- nunca se a claim é verdadeira/falsa/provada.
# "unresolved" É OMITIDO DE PROPÓSITO (nenhuma entrada aqui) -- sinal
# nulo, renderizar uma linha extra pra "a fonte não decide" deixaria
# toda claim sem relação clara ainda mais verbosa sem acrescentar
# informação real (ver `_render_source_relation_suffix`).
_SOURCE_RELATION_PRESENTATION_LABELS: dict[str, str] = {
    "supports": "apoia esta afirmação",
    "contradicts": "contradiz esta afirmação",
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
    ) -> EditorResult:
        # `prior_*` (patch de revisão do Stage 16): consumo REAL acumulado
        # de Debate + Source Analysis (se houve) + Judge -- já inclui
        # Judge aqui (diferente do que Judge recebe, que é só
        # Debate+Source Analysis).
        #
        # Patch de apresentação com fonte -- `source_analysis_result` é
        # NOVO aqui (default None preserva toda chamada existente sem
        # fonte), mas o Editor LLM/`EditorPlan` continuam TÃO cegos a
        # Source Analysis quanto antes: só o RENDERIZADOR determinístico
        # (`_render_final_answer_text`) o recebe, via
        # `source_relations_by_claim_id` abaixo -- nunca entra em
        # `build_editor_request`/no prompt da LLM.
        if judge_result.verdict is None:
            return self._no_verdict_result(
                debate_result,
                judge_result,
                run_config,
                prior_input_tokens=prior_input_tokens,
                prior_output_tokens=prior_output_tokens,
                prior_cost_usd=prior_cost_usd,
            )

        verdict = judge_result.verdict
        current_claims = get_current_claims(debate_result.claims)
        source_relations_by_claim_id = _source_relations_by_claim_id(source_analysis_result)

        input_before, output_before, cost_before = (
            prior_input_tokens,
            prior_output_tokens,
            prior_cost_usd,
        )

        if compute_budget_exceeded(input_before, output_before, cost_before, run_config):
            return EditorResult(
                final_answer=self._deterministic_from_verdict_answer(
                    run_config.question, verdict, current_claims, source_relations_by_claim_id
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
                attempts.append(_transport_error_attempt(attempt_number, provider_response))
                break  # sem retry desta camada pra erro de transporte

            try:
                parsed = _parse_and_validate(provider_response.text)
            except MalformedEditorOutputError as exc:
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number, provider_response, "malformed", str(exc)
                    )
                )
                continue

            attempts.append(_accepted_attempt(attempt_number, provider_response))
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
                    run_config.question, verdict, current_claims, source_relations_by_claim_id
                ),
                attempts=attempts,
                fallback_reason=reason,
                editor_provider=run_config.editor_provider,
                cumulative_budget_exceeded=cumulative_budget_exceeded,
            )

        answer_text = _render_final_answer_text(
            run_config.question, verdict, current_claims, parsed, source_relations_by_claim_id
        )
        final_answer = FinalAnswer(
            answer_text=answer_text,
            limitations=list(verdict.debate_limitations),
            status="llm_planned",
            editor_model=accepted_response.model,
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
    ) -> EditorResult:
        current_claims = get_current_claims(debate_result.claims)
        reason_text = _JUDGE_REASON_LABELS.get(
            judge_result.verdict_unavailable_reason or "", "a avaliação final não foi concluída"
        )

        if current_claims:
            claim_lines = "\n".join(f"- {c.text}" for c in current_claims)
            answer_text = (
                f"A avaliação final não pôde ser concluída: {reason_text}. As seguintes "
                "afirmações foram levantadas pelos modelos participantes, mas não foram "
                f"avaliadas:\n{claim_lines}"
            )
        else:
            answer_text = (
                f"Não foi possível produzir uma resposta avaliável para esta pergunta: "
                f"{reason_text}."
            )

        final_answer = FinalAnswer(
            answer_text=answer_text,
            limitations=[f"Avaliação final não realizada: {reason_text}."],
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
        source_relations_by_claim_id: dict[str, ValidSourceRelation],
    ) -> FinalAnswer:
        """Fallback (transporte/parse/budget) -- usa o MESMO
        `_render_final_answer_text` do caminho de sucesso, com
        `_DEFAULT_PLAN`, nunca um renderizador próprio (Etapa 17B): não
        pode haver dois textos possíveis pro mesmo veredito dependendo
        só de qual caminho de execução foi seguido. `source_relations_by_claim_id`
        segue o mesmo princípio (patch de apresentação com fonte): o
        fallback mostra exatamente a mesma relação de fonte que o
        caminho de sucesso mostraria pro mesmo veredito."""
        answer_text = _render_final_answer_text(
            question, verdict, current_claims, _DEFAULT_PLAN, source_relations_by_claim_id
        )
        return FinalAnswer(
            answer_text=answer_text,
            limitations=list(verdict.debate_limitations),
            status="deterministic_from_verdict",
            based_on_verdict_id=verdict.id,
            judge_confidence=verdict.confidence,
        )


def _source_relations_by_claim_id(
    source_analysis_result: SourceAnalysisResult | None,
) -> dict[str, ValidSourceRelation]:
    """Só `ValidSourceRelation` -- NUNCA `RejectedSourceEntry` (ver
    app/source_analysis/models.py: rejeitada não é uma relação
    epistêmica, é a aplicação dizendo que não confiou no que a análise
    devolveu; nunca apresentada como evidência válida aqui). `{}`
    (nenhuma relação) quando `source_analysis_result` é `None` (nenhuma
    fonte fornecida) ou tem `skipped_reason` preenchido (análise pulada/
    falhada -- e por construção não tem `claim_results` nesse caso, ver
    `SourceAnalysisResult._claim_results_only_when_not_skipped`) --
    ausência de fonte validada nunca produz nenhuma linha nova, o texto
    final fica exatamente como seria sem este patch.

    Chave = `claim_id`, o MESMO identificador estruturado que
    `ClaimAssessment.claim_id` usa -- nunca fuzzy matching por texto.
    Uma relação cujo `claim_id` não bate com nenhuma claim que o Judge
    avaliou (claim revisada/fundida, id desconhecido) simplesmente nunca
    é procurada no dict por `_render_final_answer_text` -- degrada pra
    "nenhuma relação pra esta claim", nunca uma tentativa de
    reconciliação semântica."""
    if source_analysis_result is None or source_analysis_result.skipped_reason is not None:
        return {}
    return {
        r.claim_id: r
        for r in source_analysis_result.claim_results
        if isinstance(r, ValidSourceRelation)
    }


def _render_source_relation_suffix(relation: ValidSourceRelation) -> str | None:
    """Linha ADICIONAL, nunca substituta, da avaliação do Judge --
    SOURCE RELATION != JUDGE VERDICT != TRUTH. `None` pra
    `relation="unresolved"` de propósito (sinal nulo -- ver
    `_SOURCE_RELATION_PRESENTATION_LABELS`, conservador por design,
    não por omissão).

    Correção arquitetural (auditoria de terminal-safety pós-CLI):
    `relation.excerpt` entra aqui BYTE-FIEL, sem nenhum escaping --
    `FinalAnswer.answer_text` é o dado CANÔNICO (persistido/API/
    frontend), não um artefato de terminal, e escaping terminal-
    específico NUNCA pertence a esta camada de domínio (a mesma razão
    pela qual `Claim.text`/`ClaimAssessment.explanation`, logo abaixo,
    também nunca passaram por `terminal_safe_text` aqui). A
    neutralização de controle de terminal para TODO o texto composto
    (excerpt incluído) acontece uma única vez, no limite de
    apresentação real -- `app/cli/output.py::human_run_result`, que é
    quem de fato escreve em um terminal -- nunca dentro do
    renderizador de domínio. Ver docstring de `app/text_safety.py`."""
    if relation.relation not in _SOURCE_RELATION_PRESENTATION_LABELS:
        return None
    label = _SOURCE_RELATION_PRESENTATION_LABELS[relation.relation]
    suffix = f"\n  Relação com a fonte fornecida: a fonte {label}."
    if relation.excerpt is not None:
        suffix += f'\n  Trecho da fonte: "{relation.excerpt}"'
    return suffix


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


def _render_final_answer_text(
    question: str,
    verdict: JudgeVerdict,
    current_claims: list[Claim],
    plan: EditorPlan,
    source_relations_by_claim_id: dict[str, ValidSourceRelation],
) -> str:
    """Único renderizador epistêmico do módulo (Etapa 17B) -- usado tanto
    quando a LLM Editor produz um `EditorPlan` aceito quanto em qualquer
    fallback (com `_DEFAULT_PLAN`, ver `Editor._deterministic_from_verdict_answer`).
    Nenhuma palavra aqui se origina da LLM: `claim_text` (via
    `current_claims`) e `verdict.claim_assessments`
    (verdict/explanation)/`verdict.debate_limitations` são sempre dados
    do Judge/debate, incluídos verbatim; todo o resto — rótulo por
    veredito, frases de abertura/fechamento — é template FIXO desta
    função, só selecionado (nunca escrito) pelos dois enums finitos do
    plano.

    Ordem sempre = `verdict.claim_assessments` (ordem do próprio Judge,
    nunca controlável pela LLM -- `EditorPlan` nem tem campo de
    referência a claim). Toda avaliação do Judge aparece exatamente uma
    vez -- não há como o plano omitir ou reordenar uma claim, porque o
    plano não sabe que claims existem.

    `source_relations_by_claim_id` (patch de apresentação com fonte):
    quando existe uma relação válida pra `assessment.claim_id`, uma
    linha ADICIONAL (`_render_source_relation_suffix`) é anexada
    DEPOIS da linha "Avaliação: ..." do Judge -- nunca a substitui,
    nunca muda `label`/`assessment.explanation`. As duas coexistem
    sempre que ambas existem (SOURCE RELATION != JUDGE VERDICT)."""
    claims_by_id = {c.id: c for c in current_claims}
    lines = []
    for assessment in verdict.claim_assessments:
        claim = claims_by_id.get(assessment.claim_id)
        claim_text = claim.text if claim is not None else "(claim não encontrada)"
        label = _VERDICT_LABELS.get(assessment.verdict, assessment.verdict)
        line = f"- {claim_text}\n  Avaliação: {label}. {assessment.explanation}"
        relation = source_relations_by_claim_id.get(assessment.claim_id)
        if relation is not None:
            relation_suffix = _render_source_relation_suffix(relation)
            if relation_suffix is not None:
                line += relation_suffix
        lines.append(line)

    opening = (
        _OPENING_CONTEXTUAL_TEMPLATE.format(question=_bounded_question_excerpt(question))
        if plan.opening_style == "contextual"
        else _OPENING_DIRECT
    )
    answer_text = opening + "\n" + "\n".join(lines)

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
    attempt_number: int, provider_response: ProviderResponse
) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number, **transport_error_common_fields(provider_response)
    )


def _parse_rejected_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    parse_status: Literal["malformed"],
    message: str,
) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
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
    )


def _accepted_attempt(attempt_number: int, provider_response: ProviderResponse) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
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
    )
