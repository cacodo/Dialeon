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
    lines = [
        "status: concluída",
        f"run_id: {run.id}",
        f"status_da_resposta: {run.final_answer.status}",
        f"editor_model: {terminal_safe_text(_fmt(run.final_answer.editor_model))}",
        f"editor_model_identity_source: "
        f"{_fmt_model_identity_source(run.final_answer.editor_model_identity_source)}",
        f"confiança_do_juiz: {_fmt(run.final_answer.judge_confidence)}",
        "",
        terminal_safe_text(run.final_answer.answer_text),
    ]
    if run.final_answer.limitations:
        lines.append("")
        lines.append("limitações:")
        lines.extend(f"  - {terminal_safe_text(item)}" for item in run.final_answer.limitations)
    lines.append("")
    lines.append(
        f"custo estimado: {run.accounting.estimated_cost_usd:.6f} USD "
        f"(contabilidade completa: {'não' if run.accounting.has_unknown_accounting_components else 'sim'})"
    )
    lines.append(_human_provider_execution_policy_line(run.provider_execution_policy))
    return "\n".join(lines)


def human_quorum_failure(run: QuorumFailureRunResponse) -> str:
    return "\n".join(
        [
            "status: quórum insuficiente",
            f"run_id: {run.id}",
            f"respostas bem-sucedidas: {run.successful_count}/{run.total_providers} "
            f"(mínimo pra retornar: {run.min_to_return})",
            "Nenhuma resposta final foi composta -- o quórum mínimo não foi atingido.",
            _human_provider_execution_policy_line(run.provider_execution_policy),
        ]
    )


_RUN_SUMMARY_STATUS_LABELS: dict[str, str] = {
    "completed": "concluída",
    "insufficient_quorum": "quórum insuficiente",
    "running": "em andamento",
    "failed": "falhou",
}


def human_run_summary_list(runs: list) -> str:
    if not runs:
        return "Nenhuma execução ainda."
    lines = []
    for run in runs:
        status = _RUN_SUMMARY_STATUS_LABELS.get(run.status, run.status)
        lines.append(f"{run.id}  {status:20s}  {run.started_at.isoformat()}")
    return "\n".join(lines)


def human_accepted_run(run: RunningRunResponse | FailedRunResponse) -> str:
    """T02.4 -- `run.status in ("running", "failed")`: nunca tenta
    imprimir campos de resultado/quórum que não existem pra esses dois
    estados (nenhuma persistência incremental existe -- ver docstring de
    RunningRunResponse/FailedRunResponse)."""
    if run.status == "running":
        return "\n".join(
            [
                "status: em andamento",
                f"run_id: {run.id}",
                f"iniciada em: {run.started_at.isoformat()}",
                "Nenhum desfecho terminal foi registrado ainda -- a execução pode "
                "estar em andamento, ou o processo pode ter sido interrompido antes "
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
        lines = [
            "status: concluída",
            f"run_id: {audit.id}",
            f"claims: {len(audit.claims)}",
            f"claim_processing_attempts: {len(audit.claim_processing_attempts)}",
            f"judge_verdict: {'presente' if audit.judge_verdict is not None else 'ausente'}",
            f"judge_attempts: {len(audit.judge_attempts)}",
            f"editor_attempts: {len(audit.editor_attempts)}",
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
