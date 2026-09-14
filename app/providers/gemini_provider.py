"""
Provider para Google Gemini, via SDK oficial `google-genai` (pacote `google.genai`).

Nota: existe um pacote mais antigo, `google-generativeai`, hoje deprecated
em favor deste. Usamos `google.genai.Client(...).aio.models.generate_content`
para a variante assíncrona, conforme a documentação oficial atual do SDK.

Retry: o client é construído com `HttpRetryOptions(attempts=1)`, que
desliga o retry automático do SDK (confirmado na documentação oficial —
é o mesmo padrão usado no exemplo oficial de integração com Temporal,
que também delega retry a uma camada externa). Retry é responsabilidade
exclusiva de LLMProvider.complete() (base.py) — ver correção pós-Etapa-2.
"""

from __future__ import annotations

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.models.provider_models import CompletionRequest, TokenUsage
from app.providers.base import LLMProvider
from app.providers.errors import (
    ProviderAPIError,
    ProviderAuthError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
)
from app.providers.pricing import PricingRegistry

# Códigos HTTP que a API do Gemini usa para autenticação/autorização inválida.
_AUTH_STATUS_CODES = {401, 403}
_RATE_LIMIT_STATUS_CODE = 429


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

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
        # attempts=1 no HttpRetryOptions = só a tentativa inicial, sem
        # nenhuma retentativa do SDK. Mesma razão dos outros dois
        # providers: retry é responsabilidade única de LLMProvider.
        # Confirmado no exemplo oficial de integração com Temporal (que
        # também delega retry a uma camada externa): HttpRetryOptions(attempts=1).
        no_retry_options = genai_types.HttpOptions(
            retry_options=genai_types.HttpRetryOptions(attempts=1)
        )
        self._client = (
            genai.Client(api_key=api_key, http_options=no_retry_options) if api_key else None
        )

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest) -> tuple[str, TokenUsage, str, str | None]:
        # _require_api_key() NÃO é mais chamado aqui — LLMProvider.complete()
        # (base.py) já garante isso antes de chegar aqui (Etapa 9).
        model = request.model or self._default_model_name

        # Fase 1 do MVP é single-turn (uma pergunta por vez), mas já aceitamos
        # múltiplos turnos para não ter que reescrever isto quando o Debate
        # Engine passar a mandar histórico. O Gemini aceita tanto uma string
        # única quanto uma lista de Content — convertendo turnos genéricos
        # para o formato `Content` do SDK.
        contents = [
            genai_types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[genai_types.Part(text=m.content)],
            )
            for m in request.messages
        ]

        config_kwargs: dict = {"max_output_tokens": request.max_tokens}
        if request.system_prompt:
            config_kwargs["system_instruction"] = request.system_prompt
        if request.temperature is not None:
            config_kwargs["temperature"] = request.temperature

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code in _AUTH_STATUS_CODES:
                raise ProviderAuthError(f"gemini: autenticação falhou ({code}): {exc}") from exc
            if code == _RATE_LIMIT_STATUS_CODE:
                raise ProviderRateLimitError(f"gemini: rate limit: {exc}") from exc
            # outro erro 4xx (ex.: request malformado) — não é retryable
            raise ProviderAPIError(f"gemini: erro de cliente ({code}): {exc}", retryable=False) from exc
        except genai_errors.ServerError as exc:
            raise ProviderAPIError(f"gemini: erro de servidor: {exc}", retryable=True) from exc

        return self._parse_response(response, model)

    @staticmethod
    def _parse_response(response, model: str) -> tuple[str, TokenUsage, str, str | None]:
        # Etapa 17A.1 (Objetivo A/B) — extraído ANTES da checagem de texto:
        # transporte teve sucesso, então usage_metadata/model_version/
        # finish_reason já são reais e confiáveis mesmo que o candidato não
        # tenha produzido texto (ex.: truncado por MAX_TOKENS antes de
        # qualquer conteúdo visível).
        usage_meta = getattr(response, "usage_metadata", None)
        if usage_meta is None:
            # usage_metadata inteiramente ausente — desconhecido, nunca
            # conhecido-zero (Etapa 9: preserva a semântica de unknown).
            observed_usage = TokenUsage(input_tokens=None, output_tokens=None)
        else:
            candidates_tokens = getattr(usage_meta, "candidates_token_count", None)
            thoughts_tokens = getattr(usage_meta, "thoughts_token_count", None)
            # candidates_token_count ausente (mesmo com o bloco usage_metadata
            # presente) continua desconhecido — mesma semântica que já
            # existia antes desta correção, sem regressão. thoughts_token_count
            # ausente É diferente: a doc oficial do SDK descreve o campo como
            # "if applicable" — ausência aqui significa "thinking não usado
            # nesta chamada", conhecido-zero, não desconhecido. Gemini 3.7
            # Flash usa thinking por padrão, e a doc oficial do Google
            # confirma que o preço de output cobra candidates + thoughts
            # juntos — omitir thoughts subestimaria tokens/custo/budget.
            output_tokens = (
                None
                if candidates_tokens is None
                else candidates_tokens + (thoughts_tokens or 0)
            )
            observed_usage = TokenUsage(
                input_tokens=getattr(usage_meta, "prompt_token_count", None),
                output_tokens=output_tokens,
            )

        # effective_model: o SDK real (google-genai, GenerateContentResponse)
        # expõe `model_version` — "Output only. The model version used to
        # generate the response" — a identidade EFETIVA, mesmo padrão já
        # usado por OpenAI/Anthropic (response.model or model). Ausente ->
        # cai pro modelo solicitado, nunca inventa uma identidade.
        observed_model = getattr(response, "model_version", None) or model

        # finish_reason vive em candidates[0] (um enum FinishReason) —
        # `.value` devolve a string nativa da API (ex.: "STOP",
        # "MAX_TOKENS"), nunca normalizada. Protegido contra
        # candidates ausente/vazio.
        candidates = getattr(response, "candidates", None)
        if candidates:
            finish_reason_enum = getattr(candidates[0], "finish_reason", None)
            observed_finish_reason = (
                finish_reason_enum.value if finish_reason_enum is not None else None
            )
        else:
            observed_finish_reason = None

        try:
            text = response.text
            if not text:
                raise ValueError("campo text vazio")
        except (AttributeError, ValueError) as exc:
            raise ProviderMalformedResponseError(
                f"gemini: resposta em formato inesperado: {exc}",
                observed_model=observed_model,
                observed_usage=observed_usage,
                observed_finish_reason=observed_finish_reason,
            ) from exc

        return text, observed_usage, observed_model, observed_finish_reason
