"""
DebateEngine — Etapa 5.

Coordena EXATAMENTE 2 rodadas (inicial + crítica), sem loop arbitrário de N
rodadas — o MVP é estruturalmente fixo nisso, não configurável ainda (ver
`app/config.py`, remoção de `default_max_rounds`).

Contrato:

    DebateEngine.run(run_config) -> DebateResult | raise InsufficientQuorumError

A `InsufficientQuorumError` da rodada inicial propaga sem tratamento
especial — o `DebateEngine` nunca a intercepta silenciosamente.

Segurança (defesa em profundidade — ver `app/debate/context.py` pros
detalhes de construção de prompt): a única garantia ESTRUTURAL (não
mitigação) é que texto de LLM nunca altera `RunConfig`, providers
habilitados, budget, número de rodadas, nem executa código — a única fonte
de decisão em todo este módulo é `RunConfig` (construído antes de
qualquer chamada) e o `status` estrutural de `ModelResponse`/
`ProviderResponse`. Influência semântica de uma claim sobre outra LLM não
é (e não pode ser) estruturalmente impossível — só mitigada.

Não implementa: Judge, Context Manager separado, Verification, Cost
Tracker real, persistência, loop de N rodadas.

Cross-round claim reconciliation -- depois que a Round 2 termina de
processar (extração + agrupamento, ambos escopados a uma única rodada),
`run()` tenta UMA reconciliação semântica adicional entre claims
sobreviventes do Round 1 e do Round 2 (ver `reconcile_claims`,
app/debate/claim_extraction.py) -- a única comparação desta camada que
atravessa a fronteira entre rodadas. É aditiva e opcional: nunca reduz o
conjunto de claims que existiria sem ela, nunca aborta o debate, nunca
afeta disponibilidade do Judge -- falha/budget insuficiente/ausência de
candidato em algum dos lados simplesmente resulta em nenhuma
reconciliação (o conjunto pré-reconciliação segue pro Judge exatamente
como já seguia antes desta etapa)."""

from __future__ import annotations

from app.debate.claim_extraction import extract_claims, group_claims, reconcile_claims
from app.debate.claims import get_current_claims
from app.debate.context import build_critique_requests
from app.debate.numeric_verification import DeterministicVerificationAttempt
from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import CritiqueResult, DebateResult
from app.models.domain import Claim, ModelResponse
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.providers.base import LLMProvider

_INITIAL_ROUND_NUMBER = 1
_CRITIQUE_ROUND_NUMBER = 2


class DebateEngine:
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers
        self._orchestrator = Orchestrator(providers)

    async def run(self, run_config: RunConfig) -> DebateResult:
        if run_config.claim_processor_provider not in self._providers:
            raise ValueError(
                "claim_processor_provider desconhecido: "
                f"{run_config.claim_processor_provider!r}"
            )
        processor = self._providers[run_config.claim_processor_provider]

        # --- Rodada 1 (inicial) ---
        # Reusa Orchestrator.run() sem nenhuma alteração — pode levantar
        # InsufficientQuorumError, que propaga sem ser capturada aqui.
        initial_result = await self._orchestrator.run(run_config)

        successful_round1 = [r for r in initial_result.responses if r.status == "success"]
        round1_claims, round1_attempts, round1_verifications = await self._process_round(
            successful_round1,
            round_number=_INITIAL_ROUND_NUMBER,
            total_models_in_round=initial_result.successful_count,
            processor=processor,
            max_output_tokens_per_call=run_config.max_output_tokens_per_call,
            known_claims=None,
            run_config=run_config,
            prior_input_tokens=initial_result.total_input_tokens,
            prior_output_tokens=initial_result.total_output_tokens,
            prior_cost_usd=initial_result.total_cost_usd,
        )

        all_claims: list[Claim] = list(round1_claims)
        all_attempts: list[ClaimProcessingAttempt] = list(round1_attempts)
        all_verifications: list[DeterministicVerificationAttempt] = list(round1_verifications)

        # --- Gate de budget ANTES da crítica ---
        # Considera TUDO que já rodou até aqui: respostas da rodada 1 +
        # TODAS as chamadas de processamento (aceitas e rejeitadas) — não
        # só InitialResponsesResult sozinho.
        input_so_far, output_so_far, cost_so_far = _cumulative_totals(
            initial_result, None, all_attempts
        )
        if compute_budget_exceeded(input_so_far, output_so_far, cost_so_far, run_config):
            return DebateResult(
                initial_result=initial_result,
                critique_round=None,
                claims=all_claims,
                claim_processing_attempts=all_attempts,
                numeric_verification_attempts=all_verifications,
                claim_processor_provider=run_config.claim_processor_provider,
                debate_skipped_reason="budget_exhausted_before_critique",
                cumulative_budget_exceeded=True,
            )

        # --- Quórum insuficiente na rodada 1: pula a crítica ---
        if initial_result.insufficient_data_for_consensus:
            return DebateResult(
                initial_result=initial_result,
                critique_round=None,
                claims=all_claims,
                claim_processing_attempts=all_attempts,
                numeric_verification_attempts=all_verifications,
                claim_processor_provider=run_config.claim_processor_provider,
                debate_skipped_reason="insufficient_initial_quorum",
                cumulative_budget_exceeded=False,
            )

        # --- Rodada 2 (crítica) ---
        # Participantes = só quem teve sucesso na rodada 1 — "perder
        # participantes" é o comportamento padrão esperado, não uma exceção.
        current_round1_claims = get_current_claims(all_claims)
        critique_participants = [response.provider for response in successful_round1]

        critique_requests = build_critique_requests(
            question=run_config.question,
            current_claims=current_round1_claims,
            participants=critique_participants,
            max_output_tokens_per_call=run_config.max_output_tokens_per_call,
        )

        round2_round_result = await self._orchestrator.run_round(
            critique_requests,
            round_number=_CRITIQUE_ROUND_NUMBER,
            round_dispatch_timeout_seconds=run_config.round_dispatch_timeout_seconds,
        )
        critique_result = CritiqueResult(round_result=round2_round_result)

        successful_round2 = [
            r for r in round2_round_result.responses if r.status == "success"
        ]
        round2_claims, round2_attempts, round2_verifications = await self._process_round(
            successful_round2,
            round_number=_CRITIQUE_ROUND_NUMBER,
            total_models_in_round=round2_round_result.successful_count,
            processor=processor,
            max_output_tokens_per_call=run_config.max_output_tokens_per_call,
            known_claims=current_round1_claims,
            run_config=run_config,
            prior_input_tokens=input_so_far + round2_round_result.total_input_tokens,
            prior_output_tokens=output_so_far + round2_round_result.total_output_tokens,
            prior_cost_usd=cost_so_far + round2_round_result.total_cost_usd,
        )

        all_claims = all_claims + round2_claims
        all_attempts = all_attempts + round2_attempts
        all_verifications = all_verifications + round2_verifications

        # --- Reconciliação cross-round (pós Round 2, pré-Judge) ---
        # `group_claims` (rodada 1 e rodada 2 acima) NUNCA compara uma
        # claim sobrevivente do Round 1 com uma do Round 2 -- cada chamada
        # dela é escopada a uma única rodada, por construção (ver
        # app/debate/claim_extraction.py). Isso permite que uma
        # proposição semanticamente equivalente permaneça atual duas
        # vezes só porque as duas representações vieram de rodadas
        # diferentes. `reconcile_claims` é a ÚNICA operação desta camada
        # que compara as duas rodadas entre si.
        #
        # `get_current_claims(all_claims)` (não os dois lados brutos
        # separadamente) é quem decide "sobrevivente" aqui -- inclui
        # corretamente uma revisão cross-round já existente (uma claim do
        # Round 2 com parent_claim_id apontando pro Round 1 já retira o
        # ancestral do conjunto atual, então ele nunca chega a ser
        # candidato de reconciliação -- reconciliação nunca reconsidera o
        # que a revisão já resolveu, ver app/debate/claims.py).
        current_after_round2 = get_current_claims(all_claims)
        current_round1_after_round2 = [
            c for c in current_after_round2 if c.round_introduced == _INITIAL_ROUND_NUMBER
        ]
        current_round2_after_round2 = [
            c for c in current_after_round2 if c.round_introduced == _CRITIQUE_ROUND_NUMBER
        ]

        reconciliation_claims: list[Claim] = []
        reconciliation_attempts: list[ClaimProcessingAttempt] = []

        # Só reconcilia quando os DOIS lados têm ao menos uma claim atual
        # -- não há nada pra uma chamada semântica comparar quando um dos
        # lados está vazio (nenhuma claim atual sobreviveu daquela
        # rodada), então nem uma chamada LLM é iniciada nesse caso --
        # nenhum ClaimProcessingAttempt fabricado pra uma comparação que
        # não podia produzir nada.
        if current_round1_after_round2 and current_round2_after_round2:
            (
                input_before_reconciliation,
                output_before_reconciliation,
                cost_before_reconciliation,
            ) = _cumulative_totals(initial_result, round2_round_result, all_attempts)

            # Gate de budget ANTES da chamada -- mesmo padrão já usado
            # antes de cada extração/agrupamento acima. Se o budget já
            # está esgotado, a reconciliação simplesmente não é tentada;
            # nenhum ClaimProcessingAttempt fabricado pra uma chamada que
            # nunca aconteceu (o conjunto pré-reconciliação segue
            # exatamente como está).
            if not compute_budget_exceeded(
                input_before_reconciliation,
                output_before_reconciliation,
                cost_before_reconciliation,
                run_config,
            ):
                # Universo de suporte real -- união de identidades
                # provider/model que responderam com sucesso em QUALQUER
                # uma das duas rodadas (mesma chave de identidade que
                # `Claim.supporting_models` já usa, ver
                # app/models/domain.py) -- nunca model_response_id (que
                # contaria a mesma resposta como "modelo distinto"),
                # nunca requested_model (que ignoraria um modelo
                # efetivamente diferente do reportado pelo provider),
                # nunca soma/max das contagens por rodada (que
                # sub/sobre-contaria quando os conjuntos de participantes
                # não são idênticos entre as duas rodadas -- ver
                # investigação de contrato de metadados).
                successful_model_identities = {
                    (r.provider, r.model) for r in successful_round1 + successful_round2
                }
                # Correção pós-revisão independente (HIGH 1) -- os dois
                # lados são passados SEPARADOS, nunca uma lista já
                # achatada: `reconcile_claims` precisa saber qual id
                # pertence a qual rodada pra rejeitar estruturalmente um
                # grupo same-side (ex.: 2 claims de Round 1 fundidas sem
                # nenhuma de Round 2) -- ver docstring de
                # `reconcile_claims`/`_make_cross_side_reconciliation_validator`.
                reconciliation_claims, reconciliation_attempts = await reconcile_claims(
                    current_round1_after_round2,
                    current_round2_after_round2,
                    reconciler=processor,
                    # Mesmo teto de agrupamento (não o geral de extração)
                    # -- reconciliação também exige cobertura de TODA
                    # claim candidata, mesma razão de tamanho de output
                    # já documentada pra `group_claims` abaixo.
                    max_output_tokens_per_call=run_config.max_output_tokens_grouping,
                    # Valor de rodada ORDINÁRIA (Round 2, a mais recente
                    # entre as fundidas) -- nunca a união entre rodadas,
                    # que vai em support_scope_model_count. Preserva o
                    # significado honesto de sempre de
                    # total_models_in_round.
                    total_models_in_round=round2_round_result.successful_count,
                    support_scope_model_count=len(successful_model_identities),
                    run_config=run_config,
                    prior_input_tokens=input_before_reconciliation,
                    prior_output_tokens=output_before_reconciliation,
                    prior_cost_usd=cost_before_reconciliation,
                )

        all_claims = all_claims + reconciliation_claims
        all_attempts = all_attempts + reconciliation_attempts

        input_total, output_total, cost_total = _cumulative_totals(
            initial_result, round2_round_result, all_attempts
        )
        cumulative_budget_exceeded = compute_budget_exceeded(
            input_total, output_total, cost_total, run_config
        )

        return DebateResult(
            initial_result=initial_result,
            critique_round=critique_result,
            claims=all_claims,
            claim_processing_attempts=all_attempts,
            numeric_verification_attempts=all_verifications,
            claim_processor_provider=run_config.claim_processor_provider,
            debate_skipped_reason=None,
            cumulative_budget_exceeded=cumulative_budget_exceeded,
        )

    @staticmethod
    async def _process_round(
        successful_responses: list[ModelResponse],
        round_number: int,
        total_models_in_round: int,
        processor: LLMProvider,
        max_output_tokens_per_call: int,
        known_claims: list[Claim] | None,
        *,
        run_config: RunConfig,
        prior_input_tokens: int,
        prior_output_tokens: int,
        prior_cost_usd: float,
    ) -> tuple[list[Claim], list[ClaimProcessingAttempt], list[DeterministicVerificationAttempt]]:
        """Extração (uma chamada por resposta) + agrupamento (uma chamada
        pro round inteiro) — usado tanto pra rodada 1 quanto pra rodada de
        crítica, só variando `known_claims` (None no round 1; as claims
        atuais do round anterior, com id+texto, no round 2+).

        Etapa 15: `verification_attempts` só é produzido durante a
        EXTRAÇÃO (sobre claims brutas) — `group_claims` nunca gera
        nenhum, porque a claim canônica de fusão tem texto sintetizado
        pela LLM e nunca teve proposta numérica própria (ver
        `numeric_verification.py`).

        Etapa 17A (B2): `prior_*` é o total conhecido ANTES desta rodada
        de processamento começar (dispatch do round + qualquer fase
        anterior) — verificado ANTES de cada chamada nova de extração
        (por resposta) e ANTES da chamada de agrupamento. Se o budget já
        estiver esgotado, as respostas RESTANTES simplesmente não são
        extraídas (contribuem 0 claims, mesmo formato de ausência já
        usado pra output malformado — nenhum ClaimProcessingAttempt
        fabricado) e o agrupamento nem é chamado. Nunca fabrica um
        Attempt pra uma chamada que não aconteceu.

        Dívida técnica registrada, não corrigida nesta etapa: as extrações
        abaixo rodam SEQUENCIALMENTE (um `await` por resposta, em loop),
        mesmo sendo chamadas independentes entre si — oportunidade real de
        paralelização (ex.: `asyncio.gather`), mas misturar isso com o
        patch semântico desta rodada não foi pedido; fica pra quando
        performance virar prioridade concreta."""
        raw_claims: list[Claim] = []
        attempts: list[ClaimProcessingAttempt] = []
        verification_attempts: list[DeterministicVerificationAttempt] = []

        for response in successful_responses:
            so_far_input, so_far_output, so_far_cost, _ = sum_usage_and_cost(attempts)
            if compute_budget_exceeded(
                prior_input_tokens + so_far_input,
                prior_output_tokens + so_far_output,
                prior_cost_usd + so_far_cost,
                run_config,
            ):
                break  # budget já esgotado -- respostas restantes não são extraídas

            claims, extraction_attempts, extraction_verifications = await extract_claims(
                response,
                round_number=round_number,
                total_models_in_round=total_models_in_round,
                extractor=processor,
                max_output_tokens_per_call=max_output_tokens_per_call,
                known_claims=known_claims,
                run_config=run_config,
                prior_input_tokens=prior_input_tokens + so_far_input,
                prior_output_tokens=prior_output_tokens + so_far_output,
                prior_cost_usd=prior_cost_usd + so_far_cost,
            )
            raw_claims.extend(claims)
            attempts.extend(extraction_attempts)
            verification_attempts.extend(extraction_verifications)

        extraction_input, extraction_output, extraction_cost, _ = sum_usage_and_cost(attempts)
        if compute_budget_exceeded(
            prior_input_tokens + extraction_input,
            prior_output_tokens + extraction_output,
            prior_cost_usd + extraction_cost,
            run_config,
        ):
            return raw_claims, attempts, verification_attempts  # agrupamento não é chamado

        canonical_claims, grouping_attempts = await group_claims(
            raw_claims,
            round_number=round_number,
            grouper=processor,
            # Etapa 17A.2 -- teto PRÓPRIO do agrupamento (não o
            # `max_output_tokens_per_call` geral usado pela extração
            # acima): o schema de agrupamento exige cobertura de TODA
            # claim bruta, então o output mínimo exigido cresce com a
            # contagem de claims do round, ao contrário da extração
            # (uma resposta por vez).
            max_output_tokens_per_call=run_config.max_output_tokens_grouping,
            run_config=run_config,
            prior_input_tokens=prior_input_tokens + extraction_input,
            prior_output_tokens=prior_output_tokens + extraction_output,
            prior_cost_usd=prior_cost_usd + extraction_cost,
        )
        attempts.extend(grouping_attempts)

        return raw_claims + canonical_claims, attempts, verification_attempts


def _cumulative_totals(
    initial_result: InitialResponsesResult,
    round_result: RoundResult | None,
    attempts: list[ClaimProcessingAttempt],
) -> tuple[int, int, float]:
    """Soma tokens/custo CONHECIDO de TODAS as chamadas reais até o ponto
    em que é chamada: respostas de debate (inicial + crítica, se já
    rodou) + TODOS os ClaimProcessingAttempt (aceitos e rejeitados) —
    nenhuma chamada real de LLM fica de fora da contabilidade. Usada só
    pro gate de budget (compute_budget_exceeded não usa unknown) — o
    flag de incerteza do DebateResult final é um @computed_field próprio
    da classe, não vem daqui."""
    total_input = initial_result.total_input_tokens
    total_output = initial_result.total_output_tokens
    total_cost = initial_result.total_cost_usd

    if round_result is not None:
        total_input += round_result.total_input_tokens
        total_output += round_result.total_output_tokens
        total_cost += round_result.total_cost_usd

    attempts_input, attempts_output, attempts_cost, _attempts_has_unknown = sum_usage_and_cost(
        attempts
    )
    total_input += attempts_input
    total_output += attempts_output
    total_cost += attempts_cost

    return total_input, total_output, total_cost
