"""
Exportação legível de UMA execução concluída (Provenance-Preserving
Human-Readable Run Export).

Um documento de TEXTO para pessoas, gerado sob demanda a partir do estado
autoritativo já persistido (os mesmos objetos públicos de detalhe/auditoria),
com a resposta primeiro e só a proveniência e as limitações necessárias para
interpretá-la fora do Dialeon. Não é formato de dados: nenhum schema, nenhuma
versão de exportação, nenhuma promessa de importação/reprodução -- títulos e
layout podem mudar. Nada é persistido e nenhum modelo é chamado.

Fronteira de privacidade padrão: entram a pergunta e a resposta apresentada,
INTEIRA e sem reescrita (ela pode conter, legitimamente, trechos da fonte ou --
na resposta direta -- é a própria resposta do provider). Nunca entram como
partes SEPARADAS: o texto completo da fonte, trechos da análise da fonte,
respostas individuais/brutas dos modelos, tentativas, erros dos providers,
uso/custo nem configuração/segredos.

Segurança do texto: todo texto vindo do usuário ou de modelo é escrito
INDENTADO (nunca na coluna 0, onde só ficam os títulos da exportação) e linha
a linha por `terminal_safe_text` -- nenhum caractere não imprimível
(controles, ESC, bidi, separadores Unicode) chega ao arquivo como controle
real; nenhum texto de modelo consegue forjar um título ou seção.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from app.models.provider_models import ModelIdentitySource
from app.presentation.answer_presentation import select_answer_presentation
from app.presentation.schemas import (
    CompletedRunAudit,
    DirectCompletedRunResponse,
    ModelResponsePublic,
)
from app.providers.base import is_known_output_truncation
from app.text_safety import terminal_safe_text

_INDENT = "    "

# Rótulos em português -- mesmo vocabulário da interface web
# (frontend/src/api/formatting.ts) e da CLI.
_FINAL_ANSWER_STATUS_LABELS = {
    "llm_planned": "estruturada por um planejador de apresentação a partir da avaliação do juiz",
    "llm_composed": "composta pelo editor a partir do debate (formato histórico)",
    "deterministic_from_verdict": "montada automaticamente a partir da avaliação do juiz",
    "deterministic_no_verdict": "montada automaticamente sem avaliação do juiz",
}
_PRESENTATION_LABELS = {
    "linguistic_realization": "texto redigido a partir da resposta principal",
    "natural_answer": "resposta em texto corrido gerada da resposta principal",
    "primary_answer": "resposta principal estruturada",
    "complete_assessment": "avaliação completa",
}
_DEBATE_SKIPPED_LABELS = {
    "insufficient_initial_quorum": "Poucas respostas na rodada inicial para justificar uma crítica.",
    "budget_exhausted_before_critique": "Orçamento esgotado antes da rodada de crítica.",
    "all_initial_extractions_failed": (
        "Os participantes responderam, mas a extração estruturada das afirmações falhou."
    ),
}
_JUDGE_UNAVAILABLE_LABELS = {
    "budget_exhausted_before_judge": "Orçamento esgotado antes da avaliação do juiz.",
    "no_claims_to_judge": "Não havia afirmações para avaliar.",
    "judge_transport_failed": "Falha de comunicação com o modelo juiz.",
    "judge_output_invalid": "A resposta do juiz não pôde ser interpretada.",
    "claim_extraction_failed": (
        "Os participantes responderam, mas a extração estruturada das afirmações falhou."
    ),
    "claim_extraction_incomplete": (
        "A extração estruturada das afirmações ficou incompleta -- algumas respostas dos "
        "participantes não puderam ser extraídas."
    ),
}
_EDITOR_FALLBACK_LABELS = {
    "budget_exhausted_before_editor": "Orçamento esgotado antes da composição final.",
    "editor_transport_failed": "Falha de comunicação com o modelo editor.",
    "editor_output_invalid": "A composição do editor não pôde ser interpretada.",
    "judge_verdict_unavailable": "Resposta final gerada sem avaliação do juiz.",
}
_SOURCE_ANALYSIS_SKIPPED_LABELS = {
    "no_claims_to_analyze": "Não havia afirmações para analisar contra a fonte.",
    "budget_exhausted_before_source_analysis": "Orçamento esgotado antes da análise da fonte.",
    "source_analysis_transport_failed": "Falha de comunicação durante a análise da fonte.",
    "source_analysis_output_invalid": "A análise da fonte não pôde ser interpretada corretamente.",
}
_MODEL_ORIGIN_LABELS = {
    "configured_default": "padrão configurado quando a pergunta foi aceita",
    "run_override": "escolhido nesta pergunta",
}
_READINESS_ROLE_LABELS = {
    "participant": "participante",
    "claim_extraction": "extração de afirmações",
    "source_analysis": "análise de fonte",
    "judge": "juiz",
    "editor": "editor",
    "semantic_review": "revisão semântica da redação",
}

_REALIZATION_NOTE = (
    "Texto redigido por um modelo a partir das afirmações selecionadas e avaliadas. Uma revisão "
    "indicou consistência com a resposta estruturada; não é verificação externa nem garantia de "
    "verdade."
)
_COUNCIL_NOTE = (
    "Resposta do Conselho de modelos, montada a partir das respostas de vários modelos. "
    "Concordância entre modelos e a avaliação do juiz, quando houver, dizem respeito a este "
    "debate; não são verificação externa nem garantia de verdade."
)
_DIRECT_NOTE = (
    "Resposta direta: a resposta de UM modelo, numa única chamada, sem as etapas do Conselho. "
    "Não é consenso, avaliação do juiz nem verificação."
)
_SOURCE_NOTE = (
    "O texto completo da fonte não é incluído como parte separada desta exportação; trechos dele "
    "podem aparecer dentro da própria resposta. A análise da fonte compara as afirmações do "
    "debate com esse texto fornecido pelo usuário, que não foi verificado; não é verificação "
    "externa. Sem o texto completo, esta exportação não permite conferir essa comparação."
)
_FOOTER_INTRO = (
    "Documento para leitura humana, gerado a partir do registro desta execução. Não é um formato "
    "de dados estável, não serve para importar nem reproduzir a execução e não é a auditoria "
    "completa, que continua no Dialeon."
)
_COUNCIL_OMISSIONS = (
    "Além da pergunta e da resposta apresentada acima, não são incluídos como partes separadas: "
    "o texto completo da fonte, as respostas individuais dos participantes, as tentativas "
    "registradas, os erros informados pelos providers, uso e custo. A resposta é exportada "
    "inteira e pode conter trechos desse material."
)
_DIRECT_OMISSIONS = (
    "A resposta acima é a própria resposta registrada do provider, exportada inteira. Não são "
    "incluídos: as tentativas registradas da chamada, erros informados pelo provider, uso e custo."
)


# ---------------------------------------------------------------------------
# Blocos de texto
# ---------------------------------------------------------------------------


def _untrusted_block(text: str, indent: str = _INDENT) -> list[str]:
    """Texto de usuário/modelo: preserva as quebras de linha reais (CRLF vira
    LF), cada linha neutralizada e indentada; linhas vazias ficam vazias."""
    lines = text.replace("\r\n", "\n").split("\n")
    return [f"{indent}{terminal_safe_text(line)}" if line else "" for line in lines]


def _heading(title: str, underline: str = "-") -> list[str]:
    return ["", title, underline * len(title)]


def _item(label: str, value: str) -> str:
    """Linha de fato: rótulo da aplicação + valor NEUTRALIZADO (sempre uma
    linha só, indentada)."""
    return f"{_INDENT}{label}: {terminal_safe_text(value)}"


def _bullets(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        first, *rest = item.replace("\r\n", "\n").split("\n")
        out.append(f"{_INDENT}- {terminal_safe_text(first)}")
        out.extend(f"{_INDENT}  {terminal_safe_text(line)}" if line else "" for line in rest)
    return out


def _note(text: str) -> list[str]:
    """Nota autorada pela aplicação, quebrada em linhas curtas."""
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > 76:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return [f"{_INDENT}{line}" for line in lines]


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _reported_identity(response: ModelResponsePublic) -> str:
    """Identidade reportada SEPARADA da pedida; um fallback nunca é
    apresentado como confirmação do provider."""
    source = response.model_identity_source
    if source is ModelIdentitySource.PROVIDER_REPORTED:
        return f"{response.model} (reportado pelo provider)"
    if source is ModelIdentitySource.REQUESTED_FALLBACK:
        return "não informado pelo provider (o registro repete o modelo pedido, sem confirmação)"
    return f"{response.model} (origem da identidade não registrada: execução anterior a este registro)"


def _header(title: str, run_id: str, generated_at: datetime, product_version: str) -> list[str]:
    return [
        title,
        "=" * len(title),
        "",
        _item("Execução", run_id),
        f"{_INDENT}Exportado em {_timestamp(generated_at)} pelo Dialeon {terminal_safe_text(product_version)}",
        f"{_INDENT}(a versão é a desta exportação, não necessariamente a que executou a pergunta)",
    ]


def _footer(omissions: str) -> list[str]:
    return [*_heading("Sobre esta exportação"), *_note(_FOOTER_INTRO), "", *_note(omissions), ""]


# ---------------------------------------------------------------------------
# Resposta direta
# ---------------------------------------------------------------------------


def render_direct_run_export(
    run: DirectCompletedRunResponse, *, generated_at: datetime, product_version: str
) -> str:
    config, response = run.config, run.response
    lines = _header("Dialeon — resposta direta exportada", run.id, generated_at, product_version)
    lines += [*_heading("Pergunta"), *_untrusted_block(config.question)]
    lines += [*_heading("Resposta"), *_untrusted_block(run.answer), "", *_note(_DIRECT_NOTE)]
    if is_known_output_truncation(response.provider_finish_reason):
        lines += [
            "",
            *_note(
                "O provider indicou que a resposta parou no limite de tamanho de saída: ela pode "
                "estar incompleta."
            ),
        ]
    origin = _MODEL_ORIGIN_LABELS.get(config.requested_model_origin, config.requested_model_origin)
    lines += [
        *_heading("Como esta resposta foi produzida"),
        _item("Tipo de execução", "resposta direta (um modelo, sem as etapas do Conselho)"),
        _item("Provider", config.provider),
        _item("Modelo pedido", f"{config.requested_model} ({origin})"),
        _item("Modelo reportado", _reported_identity(response)),
        _item("Iniciada em", _timestamp(run.started_at)),
        _item("Concluída em", _timestamp(run.completed_at)),
    ]
    return "\n".join([*lines, *_footer(_DIRECT_OMISSIONS)])


# ---------------------------------------------------------------------------
# Conselho
# ---------------------------------------------------------------------------


def _participant_lines(audit: CompletedRunAudit) -> list[str]:
    config = audit.config
    choices = (
        {c.provider: c for c in config.participant_models}
        if config.participant_models is not None
        else None
    )
    responses = {r.provider: r for r in audit.initial_round.responses}
    lines: list[str] = []
    for provider in config.enabled_providers:
        response = responses.get(provider)
        if choices is not None and provider in choices:
            choice = choices[provider]
            requested = (
                f"{choice.requested_model} "
                f"({_MODEL_ORIGIN_LABELS.get(choice.origin, choice.origin)})"
            )
        elif response is not None:
            requested = f"{response.requested_model} (origem não registrada: execução anterior a este registro)"
        else:
            requested = "não registrado"
        lines.append(f"{_INDENT}- {terminal_safe_text(provider)}")
        lines.append(f"{_INDENT}  modelo pedido: {terminal_safe_text(requested)}")
        if response is None:
            lines.append(f"{_INDENT}  rodada inicial: sem registro de resposta")
        elif response.status == "success":
            lines.append(f"{_INDENT}  rodada inicial: respondeu")
            lines.append(f"{_INDENT}  modelo reportado: {terminal_safe_text(_reported_identity(response))}")
        else:
            lines.append(f"{_INDENT}  rodada inicial: sem resposta utilizável")
    return lines


def _degradations(audit: CompletedRunAudit) -> list[str]:
    """Etapas não realizadas ou degradadas -- só fatos registrados."""
    items: list[str] = []
    initial = audit.initial_round
    if initial.successful_count < initial.total_providers:
        items.append(
            f"{initial.total_providers - initial.successful_count} de {initial.total_providers} "
            "participantes não produziram resposta utilizável na rodada inicial."
        )
    debate = audit.debate_outcome
    if debate.skipped_reason is not None:
        items.append(
            "Rodada de crítica não realizada: "
            + _DEBATE_SKIPPED_LABELS.get(debate.skipped_reason, debate.skipped_reason)
        )
    if debate.claim_extraction_missing_response_count > 0:
        items.append(
            f"A extração de afirmações não cobriu {debate.claim_extraction_missing_response_count} "
            f"de {debate.claim_extraction_eligible_response_count} respostas."
        )
    judge = audit.judge_outcome
    if judge.verdict_unavailable_reason is not None:
        items.append(
            "Avaliação do juiz indisponível: "
            + _JUDGE_UNAVAILABLE_LABELS.get(judge.verdict_unavailable_reason, judge.verdict_unavailable_reason)
        )
    editor = audit.editor_outcome
    if editor.fallback_reason is not None:
        items.append(
            "Composição final por fallback: "
            + _EDITOR_FALLBACK_LABELS.get(editor.fallback_reason, editor.fallback_reason)
        )
    source = audit.source_analysis
    if (
        initial.budget_exceeded
        or debate.cumulative_budget_exceeded
        or judge.cumulative_budget_exceeded
        or editor.cumulative_budget_exceeded
        or (source is not None and source.cumulative_budget_exceeded)
    ):
        items.append("O limite de orçamento da execução foi atingido.")
    admission = audit.council_admission
    if admission is not None and admission.known_degradation_acknowledged:
        roles = sorted(
            {
                _READINESS_ROLE_LABELS.get(d.role, d.role)
                for d in admission.readiness.dependencies
                if d.local_prerequisite == "missing" and d.applicability != "not_applicable"
            }
        )
        items.append(
            "A execução foi aceita sabendo que faltava configuração local"
            + (f" para: {', '.join(roles)}." if roles else ".")
        )
    return items


def _model_identity(model: str, source: ModelIdentitySource | None) -> str:
    if source is ModelIdentitySource.PROVIDER_REPORTED:
        return f"{model} (reportado pelo provider)"
    if source is ModelIdentitySource.REQUESTED_FALLBACK:
        return "não informado pelo provider (o registro repete o modelo pedido, sem confirmação)"
    return f"{model} (origem da identidade não registrada)"


def _accepted_attempt_provider(attempts) -> str | None:
    """Provider da tentativa ACEITA registrada -- nunca o provider apenas
    configurado para o papel."""
    return next((a.provider for a in attempts if a.parse_status == "accepted"), None)


def _judge_and_editor_lines(audit: CompletedRunAudit) -> list[str]:
    """Só o que a execução REGISTROU: um provider configurado para o papel
    não prova que o papel executou (configurado != executado).

    - Juiz: creditado só quando há veredito registrado.
    - Editor: creditado só quando a resposta final registra o modelo da
      tentativa aceita (`editor_model` -- sempre ausente nos caminhos
      determinísticos, inclusive quando o editor nem chegou a ser chamado ou
      falhou). Sem isso, nenhuma linha de editor: a montagem automática já
      está dita em "Como a resposta foi montada" e o motivo, em "Etapas não
      realizadas ou degradadas"."""
    final_answer = audit.final_answer
    verdict = audit.judge_verdict
    has_editor = final_answer.editor_model is not None
    lines = _heading("Juiz e editor" if has_editor else "Juiz")
    if verdict is not None:
        provider = _accepted_attempt_provider(audit.judge_attempts)
        identity = _model_identity(verdict.judge_model, verdict.judge_model_identity_source)
        lines += [
            _item("Juiz", (f"{provider}; " if provider else "") + f"modelo: {identity}"),
            *_note(
                "O juiz avaliou as afirmações extraídas deste debate, a partir das respostas dos "
                "participantes; a avaliação não consulta fontes externas."
            ),
        ]
    else:
        lines.append(_item("Juiz", "nenhuma avaliação registrada nesta execução"))
    if has_editor:
        assert final_answer.editor_model is not None
        provider = _accepted_attempt_provider(audit.editor_attempts)
        identity = _model_identity(final_answer.editor_model, final_answer.editor_model_identity_source)
        lines.append(
            _item("Editor", (f"{provider}; " if provider else "") + f"modelo da tentativa aceita: {identity}")
        )
    return lines


def render_council_run_export(
    audit: CompletedRunAudit, *, generated_at: datetime, product_version: str
) -> str:
    config, final_answer = audit.config, audit.final_answer
    presentation = select_answer_presentation(final_answer)

    lines = _header("Dialeon — resposta do Conselho exportada", audit.id, generated_at, product_version)
    lines += [*_heading("Pergunta"), *_untrusted_block(config.question)]
    lines += [*_heading("Resposta"), *_untrusted_block(presentation.text)]
    if presentation.kind == "linguistic_realization":
        lines += ["", *_note(_REALIZATION_NOTE)]
    if final_answer.limitations and not presentation.text_carries_limitations:
        lines += [*_heading("Limitações registradas"), *_bullets(final_answer.limitations)]
    lines += ["", *_note(_COUNCIL_NOTE)]

    lines += [
        *_heading("Como esta resposta foi produzida"),
        _item("Tipo de execução", "Conselho de modelos"),
        _item("Forma apresentada", _PRESENTATION_LABELS[presentation.kind]),
        *_note(
            "(escolhida pelas regras atuais de apresentação, na leitura desta exportação; não "
            "necessariamente a forma mostrada quando a execução terminou)"
        ),
        _item(
            "Como a resposta foi montada",
            _FINAL_ANSWER_STATUS_LABELS.get(final_answer.status, final_answer.status),
        ),
        _item("Iniciada em", _timestamp(audit.started_at)),
        _item("Concluída em", _timestamp(audit.completed_at)),
    ]

    lines += [*_heading("Participantes"), *_participant_lines(audit)]
    if config.participant_models is None:
        lines += _note(
            "Os modelos pedidos e a origem da escolha não foram registrados nesta execução "
            "(anterior a este registro); o modelo pedido acima vem do registro de cada resposta."
        )
    critique = audit.critique_round
    if critique is not None:
        lines.append(
            f"{_INDENT}Rodada de crítica: {critique.successful_count} de "
            f"{critique.total_participants} participantes responderam."
        )

    lines += _judge_and_editor_lines(audit)

    source_supplied = config.source_text is not None and config.source_text != ""
    if source_supplied:
        source = audit.source_analysis
        if source is None:
            status = "não registrada"
        elif source.skipped_reason is None:
            status = "realizada"
        else:
            status = "não realizada -- " + _SOURCE_ANALYSIS_SKIPPED_LABELS.get(
                source.skipped_reason, source.skipped_reason
            )
        lines += [
            *_heading("Fonte fornecida pelo usuário"),
            _item("Análise da fonte", status),
            *_note(_SOURCE_NOTE),
        ]

    degradations = _degradations(audit)
    if degradations:
        lines += [*_heading("Etapas não realizadas ou degradadas"), *_bullets(degradations)]

    return "\n".join([*lines, *_footer(_COUNCIL_OMISSIONS)])


# ---------------------------------------------------------------------------
# Nome do arquivo
# ---------------------------------------------------------------------------

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def export_filename(run_id: str, *, kind: str) -> str:
    """Nome determinístico e seguro (ASCII, sem separadores de caminho nem
    aspas) -- `kind` é "direta" ou "conselho"."""
    safe_id = _UNSAFE_FILENAME_CHARS.sub("", run_id)[:80] or "execucao"
    return f"dialeon-{kind}-{safe_id}.txt"
