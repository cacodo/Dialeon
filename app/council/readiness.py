"""
Council Local Execution Readiness & Admission V1.

Resolve o estado LOCAL de pré-requisitos (`LocalPrerequisiteState`, ver
`LLMProvider.local_prerequisite_state()`) contra as dependências EFETIVAS de
uma run do Conselho: os participantes selecionados e os papéis internos que o
pipeline atual pode chamar. É evidência pra prévia, pra admissão e pra
auditoria -- nunca autorização, nunca previsão de execução.

O QUE ISTO SIGNIFICA, exclusivamente:

    "no momento da avaliação, este deployment conhecia este estado local de
    pré-requisitos pro provider de cada dependência configurada."

O QUE ISTO NÃO SIGNIFICA:

- que a credencial é válida no fornecedor, que o provider ou o modelo
  configurado existem/estão disponíveis remotamente, ou que a chamada vai
  funcionar (nenhuma chamada de rede é feita);
- que uma etapa vai de fato rodar: `potential` é só "pode ser alcançada";
  quórum, afirmações extraídas, veredito, orçamento e falhas anteriores
  decidem isso em runtime;
- confiança epistêmica: participantes prontos não tornam uma resposta mais
  verdadeira.

`unknown` nunca é `missing` nem `met`: só uma ausência CONHECIDA (`missing`)
é degradação previsível. O modelo mostrado (`configured_default_model`) é o
modelo padrão configurado no deployment pra aquele provider -- um fato de
configuração local (o mesmo de `DefaultModelAuthoritySnapshot`), nunca prova
de que ele exista remotamente, foi solicitado ou executou.

Único avaliador: a prévia (`POST /runs/readiness`, `dialeon readiness`), a
admissão autoritativa (`CouncilExecutionService.run`) e o fato persistido no
aceite usam TODOS `evaluate_council_readiness` + `strict_admission_blockers`,
alimentados pelos mesmos objetos de provider resolvidos
(`app/application/service.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.provider_models import LocalPrerequisiteState
from app.orchestrator.config import RunConfig

_CONFIG = ConfigDict(frozen=True, extra="forbid")

COUNCIL_READINESS_CONTRACT_VERSION = "council_local_readiness_v1"
COUNCIL_ADMISSION_CONTRACT_VERSION = "council_admission_v1"

# Papel de cada dependência, na ordem em que o pipeline do Conselho pode
# alcançá-la. `semantic_review` é a revisão semântica da realização
# linguística da resposta: no pipeline atual ela usa o provider do Judge
# (`app/editor/compose.py`), então aparece como dependência própria com esse
# provider. A rodada de crítica usa os próprios participantes -- não é uma
# dependência separada.
CouncilDependencyRole = Literal[
    "participant",
    "claim_extraction",
    "source_analysis",
    "judge",
    "editor",
    "semantic_review",
]

# `selected`: participante escolhido pra pergunta (despachado na rodada
# inicial). `potential`: papel interno que o caminho pedido PODE alcançar --
# nunca garantia de execução. `not_applicable`: configurado, mas fora do
# caminho pedido (hoje, só a análise de fonte sem fonte).
DependencyApplicability = Literal["selected", "potential", "not_applicable"]

CouncilReadinessSummary = Literal["all_met", "some_unknown", "some_missing"]
CouncilAdmissionMode = Literal["standard", "strict"]


class CouncilExecutionDependencies(BaseModel):
    """As dependências de provider de uma run do Conselho pedida, sem a
    pergunta: o que a prévia e a admissão precisam pra resolver a MESMA
    avaliação. Construída a partir de um `RunConfig` (admissão) ou dos mesmos
    papéis internos configurados + a seleção (prévia, ver
    `RunConfig.internal_role_providers_from_settings`)."""

    model_config = _CONFIG

    enabled_providers: tuple[str, ...] = Field(min_length=1)
    claim_processor_provider: str = Field(min_length=1)
    judge_provider: str = Field(min_length=1)
    editor_provider: str = Field(min_length=1)
    source_analyzer_provider: str = Field(min_length=1)
    source_supplied: bool

    @classmethod
    def from_run_config(cls, run_config: RunConfig) -> "CouncilExecutionDependencies":
        # `RunConfig.source_text` já está normalizado: vazio/só espaço é None.
        return cls(
            enabled_providers=run_config.enabled_providers,
            claim_processor_provider=run_config.claim_processor_provider,
            judge_provider=run_config.judge_provider,
            editor_provider=run_config.editor_provider,
            source_analyzer_provider=run_config.source_analyzer_provider,
            source_supplied=run_config.source_text is not None,
        )

    @property
    def all_providers(self) -> frozenset[str]:
        """Mesmo conjunto de `RunConfig.all_provider_authorities`."""
        return frozenset(self.enabled_providers) | {
            self.claim_processor_provider,
            self.judge_provider,
            self.editor_provider,
            self.source_analyzer_provider,
        }


class CouncilDependencyReadiness(BaseModel):
    model_config = _CONFIG

    role: CouncilDependencyRole
    provider: str
    # Fato de configuração LOCAL (modelo padrão configurado pro provider),
    # nunca modelo solicitado/reportado/disponível.
    configured_default_model: str
    local_prerequisite: LocalPrerequisiteState
    applicability: DependencyApplicability

    @model_validator(mode="after")
    def _participants_are_selected(self) -> "CouncilDependencyReadiness":
        if (self.role == "participant") != (self.applicability == "selected"):
            raise ValueError("só participantes são 'selected', e todo participante é 'selected'")
        return self


class CouncilReadiness(BaseModel):
    """Avaliação de prontidão LOCAL de uma run do Conselho. Só as
    dependências ficam guardadas; o resumo é sempre derivado delas (nunca um
    segundo fato que pudesse divergir)."""

    model_config = _CONFIG

    contract_version: Literal["council_local_readiness_v1"] = COUNCIL_READINESS_CONTRACT_VERSION
    dependencies: tuple[CouncilDependencyReadiness, ...] = Field(min_length=1)

    @property
    def applicable_dependencies(self) -> tuple[CouncilDependencyReadiness, ...]:
        return tuple(d for d in self.dependencies if d.applicability != "not_applicable")

    @property
    def known_missing(self) -> tuple[CouncilDependencyReadiness, ...]:
        """Dependências do caminho pedido cuja ausência local é CONHECIDA --
        degradação previsível. Nunca inclui `unknown` nem `not_applicable`."""
        return tuple(d for d in self.applicable_dependencies if d.local_prerequisite == "missing")

    @property
    def unknown(self) -> tuple[CouncilDependencyReadiness, ...]:
        return tuple(d for d in self.applicable_dependencies if d.local_prerequisite == "unknown")

    @property
    def summary(self) -> CouncilReadinessSummary:
        if self.known_missing:
            return "some_missing"
        if self.unknown:
            return "some_unknown"
        return "all_met"


def evaluate_council_readiness(
    dependencies: CouncilExecutionDependencies,
    *,
    local_prerequisites: Mapping[str, LocalPrerequisiteState],
    configured_default_models: Mapping[str, str],
) -> CouncilReadiness:
    """ÚNICO avaliador. Função pura: recebe o estado local e o modelo
    configurado já lidos (uma vez por provider) dos objetos de provider
    resolvidos -- nunca relê `Settings`, nunca faz rede. Todo provider de
    `dependencies.all_providers` precisa estar nos dois mapeamentos (quem
    chama já rejeitou provider desconhecido); faltar um é erro de
    programação, não um `unknown` inventado."""
    missing_facts = sorted(
        p
        for p in dependencies.all_providers
        if p not in local_prerequisites or p not in configured_default_models
    )
    if missing_facts:
        raise ValueError(f"sem fatos locais pros providers: {missing_facts}")

    def dep(
        role: CouncilDependencyRole, provider: str, applicability: DependencyApplicability
    ) -> CouncilDependencyReadiness:
        return CouncilDependencyReadiness(
            role=role,
            provider=provider,
            configured_default_model=configured_default_models[provider],
            local_prerequisite=local_prerequisites[provider],
            applicability=applicability,
        )

    return CouncilReadiness(
        dependencies=(
            *(dep("participant", p, "selected") for p in dependencies.enabled_providers),
            dep("claim_extraction", dependencies.claim_processor_provider, "potential"),
            dep(
                "source_analysis",
                dependencies.source_analyzer_provider,
                "potential" if dependencies.source_supplied else "not_applicable",
            ),
            dep("judge", dependencies.judge_provider, "potential"),
            dep("editor", dependencies.editor_provider, "potential"),
            dep("semantic_review", dependencies.judge_provider, "potential"),
        )
    )


def strict_admission_blockers(
    readiness: CouncilReadiness,
) -> tuple[CouncilDependencyReadiness, ...]:
    """ÚNICA política de admissão estrita: bloqueia quando alguma dependência
    do caminho pedido (participante selecionado ou papel interno `potential`)
    tem ausência local CONHECIDA. `unknown` é admitido (incerto, nunca falha);
    `not_applicable` nunca bloqueia (ex.: análise de fonte sem fonte)."""
    return readiness.known_missing


class CouncilAdmissionRequest(BaseModel):
    """O que o cliente pediu na criação. `standard` (default) é o aceite de
    sempre (v1.3.0): a run segue mesmo com degradação local conhecida.
    `acknowledge_known_degradation` só registra que o cliente declarou seguir
    sabendo da degradação -- não muda o aceite. `strict` recusa quando
    `strict_admission_blockers` não é vazio; pedir `strict` e reconhecer
    degradação ao mesmo tempo é contraditório."""

    model_config = _CONFIG

    mode: CouncilAdmissionMode = "standard"
    acknowledge_known_degradation: bool = False

    @model_validator(mode="after")
    def _strict_never_acknowledges(self) -> "CouncilAdmissionRequest":
        if self.mode == "strict" and self.acknowledge_known_degradation:
            raise ValueError(
                "admissão estrita não aceita reconhecimento de degradação: escolha uma das duas"
            )
        return self


class CouncilAdmission(BaseModel):
    """Fatos de ACEITE de uma run nova do Conselho, persistidos com o
    registro de aceite e copiados verbatim pro registro terminal: a
    avaliação de prontidão usada no aceite, o modo de admissão pedido e se o
    cliente reconheceu a degradação. Nunca reconstruído da configuração
    atual; ausente (NULL) numa run anterior a este slice = fato não
    capturado."""

    model_config = _CONFIG

    contract_version: Literal["council_admission_v1"] = COUNCIL_ADMISSION_CONTRACT_VERSION
    mode: CouncilAdmissionMode
    known_degradation_acknowledged: bool
    readiness: CouncilReadiness

    @model_validator(mode="after")
    def _coherent_with_policy(self) -> "CouncilAdmission":
        CouncilAdmissionRequest(
            mode=self.mode, acknowledge_known_degradation=self.known_degradation_acknowledged
        )
        if self.mode == "strict" and strict_admission_blockers(self.readiness):
            raise ValueError("uma run aceita em modo estrito não pode ter ausência local conhecida")
        return self
