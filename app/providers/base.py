"""
Interface comum a todos os providers de LLM.

Nenhum componente fora deste pacote (app/providers/) deve importar SDKs
de OpenAI/Anthropic/Google diretamente ou saber como cada API funciona.
Tudo que qualquer outro componente precisa é:

    response = await provider.complete(request)

e a ProviderResponse normalizada.

O provider NÃO decide quantas vezes chamar, qual modelo vem a seguir,
nem qualquer outra decisão de negócio — isso é responsabilidade do
Orchestrator. O provider só sabe: recebeu um CompletionRequest, tenta
cumprir, devolve um ProviderResponse.

Accounting (Etapa 9): `cost_usd` é calculado aqui, via `PricingRegistry`
injetado, usando o identificador de modelo RESOLVIDO (`model` --
provider-reported quando observado, requested_model como fallback quando
não -- ver `ModelIdentitySource`, app/models/provider_models.py; a
origem fica registrada separadamente, nunca decide o lookup de preço em
si) — nunca inventado, `None` quando não há preço registrado. Falha ANTES de
`_call_api()` começar (API key ausente) é conhecido-zero
(`usage=None, cost_usd=0.0` — nenhuma chamada de rede foi tentada). Falha
DEPOIS de `_call_api()` começar é sempre desconhecida
(`usage=None, cost_usd=None`) — a request pode ter sido enviada e até
processada remotamente antes do erro aparecer do nosso lado; nunca
assumimos zero consumo nesse caso.
"""

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod

from app.models.provider_models import (
    CompletionRequest,
    ModelIdentitySource,
    PricingProvenance,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
    TokenUsage,
)
from app.providers.errors import ProviderAuthError, ProviderError, ProviderMalformedResponseError
from app.providers.pricing import PricingRegistry

# Backoff exponencial com jitter entre tentativas (não aplicado à 1ª tentativa).
_BASE_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 8.0


def _backoff_delay(attempt_number: int) -> float:
    """attempt_number é 1-indexado (a 1ª retentativa é attempt_number=2)."""
    delay = min(_BASE_DELAY_SECONDS * (2 ** (attempt_number - 2)), _MAX_DELAY_SECONDS)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


# Etapa 17A.2 -- vocabulário NATIVO de finish_reason já confirmado por
# adapter/teste (ver docstring de ProviderResponse.provider_finish_reason
# e tests/providers/test_{anthropic,openai,gemini}_provider.py):
# Anthropic "max_tokens", OpenAI "length", Gemini "MAX_TOKENS". Usado só
# pra decidir, DEPOIS que um output estruturado já foi rejeitado por
# outro motivo (transporte, parse, referência inconsistente), se vale a
# pena repetir a MESMA chamada -- nunca inferido de JSON malformado
# sozinho (JSON pode estar malformado por qualquer outro motivo real,
# sem relação nenhuma com truncamento).
KNOWN_OUTPUT_TRUNCATION_FINISH_REASONS = frozenset({"max_tokens", "length", "MAX_TOKENS"})


def is_known_output_truncation(provider_finish_reason: str | None) -> bool:
    """True só quando o motivo de parada NATIVO do provider confirma que
    a chamada foi cortada por atingir o teto de output configurado --
    nunca inferido de JSON malformado/parse falhado por si só (isso
    continuaria malformado por qualquer outro motivo, sem relação com
    truncamento)."""
    return provider_finish_reason in KNOWN_OUTPUT_TRUNCATION_FINISH_REASONS


def transport_error_common_fields(provider_response: ProviderResponse) -> dict:
    """Campos comuns de um ProviderResponse com `status="error"` (falha
    de transporte) -- usado pelos 4 conversores de stage-Attempt
    (extração/agrupamento, source analysis, judge, editor) pra nunca
    descartar accounting/provenance real que já existe em
    `ProviderResponse`.

    Etapa 17A (B1) -- achado real: cada um desses 4 conversores
    hardcodava `usage=None, cost_usd=None, pricing_provenance=None`
    incondicionalmente, convertendo silenciosamente custo
    CONHECIDO-ZERO (ex.: API key ausente -> `cost_usd=0.0`) em
    DESCONHECIDO. `usage=None` continua correto nesse caso (nenhum
    provider reportou uso), mas `cost_usd`/`pricing_provenance` sempre
    devem vir do `ProviderResponse` real, nunca hardcoded.

    Não é um framework de pipeline -- só elimina duplicação de conversão
    de valor já literal e idêntica nos 4 módulos."""
    return {
        "provider": provider_response.provider,
        "requested_model": provider_response.requested_model,
        "model": provider_response.model,
        "model_identity_source": provider_response.model_identity_source,
        "transport_status": "error",
        "transport_error": provider_response.error,
        "transport_attempts": provider_response.attempts,
        "raw_output_text": None,
        "parse_status": "not_attempted",
        "usage": provider_response.usage,
        "cost_usd": provider_response.cost_usd,
        "pricing_provenance": provider_response.pricing_provenance,
        "latency_ms": provider_response.latency_ms,
        "had_uncertain_prior_attempts": provider_response.had_uncertain_prior_attempts,
        "provider_finish_reason": provider_response.provider_finish_reason,
    }


class LLMProvider(ABC):
    """Classe base. Cada provider concreto só precisa implementar `_call_api`."""

    provider_name: str

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int,
        pricing: PricingRegistry,
    ):
        # T02.2 fractional-timeout fidelity repair: `float`, não `int` --
        # `asyncio.wait_for(timeout=...)` já aceita fracionário
        # nativamente (ver `complete()` abaixo), e o valor que chega aqui
        # precisa ser BIT-A-BIT o mesmo `ProviderExecutionPolicy.attempt_timeout_seconds`
        # resolvido/persistido (app/providers/factory.py) -- um `int`
        # aqui truncaria 1.5s->1s, 0.9s->0s, violando o invariante
        # "persisted == enforced".
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._pricing = pricing

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Modelo usado quando CompletionRequest.model é None."""

    @abstractmethod
    async def _call_api(
        self, request: CompletionRequest
    ) -> tuple[str, TokenUsage, str | None, str | None]:
        """Faz a chamada real à API do provider.

        Deve levantar uma subclasse de ProviderError (ver app/providers/errors.py)
        em qualquer falha — nunca deixar uma exceção do SDK escapar sem
        tradução, porque o resto do sistema só entende ProviderErrorInfo.

        Retorna (texto_da_resposta, uso_de_tokens, identidade_de_modelo_CRUA_observada_ou_None,
        motivo_de_parada_nativo_do_provider_ou_None).

        Etapa 17A.1: o 4º elemento é o motivo de parada NATIVO do
        provider (verbatim, nunca normalizado) quando o SDK expõe essa
        informação de forma confiável — `None` quando indisponível.

        Provenance de identidade de modelo -- o 3º elemento é o valor CRU
        que o adapter concreto conseguiu ler do objeto de resposta do SDK
        (`response.model`/`response.model_version`), NUNCA já mesclado
        com `requested_model` -- essa mescla (e a decisão de
        `ModelIdentitySource` que a acompanha) é resolvida uma única vez
        aqui em `complete()`, nunca duplicada em cada adapter. `None`
        quando o SDK não expôs o campo de forma confiável.
        """

    @staticmethod
    def _resolve_model_identity(
        observed_model: str | None, requested_model: str
    ) -> tuple[str, ModelIdentitySource]:
        """Único ponto de resolução model CRU-observado -> (model,
        model_identity_source) do sistema inteiro -- usado pelos 3
        pontos de construção de `ProviderResponse` abaixo (sucesso, falha
        pré-request, falha pós-request exaurida), nunca duplicado dentro
        de cada adapter concreto (mesma disciplina de
        `ProviderExecutionPolicy.from_settings`: uma resolução, nunca
        várias que possam divergir). `observed_model` falsy (None ou "")
        sempre vira fallback -- nunca inventamos uma identidade que o
        adapter não conseguiu extrair com confiança."""
        if observed_model:
            return observed_model, ModelIdentitySource.PROVIDER_REPORTED
        return requested_model, ModelIdentitySource.REQUESTED_FALLBACK

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise ProviderAuthError(f"{self.provider_name}: API key não configurada")

    def _price_usage(
        self, model: str, usage: TokenUsage | None
    ) -> tuple[float | None, PricingProvenance | None]:
        """Único ponto de precificação do provider — usado tanto no
        caminho de sucesso quanto no de falha-com-metadata-observada
        (Etapa 17A.1). `usage=None` sempre produz `(None, None)` sem
        consultar `PricingRegistry` — nada a precificar sem uso
        conhecido."""
        if usage is None:
            return None, None
        priced = self._pricing.price(self.provider_name, model, usage)
        if priced is None:
            return None, None
        return priced.cost_usd, priced.provenance

    async def complete(self, request: CompletionRequest) -> ProviderResponse:
        """Executa a requisição com timeout e retry limitado, e normaliza o resultado.

        Esta é a única lógica de retry/timeout do sistema — cada provider
        concreto não reimplementa isso, só levanta os erros certos.
        """
        # Etapa 13 (T03.A): resolvido UMA ÚNICA VEZ, antes de qualquer
        # chamada de rede — provenance histórica do que foi PEDIDO,
        # nunca reatribuída depois, mesmo em retries ou falhas.
        requested_model = request.model or self.default_model

        try:
            self._require_api_key()
        except ProviderAuthError as exc:
            # Falha 100% local — nenhuma chamada de rede foi sequer
            # tentada, `_call_api()` nunca chega a ser invocado.
            # Estruturalmente diferente de qualquer falha que ocorra
            # DEPOIS de `_call_api()` começar (essas continuam
            # "desconhecido" — usage=None/cost_usd=None, ver abaixo):
            # aqui sabemos com CERTEZA que o custo de API é zero, porque
            # nenhum provider foi contatado. `usage=None` continua honesto
            # (nenhum provider reportou usage — nenhum foi chamado);
            # `cost_usd=0.0` é atribuído diretamente (não passa pelo
            # PricingRegistry — não faz sentido precificar uma chamada
            # que nunca aconteceu).
            return ProviderResponse(
                provider=self.provider_name,
                requested_model=requested_model,
                model=requested_model,
                # Nenhuma chamada de rede foi sequer tentada -- nenhuma
                # identidade pôde ter sido observada, então isto é
                # SEMPRE fallback, nunca provider_reported.
                model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
                status="error",
                text=None,
                usage=None,
                cost_usd=0.0,
                pricing_provenance=None,  # nunca passou pelo PricingRegistry
                latency_ms=0,
                attempts=0,
                had_uncertain_prior_attempts=False,  # nunca há tentativa alguma antes desta
                provider_finish_reason=None,  # nenhuma chamada foi feita, nada a observar
                error=ProviderErrorInfo(
                    type=ProviderErrorType.AUTH,
                    message=str(exc),
                    retryable=False,
                ),
            )

        start = time.monotonic()
        attempts = 0
        last_error: ProviderError

        while True:
            attempts += 1
            try:
                # Etapa 13: variável RENOMEADA (era `effective_model`) e
                # deliberadamente separada de `requested_model` — antes da
                # Etapa 13 este nome tinha dois significados sobrepostos
                # (requested antes da chamada, reported/fallback depois).
                # `reported_or_fallback_model` é sempre o que o provider
                # efetivamente reportou, ou `requested_model` como
                # fallback quando o SDK não expõe identidade efetiva
                # (ver contrato de cada adapter._call_api()).
                text, usage, observed_model, finish_reason = await asyncio.wait_for(
                    self._call_api(request), timeout=self._timeout_seconds
                )
                # Etapa 13 / provenance de identidade de modelo: único
                # ponto de mescla observed_model (CRU, do adapter) ->
                # (model, model_identity_source) -- ver
                # `_resolve_model_identity`.
                reported_or_fallback_model, model_identity_source = self._resolve_model_identity(
                    observed_model, requested_model
                )
                # Custo estimado real + proveniência (Etapa 9/10) — via
                # PricingRegistry, usando o identificador de modelo
                # RESOLVIDO acima (`reported_or_fallback_model` --
                # provider-reported quando observado, requested_model como
                # fallback quando não; a origem fica registrada
                # separadamente em `model_identity_source`, nunca decide o
                # lookup de preço em si). None se não houver taxa
                # registrada pra esse par — nunca inventado.
                cost_usd, pricing_provenance = self._price_usage(
                    reported_or_fallback_model, usage
                )
                return ProviderResponse(
                    provider=self.provider_name,
                    requested_model=requested_model,
                    model=reported_or_fallback_model,
                    model_identity_source=model_identity_source,
                    status="success",
                    text=text,
                    usage=usage,
                    cost_usd=cost_usd,
                    pricing_provenance=pricing_provenance,
                    latency_ms=self._elapsed_ms(start),
                    attempts=attempts,
                    # Etapa 17A (B3): `attempts > 1` na tentativa que teve
                    # sucesso significa que ao menos uma tentativa ANTERIOR
                    # já tinha discado de verdade (chegou a `_call_api()`)
                    # e falhou — accounting dessa tentativa anterior é
                    # DESCONHECIDA (mesma razão de sempre: a request pode
                    # ter sido processada/cobrada do lado do provider antes
                    # do erro aparecer do nosso lado). O custo/uso
                    # CONHECIDO desta tentativa bem-sucedida é preservado
                    # integralmente acima — este flag é só um sinal
                    # ADICIONAL de incompletude, nunca substitui o valor
                    # conhecido por None.
                    had_uncertain_prior_attempts=attempts > 1,
                    provider_finish_reason=finish_reason,
                    error=None,
                )
            except asyncio.TimeoutError:
                last_error = ProviderError(
                    f"{self.provider_name}: sem resposta após {self._timeout_seconds}s",
                    ProviderErrorType.TIMEOUT,
                    retryable=True,
                )
            except ProviderError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 — captura ampla e intencional
                # Qualquer exceção não prevista (bug em nossa própria integração,
                # erro de SDK não mapeado) vira UNKNOWN e não é retentada — é
                # melhor falhar visivelmente do que mascarar um bug com retry.
                last_error = ProviderError(
                    f"{self.provider_name}: erro inesperado: {exc}",
                    ProviderErrorType.UNKNOWN,
                    retryable=False,
                )

            can_retry = last_error.retryable and attempts <= self._max_retries
            if not can_retry:
                # Depois que _call_api() já começou, uma falha sem usage
                # confiável fica DESCONHECIDA, nunca conhecido-zero — a
                # request pode ter sido enviada e até processada
                # remotamente (timeout do lado do cliente não cancela o
                # processamento do lado do provider; uma resposta 200 com
                # corpo malformado significa que o provider JÁ processou
                # e cobrou, só a extração falhou do nosso lado). Ver
                # docstring do módulo — não inventamos "zero" aqui.
                #
                # Etapa 17A.1 (Objetivo A/B) — EXCEÇÃO a essa regra padrão:
                # quando `last_error` é especificamente um
                # ProviderMalformedResponseError com metadata OBSERVADA
                # (o adapter conseguiu extrair model/usage/finish_reason
                # do objeto de resposta real antes da validação de texto
                # falhar), usamos essa metadata real em vez de descartá-la
                # -- nunca fabricada, só o que o adapter concreto
                # efetivamente observou. Pra qualquer outro tipo de erro
                # (timeout, rate limit, erro de API antes de qualquer
                # resposta) não há response real nenhuma pra observar --
                # os três campos permanecem None/fallback, exatamente como
                # antes.
                observed_model: str | None = None
                observed_usage: TokenUsage | None = None
                observed_finish_reason: str | None = None
                if isinstance(last_error, ProviderMalformedResponseError):
                    observed_model = last_error.observed_model
                    observed_usage = last_error.observed_usage
                    observed_finish_reason = last_error.observed_finish_reason

                model, model_identity_source = self._resolve_model_identity(
                    observed_model, requested_model
                )
                cost_usd, pricing_provenance = self._price_usage(model, observed_usage)
                return ProviderResponse(
                    provider=self.provider_name,
                    requested_model=requested_model,
                    # Falha DEPOIS de _call_api() começar: sem metadata
                    # observada, `requested_model` é o único valor honesto
                    # disponível (mesmo fallback do caso pré-request) —
                    # mas se o adapter observou uma identidade real antes
                    # do texto falhar, ela é preservada.
                    model=model,
                    model_identity_source=model_identity_source,
                    status="error",
                    text=None,
                    usage=observed_usage,
                    cost_usd=cost_usd,
                    pricing_provenance=pricing_provenance,
                    latency_ms=self._elapsed_ms(start),
                    attempts=attempts,
                    # Etapa 17A (B3): mesma regra do retorno de sucesso —
                    # `attempts > 1` significa que uma tentativa ANTERIOR à
                    # última também discou e falhou. Aqui a última
                    # tentativa TAMBÉM falhou, então o accounting já é
                    # totalmente desconhecido de qualquer forma
                    # (cost_usd=None acima já cobre isso) — este flag
                    # preserva a distinção honesta mesmo assim, sem
                    # depender de inferência externa.
                    had_uncertain_prior_attempts=attempts > 1,
                    provider_finish_reason=observed_finish_reason,
                    error=ProviderErrorInfo(
                        type=last_error.error_type,
                        message=str(last_error),
                        retryable=last_error.retryable,
                    ),
                )

            # Repair (adversarial review -- transport backoff correctness)
            # -- `_backoff_delay` é documentada/implementada em termos do
            # PRÓXIMO attempt_number (1-indexado; a 1ª retentativa É
            # attempt_number=2, ver docstring dela) -- `attempts` aqui é
            # o número da tentativa que ACABOU de falhar (incrementado no
            # topo do loop, ANTES da chamada), nunca o número da próxima.
            # Passar `attempts` sozinho subtraía 1 implicitamente do
            # expoente (0.25s/0.5s em vez de 0.5s/1.0s pro schedule
            # pretendido) -- `attempts + 1` é a tentativa que está prestes
            # a começar, o valor que a função sempre esperou receber.
            await asyncio.sleep(_backoff_delay(attempts + 1))

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
