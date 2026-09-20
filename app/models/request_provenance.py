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

# Repair (adversarial review, Finding C) -- a canonicalização do request
# tem sua PRÓPRIA versão, sempre que a FORMA do payload canônico muda
# (nunca confundida com `contract_version`, que versiona o CONTEÚDO
# semântico de uma OPERAÇÃO específica, ex.: "judge_v1"/"claim_extraction_v2"
# -- ver `RequestProvenance.contract_version` abaixo). v1 é o payload
# ORIGINAL (5 campos: system_prompt/messages/model/max_tokens/temperature)
# -- preservado aqui EXCLUSIVAMENTE como formato HISTÓRICO reconhecido
# na leitura/validação, nunca recomputado (nenhum request novo produz
# v1 a partir deste patch em diante). v2 (6 campos, adiciona
# `minimal_reasoning`) é o formato de TODA computação nova a partir daqui
# -- ver `_canonical_completion_request_payload`/`compute_request_digest`.
REQUEST_DIGEST_PREFIX_V1 = "completion-request-sha256-v1:"
REQUEST_DIGEST_PREFIX_V2 = "completion-request-sha256-v2:"
# Alias deliberado pro prefixo que TODA computação NOVA usa -- nunca uma
# escolha runtime entre v1/v2 (só existe UMA versão de ESCRITA em
# qualquer momento; v1 nunca é escrito de novo, só lido). Mantido com
# este nome (sem sufixo `_V2`) por compatibilidade de import -- todo
# call site de produção/teste que já referenciava `REQUEST_DIGEST_PREFIX`
# continua funcionando, sempre apontando pro prefixo ATUAL de escrita.
REQUEST_DIGEST_PREFIX = REQUEST_DIGEST_PREFIX_V2
# Formatos RECONHECIDOS na VALIDAÇÃO/LEITURA -- v1 continua aqui
# indefinidamente (nenhuma migração de linha histórica, ver docstring do
# módulo) mesmo que a escrita nova seja sempre v2.
_SUPPORTED_DIGEST_PREFIXES = (REQUEST_DIGEST_PREFIX_V1, REQUEST_DIGEST_PREFIX_V2)
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
        """Repair (adversarial review, Finding C) -- aceita QUALQUER
        prefixo em `_SUPPORTED_DIGEST_PREFIXES` (v1 histórico OU v2
        atual), nunca só o prefixo de ESCRITA corrente -- uma linha v1
        persistida antes deste patch precisa continuar validando/
        recarregando exatamente como antes, sem nenhuma migração."""
        matched_prefix = next(
            (prefix for prefix in _SUPPORTED_DIGEST_PREFIXES if value.startswith(prefix)),
            None,
        )
        if matched_prefix is None:
            raise ValueError(
                f"request_digest precisa começar com um dos prefixos suportados "
                f"{_SUPPORTED_DIGEST_PREFIXES!r} -- formato de digest desconhecido/"
                f"não suportado: {value!r}"
            )
        hex_part = value[len(matched_prefix) :]
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
    """Payload canônico v2 (ver `REQUEST_DIGEST_PREFIX_V2`) -- representação
    canônica EXATA dos campos semânticos atuais de `CompletionRequest`:
    `messages` (role+content, ORDEM preservada), `system_prompt`, `model`,
    `max_tokens`, `temperature`, `minimal_reasoning`. Nenhum campo de
    provider/resposta/timestamp/tentativa entra aqui -- esses nunca são
    input do `CompletionRequest` em si.

    Fail-closed de deriva de schema (seção 5 do contrato original):
    `tests/models/test_request_provenance.py::test_completion_request_field_set_is_pinned`
    fixa o conjunto EXATO de campos que esta função sabe canonicalizar
    contra `CompletionRequest.model_fields` -- se um campo semântico
    novo for adicionado a `CompletionRequest` sem que essa função (e a
    versão de canonicalização) seja explicitamente revisada, esse teste
    falha antes de qualquer digest silenciosamente ignorar o campo novo.

    Repair (adversarial review, Finding C) -- `minimal_reasoning` (Run02
    claim-extraction exhaustion) foi o campo que forçou esta função a
    avançar de v1 (5 campos) pra v2 (6 campos, este payload) -- É um
    campo REQUEST-LEVEL (ver docstring de `CompletionRequest.minimal_reasoning`),
    então participa do digest como qualquer outro. Como é um campo NOVO
    (nunca existiu antes; NA ÉPOCA deste patch sempre `False` pra toda
    chamada que não fosse extração -- hoje o agrupamento intra-round
    também o seta `True`, ver abaixo), a forma canônica de TODO request
    NOVO passou a incluí-lo -- inclusive as 7 operações que NÃO setavam
    `minimal_reasoning=True` (só ganham `"minimal_reasoning":false`
    explícito no payload). Isso
    NÃO é uma mudança de CONTRATO DE REQUEST dessas 7 operações (prompt/
    mensagens/model/max_tokens/temperature permanecem byte-idênticos pra
    elas) -- é uma evolução do FORMATO DE CANONICALIZAÇÃO em si, por
    isso versionada separadamente (`REQUEST_DIGEST_PREFIX_V1` ->
    `REQUEST_DIGEST_PREFIX_V2`, nunca a `contract_version` de cada
    operação). Neste patch só `CLAIM_EXTRACTION_CONTRACT_VERSION` avançou
    (v1->v2, mudança semântica REAL: prompt + `minimal_reasoning=True`);
    as outras 7 constantes de `contract_version` não avançam por causa
    desta mudança de formato de canonicalização, mesmo que seus goldens
    em `tests/models/test_request_provenance_contracts.py` agora
    apareçam com o prefixo v2 (ver nota de governança no topo daquele
    arquivo).

    Posteriormente, `CLAIM_GROUPING_CONTRACT_VERSION` também avançou
    (v1->v2), mas por um motivo INDEPENDENTE desta mudança de formato: o
    agrupamento intra-round passou a pedir `minimal_reasoning=True`
    (mudança de política de request da operação; prompt/schema/validação
    inalterados). Depois, `JUDGE_CONTRACT_VERSION` avançou (judge_v1 ->
    judge_v2) pelo mesmo tipo de motivo: o Judge passou a pedir
    `minimal_reasoning=True` (prompt/schema/validação inalterados;
    judge_v1 é histórico, com `minimal_reasoning=False`). Reconciliação e
    as demais operações seguem nas versões originais e com
    `minimal_reasoning=False`."""
    return {
        "system_prompt": request.system_prompt,
        "messages": [
            {"role": message.role, "content": message.content} for message in request.messages
        ],
        "model": request.model,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "minimal_reasoning": request.minimal_reasoning,
    }


def compute_request_digest(request: CompletionRequest) -> str:
    """Função pura CANÔNICA (a única deste projeto) que computa o
    digest de provenance de um `CompletionRequest` PROVIDER-NEUTRO já
    FINALIZADO -- antes de qualquer tradução específica de provider.
    SEMPRE produz o formato de ESCRITA atual (v2, ver
    `REQUEST_DIGEST_PREFIX_V2`) -- nunca v1, que só é reconhecido na
    LEITURA/validação de linhas históricas já persistidas (ver
    `RequestProvenance._digest_matches_supported_format`).

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

    Prefixo `completion-request-sha256-v2:` -- ver `REQUEST_DIGEST_PREFIX_V2`."""
    payload = _canonical_completion_request_payload(request)
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest_hex = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"{REQUEST_DIGEST_PREFIX_V2}{digest_hex}"


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
