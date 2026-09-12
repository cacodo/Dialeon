"""
Modelos de domínio do LLM Council (arquitetura v3).

Estes são os quatro tipos centrais que o sistema usa depois que uma
resposta de LLM entra no domínio — diferente de app/models/provider_models.py,
que é o formato bruto usado só dentro da Provider Layer (Etapa 2).

- EvidenceRef: evidência externa associada a uma Claim (Fase 5, só a forma
  do dado existe nesta etapa — verificação real fica pra depois).
- ModelResponse: registro imutável de uma resposta de uma LLM em uma rodada.
- ClaimSupport: unidade atômica de rastreabilidade — um ModelResponse
  específico que sustenta uma Claim. Fonte de verdade de onde
  `Claim.supporting_models` e `Claim.supporting_model_ratio` são
  derivados (correção pós-Etapa-3).
- Claim: afirmação estruturada extraída de uma ModelResponse. Também
  imutável — mudanças durante o debate criam uma NOVA Claim. Evolução
  1→1 usa `parent_claim_id`/`superseded_by`; fusão many→1 usa
  `merged_from_claim_ids`, campo separado com semântica distinta.
- JudgeVerdict: avaliação estruturada de um juiz sobre uma rodada. Também
  imutável — uma segunda passada (self-review) cria um novo JudgeVerdict
  referenciando o original, nunca o sobrescreve.

Nenhuma lógica de negócio (extração de claims, cálculo de status,
chamadas de LLM, o que conta como "resolvido") mora aqui — só a forma dos
dados e as invariantes estruturais que o próprio Pydantic consegue
garantir: imutabilidade, faixas de valores, e consistência entre campos
que o domínio já definiu como sempre andarem juntos (ex.: status
"superseded" sempre com superseded_by preenchido). Nada disso decide
*quando* uma claim deve ser superseded ou o que torna um argumento
"correto" — isso é lógica de negócio de componentes futuros (Debate
Engine, Judge), não do schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from app.models.provider_models import PricingProvenance, ProviderErrorInfo, TokenUsage

# Todo modelo de domínio compartilha a mesma postura: imutável depois de
# criado (frozen) e sem campos extras não declarados (forbid) — isso é o
# que permite, por exemplo, provar em teste que `supporting_model_ratio`
# não pode ser passado como argumento na criação de uma Claim: o Pydantic
# rejeita, porque não é um field declarado, é um computed_field.
_DOMAIN_MODEL_CONFIG = {"frozen": True, "extra": "forbid", "str_strip_whitespace": True}


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceRef(BaseModel):
    """Evidência externa associada a uma Claim específica (Fase 5).

    `claim_id` é o vínculo direto — Evidence pendura em Claim, não em Run
    (correção do modelo relacional feita na arquitetura v3, item 9).
    """

    model_config = _DOMAIN_MODEL_CONFIG

    id: str = Field(default_factory=_new_id)
    claim_id: str
    source_url: str | None = None
    summary: str = Field(min_length=1)
    verification_method: Literal["web_search", "none"]
    verified_at: datetime = Field(default_factory=_now)


class ModelResponse(BaseModel):
    """Resposta bruta de uma LLM em uma rodada. Imutável a partir da criação.

    É o registro de auditoria de "o que a LLM realmente disse" — nunca é
    editado, mesmo que uma Claim extraída dele seja depois contestada ou
    superada no debate (v3, item 5: Claim ≠ resposta).
    """

    model_config = _DOMAIN_MODEL_CONFIG

    id: str = Field(default_factory=_new_id)
    provider: str
    # Etapa 13 (T03.A) — ver docstring de ProviderResponse
    # (app/models/provider_models.py) pra semântica completa.
    # requested_model: o que foi PEDIDO, imutável, copiado verbatim de
    # ProviderResponse.requested_model. model: provider-reported quando
    # disponível, senão requested_model como fallback (comportamento
    # inalterado, campo preservado por compatibilidade).
    requested_model: str
    model: str
    round_number: int = Field(ge=1)
    status: Literal["success", "error"]
    response_text: str | None = None
    usage: TokenUsage | None = None
    cost_usd: float | None = None  # calculado em LLMProvider.complete() via PricingRegistry (Etapa 9)
    # Etapa 10 — ver docstring de PricingProvenance (app/models/provider_models.py).
    # Copiado verbatim de ProviderResponse.pricing_provenance — nunca
    # recalculado aqui.
    pricing_provenance: PricingProvenance | None = None
    latency_ms: int = Field(ge=0)
    attempts: int = Field(ge=0)
    # attempts=0 é o único caso especial: uma execução cancelada pelo
    # timeout global do Orchestrator (Etapa 4) antes de qualquer
    # resultado do LLMProvider chegar — não sabemos quantas tentativas
    # internas de retry já tinham ocorrido no momento do cancelamento,
    # então 0 significa "nenhuma tentativa CONCLUÍDA observável", não
    # "zero chamadas foram feitas". Em qualquer outro caso (sucesso ou
    # erro definitivo vindo do provider), attempts vem de
    # ProviderResponse.attempts (sempre ≥ 1, LLMProvider.complete()
    # sempre faz pelo menos uma tentativa).
    error: ProviderErrorInfo | None = None
    # Etapa 17A (B3) — ver docstring de ProviderResponse
    # (app/models/provider_models.py). Copiado verbatim, nunca recalculado.
    had_uncertain_prior_attempts: bool = False
    # Etapa 17A.1 (Objetivo B) — ver docstring de ProviderResponse. Copiado
    # verbatim, nunca normalizado.
    provider_finish_reason: str | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _text_and_error_are_mutually_exclusive(self) -> ModelResponse:
        if self.status == "success":
            if not self.response_text:
                raise ValueError("status='success' exige response_text não vazio")
            if self.error is not None:
                raise ValueError("status='success' não pode ter error preenchido")
        else:  # status == "error"
            if self.response_text is not None:
                raise ValueError("status='error' não pode ter response_text preenchido")
            if self.error is None:
                raise ValueError("status='error' exige error preenchido")
        return self


class ClaimSupport(BaseModel):
    """Um ModelResponse específico que sustenta uma Claim — a unidade
    atômica de rastreabilidade (correção pós-Etapa-3: `supporting_models`
    antes guardava só nomes de provider/model, sem religar a um
    ModelResponse concreto; isso quebrava quando o mesmo provider
    respondia em mais de uma rodada).

    Carrega provider/model junto com o id porque quem monta essa lista
    (o Orchestrator/Debate Engine, em etapas futuras) já tem o
    ModelResponse completo em mãos nesse momento — não é um lookup
    adicional, é só não descartar informação que já existia.
    """

    model_config = _DOMAIN_MODEL_CONFIG

    model_response_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class Claim(BaseModel):
    """Afirmação estruturada extraída de uma ModelResponse. Imutável.

    Origem (Etapa 5): exatamente UMA entre `source_model_response_id`
    (claim bruta, extraída diretamente de uma resposta — incluindo uma
    claim que revisa outra via `parent_claim_id`, que continua tendo
    origem própria honesta) e `merged_from_claim_ids` não-vazio (claim
    canônica de fusão — não tem uma única resposta de origem, porque é
    sintetizada a partir de N≥2 claims brutas, cada uma já rastreável até
    sua própria resposta).

    Lineage nesta etapa: `parent_claim_id` (evolução 1→1) e
    `merged_from_claim_ids` (fusão N→1, N≥2) são as fontes EFETIVAS —
    "esta claim ainda vale?" é sempre respondido consultando se algum
    OUTRO claim da coleção referencia este id por um desses dois campos
    (ver `get_current_claims`), nunca lendo `status`/`superseded_by` da
    própria claim. `superseded_by` e `status="superseded"` permanecem no
    schema (não removidos) mas ficam DORMENTES nesta etapa: nenhum código
    do Debate Engine os popula, porque um objeto imutável não pode saber,
    no momento da própria criação, que será superado por algo que ainda
    não existe — isso só é descobrível depois, por consulta inversa.

    `supporting_model_response_ids` é a fonte de verdade rastreável:
    uma lista de `ClaimSupport`, um item por ModelResponse que sustenta
    a claim — incluindo múltiplos responses do mesmo provider em rodadas
    diferentes, que agora não se perdem mais. `supporting_models` e
    `supporting_model_ratio` são ambos `computed_field`, sempre derivados
    dela, nunca fontes independentes (v3, item 1): `supporting_models` é
    a projeção deduplicada de "provider/model" (conveniente pra UI/
    consulta), e `supporting_model_ratio` é só a métrica puramente
    descritiva de sempre — NÃO probabilidade de verdade.

    `external_evidence` é deliberadamente independente de
    `supporting_model_ratio` — não existe nenhum validator ligando os
    dois. Essa ausência de acoplamento é intencional: consenso entre
    modelos e evidência externa são informações diferentes, e nada aqui
    deve inferir uma a partir da outra.
    """

    model_config = _DOMAIN_MODEL_CONFIG

    id: str = Field(default_factory=_new_id)
    text: str = Field(min_length=1)
    # Opcional a partir da Etapa 5: uma claim BRUTA (extraída de 1 resposta)
    # sempre tem isso preenchido; uma claim CANÔNICA de fusão (merge) não
    # tem uma única resposta de origem honesta — sua origem é
    # `merged_from_claim_ids` (cada uma delas já carrega sua própria
    # source_model_response_id). Ver validator _origin_is_exactly_one_of_response_or_merge.
    source_model_response_id: str | None = None
    round_introduced: int = Field(ge=1)

    parent_claim_id: str | None = None
    merged_from_claim_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    status: Literal["active", "consensus", "disputed", "resolved", "superseded"]

    supporting_model_response_ids: list[ClaimSupport] = Field(min_length=1)
    total_models_in_round: int = Field(ge=1)

    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    external_evidence: EvidenceRef | None = None

    created_at: datetime = Field(default_factory=_now)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supporting_models(self) -> list[str]:
        """Projeção deduplicada de "provider/model", derivada de
        `supporting_model_response_ids` — preserva a ordem de primeira
        aparição em vez de reordenar arbitrariamente."""
        seen: list[str] = []
        for support in self.supporting_model_response_ids:
            label = f"{support.provider}/{support.model}"
            if label not in seen:
                seen.append(label)
        return seen

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supporting_model_ratio(self) -> float:
        """Métrica puramente descritiva — NÃO é probabilidade de verdade.
        Derivada de `supporting_models` (já deduplicado), não da
        contagem bruta de `supporting_model_response_ids` — um mesmo
        modelo respondendo em 2 rodadas conta como 1 modelo apoiando,
        não 2."""
        return len(self.supporting_models) / self.total_models_in_round

    @model_validator(mode="after")
    def _no_duplicate_response_ids_among_supporters(self) -> Claim:
        ids = [s.model_response_id for s in self.supporting_model_response_ids]
        if len(set(ids)) != len(ids):
            raise ValueError(
                "supporting_model_response_ids não pode conter o mesmo "
                "model_response_id mais de uma vez"
            )
        return self

    @model_validator(mode="after")
    def _supporting_models_within_total(self) -> Claim:
        if len(self.supporting_models) > self.total_models_in_round:
            raise ValueError(
                "supporting_models (deduplicado) não pode ter mais entradas "
                "que total_models_in_round"
            )
        return self

    @model_validator(mode="after")
    def _lineage_is_not_self_referential(self) -> Claim:
        if self.parent_claim_id == self.id:
            raise ValueError("parent_claim_id não pode apontar para a própria claim")
        if self.superseded_by == self.id:
            raise ValueError("superseded_by não pode apontar para a própria claim")
        if self.id in self.merged_from_claim_ids:
            raise ValueError("merged_from_claim_ids não pode conter a própria claim")
        return self

    @model_validator(mode="after")
    def _merged_from_claim_ids_has_no_duplicates(self) -> Claim:
        if len(set(self.merged_from_claim_ids)) != len(self.merged_from_claim_ids):
            raise ValueError("merged_from_claim_ids não pode conter ids duplicados")
        return self

    @model_validator(mode="after")
    def _origin_is_exactly_one_of_response_or_merge(self) -> Claim:
        """Etapa 5: uma Claim tem EXATAMENTE UMA origem — uma resposta direta
        (source_model_response_id) OU uma fusão de claims brutas
        (merged_from_claim_ids não-vazio) — nunca as duas, nunca nenhuma.
        Não é hierarquia de provenance nova: a rastreabilidade de uma claim
        canônica continua completa via merged_from_claim_ids, já que cada
        claim bruta referenciada ali já tem sua própria
        source_model_response_id honesta."""
        has_source = self.source_model_response_id is not None
        has_merge = len(self.merged_from_claim_ids) > 0
        if has_source == has_merge:  # ambos True ou ambos False = inválido
            raise ValueError(
                "Claim precisa ter EXATAMENTE UMA origem: source_model_response_id "
                "(claim direta de uma resposta) OU merged_from_claim_ids não-vazio "
                "(claim canônica de fusão) — nunca as duas, nunca nenhuma"
            )
        return self

    @model_validator(mode="after")
    def _merge_requires_at_least_two_sources(self) -> Claim:
        """Um grupo de fusão com 1 membro não funde nada — e, pela regra
        acima, forçaria source_model_response_id=None numa claim que na
        verdade tem origem honesta. Ver Etapa 5, decisão do grouping."""
        if self.merged_from_claim_ids and len(self.merged_from_claim_ids) < 2:
            raise ValueError(
                "merged_from_claim_ids, quando presente, precisa ter ao menos 2 ids"
            )
        return self

    @model_validator(mode="after")
    def _attached_evidence_belongs_to_this_claim(self) -> Claim:
        if self.external_evidence is not None and self.external_evidence.claim_id != self.id:
            raise ValueError(
                "external_evidence.claim_id deve ser igual ao id desta claim"
            )
        return self

    @model_validator(mode="after")
    def _superseded_status_consistency(self) -> Claim:
        if self.status == "superseded" and self.superseded_by is None:
            raise ValueError("status='superseded' exige superseded_by preenchido")
        if self.status != "superseded" and self.superseded_by is not None:
            raise ValueError(
                "superseded_by só pode ser preenchido quando status='superseded'"
            )
        return self


class ClaimAssessment(BaseModel):
    """Avaliação do Judge sobre UMA claim atual do debate (Etapa 6).

    `verdict="rejected"` NÃO significa "provado externamente falso" — não
    existe Verifier nesta etapa. É só o veredito do Judge com base no
    conteúdo e nos argumentos do debate disponível, nada mais forte que
    isso. `"conflicting"` é diferente: existem posições incompatíveis
    relevantes e o Judge não resolve a oposição com segurança.
    `"unresolved"` é usado quando informação/raciocínio disponível é
    insuficiente pra decidir — nunca se omite uma claim por não saber
    avaliá-la, usa-se este valor.
    """

    model_config = _DOMAIN_MODEL_CONFIG

    claim_id: str = Field(min_length=1)
    verdict: Literal["supported", "partially_supported", "rejected", "conflicting", "unresolved"]
    explanation: str = Field(min_length=1)


class JudgeVerdict(BaseModel):
    """Avaliação estruturada de um juiz sobre um debate. Imutável.

    Uma segunda passada do juiz (self-review — adiada para pós-MVP, v3
    item 3) não editaria um JudgeVerdict existente: criaria um NOVO
    JudgeVerdict com `triggered_self_review=True` e `self_review_of`
    apontando para o veredito original, preservando os dois lado a lado
    para auditoria. Os campos já existem no schema; a lógica que os
    preenche fica para quando essa etapa for implementada.
    """

    model_config = _DOMAIN_MODEL_CONFIG

    id: str = Field(default_factory=_new_id)
    # Renomeado de round_number (Etapa 6): "round_number" sugeria uma
    # rodada específica, mas o Judge avalia o DebateResult inteiro, que
    # pode abranger 1 ou 2 rounds.
    # 1 = DebateResult contém só a rodada inicial (crítica foi pulada);
    # 2 = uma rodada de crítica OCORREU (existe CritiqueResult), mesmo
    # que tenha tido 0/N respostas bem-sucedidas — "ocorreu" != "teve
    # sucesso". Quem constrói este valor usa a presença de
    # DebateResult.critique_round (não None), nunca
    # CritiqueResult.critique_obtained/successful_count.
    evaluated_through_round: int = Field(ge=1)
    judge_model: str = Field(min_length=1)

    claim_assessments: list[ClaimAssessment] = Field(default_factory=list)
    best_arguments_by: dict[str, str] = Field(default_factory=dict)
    debate_limitations: list[str] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0)
    # O juiz sempre precisa justificar a decisão (requisito original da
    # arquitetura) — min_length=1 garante que reasoning não vem vazio.
    reasoning: str = Field(min_length=1)

    triggered_self_review: bool = False
    self_review_of: str | None = None

    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _self_review_consistency(self) -> JudgeVerdict:
        if self.triggered_self_review and self.self_review_of is None:
            raise ValueError("triggered_self_review=True exige self_review_of preenchido")
        if not self.triggered_self_review and self.self_review_of is not None:
            raise ValueError(
                "self_review_of só pode ser preenchido quando triggered_self_review=True"
            )
        if self.self_review_of == self.id:
            raise ValueError("self_review_of não pode apontar para o próprio veredito")
        return self

    @model_validator(mode="after")
    def _claim_assessments_have_no_duplicate_claim_id(self) -> JudgeVerdict:
        ids = [a.claim_id for a in self.claim_assessments]
        if len(set(ids)) != len(ids):
            raise ValueError("claim_assessments não pode conter claim_id duplicado")
        return self
