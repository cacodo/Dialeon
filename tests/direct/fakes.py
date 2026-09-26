"""Provider de teste pra runs diretas: só `_call_api` é roteirizado --
`LLMProvider.complete()` (retry/timeout, bloqueio local por pré-requisito,
precificação, identidade de modelo) é o de produção."""

from __future__ import annotations

from app.models.provider_models import TokenUsage
from app.providers.base import LLMProvider
from app.providers.pricing import PricingRegistry


class ScriptedApiProvider(LLMProvider):
    """Só `_call_api` é roteirizado; `complete()` é o de produção."""

    def __init__(
        self,
        name: str,
        script: list,
        *,
        api_key: str | None = "test-key",
        default_model: str = "configured-model",
        pricing: PricingRegistry | None = None,
        max_retries: int = 0,
        prerequisite: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            timeout_seconds=5.0,
            max_retries=max_retries,
            pricing=pricing or PricingRegistry({}),
        )
        self.provider_name = name
        self._default_model_name = default_model
        self.script = list(script)
        self.requests = []
        self._prerequisite = prerequisite

    @property
    def default_model(self) -> str:
        return self._default_model_name

    def local_prerequisite_state(self):
        return self._prerequisite or super().local_prerequisite_state()

    async def _call_api(self, request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # (texto, uso, modelo observado, motivo de parada)


def ok(text="Brasília.", *, observed_model=None, usage=TokenUsage(input_tokens=12, output_tokens=4)):
    return (text, usage, observed_model, "stop")
