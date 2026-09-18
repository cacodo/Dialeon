"""
Claim Extraction Coverage V1 -- Repair (adversarial review, Finding A) da
Run02 claim-extraction exhaustion repair.

ÚNICA derivação pura de "o que aconteceu com a extração de claims de cada
resposta elegível" -- usada por TRÊS chamadores que antes tinham lógicas
independentes que podiam divergir:

    - `app/debate/debate_engine.py` (short-circuit de falha ESTRUTURAL
      TOTAL da rodada 1, antes de crítica/Judge/Editor);
    - `app/judge/single_judge.py` (distinguir "no_claims_to_judge"
      genuíno de "claim_extraction_incomplete"/budget);
    - `app/debate/result.py` (computed_field de cobertura, exposto na
      API/usado pelo Editor pra disclosure determinística).

Nenhum dos três reimplementa o set-difference "alvo elegível vs. tentativa
aceita" por conta própria -- todos chamam as funções deste módulo.

Chave estruturalmente ROUND-SAFE: `(round_number, response_id)`, nunca
`response_id` sozinho -- mesmo que UUIDs sejam praticamente únicos entre
rodadas (colisão real nunca acontece), a separação entre rodada inicial e
rodada de crítica precisa ser EXPLÍCITA na própria derivação, nunca
incidental/dependente de um acidente de geração de id. Um alvo da rodada 1
NUNCA é considerado coberto por uma tentativa de extração da rodada 2 pro
MESMO response_id (estruturalmente impossível de qualquer forma -- um
`ModelResponse` só existe numa rodada -- mas a chave explícita torna essa
garantia visível na própria assinatura de dados, nunca só num invariante
implícito de quem chama).

Retry-safe por construção: um alvo com QUALQUER tentativa aceita entre
suas tentativas (mesmo depois de tentativas malformadas anteriores) conta
como `ACCEPTED` exatamente uma vez -- nunca duas vezes, nunca também
`FAILED`. Um alvo com tentativas SÓ rejeitadas (nenhuma aceita) conta como
`FAILED` exatamente uma vez, não importa quantas tentativas rejeitadas
existam pra ele (1 ou 2, ver `_MAX_STRUCTURED_OUTPUT_ATTEMPTS`,
app/debate/claim_extraction.py) -- a contagem é por CHAVE (round,
response_id), nunca por tentativa individual.
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.debate.processing_record import ClaimProcessingAttempt
from app.models.domain import ModelResponse

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Únicas 2 rodadas reais deste sistema (ver app/debate/debate_engine.py --
# nenhuma "Round 3" é inventada em lugar nenhum do domínio). Centralizado
# aqui e importado por `debate_engine.py`/`result.py` -- nunca duas
# constantes independentes que poderiam divergir.
INITIAL_ROUND_NUMBER = 1
CRITIQUE_ROUND_NUMBER = 2


class ClaimExtractionTargetStatus(str, Enum):
    """Status de UM alvo elegível (uma `ModelResponse` bem-sucedida,
    numa rodada específica) em relação à extração de claims."""

    # Ao menos uma tentativa de extração pra este alvo foi ACEITA --
    # inclusive quando o output aceito continha `claims: []` (extração
    # VÁLIDA vazia, nunca confundida com falha).
    ACCEPTED = "accepted"
    # Ao menos uma tentativa real de extração foi feita pra este alvo,
    # mas NENHUMA foi aceita (retry estruturado esgotado -- malformado,
    # referência inconsistente, ou truncamento confirmado).
    FAILED = "failed"
    # NENHUMA tentativa de extração foi feita pra este alvo -- o
    # processamento parou antes de alcançá-lo (tipicamente: budget
    # esgotado no meio da rodada, ver `DebateEngine._process_round`).
    NOT_ATTEMPTED = "not_attempted"


class ClaimExtractionTarget(BaseModel):
    """Um alvo elegível único -- `(round_number, response_id)` já
    resolvido pro seu status observado."""

    model_config = _CONFIG

    round_number: int = Field(ge=1)
    response_id: str = Field(min_length=1)
    status: ClaimExtractionTargetStatus


class ClaimExtractionCoverage(BaseModel):
    """Resumo agregado de uma lista de `ClaimExtractionTarget` -- nunca
    guarda a lista bruta (quem precisar dela chama
    `compute_claim_extraction_targets` diretamente); este objeto é só a
    contagem que os 3 chamadores realmente precisam pra decidir."""

    model_config = _CONFIG

    eligible_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    not_attempted_count: int = Field(ge=0)

    @property
    def missing_count(self) -> int:
        """`failed_count + not_attempted_count` -- "não coberto", por
        QUALQUER motivo (falha estrutural OU nunca tentado). Os dois
        chamadores que precisam da distinção FINA entre "failed" e
        "not_attempted" (ex.: pra decidir budget vs. falha estrutural)
        continuam usando os campos separados -- este é só o total
        combinado, pro caso comum ("X de Y não puderam ser extraídas")."""
        return self.failed_count + self.not_attempted_count

    @property
    def is_complete(self) -> bool:
        """True sse TODO alvo elegível teve uma extração aceita --
        nenhum failed, nenhum not_attempted. Só neste caso
        `current_claims` vazio pode honestamente significar "extração
        rodou em tudo, e nada sobrou" (`no_claims_to_judge` genuíno)."""
        return self.missing_count == 0


def compute_claim_extraction_targets(
    responses_by_round: Sequence[tuple[int, Sequence[ModelResponse]]],
    attempts: Sequence[ClaimProcessingAttempt],
) -> list[ClaimExtractionTarget]:
    """Derivação pura -- nenhuma chamada de rede, nenhum efeito colateral.

    `responses_by_round`: pares `(round_number, responses)` -- o chamador
    decide quais rodadas incluir (ex.: `DebateEngine` passa só a rodada 1
    pro short-circuit de falha total; `DebateResult`/`SingleJudge` passam
    as 2 rodadas quando a crítica ocorreu). Só `ModelResponse.status ==
    "success"` vira alvo elegível -- uma resposta que já falhou no
    dispatch (erro de transporte/timeout) nunca foi candidata a extração
    nenhuma, então nunca aparece aqui.

    `attempts`: TODOS os `ClaimProcessingAttempt` do `DebateResult`
    (extração, agrupamento E reconciliação misturados) -- filtrado aqui
    pra `operation == "extraction"` apenas; agrupamento/reconciliação
    nunca têm `target_model_response_id` (ver
    `ClaimProcessingAttempt._targets_match_operation`,
    app/debate/processing_record.py), então nunca colidiriam de qualquer
    forma, mas o filtro explícito documenta a intenção."""
    accepted_keys = {
        (attempt.round_number, attempt.target_model_response_id)
        for attempt in attempts
        if attempt.operation == "extraction" and attempt.parse_status == "accepted"
    }
    attempted_keys = {
        (attempt.round_number, attempt.target_model_response_id)
        for attempt in attempts
        if attempt.operation == "extraction"
    }

    targets: list[ClaimExtractionTarget] = []
    for round_number, responses in responses_by_round:
        for response in responses:
            if response.status != "success":
                continue
            key = (round_number, response.id)
            if key in accepted_keys:
                status = ClaimExtractionTargetStatus.ACCEPTED
            elif key in attempted_keys:
                status = ClaimExtractionTargetStatus.FAILED
            else:
                status = ClaimExtractionTargetStatus.NOT_ATTEMPTED
            targets.append(
                ClaimExtractionTarget(
                    round_number=round_number, response_id=response.id, status=status
                )
            )
    return targets


def summarize_claim_extraction_coverage(
    targets: Sequence[ClaimExtractionTarget],
) -> ClaimExtractionCoverage:
    """Agregação pura de uma lista de alvos já resolvidos -- separada de
    `compute_claim_extraction_targets` pra que um chamador que só precisa
    do resumo (a maioria) nunca precise carregar a lista bruta, mas um
    chamador que precisa auditar POR ALVO (testes, futura auditoria mais
    fina) ainda possa chamar a função de derivação diretamente."""
    accepted = sum(
        1 for t in targets if t.status == ClaimExtractionTargetStatus.ACCEPTED
    )
    failed = sum(1 for t in targets if t.status == ClaimExtractionTargetStatus.FAILED)
    not_attempted = sum(
        1 for t in targets if t.status == ClaimExtractionTargetStatus.NOT_ATTEMPTED
    )
    return ClaimExtractionCoverage(
        eligible_count=len(targets),
        accepted_count=accepted,
        failed_count=failed,
        not_attempted_count=not_attempted,
    )
