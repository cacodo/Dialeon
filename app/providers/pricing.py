"""
`PricingRegistry` — Etapa 9, com pricing provenance (Etapa 10).

Calcula `cost_usd` a partir de `(provider, effective_model, usage)`. Deliberadamente
NÃO é um `CostTracker` stateful — não guarda nenhum estado sobre chamadas já feitas,
só a tabela de preços em si, que é imutável depois de construída.

`cost_usd` é sempre ESTIMATIVA por tabela de preços públicos configurada localmente
— nunca cobrança real/efetiva (créditos, franquias free-tier, negociação de preço
nunca são modelados). Três estados possíveis, nunca confundidos entre si:

- `None` = desconhecido — sem entrada na tabela pra esse par (provider, model).
- `0.0` = conhecido-zero — taxa registrada e igual a zero (modelo genuinamente
  gratuito, incluindo self-hosted/local: sabemos que a API não cobra, então É
  conhecido-zero, nunca desconhecido — custos indiretos como energia/hardware
  continuam inteiramente fora de escopo).
- `>0.0` = conhecido, calculado a partir de uso × taxa.

Preço nunca é inferido por "marca" do provider inteiro — cada par
`(provider, effective_model)` precisa da própria entrada explícita.

Etapa 10 — pricing provenance: `price()` devolve, junto com o custo, exatamente
o que foi usado pra calculá-lo (`PricingProvenance` — taxa de input/output
EFETIVAMENTE aplicada, tier, identidade da tabela). Isso nasce aqui, no momento
do cálculo, porque é o único lugar do sistema onde `ModelRate`/tier escolhido
ficam visíveis simultaneamente — depois deste ponto, só o `cost_usd` (float
solto) seguia adiante, e não dava pra reconstruir historicamente COM QUE taxa
ele foi calculado sem consultar o registry ATUAL (que pode já ter mudado).
`PricingProvenance` resolve isso sendo carregada junto, nunca reconstituída.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.models.provider_models import PricingProvenance, TokenUsage


@dataclass(frozen=True)
class ModelRate:
    """Taxa de um par (provider, model) — imutável (frozen=True), USD por
    1 milhão de tokens. Nunca negativa (validado no __post_init__).

    `long_context_*` (opcionais, todos-ou-nenhum): alguns modelos cobram
    tarifa diferente pra sessão INTEIRA quando o input excede um limiar —
    ex.: GPT-5.5 acima de 272K tokens de input cobra 2x input e 1.5x
    output pra sessão inteira (fonte oficial, ver tabela default). É uma
    extensão declarativa mínima — não uma engine de tiers genérica (sem
    cache pricing, batch, priority, etc., que continuam fora de escopo)."""

    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float
    long_context_threshold_tokens: int | None = None
    long_context_input_multiplier: float | None = None
    long_context_output_multiplier: float | None = None

    def __post_init__(self) -> None:
        if self.input_usd_per_million_tokens < 0 or self.output_usd_per_million_tokens < 0:
            raise ValueError("ModelRate não aceita taxas negativas")
        long_context_fields = (
            self.long_context_threshold_tokens,
            self.long_context_input_multiplier,
            self.long_context_output_multiplier,
        )
        some_set = any(f is not None for f in long_context_fields)
        all_set = all(f is not None for f in long_context_fields)
        if some_set and not all_set:
            raise ValueError(
                "long_context_threshold_tokens/long_context_input_multiplier/"
                "long_context_output_multiplier devem ser todos preenchidos ou "
                "todos None — não faz sentido ter threshold sem multiplicador"
            )
        if self.long_context_input_multiplier is not None and (
            self.long_context_input_multiplier < 0 or self.long_context_output_multiplier < 0
        ):
            raise ValueError("ModelRate não aceita multiplicadores de long-context negativos")


@dataclass(frozen=True)
class PricedCost:
    """Resultado de `PricingRegistry.price()` quando o custo é calculável —
    `cost_usd` e `provenance` sempre juntos, nunca um sem o outro (ao
    contrário de `price()` como um todo, que pode devolver `None`)."""

    cost_usd: float
    provenance: PricingProvenance


class PricingRegistry:
    """Tabela de preços imutável. `rates` é copiado e envolvido numa view
    somente-leitura (MappingProxyType) no construtor — mutar o dict
    original DEPOIS de construir o registry não afeta esta instância, e
    nenhum método de mutação é exposto. Tabela de preços nova = instância
    nova, nunca mutação da existente — isso também garante que uma
    execução em andamento nunca observa preços diferentes no meio do
    pipeline.

    `source_id` identifica esta tabela pra fins de auditoria histórica
    (`PricingProvenance.source_id`) — uma string pequena e explícita, não
    um sistema de versionamento/migração. Tem um default pragmático
    (`"unspecified"`) só pra reduzir ruído em testes que usam um registry
    vazio (onde `source_id` nunca chega a ser lido, porque `price()`
    sempre devolve `None` antes de precisar dele); `DEFAULT_PRICING_REGISTRY`
    sempre define um valor real explícito.

    Patch de cobertura de pricing snapshot — `snapshot_aliases`: tabela
    EXPLÍCITA e provider-scoped de `(provider, effective_model_snapshot)
    -> canonical_model`, separada da tabela de taxas (`rates`). Existe
    porque um provider pode reportar como `effective_model` um snapshot
    datado concreto (ex.: `"gpt-5.5-2026-04-23"`) que nunca vai bater
    exatamente contra a entrada precificada da família (`"gpt-5.5"`),
    mesmo sendo uma versão conhecida e coberta dessa família. `price()`
    só consulta este alias quando o lookup direto em `rates` falha, e só
    resolve identifiers especificamente registrados aqui — nunca por
    prefixo, regex de sufixo de data, ou fallback genérico (ver
    docstring de `price()`). Cada entrada precisa apontar pra um par
    (provider, canonical_model) que já tem taxa em `rates` — validado no
    construtor — porque um alias sem taxa-alvo seria dead config, nunca
    um "quase-preço". Um snapshot ausente daqui permanece desconhecido
    (`None`), igual a qualquer outro par não registrado."""

    def __init__(
        self,
        rates: dict[tuple[str, str], ModelRate],
        source_id: str = "unspecified",
        snapshot_aliases: dict[tuple[str, str], str] | None = None,
    ):
        self._rates: Mapping[tuple[str, str], ModelRate] = MappingProxyType(dict(rates))
        self._source_id = source_id
        aliases = dict(snapshot_aliases) if snapshot_aliases is not None else {}
        for (alias_provider, alias_model), canonical_model in aliases.items():
            if (alias_provider, canonical_model) not in self._rates:
                raise ValueError(
                    f"snapshot_aliases mapeia ({alias_provider!r}, {alias_model!r}) -> "
                    f"{canonical_model!r}, mas ({alias_provider!r}, {canonical_model!r}) "
                    "não tem taxa registrada em `rates` -- um alias não pode apontar pra "
                    "um modelo canônico que a própria tabela não precifica"
                )
        self._snapshot_aliases: Mapping[tuple[str, str], str] = MappingProxyType(aliases)

    @property
    def source_id(self) -> str:
        return self._source_id

    def price(self, provider: str, model: str, usage: TokenUsage) -> PricedCost | None:
        """Custo estimado da chamada + proveniência, ou None se não há
        taxa registrada pra esse par (provider, model) — nunca inferido,
        nunca chutado.

        Lookup em duas etapas, nunca fuzzy: primeiro tenta `(provider,
        model)` direto em `rates`; só se isso falhar, tenta resolver
        `model` como um snapshot conhecido via `snapshot_aliases` (mesmo
        provider) pro identifier canônico, e tenta `rates` de novo com
        esse canônico. Se nem o direto nem o alias baterem, `None` --
        nenhum outro caminho de resolução existe. Quando a resolução usa
        alias, `PricingProvenance.canonical_model_id` guarda o
        identifier canônico usado, SEM jamais reescrever `model`
        (effective_model) em lugar nenhum -- pricing e identidade de
        execução são preocupações separadas.

        Também retorna None se `usage.input_tokens`/`usage.output_tokens`
        forem `None` — desconhecido não vira zero aqui (Etapa 9, patch
        pós-revisão): `TokenUsage(None, None)` significa "não sabemos
        quanto foi consumido", não "consumiu zero". Só `TokenUsage(0, 0)`
        (zero EXPLÍCITO) é conhecido-zero de verdade. Isso é o único
        lugar que precisa dessa checagem — nenhuma lógica
        provider-specific em lugar nenhum: qualquer adapter que produza
        `usage` parcialmente desconhecido já cai aqui automaticamente."""
        rate = self._rates.get((provider, model))
        canonical_model: str | None = None
        if rate is None:
            canonical_model = self._snapshot_aliases.get((provider, model))
            if canonical_model is not None:
                rate = self._rates.get((provider, canonical_model))
        if rate is None:
            return None
        if usage.input_tokens is None or usage.output_tokens is None:
            return None
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens

        is_long_context = (
            rate.long_context_threshold_tokens is not None
            and input_tokens > rate.long_context_threshold_tokens
        )
        if is_long_context:
            input_rate = rate.input_usd_per_million_tokens * rate.long_context_input_multiplier
            output_rate = rate.output_usd_per_million_tokens * rate.long_context_output_multiplier
            tier = "long_context"
        else:
            input_rate = rate.input_usd_per_million_tokens
            output_rate = rate.output_usd_per_million_tokens
            tier = "standard"

        cost_usd = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
        provenance = PricingProvenance(
            source_id=self._source_id,
            tier=tier,
            input_rate_usd_per_million_tokens=input_rate,
            output_rate_usd_per_million_tokens=output_rate,
            canonical_model_id=canonical_model,
        )
        return PricedCost(cost_usd=cost_usd, provenance=provenance)


# ---------------------------------------------------------------------------
# Tabela default -- precos verificados em fontes OFICIAIS em 2026-09-04/05.
#
# Cada entrada corresponde exatamente ao par (provider, effective_model) --
# a mesma string que o provider real devolve como ProviderResponse.model
# (confirmado contra Settings.{openai,anthropic,gemini}_default_model, que
# ja usam esses nomes exatos). Nenhum modelo/tarifa nao confirmavel entrou
# nesta tabela -- pares ausentes daqui sempre devolvem None (desconhecido).
#
# openai / gpt-5.5:
#   $5.00 input / $30.00 output por 1M tokens (standard, <=272K input).
#   Acima de 272K tokens de input, a sessao INTEIRA e cobrada a 2x
#   input / 1.5x output -- fonte oficial confirma isso explicitamente,
#   por isso o long-context threshold/multiplier abaixo, em vez de
#   deixar cost_usd errado (subprecificado) pra prompts grandes.
#   Fontes oficiais:
#   - openai.com/index/introducing-gpt-5-5/ ("$5 per 1M input tokens and
#     $30 per 1M output tokens").
#   - developers.openai.com/api/docs/models/gpt-5.5 ("For GPT-5.5, prompts
#     with >272K input tokens are priced at 2x input and 1.5x output for
#     the full session for standard, batch, and flex").
#
# anthropic / claude-sonnet-5:
#   $2.00 input / $10.00 output por 1M tokens. Sem tier de long-context
#   documentado além do padrao (1M de contexto, tarifa unica).
#   Fonte oficial: Anthropic, Claude Platform Docs -- Pricing --
#   platform.claude.com/docs/en/about-claude/pricing (tabela "Model
#   pricing", linha "Claude Sonnet 5": "$2 / MTok" input, "$10 / MTok"
#   output; a pagina confirma explicitamente que esse e o preco padrao
#   atual, nao so introdutorio: "The $2/$10 per million input/output
#   token pricing for Claude Sonnet 5 ... is now the standard price").
#
# gemini / gemini-3.7-flash:
#   $0.75 input / $3.75 output por 1M tokens (preco introdutorio vigente
#   ate 2026-12-31; sobe pra $1.50/$7.50 em 2027-01-01 -- a tabela reflete
#   o preco EM VIGOR na data desta implementacao, sem tentar antecipar a
#   mudanca futura).
#   Fonte oficial: Google, Gemini API docs -- "What's new in Gemini 3.8
#   Flash" -- ai.google.dev/gemini-api/docs/latest-model ("Gemini 3.7
#   Flash ... available at an introductory rate of $0.75/1M input tokens
#   and $3.75/1M output tokens through December 31, 2026"), corroborado
#   por cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing.
#
#   Chave de pricing == effective_model == response.model_version (ver
#   GeminiProvider._parse_response). Evidência real de um response de
#   exemplo mostra model_version retornando a string do modelo pinado
#   verbatim, sem sufixo (ex.: "gemini-2.5-flash"), mas isso NÃO é
#   garantido por documentação oficial explícita para todo request —
#   se `model_version` divergir de "gemini-3.7-flash" nalgum caso real,
#   PricingRegistry.price() simplesmente devolve None (chave não bate),
#   nunca um preço errado — nenhum fallback frouxo/prefixo foi
#   implementado (ver docstring de PricingRegistry.price()).
#
# Fora de escopo desta correção (dívida documentada, não implementada):
# cached/prompt-caching pricing, Batch API, Priority/Fast mode, e
# quaisquer outros tiers de serviço de qualquer provider -- o app hoje
# não usa nenhum desses caminhos, só a chamada standard síncrona.
# ---------------------------------------------------------------------------
#
# Patch de cobertura de pricing snapshot (pós-Etapa 17A.2) -- defeito
# confirmado em execução real: a OpenAI reportou como effective_model o
# snapshot datado "gpt-5.5-2026-04-23" pra uma chamada de "gpt-5.5", e
# esse snapshot não batia contra a entrada acima (chave exata
# ("openai", "gpt-5.5")), resultando em cost_usd=None/pricing_provenance
# None mesmo sendo uma versão conhecida e coberta da família GPT-5.5.
#
# `snapshot_aliases` abaixo é a correção: mapeamento EXPLÍCITO e
# provider-scoped de snapshot conhecido -> identifier canônico já
# precificado acima. Só o par confirmado por evidência real entra aqui
# -- nenhum snapshot futuro especulativo, nenhum prefixo/regex de data.
# Um snapshot ausente desta tabela (ex.: um "gpt-5.5-2099-01-01"
# hipotético) continua desconhecido (None), exatamente como antes deste
# patch.
DEFAULT_PRICING_REGISTRY = PricingRegistry(
    {
        ("openai", "gpt-5.5"): ModelRate(
            input_usd_per_million_tokens=5.00,
            output_usd_per_million_tokens=30.00,
            long_context_threshold_tokens=272_000,
            long_context_input_multiplier=2.0,
            long_context_output_multiplier=1.5,
        ),
        ("anthropic", "claude-sonnet-5"): ModelRate(
            input_usd_per_million_tokens=2.00, output_usd_per_million_tokens=10.00
        ),
        ("gemini", "gemini-3.7-flash"): ModelRate(
            input_usd_per_million_tokens=0.75, output_usd_per_million_tokens=3.75
        ),
    },
    source_id="llm-council-default-pricing-2026-09-05",
    snapshot_aliases={
        ("openai", "gpt-5.5-2026-04-23"): "gpt-5.5",
    },
)
