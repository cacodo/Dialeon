"""
Cross-Channel Reconciliation V1 -- classificação DETERMINÍSTICA de como o
canal Judge (avaliação debate-scoped) e o canal Source Analysis
(interpretação de uma fonte externa opcional, untrusted) SE RELACIONAM
entre si, por claim corrente.

Isto NÃO é um veredito de verdade. Isto NÃO muda/reinterpreta o veredito
do Judge (`app/judge/` continua inteiramente inalterado por este slice).
Isto NÃO transforma Source Analysis em autoridade -- é uma classificação
de RELACIONAMENTO ENTRE DOIS CANAIS JÁ EXISTENTES, aplicada DEPOIS que os
dois já produziram seus próprios resultados independentes.

Ver `app/reconciliation/reconcile.py` pra a função pura que produz este
resultado (nenhuma chamada de provider, nenhum prompt, nenhuma decisão
não-determinística) e `app/council/runner.py` pra onde ela roda no
pipeline (Debate -> Source Analysis -> Judge -> RECONCILIATION -> Editor).

Módulo próprio (não dentro de `app/judge/` nem `app/source_analysis/`):
nenhum dos dois canais deveria precisar saber que reconciliação existe --
colocar isto dentro de qualquer um dos dois introduziria uma dependência
de import na direção errada (o canal passaria a conhecer o conceito de
"comparação entre canais", que é estritamente uma camada acima dos dois).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONFIG = ConfigDict(frozen=True, extra="forbid")

CONTRACT_VERSION = "source_judge_reconciliation_v1"


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceChannelState(str, Enum):
    """Estado FECHADO do canal de Source Analysis pra UMA claim corrente
    -- descreve só o que o canal de fonte fez/não fez pra essa claim,
    NUNCA o relacionamento com o Judge (isso é `ChannelRelationship`,
    abaixo). Sete estados, cada um correspondendo a um fato estruturalmente
    distinto sobre o canal de fonte:

    - NOT_SUPPLIED: a execução não tem nenhum source_text (nenhuma fonte
      foi fornecida -- `SourceAnalysisResult` nem existe).
    - ANALYSIS_UNAVAILABLE: uma fonte foi fornecida, mas a análise
      canônica foi pulada/falhou de forma que nenhum resultado
      semântico por-claim existe (`SourceAnalysisResult.skipped_reason`
      preenchido).
    - ENTRY_REJECTED: a análise rodou e o(s) resultado(s) canônicos
      do lado da fonte pra esta claim são rejeitados (`RejectedSourceEntry`
      -- NÃO epistêmico, nunca uma relação real), não relações válidas.
    - SUPPORTS/CONTRADICTS: todas as relações válidas direcionais
      aplicáveis pra esta claim são unanimemente supports/contradicts.
    - UNRESOLVED: o resultado canônico válido aplicável é unresolved, e
      nenhuma relação direcional estabelece outro estado.
    - MIXED: resultados estruturalmente presentes do lado da fonte pra
      esta claim NÃO SÃO redutíveis a um único estado coerente
      (especialmente supports+contradicts, ou relação válida coexistindo
      anomalamente com rejeição) -- ver `reconcile.py::_reduce_source_group`
      pra a tabela de redução exata, testada explicitamente. Chamado
      "mixed", nunca "multiple sources" -- não existe um modelo de
      identidade de fonte real ainda (uma única fonte, um único
      source_text por Run); "mixed" descreve o ESTADO RESULTANTE
      incoerente, não uma contagem de fontes distintas.
    """

    NOT_SUPPLIED = "not_supplied"
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"
    ENTRY_REJECTED = "entry_rejected"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    UNRESOLVED = "unresolved"
    MIXED = "mixed"


class ChannelRelationship(str, Enum):
    """Classificação DETERMINÍSTICA e FECHADA de RELACIONAMENTO entre o
    canal Judge e o canal Source Analysis pra uma claim -- NUNCA verdade,
    NUNCA autoridade, NUNCA usada pra mudar veredito/composição de plano
    em nenhum dos dois canais. Descreve só COMO os dois canais se
    relacionam, nunca QUAL dos dois está certo.

    - DIRECTIONALLY_ALIGNED: os dois canais apontam na MESMA direção
      (ex.: Judge sustentou a claim E a fonte a apoia).
    - IN_TENSION: os dois canais apontam em direções OPOSTAS.
    - SOURCE_ADDS_DIRECTION: o Judge não deu direção nenhuma (conflicting/
      unresolved), mas a fonte tem uma relação direcional -- a fonte
      ACRESCENTA um dado que o debate sozinho não tinha, nunca resolve o
      Judge por conta própria.
    - SOURCE_UNRESOLVED: a fonte também não decide nada (mesma
      indeterminação do lado da fonte, independente do veredito).
    - SOURCE_CHANNEL_CONFLICT: o canal de fonte em si tem resultados
      conflitantes/anômalos pra esta claim (`SourceChannelState.MIXED`)
      -- não comparável com o Judge de forma alguma, o problema é
      inteiramente do lado da fonte.
    - NOT_COMPARABLE: não há base nenhuma pra comparação (sem fonte
      utilizável, OU sem veredito do Judge disponível)."""

    DIRECTIONALLY_ALIGNED = "directionally_aligned"
    IN_TENSION = "in_tension"
    SOURCE_ADDS_DIRECTION = "source_adds_direction"
    SOURCE_UNRESOLVED = "source_unresolved"
    SOURCE_CHANNEL_CONFLICT = "source_channel_conflict"
    NOT_COMPARABLE = "not_comparable"


# (source_state -> relacionamentos válidos) quando o Judge está
# DISPONÍVEL (status="complete") -- o relacionamento EXATO dentro do
# subconjunto de SUPPORTS/CONTRADICTS depende também do veredito do Judge
# (ver app/reconciliation/reconcile.py, `_SUPPORTS_MAPPING`/
# `_CONTRADICTS_MAPPING`); os outros 5 source_states têm exatamente UM
# relacionamento possível, sempre.
VALID_RELATIONSHIPS_WHEN_COMPLETE: dict[SourceChannelState, frozenset[ChannelRelationship]] = {
    SourceChannelState.NOT_SUPPLIED: frozenset({ChannelRelationship.NOT_COMPARABLE}),
    SourceChannelState.ANALYSIS_UNAVAILABLE: frozenset({ChannelRelationship.NOT_COMPARABLE}),
    SourceChannelState.ENTRY_REJECTED: frozenset({ChannelRelationship.NOT_COMPARABLE}),
    SourceChannelState.UNRESOLVED: frozenset({ChannelRelationship.SOURCE_UNRESOLVED}),
    SourceChannelState.MIXED: frozenset({ChannelRelationship.SOURCE_CHANNEL_CONFLICT}),
    SourceChannelState.SUPPORTS: frozenset(
        {
            ChannelRelationship.DIRECTIONALLY_ALIGNED,
            ChannelRelationship.IN_TENSION,
            ChannelRelationship.SOURCE_ADDS_DIRECTION,
        }
    ),
    SourceChannelState.CONTRADICTS: frozenset(
        {
            ChannelRelationship.DIRECTIONALLY_ALIGNED,
            ChannelRelationship.IN_TENSION,
            ChannelRelationship.SOURCE_ADDS_DIRECTION,
        }
    ),
}


class ClaimReconciliationOutcome(BaseModel):
    """Resultado por-claim-corrente da reconciliação. NUNCA duplica texto
    de claim/explicação do Judge/excerto de fonte/texto de fonte/
    metadados de provider/prompts -- referencia registros canônicos só
    por ID (`source_claim_result_ids`), nunca copia conteúdo (ver
    `app/models/domain.py::Claim`/`app/judge/schemas.py`/
    `app/source_analysis/models.py` pra onde o conteúdo real vive)."""

    model_config = _CONFIG

    claim_id: str = Field(min_length=1)
    # None SÓ quando o Judge está indisponível pra esta execução --
    # NUNCA fabricado quando não existe veredito.
    judge_verdict_id: str | None = None
    # Ordem EXATA preservada -- nunca um set, nunca reordenado, nunca
    # last-write-wins (ver reconcile.py::_reduce_source_group). Vazio
    # quando nenhum resultado de fonte existe pra esta claim
    # (source_state em not_supplied/analysis_unavailable).
    source_claim_result_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_state: SourceChannelState
    channel_relationship: ChannelRelationship

    @model_validator(mode="after")
    def _ids_non_empty(self) -> ClaimReconciliationOutcome:
        """Repair #2 (revisão adversarial) -- string vazia NUNCA é
        normalizada como ausência: `judge_verdict_id=""` e um id vazio
        dentro de `source_claim_result_ids` são estados estruturalmente
        impossíveis (nenhum produtor real gera ID vazio), rejeitados
        explicitamente ao invés de aceitos como um valor "presente mas
        vazio"."""
        if self.judge_verdict_id is not None and len(self.judge_verdict_id) == 0:
            raise ValueError(
                "judge_verdict_id, quando presente, não pode ser string vazia"
            )
        if any(len(sid) == 0 for sid in self.source_claim_result_ids):
            raise ValueError("source_claim_result_ids não pode conter string vazia")
        return self

    @model_validator(mode="after")
    def _no_duplicate_source_result_ids(self) -> ClaimReconciliationOutcome:
        ids = list(self.source_claim_result_ids)
        if len(set(ids)) != len(ids):
            raise ValueError(
                "source_claim_result_ids não pode conter o mesmo id mais de uma vez"
            )
        return self

    @model_validator(mode="after")
    def _source_id_cardinality_matches_state(self) -> ClaimReconciliationOutcome:
        """Repair #2 -- contrato estruturalmente verdadeiro mínimo entre
        `source_state` e a cardinalidade de `source_claim_result_ids`,
        independente de qualquer conhecimento de claims/Judge/fonte reais
        (isso é responsabilidade de `validate_reconciliation_coherence`,
        ver reconcile.py) -- só a forma estrutural é validada aqui:

        - NOT_SUPPLIED/ANALYSIS_UNAVAILABLE: nenhum resultado de fonte
          pode existir (não há canal de fonte utilizável pra referenciar).
        - ENTRY_REJECTED/SUPPORTS/CONTRADICTS/UNRESOLVED: exige pelo menos
          1 id -- o estado É sobre resultado(s) de fonte concreto(s).
        - MIXED: exige pelo menos 2 ids distintos (a duplicidade já é
          proibida acima) -- por definição, "mixed" só existe quando há
          mais de um resultado estruturalmente presente e incoerente
          entre si (ver reconcile.py::_reduce_source_group)."""
        n = len(self.source_claim_result_ids)
        if self.source_state in (
            SourceChannelState.NOT_SUPPLIED,
            SourceChannelState.ANALYSIS_UNAVAILABLE,
        ):
            if n != 0:
                raise ValueError(
                    f"source_state={self.source_state!r} exige "
                    "source_claim_result_ids vazio"
                )
        elif self.source_state == SourceChannelState.MIXED:
            if n < 2:
                raise ValueError(
                    "source_state=mixed exige pelo menos 2 source_claim_result_ids "
                    "distintos"
                )
        else:  # ENTRY_REJECTED, SUPPORTS, CONTRADICTS, UNRESOLVED
            if n < 1:
                raise ValueError(
                    f"source_state={self.source_state!r} exige pelo menos 1 "
                    "source_claim_result_id"
                )
        return self

    @model_validator(mode="after")
    def _no_verdict_requires_not_comparable(self) -> ClaimReconciliationOutcome:
        if (
            self.judge_verdict_id is None
            and self.channel_relationship != ChannelRelationship.NOT_COMPARABLE
        ):
            raise ValueError(
                "judge_verdict_id=None (Judge indisponível) exige "
                "channel_relationship=not_comparable -- nenhuma comparação "
                "direcional é possível sem veredito"
            )
        return self

    @model_validator(mode="after")
    def _relationship_valid_for_source_state_when_verdict_present(
        self,
    ) -> ClaimReconciliationOutcome:
        if self.judge_verdict_id is not None:
            allowed = VALID_RELATIONSHIPS_WHEN_COMPLETE[self.source_state]
            if self.channel_relationship not in allowed:
                raise ValueError(
                    f"channel_relationship={self.channel_relationship!r} não é válido "
                    f"pra source_state={self.source_state!r} (permitidos: "
                    f"{sorted(r.value for r in allowed)})"
                )
        return self


class SourceJudgeReconciliationResult(BaseModel):
    """Resultado top-level da reconciliação determinística Source<->Judge
    -- contrato `source_judge_reconciliation_v1`. Computado/persistido
    SEMPRE que uma execução chega até este estágio do pipeline (depois do
    Judge, antes do Editor) -- inclusive quando não há fonte, quando a
    análise de fonte falhou/foi pulada, e quando o Judge não produziu
    veredito (`status="judge_unavailable"` nesse caso, nunca ausente).

    `None` no nível de `app.council.result.CouncilRunResult.reconciliation`
    significa EXCLUSIVAMENTE "execução persistida ANTES deste slice
    existir" -- NUNCA reinterpretado como `not_comparable`: ausência
    histórica de reconciliação estruturada é um fato DIFERENTE de "os
    canais foram comparados e achados não-comparáveis" (ver seção 11 do
    contrato desta slice)."""

    model_config = _CONFIG

    id: str = Field(default_factory=_new_id, min_length=1)
    contract_version: Literal["source_judge_reconciliation_v1"] = CONTRACT_VERSION
    status: Literal["complete", "judge_unavailable"]
    # Ordem EXATA = ordem de `get_current_claims(...)` no momento da
    # reconciliação -- nunca reordenado.
    claim_outcomes: list[ClaimReconciliationOutcome] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _no_duplicate_claim_outcomes(self) -> SourceJudgeReconciliationResult:
        ids = [o.claim_id for o in self.claim_outcomes]
        if len(set(ids)) != len(ids):
            raise ValueError("claim_outcomes não pode conter o mesmo claim_id mais de uma vez")
        return self

    @model_validator(mode="after")
    def _judge_verdict_id_consistent_with_status(self) -> SourceJudgeReconciliationResult:
        verdict_ids = {o.judge_verdict_id for o in self.claim_outcomes}
        if self.status == "judge_unavailable":
            if verdict_ids - {None}:
                raise ValueError(
                    "status='judge_unavailable' exige judge_verdict_id=None em "
                    "TODOS os claim_outcomes -- nunca fabrica um veredito que não existe"
                )
        else:  # "complete"
            if None in verdict_ids:
                raise ValueError(
                    "status='complete' exige judge_verdict_id preenchido em TODOS "
                    "os claim_outcomes"
                )
            if len(verdict_ids) > 1:
                raise ValueError(
                    "status='complete' exige o MESMO judge_verdict_id em todos os "
                    "claim_outcomes -- só existe um JudgeVerdict por execução"
                )
        return self
