"""
Contrato SEMÂNTICO do agrupamento intra-round (`group_claims`), sob
claim_grouping_v4 (partição consultiva em `clusters`).

ESCOPO E LIMITE DESTES TESTES -- leia antes de interpretá-los:

Agrupar é uma operação SEMÂNTICA (decidir o que é a mesma proposição
material), não só geração de JSON. Estes testes usam respostas ROTEIRIZADAS
("scripted") de um modelo falso: o roteiro ENCODA a partição que o contrato
exige (ex.: "negação nunca fica no mesmo cluster"), e o teste verifica que o
PIPELINE de produção
- entrega ao modelo cada claim VERBATIM (qualificador/negação/número/modal
  chegam sem normalização),
- carrega o contrato semântico no prompt,
- aceita/audita a partição devolvida (a resposta bruta fica na tentativa),
- e, MAIS IMPORTANTE, NUNCA deixa a proposta virar autoridade: nenhuma claim
  é criada, unida, supersedida ou removida -- todas as originais seguem atuais
  e intactas, INCLUSIVE quando o "modelo" propõe o merge ERRADO.

Eles NÃO provam que o modelo REAL, com raciocínio desabilitado, toma essas
decisões corretamente: o validador de produção é ESTRUTURAL (partição exata)
e não detecta um cluster semanticamente errado. Qualidade real só por replay
pago controlado. A validade semântica dos fixtures é revisada SEPARADAMENTE
(revisão humana/adversarial); o único check automático sobre eles
(`test_grouping_fixtures_structural_sanity`) é uma sanidade estrutural, NÃO um
oráculo semântico.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from app.debate.claim_extraction import group_claims
from app.debate.claims import get_current_claims
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import CallableProvider, text_response
from tests.judge.fixtures import raw_claim

_PROVIDERS = ("openai", "anthropic", "gemini")


@dataclass(frozen=True)
class Case:
    name: str
    claims: tuple[str, ...]
    # clusters de 2+ claims que o contrato PERMITE (mesma proposição material)
    merge: tuple[tuple[int, ...], ...] = ()
    # claims que devem ficar em cluster unitário (must-not-merge / sem par seguro)
    apart: tuple[int, ...] = field(default_factory=tuple)
    # Para claims mantidas separadas: o traço materialmente significativo que
    # as distingue (escopo, polaridade, modalidade, condição, número, causalidade...).
    distinction: str = ""

    def partition(self) -> list[list[int]]:
        return [list(c) for c in self.merge] + [[i] for i in self.apart]


# REGRA DOS FIXTURES: todo caso de MERGE usa entradas que expressam a MESMA
# proposição material (mesmo escopo, modalidade, condição, polaridade, número,
# causalidade). Todo caso com claims separadas declara em `distinction` o traço
# materialmente significativo que as separa. "Limítrofe" NUNCA autoriza apagar
# escopo/modalidade/condição/polaridade/número/causalidade. Nenhum cluster é
# "verdade": é só uma proposta consultiva.
CASES = [
    Case(
        name="1_paraphrases_may_share_a_cluster",
        claims=(
            "O céu é azul por causa do espalhamento de Rayleigh.",
            "O espalhamento de Rayleigh é a causa de o céu ser azul.",
        ),
        merge=((0, 1),),
    ),
    Case(
        name="2_same_topic_different_claims_stay_separate",
        claims=(
            "Um CRM SaaS tem custo previsível por assinatura mensal.",
            "Um CRM SaaS pode ter reajuste de preço unilateral do fornecedor.",
        ),
        apart=(0, 1),
        distinction="afirmações distintas sobre custo (previsibilidade vs. risco de reajuste)",
    ),
    Case(
        name="3a_condition_makes_claims_different",
        claims=(
            "O CRM self-hosted é mais barato.",
            "O CRM self-hosted é mais barato apenas se a organização já tiver TI dedicada.",
        ),
        apart=(0, 1),
        distinction="condição (só se houver TI dedicada) presente em uma e ausente na outra",
    ),
    Case(
        name="3b_same_necessary_condition_may_share_a_cluster",
        claims=(
            "O self-hosted só compensa se houver equipe de TI dedicada.",
            "O self-hosted compensa apenas quando existe equipe de TI dedicada.",
        ),
        merge=((0, 1),),
    ),
    Case(
        name="3c_necessary_vs_sufficient_condition_never_share_a_cluster",
        claims=(
            "O self-hosted só compensa se houver equipe de TI dedicada.",
            "Havendo equipe de TI dedicada, o self-hosted compensa.",
        ),
        apart=(0, 1),
        distinction=(
            "TI dedicada é condição NECESSÁRIA ('só compensa se') numa e SUFICIENTE "
            "('havendo... compensa') na outra -- proposições logicamente distintas"
        ),
    ),
    Case(
        name="4a_modal_strength_differs_stays_separate",
        claims=("A migração deve reduzir custos.", "A migração vai reduzir custos com certeza."),
        apart=(0, 1),
        distinction="modalidade: expectativa ('deve') vs. certeza ('com certeza')",
    ),
    Case(
        name="4b_same_hedge_may_share_a_cluster",
        claims=(
            "A migração provavelmente reduzirá os custos.",
            "É provável que a migração reduza os custos.",
        ),
        merge=((0, 1),),
    ),
    Case(
        name="5_negation_never_shares_a_cluster",
        claims=(
            "O SaaS oferece exportação completa dos dados.",
            "O SaaS não oferece exportação completa dos dados.",
        ),
        apart=(0, 1),
        distinction="polaridade: oferece vs. não oferece",
    ),
    Case(
        name="6a_numeric_difference_stays_separate",
        claims=("O custo é de US$ 12 por usuário por mês.", "O custo é de US$ 21 por usuário por mês."),
        apart=(0, 1),
        distinction="valor numérico: US$ 12 vs. US$ 21",
    ),
    Case(
        name="6b_same_number_may_share_a_cluster",
        claims=("O custo é de US$ 12 por usuário por mês.", "Cada usuário custa US$ 12 mensais."),
        merge=((0, 1),),
    ),
    Case(
        name="7_causal_vs_correlational_stay_separate",
        claims=("O uso de CRM aumenta as doações.", "O uso de CRM está associado a mais doações."),
        apart=(0, 1),
        distinction="causalidade ('aumenta') vs. mera associação ('está associado a')",
    ),
    Case(
        name="8_refinement_with_extra_detail_stays_separate",
        claims=(
            "O SaaS reduz a carga de manutenção.",
            "O SaaS reduz a carga de manutenção, mas não elimina a gestão de usuários e permissões.",
        ),
        apart=(0, 1),
        distinction="a segunda acrescenta uma ressalva material (não elimina a gestão de usuários)",
    ),
    Case(
        name="9_contradiction_stays_separate",
        claims=("A migração é segura.", "A migração é arriscada."),
        apart=(0, 1),
        distinction="polaridade oposta sobre o risco da migração",
    ),
    Case(
        name="10a_borderline_scope_and_aspect_differ_stays_separate",
        claims=("A ferramenta é fácil de usar por leigos.", "A ferramenta tem curva de aprendizado baixa."),
        apart=(0, 1),
        distinction=(
            "escopo (por leigos) e aspecto (facilidade de USO vs. curva de APRENDIZADO) "
            "diferem -- 'limítrofe' não autoriza apagar isso"
        ),
    ),
    Case(
        name="10b_near_synonyms_may_share_a_cluster",
        claims=("A ferramenta é fácil de usar.", "A ferramenta é simples de usar."),
        merge=((0, 1),),
    ),
    Case(
        name="10c_near_synonyms_left_separate_loses_no_meaning",
        claims=("A ferramenta é fácil de usar.", "A ferramenta é simples de usar."),
        apart=(0, 1),
        distinction=(
            "nenhuma distinção material: separar é a saída conservadora válida "
            "(custo = só deduplicação), nunca perda de significado"
        ),
    ),
    Case(
        name="11a_scope_hedge_and_period_equivalent_may_share_a_cluster",
        claims=(
            "Para ONGs com menos de 15 pessoas, o SaaS tende a ser mais barato no 1º ano.",
            "Em ONGs com menos de 15 pessoas, o SaaS costuma custar menos no 1º ano.",
        ),
        merge=((0, 1),),
    ),
    Case(
        name="11b_different_scope_never_shares_a_cluster",
        claims=(
            "Para ONGs com menos de 15 pessoas, o SaaS tende a ser mais barato no 1º ano.",
            "Para organizações pequenas em geral, o SaaS tende a ser mais barato no 1º ano.",
        ),
        apart=(0, 1),
        distinction=(
            "escopo: ONGs com menos de 15 pessoas (estreito e quantificado) vs. "
            "organizações pequenas em geral (mais amplo, sem limite)"
        ),
    ),
    Case(
        name="12_same_conclusion_different_reasoning_stays_separate",
        claims=(
            "O SaaS é a melhor escolha porque reduz a carga de manutenção.",
            "O SaaS é a melhor escolha porque tem custo previsível.",
        ),
        apart=(0, 1),
        distinction=(
            "mesma conclusão, mas razões distintas (manutenção vs. previsibilidade de custo) "
            "-- mesma conclusão/raciocínio parecido não basta pra agrupar"
        ),
    ),
    Case(
        name="13_exception_clause_makes_claims_different",
        claims=(
            "O SaaS deve ser escolhido.",
            "O SaaS deve ser escolhido, a menos que haja necessidades incomuns de customização.",
        ),
        apart=(0, 1),
        distinction="exceção ('a menos que…') presente em uma e ausente na outra",
    ),
    Case(
        name="14_mirror_image_arguments_stay_separate",
        claims=(
            "O SaaS converte o fardo de manutenção em uma assinatura previsível.",
            "O self-hosting é um risco operacional que conflita com o objetivo de baixa manutenção.",
        ),
        apart=(0, 1),
        distinction=(
            "argumentos espelhados (vantagem do SaaS vs. risco do self-hosting) -- compatíveis, "
            "mas proposições distintas: 'conclusões compatíveis' não bastam"
        ),
    ),
]


def _make_claims(texts):
    return [raw_claim(text, f"resp-{i}", provider=_PROVIDERS[i % 3]) for i, text in enumerate(texts)]


def _clusters_handler(partition: list[list[int]], seen: list[str]):
    """Roteiro: devolve a partição do caso, traduzindo índices -> ids reais
    lendo o payload `CLAIMS_BRUTAS` do request (ids são gerados em runtime)."""

    async def handler(call_index, request):
        body = request.messages[0].content
        seen.append(body)
        payload = json.loads(body.split("CLAIMS_BRUTAS:\n", 1)[1])
        ids = [item["id"] for item in payload]
        return text_response(
            "anthropic", json.dumps({"clusters": [[ids[i] for i in c] for c in partition]})
        )

    return handler


async def _run(case: Case, partition: list[list[int]] | None = None):
    claims = _make_claims(case.claims)
    before = [c.model_dump() for c in claims]
    seen: list[str] = []
    provider = CallableProvider("anthropic", _clusters_handler(partition or case.partition(), seen))
    attempts = await group_claims(
        claims,
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=8192,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    return claims, before, attempts, provider, seen


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
@pytest.mark.asyncio
async def test_pipeline_accepts_audits_and_never_applies_the_scripted_partition(case: Case):
    claims, before, attempts, provider, seen = await _run(case)

    assert [a.parse_status for a in attempts] == ["accepted"]
    assert len(provider.received_requests) == 1

    # (a) cada claim chega ao modelo VERBATIM e na ordem original --
    # qualificadores/negação/números/modais nunca são normalizados antes.
    sent_payload = json.loads(seen[0].split("CLAIMS_BRUTAS:\n", 1)[1])
    assert [item["text"] for item in sent_payload] == list(case.claims)

    # (b) a proposta fica AUDITÁVEL na resposta bruta, exatamente como devolvida
    ids = [c.id for c in claims]
    audited = json.loads(attempts[0].raw_output_text)["clusters"]
    assert audited == [[ids[i] for i in cluster] for cluster in case.partition()]
    flat = [i for cluster in audited for i in cluster]
    assert sorted(flat) == sorted(ids)  # partição exata

    # (c) AUTORIDADE: nada criado/unido/supersedido -- todas as originais atuais e intactas
    assert [c.model_dump() for c in claims] == before
    assert {c.id for c in get_current_claims(claims)} == set(ids)
    assert all(not c.merged_from_claim_ids and c.superseded_by is None for c in claims)


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.apart and c.distinction], ids=lambda c: c.name
)
@pytest.mark.asyncio
async def test_even_a_wrong_scripted_merge_of_must_not_merge_claims_never_alters_any_claim(case: Case):
    """O 'modelo' propõe o merge ERRADO (todas as claims num único cluster).
    A proposta é aceita estruturalmente e fica auditável, mas NENHUMA claim
    some, é reescrita nem herda o suporte da outra. (Contenção de autoridade,
    não aprovação do merge.)"""
    everything_in_one_cluster = [list(range(len(case.claims)))]

    claims, before, attempts, _provider, _seen = await _run(case, partition=everything_in_one_cluster)

    assert [a.parse_status for a in attempts] == ["accepted"]
    assert [c.model_dump() for c in claims] == before
    assert {c.id for c in get_current_claims(claims)} == {c.id for c in claims}
    for i, claim in enumerate(claims):
        assert [s.provider for s in claim.supporting_model_response_ids] == [_PROVIDERS[i % 3]]


@pytest.mark.asyncio
async def test_request_carries_the_v4_semantic_partition_contract_and_minimal_reasoning():
    """O prompt carrega o contrato que o modelo precisa seguir; v4 desliga o
    raciocínio -- e é disso que a qualidade real (não medida aqui) depende."""
    _claims, _before, _attempts, provider, _seen = await _run(CASES[0])

    request = provider.received_requests[0]
    system = request.system_prompt
    assert "MESMA proposição material" in system
    assert "apenas uma PROPOSTA" in system
    assert "não é equivalência verificada, consenso nem verdade" in system
    assert "mesmo tema, raciocínio parecido, conclusões compatíveis ou argumentos espelhados NÃO bastam" in system
    for axis in ("escopo", "polaridade/negação", "incerteza/modalidade", "condições/exceções",
                 "sentido numérico", "causalidade"):
        assert axis in system
    assert "cluster de um único id" in system and "na dúvida, separe" in system
    assert "TODO id da lista de CLAIMS_BRUTAS precisa aparecer em exatamente um cluster" in system
    assert "canonical_text" not in system  # v4 não gera texto sintetizado
    assert request.minimal_reasoning is True
    assert request.max_tokens == 8192


def test_grouping_fixtures_structural_sanity():
    """Sanidade ESTRUTURAL barata dos PRÓPRIOS fixtures. NÃO é um oráculo
    semântico e NÃO prova equivalência nem validade semântica -- isso é
    revisado separadamente. Verifica apenas: (1) todo caso com claims
    mantidas separadas declara uma `distinction` (o conteúdo dela não é
    avaliado aqui); (2) cada claim aparece EXATAMENTE uma vez na partição do
    caso; (3) clusters de merge têm >= 2 claims; (4) nomes únicos."""
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
    for case in CASES:
        if case.apart:
            assert case.distinction.strip(), f"{case.name}: falta `distinction`"
        assert all(len(c) >= 2 for c in case.merge), case.name
        used = [i for cluster in case.partition() for i in cluster]
        assert sorted(used) == list(range(len(case.claims))), case.name
