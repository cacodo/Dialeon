"""
Formatação de saída da CLI -- Etapa 14 (T19A.1).

Duas responsabilidades bem separadas:

- `emit_json(schema)`: serializa um schema Pydantic já existente
  (`app.presentation.schemas`/`app.presentation.mappers` -- camada
  neutra compartilhada com a API, ver docstring de `commands.py`).
  `model_dump(mode="json")` já preserva `None` como `null` corretamente
  -- nenhuma transformação adicional necessária (Unknown != 0/false).
- `emit_json_error(code, message, details)`: mesmo formato de erro já
  usado pela API (`ErrorResponse`), pra scripts tratarem os dois de
  forma idêntica.
- Funções `human_*`: texto simples pro terminal, nunca reconstruído a
  partir do JSON -- lidas diretamente dos mesmos objetos de domínio/
  schema. `_fmt` nunca transforma `None` em `0`/`False`/string vazia.

stdout é reservado pro resultado (humano ou `--json`); stderr é só pra
mensagens de erro -- ver `main.py`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import BaseModel

from app.models.provider_models import ModelIdentitySource, ProviderExecutionPolicy
from app.presentation.schemas import (
    CompletedRunResponse,
    DirectCompletedRunResponse,
    DirectFailedRunResponse,
    DirectRunningRunResponse,
    FailedRunResponse,
    QuorumFailureRunResponse,
    RunningRunResponse,
)
from app.text_safety import terminal_safe_text

# Patch de seguranca de terminal -- terminal_safe_text mora em
# app/text_safety.py (nao aqui) porque e um utilitario neutro de topo,
# no mesmo espirito de app/structured_output.py. app/editor/compose.py
# NAO usa terminal_safe_text -- e codigo de dominio, terminal-agnostico
# de proposito (ver docstring de app/text_safety.py); toda neutralizacao
# de terminal acontece exclusivamente nas funcoes human_* deste modulo,
# no unico lugar que de fato escreve num terminal real.


def emit_json(schema: BaseModel) -> None:
    print(schema.model_dump_json())


def emit_json_error(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    print(json.dumps(body, ensure_ascii=False), file=sys.stderr)


def print_error(message: str) -> None:
    print(message, file=sys.stderr)


def _fmt(value: Any, *, unit: str = "") -> str:
    """Nunca transforma None em 0/false/"" -- sempre um rótulo explícito
    de desconhecido, que é o que None realmente significa nestes
    contratos (custo/token não observável, não zero)."""
    if value is None:
        return "desconhecido"
    return f"{value}{unit}"


def _fmt_model_identity_source(source: ModelIdentitySource | None) -> str:
    """`None` é o valor HONESTO pra runs persistidos antes desta coluna
    existir -- nunca confundido com `REQUESTED_FALLBACK` (ver docstring
    de `ModelIdentitySource`, app/models/provider_models.py)."""
    if source is None:
        return "não registrada (execução anterior a este registro)"
    if source is ModelIdentitySource.PROVIDER_REPORTED:
        return "reportada pelo provider"
    return "fallback do modelo solicitado"


def _human_provider_execution_policy_line(policy: ProviderExecutionPolicy | None) -> str:
    """T02.2 -- `None` é o valor HONESTO pra runs persistidos antes
    desta feature existir (nunca um default atual inventado -- ver
    docstring de ProviderExecutionPolicy)."""
    if policy is None:
        return "política_de_execução_do_provider: desconhecida (execução anterior a este registro)"
    line = (
        "política_de_execução_do_provider: "
        f"timeout_por_tentativa={policy.attempt_timeout_seconds}s, "
        f"tentativas_de_transporte_max={policy.max_transport_attempts_per_completion}"
    )
    if policy.judge_override is not None:
        # Judge Transport Execution Policy V1 -- os dois números acima
        # são o DEFAULT (todas as operações exceto Judge); o Judge tem
        # política própria persistida no snapshot. Sem esta distinção a
        # linha diria que o default vale pro run inteiro.
        line += (
            " (padrão, exceto Judge); "
            f"juiz: timeout_por_tentativa={policy.judge_override.attempt_timeout_seconds}s, "
            "tentativas_de_transporte_max="
            f"{policy.judge_override.max_transport_attempts_per_completion}"
        )
    return line


# Status cujo `answer_text` já termina com as limitações registradas (ver o
# fechamento em app/editor/compose.py::_compose_answer) -- a mesma lista que
# a interface web usa (FinalAnswerView).
_STATUSES_WHOSE_TEXT_INCLUDES_LIMITATIONS = frozenset({"llm_planned", "deterministic_from_verdict"})


def _audit_pointer_lines(run_id: str, *, json_detail: str) -> list[str]:
    """Onde aprofundar -- descrevendo só o que cada destino realmente
    mostra: a saída humana de `audit` é um resumo; o `--json` traz a
    auditoria estruturada (`json_detail` diz o que interessa nela aqui)."""
    return [
        f"resumo da auditoria: dialeon audit {run_id}",
        f"{json_detail} (JSON): dialeon audit {run_id} --json",
    ]


def human_run_result(run: CompletedRunResponse) -> str:
    """Patch de segurança de terminal (FinalAnswer da CLI, round 2) --
    `run.final_answer.answer_text`/`limitations`/`editor_model` podem
    conter texto NÃO CONFIÁVEL: `answer_text` mistura template autorado
    pela aplicação com `Claim.text`/`ClaimAssessment.explanation`
    (via o renderizador determinístico de app/editor/compose.py) e
    excerpt de fonte, já achatados numa única string -- a proveniência
    de cada `\\n` dentro dela se perde antes de chegar aqui, então
    NENHUM `\\n` pode ser tratado como "seguro" só por já estar em
    `answer_text` (ver docstring de `terminal_safe_text`); `limitations`
    é texto livre do Judge; `editor_model` é a identidade de modelo da
    tentativa aceita -- mas NÃO é necessariamente reportada pelo
    provider: `editor_model_identity_source` (ver
    `_fmt_model_identity_source` abaixo) é quem distingue isso, nunca
    presuma "sempre reportada" só porque o valor existe (ver
    `ModelIdentitySource`, app/models/provider_models.py). Byte-fiéis no domínio/API/
    persistência/frontend (nunca mutados aqui: `run`/`run.final_answer`
    permanecem intocados, só as STRINGS impressas passam por
    `terminal_safe_text`, sempre em modo estrito). Este é o único
    ponto que de fato escreve num terminal real nesta função -- é aqui,
    e só aqui, que a neutralização acontece."""
    final_answer = run.final_answer
    shows_realization = (
        final_answer.linguistic_realization is not None
        and final_answer.linguistic_realization_presentation_eligible
    )
    shows_natural = not shows_realization and (
        final_answer.natural_answer is not None and final_answer.natural_answer_presentation_eligible
    )
    shows_primary = (
        not shows_realization and not shows_natural and final_answer.primary_answer is not None
    )
    # A seção separada de limitações só aparece quando o texto mostrado como
    # resposta NÃO as traz (mesma distinção da interface web), decidida pelos
    # campos estruturados -- nunca procurando o texto dentro da resposta:
    # - realização linguística: não traz -> mostra;
    # - resposta natural/principal: renderizadas da resposta principal, que
    #   carrega as próprias limitações; se forem as mesmas da resposta final
    #   (o contrato de coerência exige), já estão no texto -> omite;
    # - avaliação completa: `llm_planned`/`deterministic_from_verdict` sempre
    #   ecoam as limitações em `answer_text` (app/editor/compose.py) -> omite;
    #   os demais status (sem veredito, histórico) não -> mostra.
    if shows_realization:
        answer_carries_limitations = False
    elif shows_natural or shows_primary:
        answer_carries_limitations = (
            final_answer.primary_answer is not None
            and list(final_answer.primary_answer.limitations) == list(final_answer.limitations)
        )
    else:
        answer_carries_limitations = final_answer.status in _STATUSES_WHOSE_TEXT_INCLUDES_LIMITATIONS

    # ANSWER FIRST (mesma hierarquia da interface web): 1) a resposta, 2) as
    # limitações registradas, quando o texto da resposta ainda não as traz,
    # 3) um bloco curto de detalhes da execução, 4) como a resposta foi
    # montada (proveniência, resposta principal estruturada, avaliação
    # completa), 5) onde ver a auditoria (resumo e JSON). A escolha da resposta
    # é a MESMA ordem de fallback de sempre: realização linguística
    # (elegível) -> resposta natural (elegível) -> resposta principal ->
    # avaliação completa; o rótulo de cada seção continua dizendo qual foi.
    # Mesmo tratamento de segurança de terminal: texto de claim (verbatim em
    # todas) é não confiável.
    lines: list[str] = []
    if shows_realization:
        lines.append("resposta:")
        lines.append(terminal_safe_text(final_answer.linguistic_realization.rendered_text))
        lines.append("")
        lines.append(
            "nota: redação gerada por modelo a partir de afirmações selecionadas e "
            "avaliadas pelo Judge; não é verificação externa."
        )
    elif shows_natural:
        lines.append("resposta:")
        lines.append(terminal_safe_text(final_answer.natural_answer.rendered_text))
    elif shows_primary:
        lines.append("resposta principal:")
        lines.append(terminal_safe_text(final_answer.primary_answer.rendered_text))
    else:
        # Sem resposta principal: a avaliação completa JÁ é a resposta.
        lines.append("resposta (avaliação completa):")
        lines.append(terminal_safe_text(final_answer.answer_text))
    if final_answer.limitations and not answer_carries_limitations:
        lines.append("")
        lines.append("limitações:")
        lines.extend(f"  - {terminal_safe_text(item)}" for item in final_answer.limitations)

    lines.append("")
    lines.append("detalhes:")
    lines.append("status: concluída")
    lines.append(f"run_id: {run.id}")
    # Os providers PEDIDOS pra execução -- não prova quais responderam nem
    # quais contribuíram pra resposta (isso está na auditoria).
    lines.append(
        "providers solicitados: "
        + ", ".join(terminal_safe_text(p) for p in run.config.enabled_providers)
    )
    lines.append(
        f"custo estimado: {run.accounting.estimated_cost_usd:.6f} USD "
        f"(contabilidade completa: {'não' if run.accounting.has_unknown_accounting_components else 'sim'})"
    )
    lines.append(f"concluída em: {run.completed_at.isoformat()}")

    lines.append("")
    lines.append("como a resposta foi montada:")
    lines.append(f"status_da_resposta: {final_answer.status}")
    lines.append(f"editor_model: {terminal_safe_text(_fmt(final_answer.editor_model))}")
    lines.append(
        "editor_model_identity_source: "
        f"{_fmt_model_identity_source(final_answer.editor_model_identity_source)}"
    )
    lines.append(f"confiança_do_juiz: {_fmt(final_answer.judge_confidence)}")
    lines.append(_human_provider_execution_policy_line(run.provider_execution_policy))
    if (shows_realization or shows_natural) and final_answer.primary_answer is not None:
        lines.append("")
        lines.append("resposta principal (estruturada):")
        lines.append(terminal_safe_text(final_answer.primary_answer.rendered_text))
    if shows_realization or shows_natural or shows_primary:
        lines.append("")
        lines.append("avaliação completa:")
        lines.append(terminal_safe_text(final_answer.answer_text))

    lines.append("")
    lines.extend(_audit_pointer_lines(run.id, json_detail="dados estruturados"))
    return "\n".join(lines)


def human_quorum_failure(run: QuorumFailureRunResponse) -> str:
    # Sem resposta, o desfecho É o resultado: status e explicação primeiro,
    # depois o identificador/política e onde ver o motivo de cada modelo.
    return "\n".join(
        [
            "status: quórum insuficiente",
            "Nenhuma resposta final foi composta -- o quórum mínimo não foi atingido.",
            f"respostas bem-sucedidas: {run.successful_count}/{run.total_providers} "
            f"(mínimo pra retornar: {run.min_to_return})",
            "",
            "detalhes:",
            f"run_id: {run.id}",
            _human_provider_execution_policy_line(run.provider_execution_policy),
            "",
            *_audit_pointer_lines(run.id, json_detail="status e erro de cada resposta"),
        ]
    )


_RUN_SUMMARY_STATUS_LABELS: dict[str, str] = {
    "completed": "concluída",
    "insufficient_quorum": "quórum insuficiente",
    # `status="running"` persistido prova só "aceita e sem desfecho terminal
    # registrado" -- nunca que a execução está progredindo agora (mesma
    # semântica neutra do histórico do frontend).
    "running": "sem desfecho registrado",
    "failed": "falhou",
}


def human_run_summary_list(runs: list) -> str:
    if not runs:
        return "Nenhuma execução ainda."
    lines = []
    for run in runs:
        status = _RUN_SUMMARY_STATUS_LABELS.get(run.status, run.status)
        line = f"{run.id}  {status:24s}  {run.started_at.isoformat()}"
        # Direct Answer Execution V1: runs do Conselho continuam com a mesma linha.
        if run.kind == "direct":
            line += "  (resposta direta)"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Direct Answer Execution V1 -- saída humana de uma run DIRETA: a resposta
# primeiro, dita como resposta de UM provider (nunca "conselho", consenso,
# veredito ou verificação), depois detalhes curtos. Todo texto vindo do
# provider/usuário passa por `terminal_safe_text` (modo estrito).
# ---------------------------------------------------------------------------

_DIRECT_NOTE = (
    "nota: resposta de um único provider, sem as etapas do conselho; não é "
    "consenso, veredito do juiz nem verificação."
)


def _yes_no(value: bool) -> str:
    return "sim" if value else "não"


def _direct_identity_lines(response) -> list[str]:
    return [
        f"modelo solicitado: {terminal_safe_text(response.requested_model)}",
        f"modelo reportado: {terminal_safe_text(response.model)} "
        f"({_fmt_model_identity_source(response.model_identity_source)})",
    ]


def _direct_cost_line(response) -> str:
    """Custo da ÚNICA chamada: desconhecido nunca vira 0; uma tentativa
    anterior incerta torna o valor conhecido incompleto."""
    cost = "desconhecido" if response.cost_usd is None else f"{response.cost_usd:.6f} USD"
    complete = response.cost_usd is not None and not response.had_uncertain_prior_attempts
    return f"custo estimado: {cost} (contabilidade completa: {'sim' if complete else 'não'})"


# M4 (revisão adversarial) -- o Dialeon só observa o SEU lado da chamada:
# nenhum status HTTP, tipo de erro, timeout ou texto vazio prova o que o
# modelo produziu (ou não) do lado do provider. Sem resposta utilizável
# registrada, o texto diz só o que o Dialeon sabe, e depende apenas de ONDE a
# run parou -- nunca do tipo de erro:
# - "provider": a chamada terminou e está registrada, sem resposta
#   utilizável -> nenhuma resposta utilizável foi recebida;
# - execução/gravação do desfecho: nenhuma resposta foi registrada (o
#   provider pode ter respondido).
def _direct_no_answer_lines(run: DirectFailedRunResponse) -> list[str]:
    message = terminal_safe_text(_fmt(run.message))
    if run.failure_stage == "provider":
        return [f"nenhuma resposta utilizável foi recebida: {message}"]
    if run.failure_stage == "terminal_persistence":
        caveat = (
            "o provider pode ter produzido uma resposta, mas o resultado não pôde ser gravado."
        )
    else:
        caveat = "não é possível confirmar se o provider chegou a produzir uma resposta."
    return [f"nenhuma resposta foi registrada: {message}", caveat]


def human_direct_run(
    run: DirectCompletedRunResponse | DirectFailedRunResponse | DirectRunningRunResponse,
) -> str:
    provider = terminal_safe_text(run.config.provider)
    pointers = _audit_pointer_lines(run.id, json_detail="dados estruturados")
    if isinstance(run, DirectCompletedRunResponse):
        lines = [
            f"resposta direta ({provider}):",
            terminal_safe_text(run.answer),
            "",
            _DIRECT_NOTE,
            "",
            "detalhes:",
            "tipo: resposta direta",
            "status: concluída",
            f"run_id: {run.id}",
            f"provider: {provider}",
            *_direct_identity_lines(run.response),
            _direct_cost_line(run.response),
            f"concluída em: {run.completed_at.isoformat()}",
            "",
            *pointers,
        ]
        return "\n".join(lines)
    if isinstance(run, DirectFailedRunResponse):
        lines = ["status: falhou", *_direct_no_answer_lines(run)]
        if run.response is not None and run.response.error is not None:
            lines.append(
                f"erro do provider: {run.response.error.type.value} -- "
                f"{terminal_safe_text(run.response.error.message)}"
            )
        lines += [
            "",
            "detalhes:",
            "tipo: resposta direta",
            f"run_id: {run.id}",
            f"provider: {provider}",
            f"modelo solicitado: {terminal_safe_text(run.config.requested_model)}",
            f"estágio da falha: {_fmt(run.failure_stage)}",
        ]
        if run.response is not None:
            lines.append(f"tentativas_de_transporte: {run.response.attempts}")
            lines.append(
                "tentativa_anterior_incerta: "
                f"{_yes_no(run.response.had_uncertain_prior_attempts)}"
            )
        if run.response is not None:
            lines.append(_direct_cost_line(run.response))
        if run.failed_at is not None:
            lines.append(f"falhou em: {run.failed_at.isoformat()}")
        lines += ["", *pointers]
        return "\n".join(lines)
    return "\n".join(
        [
            "status: sem desfecho registrado",
            "Nenhum desfecho terminal foi registrado -- a execução pode ainda "
            "estar ativa, ou o processo pode ter sido interrompido antes "
            "de terminar; os dois casos são indistinguíveis a partir deste registro.",
            "",
            "detalhes:",
            "tipo: resposta direta",
            f"run_id: {run.id}",
            f"provider: {provider}",
            f"modelo solicitado: {terminal_safe_text(run.config.requested_model)}",
            f"iniciada em: {run.started_at.isoformat()}",
        ]
    )


def human_direct_audit(
    run: DirectCompletedRunResponse | DirectFailedRunResponse | DirectRunningRunResponse,
) -> str:
    """Resumo técnico da única chamada de uma run direta -- só fatos que
    existem pra ela (nenhuma seção do conselho)."""
    status = {"completed": "concluída", "failed": "falhou", "running": "sem desfecho registrado"}
    lines = [
        f"status: {status[run.status]}",
        f"run_id: {run.id}",
        "tipo: resposta direta",
        f"provider: {terminal_safe_text(run.config.provider)}",
        f"modelo solicitado (aceite): {terminal_safe_text(run.config.requested_model)}",
        f"max_output_tokens: {run.config.max_output_tokens}",
    ]
    response = getattr(run, "response", None)
    if response is not None:
        usage = response.usage
        lines += [
            f"modelo reportado: {terminal_safe_text(response.model)} "
            f"({_fmt_model_identity_source(response.model_identity_source)})",
            f"status_da_chamada: {response.status}",
            f"tokens_de_entrada: {_fmt(usage.input_tokens if usage else None)}",
            f"tokens_de_saída: {_fmt(usage.output_tokens if usage else None)}",
            f"custo_estimado_usd: {_fmt(response.cost_usd)}",
            "precificação: "
            + (
                f"{terminal_safe_text(response.pricing_provenance.source_id)} "
                f"({terminal_safe_text(response.pricing_provenance.tier)})"
                if response.pricing_provenance is not None
                else "desconhecida"
            ),
            f"tentativas_de_transporte: {response.attempts}",
            f"tentativa_anterior_incerta: {_yes_no(response.had_uncertain_prior_attempts)}",
            f"latência_ms: {response.latency_ms}",
            f"motivo_de_parada_do_provider: {terminal_safe_text(_fmt(response.provider_finish_reason))}",
        ]
        if response.request_provenance is not None:
            lines += [
                f"contrato_do_request: {response.request_provenance.contract_version}",
                f"digest_do_request: {response.request_provenance.request_digest}",
            ]
        if response.error is not None:
            lines.append(
                f"erro_do_provider: {response.error.type.value} -- "
                f"{terminal_safe_text(response.error.message)}"
            )
    if isinstance(run, DirectFailedRunResponse):
        lines.append(f"estágio_da_falha: {_fmt(run.failure_stage)}")
        lines.append(f"classificação: {terminal_safe_text(_fmt(run.failure_reason))}")
    lines.append(_human_provider_execution_policy_line(run.provider_execution_policy))
    return "\n".join(lines)


def human_accepted_run(run: RunningRunResponse | FailedRunResponse) -> str:
    """T02.4 -- `run.status in ("running", "failed")`: nunca tenta
    imprimir campos de resultado/quórum que não existem pra esses dois
    estados (nenhuma persistência incremental existe -- ver docstring de
    RunningRunResponse/FailedRunResponse)."""
    if run.status == "running":
        return "\n".join(
            [
                "status: sem desfecho registrado",
                f"run_id: {run.id}",
                f"iniciada em: {run.started_at.isoformat()}",
                "Nenhum desfecho terminal foi registrado -- a execução pode ainda "
                "estar ativa, ou o processo pode ter sido interrompido antes "
                "de terminar; os dois casos são indistinguíveis a partir deste registro.",
                _human_provider_execution_policy_line(run.provider_execution_policy),
            ]
        )
    return "\n".join(
        [
            "status: falhou",
            f"run_id: {run.id}",
            f"iniciada em: {run.started_at.isoformat()}",
            f"falhou em: {run.failed_at.isoformat()}",
            f"classificação: {terminal_safe_text(run.failure_reason)}",
            terminal_safe_text(run.message),
            _human_provider_execution_policy_line(run.provider_execution_policy),
        ]
    )


def human_providers_list(providers: list[str]) -> str:
    if not providers:
        return "Nenhum provider disponível."
    return "\n".join(providers)


_SOURCE_ANALYSIS_SKIPPED_LABELS: dict[str, str] = {
    "no_claims_to_analyze": "não havia claims para analisar",
    "budget_exhausted_before_source_analysis": "o orçamento se esgotou antes da análise de fonte",
    "source_analysis_transport_failed": "houve uma falha de comunicação durante a análise de fonte",
    "source_analysis_output_invalid": "a saída da análise de fonte não pôde ser interpretada corretamente",
}

# Etapa 16 (patch de visibilidade humana) -- rótulos de RELAÇÃO com a
# fonte, nunca de verdade externa: "apoia"/"contradiz" descrevem o que a
# análise encontrou entre a claim e o texto da fonte fornecida, nunca se
# a claim é verdadeira/falsa/provada (ver app/source_analysis/models.py
# -- SOURCE RELATION != TRUTH VERDICT, mesma disciplina de
# app/editor/compose.py::_VERDICT_LABELS pro Judge).
_SOURCE_RELATION_LABELS: dict[str, str] = {
    "supports": "segundo a análise, a fonte apoia esta claim",
    "contradicts": "segundo a análise, a fonte contradiz esta claim",
    "unresolved": "a análise não conseguiu determinar a relação com a fonte",
}

_SOURCE_REJECTED_REASON_LABELS: dict[str, str] = {
    "omitted_by_model": "a análise não endereçou esta claim",
    "duplicate_claim_id": "a análise devolveu mais de uma entrada para a mesma claim (descartada)",
    "invalid_entry": "a entrada da análise não pôde ser validada",
}


def _human_source_analysis_lines(source_analysis: Any) -> list[str]:
    """Etapa 16 (patch de visibilidade humana) -- `source_analysis`
    permanece AUDIT-ONLY: estas linhas só COMUNICAM o que já foi
    computado/persistido, nunca influenciam veredito/resposta final.
    Compacto por design (mesma disciplina de `human_run_audit`) -- lista
    cada relação/entrada rejeitada uma vez, sem reproduzir o JSON
    inteiro (`attempts`, `excerpt_start`/`excerpt_end`, `raw_entry`
    seguem só em `--json`).

    Patch de segurança de terminal -- `relation.excerpt` é o ÚNICO
    campo de texto livre NÃO CONFIÁVEL impresso neste bloco (fatiado do
    `source_text` fornecido pelo usuário/fonte externa) e por isso o
    único que passa por `terminal_safe_text()` (app/text_safety.py)
    antes de virar linha de terminal. `relation.claim_id`/
    `entry.claim_id` NUNCA são texto
    livre -- ou apontam pra um id de claim real já conhecido pela
    aplicação (`uuid4()`, nunca ecoado de LLM) ou são `None`/
    "desconhecida" (ver app/source_analysis/models.py:
    `RejectedSourceEntry.claim_id` nunca fabrica uma FK falsa a partir
    de texto suspeito). `relation.relation`/`entry.reason`/
    `source_analysis.skipped_reason` são sempre um `Literal[...]`
    Pydantic de valores fixos conhecidos, nunca string arbitrária --
    não precisam de sanitização, só os rótulos fixos já usados acima."""
    if source_analysis is None:
        return ["análise_de_fonte: nenhuma fonte foi fornecida nesta execução"]

    if source_analysis.skipped_reason is not None:
        reason_label = _SOURCE_ANALYSIS_SKIPPED_LABELS.get(
            source_analysis.skipped_reason, source_analysis.skipped_reason
        )
        return [f"análise_de_fonte: não concluída -- {reason_label}"]

    relations = [r for r in source_analysis.claim_results if r.kind == "relation"]
    rejected = [r for r in source_analysis.claim_results if r.kind == "rejected"]
    supports = sum(1 for r in relations if r.relation == "supports")
    contradicts = sum(1 for r in relations if r.relation == "contradicts")
    unresolved = sum(1 for r in relations if r.relation == "unresolved")

    lines = [
        f"análise_de_fonte: concluída -- relações: {len(relations)} "
        f"(apoia: {supports}, contradiz: {contradicts}, não determinada: {unresolved}); "
        f"entradas rejeitadas: {len(rejected)}"
    ]
    for relation in relations:
        label = _SOURCE_RELATION_LABELS.get(relation.relation, relation.relation)
        lines.append(f"  - claim {relation.claim_id}: {label}")
        if relation.excerpt is not None:
            lines.append(f'      trecho da fonte: "{terminal_safe_text(relation.excerpt)}"')
    for entry in rejected:
        reason_label = _SOURCE_REJECTED_REASON_LABELS.get(entry.reason, entry.reason)
        claim_ref = entry.claim_id if entry.claim_id is not None else "desconhecida"
        lines.append(f"  - entrada rejeitada (claim {claim_ref}): {reason_label}")
    return lines


# Cross-Channel Reconciliation V1 -- rótulos de RELACIONAMENTO ENTRE
# CANAIS, mesma disciplina de `_SOURCE_RELATION_LABELS` acima: nunca
# verdade, nunca autoridade. Ver app/reconciliation/models.py.
_CHANNEL_RELATIONSHIP_LABELS: dict[str, str] = {
    "directionally_aligned": "alinhado com o debate",
    "in_tension": "em tensão com o debate",
    "source_adds_direction": "acrescenta direção onde o debate não decidiu",
    "source_unresolved": "fonte também indeterminada",
    # Repair #5 (revisão adversarial) -- "mixed" cobre qualquer
    # combinação incoerente de entradas do lado da fonte (não só
    # supports+contradicts), e a anomalia é da ANÁLISE de fonte
    # (processo), nunca do texto da fonte -- nunca "conflito interno na
    # fonte" (atribuiria conflito ao texto/à fonte em si).
    "source_channel_conflict": "análise da fonte não redutível a um estado único",
    "not_comparable": "não comparável",
}


def _human_reconciliation_lines(reconciliation: Any) -> list[str]:
    """Cross-Channel Reconciliation V1 -- resumo compacto (mesma
    disciplina de `_human_source_analysis_lines` acima). `None` é o
    valor HONESTO pra execuções persistidas antes deste recurso existir
    -- NUNCA confundido com nenhum `channel_relationship` real (nem
    "not_comparable", que é um FATO computado, não ausência de
    computação)."""
    if reconciliation is None:
        return [
            "reconciliação_fonte_julgamento: não registrada "
            "(execução anterior a este recurso)"
        ]

    counts: dict[str, int] = {}
    for outcome in reconciliation.claim_outcomes:
        key = outcome.channel_relationship
        counts[key] = counts.get(key, 0) + 1
    summary = ", ".join(
        f"{_CHANNEL_RELATIONSHIP_LABELS.get(key, key)}: {count}" for key, count in counts.items()
    )
    status_line = f"reconciliação_fonte_julgamento: {reconciliation.status}"
    return [f"{status_line} -- {summary}" if summary else status_line]


def human_run_audit(audit: Any) -> str:
    """Resumo compacto -- não uma réplica exaustiva de toda a árvore de
    auditoria (isso é o que `--json` é para). Mostra só o que já é
    imediatamente útil de ler no terminal; o resto está no JSON."""
    if audit.status == "completed":
        if audit.final_answer.natural_answer is None:
            natural_answer_status = "resposta_natural: ausente" + (
                f" ({audit.editor_outcome.natural_answer_fallback_reason})"
                if audit.editor_outcome.natural_answer_fallback_reason
                else ""
            )
        elif audit.final_answer.natural_answer_presentation_eligible:
            natural_answer_status = "resposta_natural: presente"
        else:
            natural_answer_status = (
                "resposta_natural: presente (preservada; não preferida pela política "
                "de apresentação atual)"
            )
        if audit.final_answer.linguistic_realization is None:
            realization_status = "realização_linguística: ausente" + (
                f" ({audit.editor_outcome.linguistic_realization_fallback_reason})"
                if audit.editor_outcome.linguistic_realization_fallback_reason
                else ""
            )
        elif audit.final_answer.linguistic_realization_presentation_eligible:
            realization_status = "realização_linguística: presente"
        else:
            realization_status = (
                "realização_linguística: presente (preservada; não preferida pela política "
                "de apresentação atual)"
            )
        lines = [
            "status: concluída",
            f"run_id: {audit.id}",
            f"claims: {len(audit.claims)}",
            f"claim_processing_attempts: {len(audit.claim_processing_attempts)}",
            f"judge_verdict: {'presente' if audit.judge_verdict is not None else 'ausente'}",
            f"judge_attempts: {len(audit.judge_attempts)}",
            f"editor_attempts: {len(audit.editor_attempts)}",
            (
                "resposta_principal: presente"
                if audit.final_answer.primary_answer is not None
                else "resposta_principal: ausente"
                + (
                    f" ({audit.editor_outcome.primary_answer_fallback_reason})"
                    if audit.editor_outcome.primary_answer_fallback_reason
                    else ""
                )
            ),
            natural_answer_status,
            realization_status,
            f"resposta_final_status: {audit.final_answer.status}",
            f"custo_estimado_usd: {audit.accounting.estimated_cost_usd:.6f}",
            f"contabilidade_completa: {'não' if audit.accounting.has_unknown_accounting_components else 'sim'}",
        ]
        lines.extend(_human_source_analysis_lines(audit.source_analysis))
        lines.extend(_human_reconciliation_lines(audit.reconciliation))
        lines.append(_human_provider_execution_policy_line(audit.provider_execution_policy))
        return "\n".join(lines)

    return "\n".join(
        [
            "status: quórum insuficiente",
            f"run_id: {audit.id}",
            f"respostas bem-sucedidas: {audit.successful_count}/{audit.total_providers} "
            f"(mínimo pra retornar: {audit.min_to_return})",
            _human_provider_execution_policy_line(audit.provider_execution_policy),
        ]
    )
