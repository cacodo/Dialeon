"""
Provider-Neutral Request Provenance V1.

Provenance de UM `CompletionRequest` provider-neutro já FINALIZADO,
antes de qualquer tradução específica de provider -- não do payload
nativo que o SDK/HTTP de um provider concreto de fato envia (que pode
divergir por detalhes de tradução -- ver `app/providers/*_provider.py`),
e não do que o provider remoto de fato aceitou/executou.

O que esta provenance SIGNIFICA:

    "este CompletionRequest provider-neutro foi construído sob este
    contrato de request explícito e entregue à abstração de provider."

O que esta provenance NÃO significa (e NUNCA deve ser apresentada como
significando):

- que o provider remoto aceitou o request;
- que este é o payload EXATO enviado via SDK/HTTP nativo do provider;
- que o provider não usou nenhum default não-documentado;
- confidencialidade ou autenticação (o digest não é um HMAC/assinatura --
  qualquer um com o mesmo request reproduz o mesmo digest);
- que o prompt histórico exato é recuperável sem o código-fonte
  compatível (o digest é de mão única, nunca reversível).

Persistência do request BRUTO está EXPLICITAMENTE fora de escopo deste
slice -- ver relatório da tarefa "Provider-Neutral Request Provenance
V1".
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.provider_models import CompletionRequest

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Único prefixo de digest suportado nesta versão -- qualquer outro rótulo
# de algoritmo (ex.: um futuro "completion-request-sha256-v2" com
# canonicalização diferente) precisa de um novo prefixo explícito, nunca
# aceito silenciosamente como se fosse compatível com este.
REQUEST_DIGEST_PREFIX = "completion-request-sha256-v1:"
_DIGEST_HEX_LENGTH = 64  # SHA-256 = 32 bytes = 64 caracteres hex


class RequestProvenance(BaseModel):
    """Provenance provider-neutra de UM `CompletionRequest` finalizado --
    versão do contrato de request da OPERAÇÃO (ex.: "judge_v1") + digest
    determinístico do conteúdo semântico do request.

    Deliberadamente NÃO carrega (mesmo indiretamente):

    - identidade de provider;
    - modelo efetivo/reportado (`ModelIdentitySource` e similares);
    - credenciais;
    - o prompt/mensagens brutos.

    `None` no nível de quem referencia este value object (ModelResponse/
    *Attempt) significa EXCLUSIVAMENTE "provenance de request não foi
    registrada pra este registro histórico" -- nunca "contrato v1 vazio"
    nem "provenance inferida"."""

    model_config = _CONFIG

    contract_version: str = Field(min_length=1)
    request_digest: str

    @field_validator("request_digest")
    @classmethod
    def _digest_matches_supported_format(cls, value: str) -> str:
        if not value.startswith(REQUEST_DIGEST_PREFIX):
            raise ValueError(
                f"request_digest precisa começar com {REQUEST_DIGEST_PREFIX!r} -- "
                f"formato de digest desconhecido/não suportado: {value!r}"
            )
        hex_part = value[len(REQUEST_DIGEST_PREFIX) :]
        if len(hex_part) != _DIGEST_HEX_LENGTH or any(
            c not in "0123456789abcdef" for c in hex_part
        ):
            raise ValueError(
                f"request_digest precisa ter exatamente {_DIGEST_HEX_LENGTH} "
                "caracteres hexadecimais minúsculos após o prefixo -- "
                f"recebido: {value!r}"
            )
        return value


def _canonical_completion_request_payload(request: CompletionRequest) -> dict:
    """Representação canônica EXATA dos campos semânticos atuais de
    `CompletionRequest` -- `messages` (role+content, ORDEM preservada),
    `system_prompt`, `model`, `max_tokens`, `temperature`. Nenhum campo
    de provider/resposta/timestamp/tentativa entra aqui -- esses nunca
    são input do `CompletionRequest` em si.

    Fail-closed de deriva de schema (seção 5 do contrato desta slice):
    `tests/models/test_request_provenance.py::test_completion_request_field_set_is_pinned`
    fixa o conjunto EXATO de campos que esta função sabe canonicalizar
    (`messages`, `system_prompt`, `model`, `max_tokens`, `temperature`)
    contra `CompletionRequest.model_fields` -- se um campo semântico
    novo for adicionado a `CompletionRequest` sem que essa função (e a
    versão de canonicalização) seja explicitamente revisada, esse teste
    falha antes de qualquer digest silenciosamente ignorar o campo
    novo."""
    return {
        "system_prompt": request.system_prompt,
        "messages": [
            {"role": message.role, "content": message.content} for message in request.messages
        ],
        "model": request.model,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }


def compute_request_digest(request: CompletionRequest) -> str:
    """Função pura CANÔNICA (a única deste projeto) que computa o
    digest de provenance de um `CompletionRequest` PROVIDER-NEUTRO já
    FINALIZADO -- antes de qualquer tradução específica de provider.

    Canonicalização determinística via `json.dumps` da stdlib:
    - chaves de objeto ordenadas (`sort_keys=True`);
    - separadores compactos (`,`/`:`, sem espaço extra);
    - `ensure_ascii=False` -- Unicode preservado como Unicode, nunca
      escapado \\uXXXX (dois requests com o mesmo texto Unicode sempre
      produzem o mesmo digest, texto ASCII-safe ou não);
    - NENHUMA normalização Unicode (NFC/NFKC/etc.) -- o valor exato de
      `str` em memória é o que entra no digest, verbatim;
    - `null` JSON explícito pra `None` (`system_prompt`/`model`/
      `temperature` ausentes viram `null`, nunca uma chave omitida --
      omitir mudaria a forma canônica e o digest junto);
    - ordem da lista de mensagens preservada EXATAMENTE (nunca
      reordenada -- `sort_keys` afeta só chaves de objeto, nunca ordem
      de lista).

    Codificado UTF-8 antes do SHA-256 -- `hashlib.sha256` exige bytes,
    nunca `str`.

    Prefixo `completion-request-sha256-v1:` -- ver `REQUEST_DIGEST_PREFIX`."""
    payload = _canonical_completion_request_payload(request)
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest_hex = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"{REQUEST_DIGEST_PREFIX}{digest_hex}"


def build_request_provenance(
    contract_version: str, request: CompletionRequest
) -> RequestProvenance:
    """Único helper de construção de `RequestProvenance` do projeto --
    combina a versão de contrato EXPLÍCITA da operação (ex.:
    "judge_v1") com o digest canônico do `CompletionRequest`
    EFETIVAMENTE finalizado. Nenhum dos 8 call sites de produção
    constrói `RequestProvenance`/`request_digest` manualmente -- todos
    delegam pra esta função, sobre o MESMO objeto `request` que é
    entregue a `LLMProvider.complete()` (nunca reconstruído/regenerado
    depois, nunca um request "parecido")."""
    return RequestProvenance(
        contract_version=contract_version,
        request_digest=compute_request_digest(request),
    )
