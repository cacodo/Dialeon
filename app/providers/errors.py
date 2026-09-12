"""
Exceções internas usadas pelos providers concretos para sinalizar falhas
ao LLMProvider.complete() (em base.py), que as traduz em ProviderResponse
com status="error" — nenhuma dessas exceções deve escapar para fora da
Provider Layer.
"""

from app.models.provider_models import ProviderErrorType, TokenUsage


class ProviderError(Exception):
    """Classe base. Carrega o tipo de erro e se vale a pena tentar de novo."""

    def __init__(self, message: str, error_type: ProviderErrorType, retryable: bool):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


class ProviderAuthError(ProviderError):
    """Chave ausente ou inválida. Nunca vale a pena tentar de novo."""

    def __init__(self, message: str):
        super().__init__(message, ProviderErrorType.AUTH, retryable=False)


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, ProviderErrorType.TIMEOUT, retryable=True)


class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, ProviderErrorType.RATE_LIMIT, retryable=True)


class ProviderAPIError(ProviderError):
    """Erro do lado do provider: 5xx são retryable; 4xx de request malformado não são."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message, ProviderErrorType.API_ERROR, retryable=retryable)


class ProviderMalformedResponseError(ProviderError):
    """A chamada teve sucesso (HTTP 200) mas o corpo não tem o formato esperado.

    Não é retryable por padrão: se a API respondeu 200 com um formato
    inesperado, tentar de novo com o mesmo request tende a repetir o
    problema — mas o parâmetro existe para o caso raro de já se saber
    que é uma falha transiente conhecida.

    Etapa 17A.1 (achado real de produção) — transporte com sucesso +
    payload de aplicação malformado (ex.: nenhum bloco de texto no
    content) NÃO significa "nada foi observado do provider". O SDK real
    ainda expõe, no objeto de resposta que já chegou, metadata
    genuinamente confiável ANTES da validação de texto falhar: o modelo
    efetivamente usado, o uso de tokens, e o motivo de parada nativo do
    provider (ex.: truncamento por limite de output). Esses três campos
    são OPCIONAIS e só preenchidos quando o adapter concreto
    efetivamente conseguiu extraí-los com segurança do objeto de
    resposta -- nunca fabricados. `LLMProvider.complete()` (base.py) os
    usa pra não descartar accounting/provenance já observada só porque
    o texto em si não pôde ser extraído."""

    def __init__(
        self,
        message: str,
        retryable: bool = False,
        *,
        observed_model: str | None = None,
        observed_usage: TokenUsage | None = None,
        observed_finish_reason: str | None = None,
    ):
        super().__init__(message, ProviderErrorType.MALFORMED_RESPONSE, retryable=retryable)
        self.observed_model = observed_model
        self.observed_usage = observed_usage
        self.observed_finish_reason = observed_finish_reason
