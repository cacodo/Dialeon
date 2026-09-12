"""
`get_current_claims` — determina quais Claims de uma coleção ainda "valem",
sem nunca consultar `status="superseded"` nem `superseded_by`.

Claims são imutáveis: uma claim não pode ser informada, no momento da
própria criação, de que será superada por algo que ainda não existe — nem
no caso de fusão (agrupamento) nem no de revisão entre rounds
(`parent_claim_id`). Por isso `superseded_by`/`status="superseded"` ficam
dormentes nesta etapa (documentado em app/models/domain.py), e a única
fonte confiável de "isto ainda vale" é consulta inversa: uma claim deixa
de ser atual se o próprio id dela for referenciado por `parent_claim_id`
ou `merged_from_claim_ids` de QUALQUER OUTRA claim na coleção.

Função pura, sem escopo de round embutido — quem chama decide o que
passar (só claims de um round, pra montar contexto de crítica; ou a lista
completa de todos os rounds, pra obter o conjunto final do debate).
"""

from __future__ import annotations

from app.models.domain import Claim


def get_current_claims(all_claims: list[Claim]) -> list[Claim]:
    referenced_ids: set[str] = set()
    for claim in all_claims:
        if claim.parent_claim_id is not None:
            referenced_ids.add(claim.parent_claim_id)
        referenced_ids.update(claim.merged_from_claim_ids)

    return [claim for claim in all_claims if claim.id not in referenced_ids]
