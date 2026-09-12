"""
Provider para OpenAI.

Escolha deliberada: uso a Chat Completions API (client.chat.completions.create),
não a Responses API mais nova. A documentação oficial atual recomenda a
Responses API para integrações novas, mas também declara a Chat Completions
"supported indefinitely". Fico com Chat Completions aqui porque seu formato
de `messages` (lista de turnos user/assistant + system separado) é o que
permite este LLMProvider ficar uniforme com Anthropic e Gemini sem lógica
especial por provider — é uma troca consciente de "API mais nova" por
"abstração mais simples", não um descuido. Se isso mudar de ideia mais pra
frente (ex.: precisarmos de alguma feature só da Responses API), é uma
mudança isolada neste arquivo, sem tocar no restante do sistema.
"""

from __future__ import annotations

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
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


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: int,
        max_retries: int,
        default_model: str,
        pricing: PricingRegistry,
    ):
        super().__init__(api_key, timeout_seconds, max_retries, pricing)
        self._default_model_name = default_model
        # max_retries=0: desliga o retry automático do SDK. Todo retry é
        # controlado por LLMProvider.complete() (base.py) — ver correção
        # pós-Etapa-2 sobre retry duplicado. Doc oficial confirma que
        # max_retries=0 desabilita completamente o retry do client.
        self._client = AsyncOpenAI(api_key=api_key, max_retries=0) if api_key else None

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest) -> tuple[str, TokenUsage, str, str | None]:
        # _require_api_key() NÃO é mais chamado aqui — LLMProvider.complete()
        # (base.py) já garante que _call_api() nunca é invocado sem API key
        # configurada (Etapa 9 — permite distinguir estruturalmente falha
        # local, conhecido-zero, de falha depois do request já ter saído).
        model = request.model or self._default_model_name

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            # gpt-5.5 (o único modelo OpenAI configurado neste repositório
            # -- ver app/providers/pricing.py/app/config.py, nenhum outro
            # jamais foi testado/suportado) rejeita `max_tokens` na Chat
            # Completions API, exigindo `max_completion_tokens` em seu
            # lugar (reprodução real confirmada: HTTP 400 "Unsupported
            # parameter: 'max_tokens' is not supported with this model.
            # Use 'max_completion_tokens' instead."). Sem branching por
            # modelo -- não há segundo caso real no repositório hoje pra
            # justificar isso; se um outro modelo OpenAI genuinamente
            # precisar de `max_tokens` no futuro, esse é o gatilho
            # concreto pra introduzir a distinção, não antes.
            "max_completion_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise ProviderAuthError(f"openai: autenticação falhou: {exc}") from exc
        except RateLimitError as exc:
            raise ProviderRateLimitError(f"openai: rate limit: {exc}") from exc
        except APITimeoutError as exc:
            raise ProviderTimeoutError(f"openai: timeout: {exc}") from exc
        except APIConnectionError as exc:
            raise ProviderTimeoutError(f"openai: falha de conexão: {exc}") from exc
        except APIStatusError as exc:
            # 4xx de request malformado não é retryable; 5xx é.
            retryable = exc.status_code >= 500
            raise ProviderAPIError(
                f"openai: status={exc.status_code}: {exc.message}", retryable=retryable
            ) from exc

        return self._parse_response(response, model)

    @staticmethod
    def _parse_response(response, model: str) -> tuple[str, TokenUsage, str, str | None]:
        # Etapa 17A.1 (Objetivo A/B) — extraído ANTES da checagem de texto:
        # transporte teve sucesso (HTTP 200), então usage/model do nível
        # de topo já são reais e confiáveis mesmo que `choices` esteja
        # vazio ou `message.content` ausente. `finish_reason` vive dentro
        # de `choices[0]` especificamente -- só extraído se `choices[0]`
        # existir (protegido por IndexError), nunca inventado.
        observed_usage = TokenUsage(
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
        )
        observed_model = getattr(response, "model", None) or model
        try:
            observed_finish_reason = response.choices[0].finish_reason
        except (IndexError, AttributeError):
            observed_finish_reason = None

        try:
            choice = response.choices[0]
            text = choice.message.content
            if not text:
                raise ValueError("campo message.content vazio ou ausente")
        except (IndexError, AttributeError, ValueError) as exc:
            raise ProviderMalformedResponseError(
                f"openai: resposta em formato inesperado: {exc}",
                observed_model=observed_model,
                observed_usage=observed_usage,
                observed_finish_reason=observed_finish_reason,
            ) from exc

        return text, observed_usage, observed_model, observed_finish_reason
