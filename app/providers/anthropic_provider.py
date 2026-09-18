from __future__ import annotations

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    RateLimitError,
)

from app.models.provider_models import CompletionRequest, TokenUsage
from app.providers.base import LLMProvider
from app.providers.errors import (
    ProviderAPIError,
    ProviderAuthError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.providers.pricing import PricingRegistry


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int,
        default_model: str,
        pricing: PricingRegistry,
    ):
        super().__init__(api_key, timeout_seconds, max_retries, pricing)
        self._default_model_name = default_model
        # max_retries=0: mesma razão do OpenAIProvider — retry é
        # responsabilidade única de LLMProvider.complete().
        self._client = AsyncAnthropic(api_key=api_key, max_retries=0) if api_key else None

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(
        self, request: CompletionRequest
    ) -> tuple[str, TokenUsage, str | None, str | None]:
        # _require_api_key() NÃO é mais chamado aqui — LLMProvider.complete()
        # (base.py) já garante isso antes de chegar aqui (Etapa 9).
        model = request.model or self._default_model_name

        kwargs: dict = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.minimal_reasoning:
            # Repair (Run02 claim-extraction exhaustion) -- único
            # mapeamento concreto de `CompletionRequest.minimal_reasoning`
            # neste repositório. O SDK instalado (anthropic>=1.5) expõe
            # `thinking={"type": "disabled"}` como um valor de request
            # genuinamente suportado (distinto de simplesmente omitir
            # `thinking`, que alguns modelos desta geração podem tratar
            # como "adaptive" por padrão em vez de "sem raciocínio
            # nenhum" -- ver `ThinkingConfigAdaptiveParam` no SDK) --
            # "disabled" é literalmente possível aqui, então usamos o
            # valor exato, nunca uma aproximação "minimal" quando
            # "disabled" já é suportado.
            kwargs["thinking"] = {"type": "disabled"}

        try:
            response = await self._client.messages.create(**kwargs)
        except AuthenticationError as exc:
            raise ProviderAuthError(f"anthropic: autenticação falhou: {exc}") from exc
        except RateLimitError as exc:
            raise ProviderRateLimitError(f"anthropic: rate limit: {exc}") from exc
        except APITimeoutError as exc:
            raise ProviderTimeoutError(f"anthropic: timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise ProviderTimeoutError(f"anthropic: falha de conexão: {exc}") from exc
        except APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise ProviderAPIError(
                f"anthropic: status={exc.status_code}: {exc.message}", retryable=retryable
            ) from exc

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response) -> tuple[str, TokenUsage, str | None, str | None]:
        # Etapa 17A.1 (Objetivo A/B) — extraído ANTES da checagem de texto,
        # com sua própria proteção defensiva: transporte teve sucesso (HTTP
        # 200), então usage/model/stop_reason já são reais e confiáveis no
        # objeto de resposta, mesmo que o campo `content` específico esteja
        # vazio/malformado. `getattr(..., None)` nunca levanta, então isso
        # nunca mascara o erro de texto real abaixo.
        observed_usage = TokenUsage(
            input_tokens=getattr(getattr(response, "usage", None), "input_tokens", None),
            output_tokens=getattr(getattr(response, "usage", None), "output_tokens", None),
        )
        # Provenance de identidade de modelo -- CRU, ver mesma nota em
        # OpenAIProvider._parse_response.
        observed_model = getattr(response, "model", None)
        observed_finish_reason = getattr(response, "stop_reason", None)

        try:
            text_blocks = [block.text for block in response.content if block.type == "text"]
            text = "".join(text_blocks)
            if not text:
                raise ValueError("nenhum bloco de texto no campo content")
        except (AttributeError, ValueError) as exc:
            raise ProviderMalformedResponseError(
                f"anthropic: resposta em formato inesperado: {exc}",
                observed_model=observed_model,
                observed_usage=observed_usage,
                observed_finish_reason=observed_finish_reason,
            ) from exc

        return text, observed_usage, observed_model, observed_finish_reason
