"""
`FinalAnswer`/`EditorResult` — Etapa 7 (status `llm_planned` adicionado
na Etapa 17B, ver docstring de `FinalAnswer.status` abaixo).

Separação deliberada: `FinalAnswer` é o artefato final consumível/exibível
— sem telemetria operacional. `EditorResult` é o resultado operacional da
etapa — o que ela FEZ (tentativas, motivo de fallback, budget), sempre
envolvendo uma `FinalAnswer`.

Diferença explícita em relação a `JudgeResult` (não é cópia cega do
padrão): `JudgeResult.verdict` pode ser `None` — o Judge genuinamente pode
não decidir nada. `EditorResult.final_answer` NUNCA é `None` — mesmo sem
veredito do Judge, ainda existe algo honesto e útil a devolver (as claims
não avaliadas, ou uma mensagem clara do motivo). Copiar o padrão `X | None`
aqui replicaria uma forma que não reflete a realidade desta etapa.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.editor.answer_blocks import (
    ANSWER_SECTION_HEADING_ORDER,
    ANSWER_SECTION_HEADING_VALID_VERDICT_LABELS,
    AnswerBlock,
    AnswerClaimSectionBlock,
    AnswerParagraphBlock,
)
from app.editor.attempt import EditorAttempt
from app.models.provider_models import ModelIdentitySource
from app.orchestrator.budget import sum_usage_and_cost

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FinalAnswer(BaseModel):
    """O artefato final. Sem `EditorAttempt`, sem usage/cost, sem
    `cumulative_budget_exceeded`, sem `fallback_reason` — isso tudo vive
    em `EditorResult`, não aqui. `FinalAnswer` é o que seria mostrado ao
    usuário; `EditorResult` é o que a etapa fez pra chegar lá."""

    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    answer_text: str = Field(min_length=1)
    # UI Slice 3 (Structured Final Answer) -- representação ADITIVA e
    # opcional de `answer_text`, produzida pela MESMA chamada de
    # composição (`_compose_answer`, app/editor/compose.py) que produz
    # `answer_text` -- nunca uma segunda derivação independente, nunca
    # por parsing de `answer_text` (ver docstring de
    # app/editor/answer_blocks.py). `None` em três casos, todos honestos:
    # (1) run persistido antes desta coluna existir; (2)
    # `status="deterministic_no_verdict"` (decisão de escopo explícita,
    # ver docstring de app/editor/answer_blocks.py); (3)
    # `status="llm_composed"` (histórico, nenhum código novo produz esse
    # status). O frontend trata `None` sempre da mesma forma -- fallback
    # pra divisão de parágrafos sobre `answer_text`, nunca reconstrói
    # estrutura retroativamente.
    #
    # Repair (revisão adversarial, achado 2) -- `tuple[...]`, nunca
    # `list[...]`: mesma disciplina de `AnswerClaimSectionBlock.items`
    # (ver app/editor/answer_blocks.py) -- `frozen=True` sozinho nunca
    # impediria `answer_blocks.append(...)` numa lista; a tupla, e a
    # validação Pydantic que sempre a reconstrói a partir do que for
    # passado (lista mutável do chamador, ou lista reconstruída de JSON
    # persistido), fecham essa lacuna sem cópia defensiva manual.
    answer_blocks: tuple[AnswerBlock, ...] | None = None
    # Sempre cópia VERBATIM de JudgeVerdict.debate_limitations quando há
    # veredito — o Editor nunca reescreve/resume/escolhe limitações
    # (ver app/editor/compose.py). Sem veredito, é texto app-autorado
    # explicando por que a avaliação não pôde ser concluída.
    limitations: list[str] = Field(default_factory=list)
    # Etapa 17B -- "llm_planned" é o status produzido por runs NOVOS
    # quando uma LLM escolhe com sucesso um EditorPlan (opening_style/
    # closing_style) e a aplicação renderiza toda a prosa
    # deterministicamente a partir dele. "llm_composed" é mantido só
    # para LEITURA de runs históricos anteriores à Etapa 17B (quando a
    # LLM ainda escrevia a prosa livre em si, ver app/editor/schemas.py)
    # -- nenhum código novo produz esse valor; nenhuma run antiga é
    # reescrita/migrada. Os dois têm a MESMA regra de coerência abaixo
    # (exigem editor_model/based_on_verdict_id/judge_confidence) porque,
    # em ambos, uma tentativa de LLM real foi aceita e um `editor_model`
    # real existe -- a diferença entre eles é só QUEM escreveu o texto
    # final (a LLM, historicamente; a aplicação, desde a Etapa 17B),
    # nunca a presença/ausência de provenance.
    status: Literal[
        "llm_planned", "llm_composed", "deterministic_from_verdict", "deterministic_no_verdict"
    ]
    # Modelo REAL da tentativa aceita — nunca presumido a partir de
    # config. None em qualquer caminho determinístico.
    editor_model: str | None = None
    # Provenance de editor_model -- ver ModelResponse.model_identity_source
    # (app/models/domain.py) pra semântica completa. NUNCA `None` numa
    # composição NOVA quando editor_model está preenchido (sempre copiado
    # de accepted_response.model_identity_source, ver app/editor/compose.py)
    # -- mas DELIBERADAMENTE não acoplado por validator a editor_model:
    # uma linha histórica persistida antes desta coluna existir pode
    # legitimamente ter editor_model preenchido com
    # editor_model_identity_source=None (ver
    # `_upgrade_legacy_model_identity_source`, app/storage/database.py) --
    # nunca retroativamente inferido. `None` em todo caminho
    # determinístico (mesmo motivo de editor_model ser None ali).
    editor_model_identity_source: ModelIdentitySource | None = None
    based_on_verdict_id: str | None = None
    # Eco de JudgeVerdict.confidence — provenance/auditoria da resposta,
    # NUNCA algo que answer_text deva citar como percentual cru (Etapa 7,
    # "consenso ≠ verdade" — confidence calibra tom, não é probabilidade
    # factual calibrada).
    judge_confidence: float | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _status_field_coherence(self) -> FinalAnswer:
        if self.status in ("llm_planned", "llm_composed"):
            if self.editor_model is None or self.based_on_verdict_id is None or self.judge_confidence is None:
                raise ValueError(
                    f"status={self.status!r} exige editor_model, based_on_verdict_id e "
                    "judge_confidence preenchidos"
                )
        elif self.status == "deterministic_from_verdict":
            if self.editor_model is not None:
                raise ValueError("status='deterministic_from_verdict' não deve ter editor_model")
            if self.based_on_verdict_id is None or self.judge_confidence is None:
                raise ValueError(
                    "status='deterministic_from_verdict' exige based_on_verdict_id e "
                    "judge_confidence preenchidos"
                )
        else:  # deterministic_no_verdict
            if (
                self.editor_model is not None
                or self.based_on_verdict_id is not None
                or self.judge_confidence is not None
            ):
                raise ValueError(
                    "status='deterministic_no_verdict' não deve ter editor_model/"
                    "based_on_verdict_id/judge_confidence"
                )
        return self

    @model_validator(mode="after")
    def _answer_blocks_forbidden_for_unstructured_statuses(self) -> FinalAnswer:
        """Repair (fechamento do contrato estruturado) -- fecha o lado
        status->estrutura que faltava: `status="llm_composed"` (histórico,
        nenhum código novo produz esse valor -- a LLM Editor escrevia
        prosa livre, nunca blocos tipados) e `status="deterministic_no_verdict"`
        (decisão de escopo explícita, ver docstring de
        app/editor/answer_blocks.py) NUNCA devem carregar `answer_blocks`
        não-None -- um valor persistido/construído assim seria uma
        estrutura sem NENHUM compositor real que a tenha produzido.

        `status in ("llm_planned", "deterministic_from_verdict")` continua
        SEM exigência aqui de propósito: `answer_blocks=None` continua
        legítimo pra esses dois (run persistido antes da coluna existir,
        ver docstring de `answer_blocks` acima) -- este validador só
        fecha o lado "nunca deveria ter", nunca força "sempre deve ter"."""
        if self.answer_blocks is not None and self.status in (
            "llm_composed",
            "deterministic_no_verdict",
        ):
            raise ValueError(
                f"status={self.status!r} nunca deve ter answer_blocks -- este status "
                "é intencionalmente não-estruturado"
            )
        return self

    @model_validator(mode="after")
    def _answer_blocks_follow_the_fixed_document_shape(self) -> FinalAnswer:
        """Repair (revisão adversarial, achado 3) -- fecha o contrato do
        documento estruturado: um `answer_blocks` não-nulo só pode
        representar a ÚNICA forma que `_compose_answer`
        (app/editor/compose.py) realmente produz hoje -- nunca uma
        estrutura arbitrária que `AnswerBlock`, sozinho (união
        discriminada + tuplas não-vazias), não bastaria pra rejeitar:

        1. nunca vazio (um `[]` explícito é indistinguível de "nenhuma
           estrutura" pro frontend -- ver Finding #4 -- então nunca deve
           existir como valor persistido/construído);
        2. o primeiro bloco é sempre o parágrafo de abertura;
        3. pelo menos uma seção de claims segue a abertura (Repair,
           fechamento do contrato estruturado -- um documento
           "só-abertura", sem NENHUMA seção, nunca é produzido por
           `_compose_answer`: `verdict.claim_assessments` sempre tem pelo
           menos uma avaliação em todo caminho que chega a construir
           `answer_blocks` não-None, ver app/editor/compose.py -- então
           um `answer_blocks` de comprimento 1 é tão estruturalmente
           impossível quanto vazio, e fechado pela mesma razão);
        4. todo bloco subsequente é uma seção de claims, cujo `heading`
           está em `ANSWER_SECTION_HEADING_ORDER` (fechado, achado 3) e
           nunca se repete (uma seção forjada/duplicada é rejeitada);
        5. as seções aparecem na mesma ordem relativa de
           `ANSWER_SECTION_HEADING_ORDER` (bucket A antes de B) --
           nunca invertida;
        6. Repair (fechamento do contrato estruturado) -- COERÊNCIA
           seção<->veredito: todo `AnswerClaimItem.verdict_label` dentro
           de uma seção só pode ser um dos rótulos que
           `ANSWER_SECTION_HEADING_VALID_VERDICT_LABELS[heading]`
           permite pra aquele heading -- espelha a partição FIXA de
           `_VERDICT_TO_BUCKET` (app/editor/compose.py): um rótulo de
           bucket B (ex. "rejeitada pelo juiz...") dentro da seção de
           bucket A ("Conclusões sustentadas...") é uma combinação
           semanticamente impossível, mesmo que cada campo, isolado, seja
           um Literal válido -- nunca aceita como documento genuíno.

        Deliberadamente NÃO um schema/framework de documento genérico --
        só a validação concreta desta forma específica, única e fixa."""
        if self.answer_blocks is None:
            return self
        if len(self.answer_blocks) == 0:
            raise ValueError("answer_blocks, quando não None, nunca pode ser vazio")
        if not isinstance(self.answer_blocks[0], AnswerParagraphBlock):
            raise ValueError("answer_blocks[0] deve ser sempre o parágrafo de abertura")
        section_blocks = self.answer_blocks[1:]
        if len(section_blocks) == 0:
            raise ValueError(
                "answer_blocks, quando não None, deve conter pelo menos uma seção de "
                "claims além do parágrafo de abertura"
            )
        seen_headings: set[str] = set()
        for block in section_blocks:
            if not isinstance(block, AnswerClaimSectionBlock):
                raise ValueError("blocos após a abertura só podem ser seções de claims")
            if block.heading in seen_headings:
                raise ValueError(f"heading de seção duplicado: {block.heading!r}")
            seen_headings.add(block.heading)
            valid_labels = ANSWER_SECTION_HEADING_VALID_VERDICT_LABELS[block.heading]
            for item in block.items:
                if item.verdict_label not in valid_labels:
                    raise ValueError(
                        f"verdict_label={item.verdict_label!r} não é válido pra seção "
                        f"heading={block.heading!r} -- combinação seção/veredito "
                        "semanticamente impossível"
                    )
        headings_in_order = [block.heading for block in section_blocks]
        expected_order = [h for h in ANSWER_SECTION_HEADING_ORDER if h in seen_headings]
        if headings_in_order != expected_order:
            raise ValueError("seções de answer_blocks fora da ordem fixa (bucket A antes de B)")
        return self


class EditorResult(BaseModel):
    """Resultado operacional da etapa. `final_answer` é obrigatório e
    NUNCA `Optional` — ver docstring do módulo pra por que isso é
    deliberadamente diferente de `JudgeResult.verdict`.

    `editor_input_tokens`/`editor_output_tokens`/`editor_cost_usd` são
    `computed_field` — mesma disciplina de `JudgeResult.judge_input_tokens`
    (Etapa 6): somam só `self.attempts` (sem dependência externa), sempre
    deriváveis, nunca fonte independente. Patch pós-Etapa-7 (Etapa 8):
    antes desses campos, `Editor.compose()` já calculava essa soma
    internamente (via `sum_usage_and_cost`) só pra alimentar o gate de
    budget, e descartava o resultado — essa lacuna entre `JudgeResult` (já
    tinha a conveniência) e `EditorResult` (não tinha) é o que este patch
    fecha, sem mover accounting pra fora desta classe."""

    model_config = _CONFIG

    final_answer: FinalAnswer
    attempts: list[EditorAttempt] = Field(default_factory=list)
    fallback_reason: (
        Literal[
            "budget_exhausted_before_editor",
            "editor_transport_failed",
            "editor_output_invalid",
            "judge_verdict_unavailable",
        ]
        | None
    ) = None
    # Eco do provider CONFIGURADO — presente em todo resultado, mesmo nos
    # caminhos onde ele nunca chegou a ser validado/usado (ver ordem de
    # validação em app/editor/compose.py). Não implica que foi checado.
    editor_provider: str = Field(min_length=1)
    cumulative_budget_exceeded: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def editor_input_tokens(self) -> int:
        return sum((a.usage.input_tokens or 0) for a in self.attempts if a.usage is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def editor_output_tokens(self) -> int:
        return sum((a.usage.output_tokens or 0) for a in self.attempts if a.usage is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def editor_cost_usd(self) -> float:
        return sum((a.cost_usd or 0.0) for a in self.attempts)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        """`attempts=[]` (fallback por budget/sem veredito) -> False — sem
        chamada, sem desconhecido. `True` se alguma tentativa real teve
        `cost_usd=None` OU `had_uncertain_prior_attempts=True` (Etapa
        17A, B3). Reusa `sum_usage_and_cost` -- mesma regra completa já
        usada em `SourceAnalysisResult`, nunca uma segunda definição
        divergente."""
        return sum_usage_and_cost(self.attempts)[3]

    @model_validator(mode="after")
    def _status_reason_and_attempts_are_coherent(self) -> EditorResult:
        status = self.final_answer.status
        if status in ("llm_planned", "llm_composed"):
            if self.fallback_reason is not None:
                raise ValueError(f"status={status!r} não deve ter fallback_reason")
            if not self.attempts or self.attempts[-1].parse_status != "accepted":
                raise ValueError(
                    f"status={status!r} exige attempts não-vazio terminando em uma "
                    "tentativa aceita"
                )
        elif status == "deterministic_from_verdict":
            if self.fallback_reason not in (
                "budget_exhausted_before_editor",
                "editor_transport_failed",
                "editor_output_invalid",
            ):
                raise ValueError(
                    "status='deterministic_from_verdict' exige fallback_reason em "
                    "{'budget_exhausted_before_editor','editor_transport_failed',"
                    "'editor_output_invalid'}"
                )
            if self.fallback_reason == "budget_exhausted_before_editor" and self.attempts:
                raise ValueError(
                    "fallback_reason='budget_exhausted_before_editor' não deveria ter "
                    "attempts — o gate impediu a chamada antes de qualquer tentativa"
                )
            if (
                self.fallback_reason in ("editor_transport_failed", "editor_output_invalid")
                and not self.attempts
            ):
                raise ValueError(
                    f"fallback_reason={self.fallback_reason!r} exige attempts preservados "
                    "da(s) tentativa(s) real(is) que falhou(aram)"
                )
        else:  # deterministic_no_verdict
            if self.fallback_reason != "judge_verdict_unavailable":
                raise ValueError(
                    "status='deterministic_no_verdict' exige "
                    "fallback_reason='judge_verdict_unavailable'"
                )
            if self.attempts:
                raise ValueError(
                    "status='deterministic_no_verdict' nunca deveria ter attempts — sem "
                    "veredito, o Editor LLM nunca é sequer considerado"
                )
        return self

    @model_validator(mode="after")
    def _all_attempts_use_declared_provider(self) -> EditorResult:
        for attempt in self.attempts:
            if attempt.provider != self.editor_provider:
                raise ValueError(
                    f"EditorAttempt.provider={attempt.provider!r} diverge de "
                    f"editor_provider={self.editor_provider!r} — nenhuma troca "
                    "silenciosa de provider é permitida"
                )
        return self
