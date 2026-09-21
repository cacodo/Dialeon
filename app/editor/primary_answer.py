"""
Primary Answer -- resposta CONCISA e determinística, PLANEJADA (não escrita)
pelo Editor.

Contexto de segurança (herdado da Etapa 17B, app/editor/schemas.py): antes
da 17B, a LLM Editor escrevia prosa livre, e um id/rótulo válido nunca
validou a PROSA que o acompanhava (a prosa podia contradizer o veredito do
Judge). Este módulo NÃO reintroduz prosa factual gerada por modelo:

- a LLM produz só um `PrimaryAnswerPlan`: listas de IDs de claims já
  avaliadas pelo Judge, agrupadas em PAPÉIS finitos (nenhum campo de texto
  livre; `extra="forbid"`);
- a APLICAÇÃO valida o plano contra o veredito real (ids existentes, atuais,
  avaliadas, sem duplicata, papel compatível com o veredito, tamanhos
  limitados) e o RENDERIZA deterministicamente, com andaimes
  (títulos/conectivos) fixos DESTE módulo e o texto das claims verbatim;
- a seleção é APRESENTACIONAL, nunca epistêmica: ela não altera autoridade
  de claim, veredito do Judge, relação de Source Analysis nem status de
  verdade. Um veredito "parcialmente sustentada" continua rotulado como tal
  ao lado da claim; incerteza/limitações do Judge nunca são omitidas do
  texto; e o texto declara explicitamente que é uma SELEÇÃO restrita ao que
  o debate/Judge avaliaram, não verificação externa.

Não há campo de "importância"/saliência global no domínio: quem escolhe o
que responde à pergunta ESPECÍFICA é o plano, e só apresentacionalmente.
Concordância entre modelos, ordem das claims e Source Analysis não são
usadas como importância. Source Analysis fica fora da primeira versão.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.editor.answer_blocks import AnswerVerdictLabel
from app.editor.errors import EditorError

PRIMARY_ANSWER_CONTRACT_VERSION = "primary_answer_plan_v1"

PrimaryAnswerRole = Literal[
    "central_conclusion",
    "supporting_reasons",
    "tradeoffs",
    "conditions",
    "uncertainties",
]

# Ordem FIXA de apresentação (nunca decidida pela LLM).
PRIMARY_ANSWER_ROLE_ORDER: tuple[PrimaryAnswerRole, ...] = (
    "central_conclusion",
    "supporting_reasons",
    "tradeoffs",
    "conditions",
    "uncertainties",
)

# Títulos FIXOS (autorados pela aplicação).
PRIMARY_ANSWER_ROLE_HEADINGS: dict[PrimaryAnswerRole, str] = {
    "central_conclusion": "Conclusão central:",
    "supporting_reasons": "Principais razões:",
    "tradeoffs": "Contrapontos relevantes:",
    "conditions": "Condições que podem mudar a resposta:",
    "uncertainties": "Incertezas e pontos não estabelecidos:",
}

# Vocabulário fechado de rótulos de veredito -- MESMOS valores de
# `_VERDICT_LABELS` em app/editor/compose.py (consistência garantida por
# teste, não por import: compose.py importa este módulo).
VERDICT_LABELS: dict[str, AnswerVerdictLabel] = {
    "supported": "sustentada pelo debate",
    "partially_supported": "parcialmente sustentada, com ressalvas",
    "rejected": "rejeitada pelo juiz com base no debate disponível",
    "conflicting": "com posições conflitantes, não resolvida",
    "unresolved": "sem informação suficiente para decidir",
}

_SUPPORTED = VERDICT_LABELS["supported"]
_PARTIAL = VERDICT_LABELS["partially_supported"]
_CONFLICTING = VERDICT_LABELS["conflicting"]
_UNRESOLVED = VERDICT_LABELS["unresolved"]

# Compatibilidade papel <-> veredito do Judge. NENHUM papel afirmativo
# (conclusão/razões/condições) aceita claim rejeitada, conflitante ou não
# resolvida: isso fortaleceria o status epistêmico do que o Judge avaliou.
# Um "parcialmente sustentada" pode aparecer, mas sempre carrega o rótulo.
PRIMARY_ANSWER_ROLE_VALID_LABELS: dict[PrimaryAnswerRole, frozenset[str]] = {
    "central_conclusion": frozenset({_SUPPORTED, _PARTIAL}),
    "supporting_reasons": frozenset({_SUPPORTED, _PARTIAL}),
    "tradeoffs": frozenset({_SUPPORTED, _PARTIAL, _CONFLICTING}),
    "conditions": frozenset({_SUPPORTED, _PARTIAL}),
    "uncertainties": frozenset({_PARTIAL, _CONFLICTING, _UNRESOLVED}),
}

# Limites estruturais (min, max) de itens por papel.
PRIMARY_ANSWER_ROLE_LIMITS: dict[PrimaryAnswerRole, tuple[int, int]] = {
    "central_conclusion": (1, 2),
    "supporting_reasons": (0, 4),
    "tradeoffs": (0, 2),
    "conditions": (0, 3),
    "uncertainties": (0, 3),
}

# Andaimes FIXOS do texto renderizado.
PRIMARY_ANSWER_LEAD_IN = (
    "Resposta principal, restrita ao que o debate e o Judge avaliaram "
    "(não é verificação externa):"
)
PRIMARY_ANSWER_LIMITATIONS_HEADING = "Limitações registradas:"

_MAX_ID_LENGTH = 200


class InvalidPrimaryAnswerPlanError(EditorError):
    """O plano é JSON/schema válido, mas viola uma regra semântica
    verificada pela aplicação (id inexistente/retirado/não avaliado,
    duplicata, papel incompatível com o veredito)."""


class PrimaryAnswerPlan(BaseModel):
    """Único artefato que a LLM pode produzir aqui: só IDs, agrupados por
    papel. Nenhum campo de texto livre. IDs são comparados EXATAMENTE
    (nenhuma normalização de whitespace)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    central_conclusion: list[str] = Field(min_length=1, max_length=2)
    supporting_reasons: list[str] = Field(default_factory=list, max_length=4)
    tradeoffs: list[str] = Field(default_factory=list, max_length=2)
    conditions: list[str] = Field(default_factory=list, max_length=3)
    uncertainties: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _ids_are_bounded_strings(self) -> PrimaryAnswerPlan:
        for role in PRIMARY_ANSWER_ROLE_ORDER:
            for claim_id in getattr(self, role):
                if not claim_id or len(claim_id) > _MAX_ID_LENGTH:
                    raise ValueError(f"id inválido em {role!r}")
        return self


_DOMAIN_CONFIG = ConfigDict(frozen=True, extra="forbid")


class PrimaryAnswerItem(BaseModel):
    """Uma claim JÁ AVALIADA, referenciada por id, com o texto verbatim
    (snapshot do que foi mostrado) e o rótulo do veredito do Judge."""

    model_config = _DOMAIN_CONFIG

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    verdict_label: AnswerVerdictLabel


class PrimaryAnswerSection(BaseModel):
    model_config = _DOMAIN_CONFIG

    role: PrimaryAnswerRole
    heading: str
    items: tuple[PrimaryAnswerItem, ...] = Field(min_length=1)


def build_scope_note(assessed: int, selected: int, omitted_not_established: int) -> str:
    text = (
        f"Seleção apresentacional: {selected} de {assessed} afirmações avaliadas pelo Judge. "
    )
    if omitted_not_established > 0:
        text += (
            f"{omitted_not_established} avaliadas como não estabelecidas (rejeitadas, "
            "conflitantes ou sem informação suficiente) não aparecem aqui como "
            "estabelecidas. "
        )
    return text + "A avaliação completa lista todas."


def render_primary_answer_text(
    sections: tuple[PrimaryAnswerSection, ...],
    limitations: tuple[str, ...],
    scope_note: str,
) -> str:
    """Renderização DETERMINÍSTICA: só andaimes fixos + texto verbatim.
    Seções vazias nunca existem (o modelo exige >=1 item por seção)."""
    parts: list[str] = [PRIMARY_ANSWER_LEAD_IN]
    for section in sections:
        lines = [section.heading]
        lines.extend(f"- {item.claim_text} ({item.verdict_label})" for item in section.items)
        parts.append("\n".join(lines))
    if limitations:
        parts.append(
            "\n".join([PRIMARY_ANSWER_LIMITATIONS_HEADING, *(f"- {text}" for text in limitations)])
        )
    parts.append(scope_note)
    return "\n\n".join(parts)


class PrimaryAnswer(BaseModel):
    """Representação persistida/pública da resposta principal. NUNCA
    substitui `FinalAnswer.answer_text`/`answer_blocks` (a avaliação
    completa continua sendo o registro investigativo)."""

    model_config = _DOMAIN_CONFIG

    contract_version: Literal["primary_answer_plan_v1"] = PRIMARY_ANSWER_CONTRACT_VERSION  # type: ignore[assignment]
    based_on_verdict_id: str = Field(min_length=1)
    lead_in: str
    sections: tuple[PrimaryAnswerSection, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    assessed_claim_count: int = Field(ge=1)
    selected_claim_count: int = Field(ge=1)
    omitted_not_established_count: int = Field(ge=0)
    scope_note: str
    # Texto EXATO que o usuário vê / o que "Copiar resposta" copia.
    rendered_text: str

    @model_validator(mode="after")
    def _structure_is_the_fixed_shape(self) -> PrimaryAnswer:
        roles = [section.role for section in self.sections]
        if len(set(roles)) != len(roles):
            raise ValueError("papel de seção duplicado")
        if roles != [r for r in PRIMARY_ANSWER_ROLE_ORDER if r in roles]:
            raise ValueError("seções fora da ordem fixa de papéis")
        if "central_conclusion" not in roles:
            raise ValueError("a resposta principal exige uma conclusão central")
        seen: set[str] = set()
        for section in self.sections:
            if section.heading != PRIMARY_ANSWER_ROLE_HEADINGS[section.role]:
                raise ValueError(f"heading forjado para o papel {section.role!r}")
            low, high = PRIMARY_ANSWER_ROLE_LIMITS[section.role]
            if not low <= len(section.items) <= high:
                raise ValueError(f"quantidade de itens fora do limite para {section.role!r}")
            valid_labels = PRIMARY_ANSWER_ROLE_VALID_LABELS[section.role]
            for item in section.items:
                if item.verdict_label not in valid_labels:
                    raise ValueError(
                        f"verdict_label={item.verdict_label!r} incompatível com o papel "
                        f"{section.role!r} -- fortaleceria o status epistêmico do veredito"
                    )
                if item.claim_id in seen:
                    raise ValueError(f"claim referenciada mais de uma vez: {item.claim_id!r}")
                seen.add(item.claim_id)
        return self

    @model_validator(mode="after")
    def _counts_and_text_are_consistent(self) -> PrimaryAnswer:
        selected = sum(len(section.items) for section in self.sections)
        if selected != self.selected_claim_count:
            raise ValueError("selected_claim_count diverge dos itens")
        if self.selected_claim_count > self.assessed_claim_count:
            raise ValueError("mais itens selecionados do que claims avaliadas")
        if self.lead_in != PRIMARY_ANSWER_LEAD_IN:
            raise ValueError("lead_in forjado")
        expected_scope = build_scope_note(
            self.assessed_claim_count, self.selected_claim_count, self.omitted_not_established_count
        )
        if self.scope_note != expected_scope:
            raise ValueError("scope_note forjado")
        expected_text = render_primary_answer_text(self.sections, self.limitations, self.scope_note)
        if self.rendered_text != expected_text:
            raise ValueError("rendered_text diverge da renderização determinística")
        return self


class ValidatedSelection(BaseModel):
    """Resultado da validação (antes da renderização)."""

    model_config = _DOMAIN_CONFIG

    sections: tuple[PrimaryAnswerSection, ...]
    assessed_claim_count: int
    omitted_not_established_count: int


def eligible_assessed_claims(verdict, current_claims) -> dict[str, tuple[str, AnswerVerdictLabel]]:
    """Claims ELEGÍVEIS: atuais E avaliadas pelo Judge, na ordem do Judge.
    Devolve {claim_id: (texto verbatim, rótulo do veredito)}."""
    current_by_id = {claim.id: claim for claim in current_claims}
    eligible: dict[str, tuple[str, AnswerVerdictLabel]] = {}
    for assessment in verdict.claim_assessments:
        claim = current_by_id.get(assessment.claim_id)
        if claim is None or assessment.claim_id in eligible:
            continue
        eligible[assessment.claim_id] = (claim.text, VERDICT_LABELS[assessment.verdict])
    return eligible


def has_selectable_support(eligible: dict[str, tuple[str, AnswerVerdictLabel]]) -> bool:
    return any(label in (_SUPPORTED, _PARTIAL) for _text, label in eligible.values())


def count_omitted_not_established(
    eligible: dict[str, tuple[str, AnswerVerdictLabel]], selected_ids: set[str]
) -> int:
    """Regra ÚNICA (usada pelo validador do plano E pela verificação
    cruzada de registros): quantas claims avaliadas como NÃO estabelecidas
    (rejeitada/conflitante/sem informação) ficaram fora da seleção."""
    not_established_labels = {VERDICT_LABELS["rejected"], _CONFLICTING, _UNRESOLVED}
    return sum(
        1
        for claim_id, (_text, label) in eligible.items()
        if claim_id not in selected_ids and label in not_established_labels
    )


def validate_plan(
    plan: PrimaryAnswerPlan,
    *,
    verdict,
    current_claims,
    all_claims,
) -> ValidatedSelection:
    """Valida o plano contra o veredito REAL. Não depende de compliance de
    prompt. Levanta `InvalidPrimaryAnswerPlanError`."""
    eligible = eligible_assessed_claims(verdict, current_claims)
    all_ids = {claim.id for claim in all_claims}
    current_ids = {claim.id for claim in current_claims}

    seen: set[str] = set()
    sections: list[PrimaryAnswerSection] = []
    for role in PRIMARY_ANSWER_ROLE_ORDER:
        claim_ids: list[str] = getattr(plan, role)
        if not claim_ids:
            continue
        items: list[PrimaryAnswerItem] = []
        for claim_id in claim_ids:
            if claim_id in seen:
                raise InvalidPrimaryAnswerPlanError(f"id duplicado no plano: {claim_id!r}")
            seen.add(claim_id)
            if claim_id not in eligible:
                if claim_id not in all_ids:
                    raise InvalidPrimaryAnswerPlanError(f"id inexistente: {claim_id!r}")
                if claim_id not in current_ids:
                    raise InvalidPrimaryAnswerPlanError(
                        f"id de claim retirada/substituída (não atual): {claim_id!r}"
                    )
                raise InvalidPrimaryAnswerPlanError(
                    f"id de claim não avaliada pelo Judge: {claim_id!r}"
                )
            text, label = eligible[claim_id]
            if label not in PRIMARY_ANSWER_ROLE_VALID_LABELS[role]:
                raise InvalidPrimaryAnswerPlanError(
                    f"papel {role!r} incompatível com o veredito ({label!r}) da claim {claim_id!r}"
                )
            items.append(PrimaryAnswerItem(claim_id=claim_id, claim_text=text, verdict_label=label))
        sections.append(
            PrimaryAnswerSection(
                role=role, heading=PRIMARY_ANSWER_ROLE_HEADINGS[role], items=tuple(items)
            )
        )

    omitted = count_omitted_not_established(eligible, seen)
    return ValidatedSelection(
        sections=tuple(sections),
        assessed_claim_count=len(eligible),
        omitted_not_established_count=omitted,
    )


def render_primary_answer(
    selection: ValidatedSelection, *, based_on_verdict_id: str, limitations: tuple[str, ...]
) -> PrimaryAnswer:
    selected = sum(len(section.items) for section in selection.sections)
    scope_note = build_scope_note(
        selection.assessed_claim_count, selected, selection.omitted_not_established_count
    )
    return PrimaryAnswer(
        based_on_verdict_id=based_on_verdict_id,
        lead_in=PRIMARY_ANSWER_LEAD_IN,
        sections=selection.sections,
        limitations=limitations,
        assessed_claim_count=selection.assessed_claim_count,
        selected_claim_count=selected,
        omitted_not_established_count=selection.omitted_not_established_count,
        scope_note=scope_note,
        rendered_text=render_primary_answer_text(selection.sections, limitations, scope_note),
    )
