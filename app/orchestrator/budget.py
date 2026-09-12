"""
Comparação de budget compartilhada entre `Orchestrator.run()` (Fase 1 isolada),
o `DebateEngine` (cumulativo, entre rodadas) e o `SingleJudge`/`Editor` (Etapas
6/7) — uma única fórmula, pra não correr o risco de duas versões divergentes
do mesmo cálculo.

`max_total_tokens`/`max_cost_usd`: orçamento AGREGADO, soft — comparado
DEPOIS que o consumo já aconteceu, nunca usado pra interromper uma chamada
em andamento. Ver docstring de RunConfig pra semântica completa.

Comparação `>=` (não `>`, correção pós-Etapa-6): "o orçamento já foi
INTEIRAMENTE consumido" (consumido == limite) também bloqueia o próximo
trabalho — a leitura "soft aggregate budget + HARD GATE antes de iniciar
trabalho futuro" só é coerente se um empate exato já conta como esgotado.
É a MESMA fórmula em todo o sistema, nunca uma segunda semântica só pra
uma camada.

Etapa 9 — accounting de custo/uso honesto: `cost_usd` de uma chamada real
pode ser `None` (desconhecido — nem toda combinação provider/model tem
preço configurado, e nem toda falha permite saber quanto foi consumido),
`0.0` (conhecido-zero — preço configurado e igual a zero, ou chamada que
comprovadamente nunca saiu do processo) ou `>0.0` (conhecido, estimado por
`PricingRegistry`). `max_cost_usd`/`max_total_tokens` são hard gates sobre
o CONHECIDO/reportado, nunca uma garantia de teto real quando existe
accounting não observável — `budget_exceeded=False` +
`has_unknown_accounting_components=True` significa "nenhum subtotal
conhecido excedeu o limite, mas não há garantia sobre o consumo/custo
remoto total", nunca "garantidamente dentro do orçamento real". Essa
incerteza NUNCA participa da decisão booleana de `compute_budget_exceeded`
— ela nunca apaga um subtotal conhecido que já excede o limite, e nunca
some no lugar do desconhecido: fica só nos objetos de resultado
(`has_unknown_accounting_components`), lida separadamente por quem chama.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from app.models.provider_models import TokenUsage
from app.orchestrator.config import RunConfig


def compute_budget_exceeded(
    total_input_tokens: int,
    total_output_tokens: int,
    known_total_cost_usd: float,
    run_config: RunConfig,
) -> bool:
    """Hard gate sobre accounting CONHECIDO/reportado — nunca sobre o
    consumo/custo real total quando existem componentes desconhecidos.
    Não recebe `has_unknown_accounting_components`: a incerteza nunca
    entra nesta decisão booleana (nem apagando um subtotal conhecido que
    já excede o limite, nem sendo tratada como zero) — só vive nos objetos
    de resultado, pra quem chamar ler separadamente."""
    token_exceeded = (total_input_tokens + total_output_tokens) >= run_config.max_total_tokens
    known_cost_exceeded = known_total_cost_usd >= run_config.max_cost_usd
    return token_exceeded or known_cost_exceeded


class _HasUsageAndCost(Protocol):
    """Qualquer registro de chamada real de LLM com esses três campos —
    `ClaimProcessingAttempt`, `JudgeAttempt`, `EditorAttempt` e
    `SourceAnalysisAttempt` satisfazem isso por tipagem estrutural, sem
    que `app/judge/`/`app/editor/`/etc precisem importar de `app/debate/`
    (ou entre si) só pra reusar esta soma."""

    usage: TokenUsage | None
    cost_usd: float | None
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: bool


def sum_usage_and_cost(
    records: Sequence[_HasUsageAndCost],
) -> tuple[int, int, float, bool]:
    """Soma tokens/custo de uma lista de registros de chamada — usado por
    quem precisa agregar `ClaimProcessingAttempt`/`JudgeAttempt`/
    `EditorAttempt`/`SourceAnalysisAttempt` (aceitos OU rejeitados; uma
    tentativa rejeitada ainda consumiu tokens de verdade). Não é uma
    "framework de accounting" — só a soma, pra não duplicar a fórmula em
    cada camada que precisa dela.

    Retorna (total_input_tokens, total_output_tokens, known_total_cost_usd,
    has_unknown_accounting_components).

    Tokens usam `or 0`: ausência de `usage` numa chamada que falhou antes
    de qualquer resposta contribui 0 — honesto (não é uma alegação de que
    zero tokens foram consumidos remotamente, só que não foram
    reportados; ver docstring de `ProviderResponse`/`LLMProvider.complete()`
    pra por que isso é diferente de "sabemos que foi zero").

    `known_total_cost_usd` é a soma de tudo que TEM `cost_usd` conhecido —
    matematicamente idêntica à fórmula que já existia (`None` e `0.0`
    contribuem igual, `0`, pra soma); nunca reduzido/zerado por incerteza
    de tentativa anterior (Etapa 17A) — o valor CONHECIDO de uma tentativa
    bem-sucedida permanece na soma mesmo que uma tentativa anterior
    daquele mesmo registro tenha sido incerta.

    `has_unknown_accounting_components` (Etapa 17A, ampliado pra B3):
    `True` se QUALQUER record tinha `cost_usd=None` OU
    `had_uncertain_prior_attempts=True` — os dois são sinais
    independentes de incompletude que nunca se cancelam: um record pode
    ter custo final CONHECIDO (`cost_usd` positivo) e AINDA ASSIM marcar
    o agregado como incompleto, porque uma tentativa anterior àquela
    mesma chamada teve accounting desconhecido (retry: falha
    pós-dispatch seguida de sucesso). `cost_usd` só fica `None` quando
    `usage is None` OU quando não há preço registrado pro modelo, nunca
    por outro motivo; então `has_unknown_accounting_components=False`
    prova que todo `usage`/`cost_usd` relevante é conhecido E que nenhum
    record teve tentativa anterior incerta (ver `app/providers/base.py`).
    """
    total_input = sum((r.usage.input_tokens or 0) for r in records if r.usage is not None)
    total_output = sum((r.usage.output_tokens or 0) for r in records if r.usage is not None)
    known_total_cost = sum((r.cost_usd or 0.0) for r in records)
    has_unknown_accounting_components = any(
        r.cost_usd is None or r.had_uncertain_prior_attempts for r in records
    )
    return total_input, total_output, known_total_cost, has_unknown_accounting_components
