"""
Estruturas de dados da Provider Layer.

Estas são as únicas estruturas que o resto do sistema (quando existir:
Orchestrator, Debate Engine, etc.) vai conhecer sobre os providers.
Nenhum componente fora de app/providers/ deve saber que um provider é
"OpenAI" ou "Gemini" para interpretar sua resposta — tudo passa por
ProviderResponse.

Semântica de `cost_usd` (Etapa 9 — calculado em `LLMProvider.complete()`, via
`PricingRegistry`, nunca nos adapters concretos): é sempre uma ESTIMATIVA
por tabela de preços públicos configurada localmente, nunca cobrança
real/efetiva (créditos, franquias free-tier, negociação de preço nunca são
modelados). Três estados, nunca confundidos entre si:

- `None` = desconhecido — sem preço registrado pro par (provider, model),
  OU uma falha ocorreu depois que a chamada real já havia começado (a
  request pode ter sido enviada e até processada remotamente antes do
  erro aparecer do nosso lado — nunca assumimos consumo zero nesse caso).
- `0.0` = conhecido-zero — taxa registrada e igual a zero, OU uma falha
  local comprovada (ex.: API key ausente) que nunca chegou a sair do
  processo, então sabemos com certeza que o custo de API é zero.
- `>0.0` = conhecido, calculado a partir de uso × taxa.
"""

from __future__ import annotations

import math
from enum import Enum
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.config import Settings


class Message(BaseModel):
    """Um turno de uma conversa, no formato genérico usado por todos os providers."""

    role: Literal["user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    """Requisição normalizada enviada a qualquer LLMProvider.

    `model` é opcional: se omitido, o provider usa o default configurado
    (ver app/config.py). Isso permite ao chamador não precisar saber
    nomes de modelo específicos na Fase 1 do MVP, mas ainda permite
    escolher um modelo específico quando necessário.
    """

    messages: list[Message] = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int = 1024
    temperature: float | None = None


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderErrorType(str, Enum):
    TIMEOUT = "timeout"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    API_ERROR = "api_error"  # erro do lado do provider (5xx, erro de request malformado, etc.)
    MALFORMED_RESPONSE = "malformed_response"  # resposta 200 mas em formato inesperado
    UNKNOWN = "unknown"


class ModelIdentitySource(str, Enum):
    """Provenance FECHADA de `ProviderResponse.model` (e de todo campo
    que copia essa identidade adiante -- `ModelResponse.model`,
    `*Attempt.model`, `JudgeVerdict.judge_model`,
    `FinalAnswer.editor_model`): resolve a ambiguidade que `model`
    sozinho sempre teve (ver docstring de `ProviderResponse` abaixo) --
    "provider-reported quando disponível, senão requested_model como
    fallback" nunca dizia QUAL dos dois aconteceu numa resposta
    específica.

    - `PROVIDER_REPORTED`: o adapter concreto extraiu um identificador de
      modelo do próprio objeto de resposta do SDK (`response.model`
      OpenAI/Anthropic, `response.model_version` Gemini) -- um FATO
      observado, nunca inferido.
    - `REQUESTED_FALLBACK`: nenhuma identidade foi observada (SDK não
      expôs o campo, ou nenhuma chamada de rede sequer ocorreu -- falha
      pré-request/pré-`_call_api()`) -- `model` recebeu `requested_model`
      como substituto honesto, nunca uma alegação de que o provider
      confirmou aquele identificador.

    Deliberadamente SEM um terceiro valor de enum pra "desconhecido" --
    resolvido `None` no nível dos campos que usam este tipo (`X |
    None`), reservado EXCLUSIVAMENTE para linhas persistidas antes desta
    coluna existir (nunca para uma resposta nova: toda resposta nova
    sempre sabe, no momento em que é construída, qual dos dois valores
    reais se aplica -- ver `LLMProvider.complete()`)."""

    PROVIDER_REPORTED = "provider_reported"
    REQUESTED_FALLBACK = "requested_fallback"


class ProviderErrorInfo(BaseModel):
    type: ProviderErrorType
    message: str
    retryable: bool


class PricingProvenance(BaseModel):
    """O que produziu um `cost_usd` conhecido — nasce dentro de
    `PricingRegistry.price()` (Etapa 10), no exato momento do cálculo, e
    viaja junto com o `cost_usd` correspondente até a persistência. NUNCA
    reconstituída depois consultando o registry atual — se a tabela de
    preços mudar no futuro, isso não pode alterar o que uma resposta
    histórica já registrou aqui.

    `input_rate_usd_per_million_tokens`/`output_rate_usd_per_million_tokens`
    são as taxas EFETIVAMENTE aplicadas àquela chamada específica — já
    multiplicadas pelo fator de long-context quando `tier="long_context"`
    (ex.: GPT-5.5 acima de 272K tokens de input: `input_rate_usd_per_million_tokens=10.0`,
    não `5.0` com um multiplicador separado). `source_id` identifica a
    tabela de preços usada (ex.: `DEFAULT_PRICING_REGISTRY.source_id`) —
    um identificador pequeno e explícito, não um sistema de
    versionamento/migração.

    Sempre acompanha um `cost_usd` conhecido — mas a implicação só vale
    numa direção: `cost_usd is None` sempre implica `pricing_provenance
    is None` (nunca inventamos proveniência pra um custo desconhecido).
    A volta não vale: existe um caso legítimo de `cost_usd=0.0` SEM
    provenance — a falha local pré-request (API key ausente, Etapa 9),
    porque aquela chamada nunca chegou a passar pelo `PricingRegistry`
    (não houve chamada real pra precificar).

    Patch de cobertura de pricing snapshot — `canonical_model_id`:
    `None` quando a chave `(provider, model)` bateu DIRETAMENTE na
    tabela — o `model`/`effective_model` já é, ele mesmo, o identifier
    precificado, sem alias envolvido (todo o histórico anterior a este
    patch, e a maioria dos casos daqui pra frente, cai aqui). Preenchido
    apenas quando `PricingRegistry.price()` resolveu a taxa através de
    um alias explícito de snapshot (`PricingRegistry.snapshot_aliases`)
    — contém o identifier CANÔNICO cuja taxa foi efetivamente usada
    (ex.: `"gpt-5.5"`), que é diferente do `model`/`effective_model`
    original (ex.: `"gpt-5.5-2026-04-23"`). Isso nunca reescreve
    `ProviderResponse.model`/`requested_model` — só documenta, pra
    auditoria, que a taxa aplicada veio de um modelo canônico distinto
    do identifier RESOLVIDO usado pra precificar (`model` -- provider-
    reported ou requested-model fallback, ver `ModelIdentitySource`;
    qual dos dois foi é rastreado separadamente por
    `ProviderResponse.model_identity_source`, nunca por este campo).
    Default `None` preserva leitura de registros históricos persistidos
    antes deste campo existir (chave ausente no JSON -> Pydantic aplica
    o default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    tier: Literal["standard", "long_context"]
    input_rate_usd_per_million_tokens: float = Field(ge=0)
    output_rate_usd_per_million_tokens: float = Field(ge=0)
    canonical_model_id: str | None = None


class ProviderExecutionPolicy(BaseModel):
    """T02.2 -- snapshot IMUTÁVEL da política de transporte (timeout +
    retry) que um deployment tinha REALMENTE configurado no momento em
    que um Run foi aceito. Resolvido UMA ÚNICA VEZ por aplicação
    composta (`from_settings`, chamado uma vez em `app/bootstrap.py`) e
    injetado na MESMA instância em todo `LLMProvider` construído
    (`app/providers/factory.py`) E em `CouncilExecutionService`
    (`app/application/service.py`) -- nunca duas resoluções
    independentes que possam divergir entre "o que os providers
    realmente aplicam" e "o que fica registrado no Run".

    Deliberadamente SEPARADO de `RunConfig`
    (`app/orchestrator/config.py`): `RunConfig` nunca aplica política de
    transporte nenhuma -- é `LLMProvider` quem aplica, e `LLMProvider`
    nunca lê `RunConfig`. Persistir estes dois números DENTRO de
    `RunConfig` criaria uma autoridade duplicada/potencialmente
    contraditória (RunConfig "diria" um timeout que ninguém garante que
    o LLMProvider realmente aplicou). Este objeto é só um SNAPSHOT de
    provenance de deployment -- nunca a autoridade de enforcement em si
    (ver relatório T02.2, "AUTHORITY MODEL").

    Contém APENAS os dois números normalizados abaixo -- nunca backoff,
    jitter, timeout nativo do SDK, retry nativo do SDK (esses continuam
    exclusivamente desligados/configurados dentro de cada adapter
    concreto, ver `AnthropicProvider`/`OpenAIProvider`/`GeminiProvider`),
    nome de provider/model, endpoint, ou segredo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Teto de espera EM NÍVEL DE APLICAÇÃO ao redor de UMA invocação de
    # `_call_api` (`LLMProvider.complete`, via `asyncio.wait_for`) --
    # reseta a cada tentativa de transporte normalizada, EXCLUI o tempo
    # de backoff entre tentativas (`_backoff_delay`, fora deste
    # timeout). NÃO afirma representar o timeout nativo de
    # conexão/leitura default do SDK -- isso é responsabilidade
    # exclusiva de cada client concreto, nunca modelado aqui.
    attempt_timeout_seconds: float = Field(gt=0)

    # Número máximo de invocações de `_call_api` dentro de UM
    # `LLMProvider.complete()` -- derivado de `provider_max_retries + 1`
    # (ver `from_settings`), NUNCA persistido/nomeado como "max
    # retries" (esse número descreveria retries, não o teto real de
    # tentativas -- ver relatório T02.2). Descreve só tentativas de
    # TRANSPORTE normalizadas -- nunca o contador de retry de output
    # ESTRUTURADO (ClaimProcessingAttempt.attempt_number/
    # SourceAnalysisAttempt.attempt_number/JudgeAttempt.attempt_number/
    # EditorAttempt.attempt_number são contadores INTEIRAMENTE
    # DISTINTOS, nunca renomeados/colapsados com este).
    max_transport_attempts_per_completion: int = Field(ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderExecutionPolicy":
        """Único ponto de resolução Settings -> ProviderExecutionPolicy
        do projeto inteiro -- chamado uma única vez, em
        `app/bootstrap.py` (composition root), nunca dentro de
        `app/providers/factory.py`/`CouncilExecutionService`/da camada
        de persistência (que só recebem o valor JÁ resolvido). Falha
        (ValidationError) se `provider_timeout_seconds<=0` ou
        `provider_max_retries<0` -- mesma convenção de configuração já
        usada por `QuorumPolicy.from_settings`/`RunConfig.from_settings`
        (app/orchestrator/config.py): nenhum wrapper de erro especial,
        nenhum clamp silencioso, a validação de campo do Pydantic já é
        a falha de configuração."""
        return cls(
            attempt_timeout_seconds=float(settings.provider_timeout_seconds),
            max_transport_attempts_per_completion=settings.provider_max_retries + 1,
        )


def validate_provider_execution_policy_for_new_execution(
    policy: ProviderExecutionPolicy,
) -> None:
    """Provider Execution Policy Finite New-Execution Boundary V1 --
    ÚNICA função de validação de FACTIBILIDADE de
    `ProviderExecutionPolicy` pra ACEITE DE EXECUÇÕES NOVAS (mesma
    disciplina de `validate_question`/`validate_quorum_feasibility`,
    app/orchestrator/config.py):

        math.isfinite(policy.attempt_timeout_seconds)
        and policy.attempt_timeout_seconds > 0

    `ProviderExecutionPolicy` (`Field(gt=0)`, sem `allow_inf_nan=False`)
    continua CONSTRUÍVEL diretamente com `attempt_timeout_seconds=+inf`
    -- deliberadamente: `_policy_from_json`
    (app/storage/repository.py) reconstrói o objeto a partir de JSON
    persistido, e um deployment de ANTES desta correção existir não
    podia produzir +inf pelo caminho suportado
    (`Settings.provider_timeout_seconds` é `int`, então
    `float(int)` nunca é +inf/NaN) -- mas mesmo que um valor assim
    tivesse sido persistido, ele precisa continuar reconstruível
    verbatim pra auditoria, nunca rejeitado/reinterpretado na carga
    histórica. CONSTRUÍVEL != AUTORIZADO PRA EXECUÇÃO NOVA: esta função
    é a boundary separada que aplica a segunda metade dessa distinção,
    chamada pelas autoridades de composição de execução NOVA
    (`build_all_providers`, `CouncilExecutionService.__init__`), NUNCA
    pela reconstrução histórica.

    ÚNICO ponto de comparação numérica desta regra no repositório --
    qualquer chamador delega aqui, nunca reimplementa a comparação."""
    value = policy.attempt_timeout_seconds
    if not (math.isfinite(value) and value > 0):
        raise ValueError(
            "ProviderExecutionPolicy.attempt_timeout_seconds "
            f"({value!r}) não é válido pra execução nova -- precisa ser positivo "
            "e finito, mesmo que o objeto em si permaneça construível com outros "
            "valores pra fins de reconstrução histórica."
        )


class DefaultModelAuthoritySnapshot(BaseModel):
    """Provider Default-Model Snapshot Provenance V1 -- snapshot IMUTÁVEL,
    tomado no instante do ACEITE de uma execução NOVA, da autoridade de
    modelo padrão/fallback CONFIGURADA em cada provider autorizado
    (`RunConfig.all_provider_authorities`) pra essa execução --
    construído a partir dos objetos `LLMProvider` REALMENTE instanciados
    no registry de runtime vigente naquele momento (`provider.default_model`),
    nunca recalculado depois a partir de `Settings`/objetos de provider
    reconstruídos posteriormente.

    O QUE ISTO SIGNIFICA, exclusivamente:

        "esta era a autoridade de modelo fallback configurada, disponível
        a esta execução aceita, no momento do aceite."

    O QUE ISTO NÃO SIGNIFICA (nunca deve ser apresentado como
    significando):

    - que aquele provider foi de fato chamado;
    - que aquele modelo foi solicitado em algum `CompletionRequest`
      (isso é `RequestProvenance`/`CompletionRequest.model`, inteiramente
      separado -- ver `app/models/request_provenance.py`);
    - que aquele modelo foi reportado pelo provider como tendo executado
      (isso é `ModelIdentitySource`/`ModelResponse.model` -- inteiramente
      separado);
    - que a chamada teve sucesso;
    - que aquele modelo estava disponível remotamente;
    - que as credenciais eram válidas.

    Deliberadamente nomeado `configured_default_models` (nunca
    `models_used`/`models_executed`/`requested_models`) -- nenhum desses
    rótulos seria verdadeiro sobre o que este snapshot prova.

    Existe EXCLUSIVAMENTE pra fechar um gap de auditoria: execuções
    aceitas/em andamento/falhas inesperadas podem não ter NENHUMA
    evidência de resposta de provider persistida (falha antes de
    qualquer child record existir) -- sem este snapshot, um restart de
    processo ou mudança de configuração de deployment tornaria
    impossível recuperar qual autoridade de modelo fallback estava
    configurada quando aquela execução foi aceita.

    Chaves são os identificadores canônicos de provider EXATAMENTE de
    `RunConfig.all_provider_authorities` (participantes selecionados +
    os 4 papéis internos, deduplicados por construção de `frozenset`) --
    nunca todo provider instalado, nunca todo provider do Settings.

    F2 (repair pós-revisão independente, MEDIUM) -- `frozen=True` do
    Pydantic só impede REATRIBUIR o campo (`snapshot.configured_default_models
    = {...}`); o `dict` mutável por baixo continuava aceitando mutação
    de ITEM (`snapshot.configured_default_models["x"] = "y"`) sem
    levantar nada -- uma cópia defensiva sozinha não bastaria, porque o
    campo EXPOSTO continuaria mutável pra qualquer referência que o
    chamador guardasse. `configured_default_models` é tipado como
    `Mapping[str, str]` (nunca `dict[str, str]`) e o validador abaixo
    congela o valor validado num `types.MappingProxyType` -- item
    assignment levanta `TypeError` genuíno, em toda instância (aceita,
    reconstruída de storage, ou pública), sem exceção. `field_serializer`
    devolve um `dict` comum na serialização -- o formato de wire/
    persistência (`{"configured_default_models": {"provider": "model",
    ...}}`) permanece BYTE-IDÊNTICO ao de antes deste repair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    configured_default_models: Mapping[str, str] = Field(min_length=1)

    @field_validator("configured_default_models")
    @classmethod
    def _provider_and_model_identifiers_are_well_formed(
        cls, value: Mapping[str, str]
    ) -> MappingProxyType[str, str]:
        for provider_name, model_name in value.items():
            if not provider_name or not provider_name.strip():
                raise ValueError(
                    "configured_default_models não pode ter chave de provider vazia"
                )
            if not model_name or not model_name.strip():
                raise ValueError(
                    f"configured_default_models[{provider_name!r}] não pode ser um "
                    "modelo padrão vazio/só espaço em branco"
                )
        # Congela DEPOIS de validar -- devolve um mapping genuinamente
        # imutável (item assignment levanta TypeError), nunca o dict
        # mutável original recebido como input.
        return MappingProxyType(dict(value))

    @field_serializer("configured_default_models")
    def _serialize_configured_default_models(
        self, value: Mapping[str, str]
    ) -> dict[str, str]:
        # O wire format continua um dict JSON comum -- MappingProxyType
        # é só a representação INTERNA imutável, nunca o formato
        # persistido/público.
        return dict(value)


class ProviderResponse(BaseModel):
    """Resposta normalizada — o único formato que o resto do sistema conhece.

    Etapa 13 (T03.A) — `requested_model` vs `model`:

    - `requested_model`: o identificador exato que foi SOLICITADO nesta
      chamada (`CompletionRequest.model` OR `provider.default_model`),
      resolvido uma única vez ANTES de qualquer chamada de rede em
      `LLMProvider.complete()`. Provenance histórica, imutável durante
      retries/falhas — não muda mesmo que a chamada seja reexecutada.
    - `model`: campo já existente, preservado por compatibilidade.
      Semântica inalterada: provider-reported model quando o
      provider/SDK expõe essa informação, senão `requested_model` como
      fallback. NÃO é prova de que o provider informou um
      snapshot/version efetivamente executado — apenas
      "provider-reported quando disponível, senão o que foi pedido".
    - `model_identity_source`: resolve a ambiguidade acima explicitamente
      -- ver `ModelIdentitySource`. NUNCA `None` aqui (diferente dos
      campos que copiam esta identidade adiante em storage/apresentação):
      toda `ProviderResponse` é construída FRESCA em tempo de execução
      (nunca reconstruída de uma linha histórica), então sempre existe
      um dos dois valores reais no momento da criação -- resolvido uma
      única vez, dentro de `LLMProvider.complete()` (nunca recalculado
      pelos 3 adapters concretos, que só expõem o valor CRU observado ou
      `None`).
    """

    provider: str
    requested_model: str
    model: str
    model_identity_source: ModelIdentitySource
    status: Literal["success", "error"]
    text: str | None = None
    usage: TokenUsage | None = None
    cost_usd: float | None = None  # ver docstring do módulo — None/0.0/>0, nunca confundidos
    # Etapa 10 — ver docstring de PricingProvenance. None sempre que
    # cost_usd for None; também None no caso pré-request known-zero
    # (cost_usd=0.0 sem ter passado pelo PricingRegistry).
    pricing_provenance: PricingProvenance | None = None
    latency_ms: int
    attempts: int
    # Etapa 17A (B3) — True sse ao menos UMA tentativa de transporte
    # ANTES da última (a que produziu este ProviderResponse, sucesso ou
    # falha) já tinha discado de verdade (chegou a `_call_api()`) e
    # falhou. Independente do status final: `attempts > 1` já implica
    # isso, sempre — sinal ADICIONAL de incompletude de accounting, nunca
    # substitui `cost_usd`/`usage` conhecidos por None. Um único sucesso
    # (attempts=1) ou uma única falha final (attempts=1) são sempre
    # False -- não há tentativa "anterior" a uma tentativa única.
    had_uncertain_prior_attempts: bool = False
    # Etapa 17A.1 (Objetivo B) — motivo de parada NATIVO do provider
    # (ex.: "end_turn"/"max_tokens" da Anthropic, "stop"/"length" da
    # OpenAI, "STOP"/"MAX_TOKENS" da Gemini), preservado VERBATIM, nunca
    # normalizado pra um enum cross-provider inventado (os três providers
    # têm vocabulários genuinamente diferentes -- fingir que são o mesmo
    # esconderia informação real). `None` quando desconhecido/indisponível
    # (nunca inferido/fabricado) -- inclusive em qualquer falha ANTES de
    # `_call_api()` (nada foi observado) e em falhas onde o SDK não expôs
    # esse campo de forma confiável.
    provider_finish_reason: str | None = None
    error: ProviderErrorInfo | None = None

    @model_validator(mode="after")
    def _unknown_cost_never_has_provenance(self) -> ProviderResponse:
        if self.cost_usd is None and self.pricing_provenance is not None:
            raise ValueError(
                "cost_usd=None não pode ter pricing_provenance preenchido — "
                "nunca inventamos proveniência pra um custo desconhecido"
            )
        return self
