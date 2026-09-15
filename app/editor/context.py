"""
Builder do contexto do Editor (Etapa 7; reescrito na Etapa 17B) — monta o
`CompletionRequest` enviado ao `editor_provider`.

Etapa 17B — o Editor deixou de ser autor de prosa e virou planejador de
apresentação (`EditorPlan`, ver app/editor/schemas.py): a única decisão
que ele toma é `opening_style`/`closing_style`, dois enums finitos. O
contexto encolhe de acordo -- ele nunca precisou do CONTEÚDO factual das
claims/explicações pra tomar essa decisão, só de METADADOS estruturais
sobre o veredito (contagem por tipo de veredito, se há limitações
registradas) e da pergunta original + confiança do juiz (pra calibrar
tom). Excluído deliberadamente, e removido nesta reescrita (não apenas
"não usado"): `Claim.text`, `ClaimAssessment.claim_id`/`explanation`
individuais -- nenhum dado factual do debate atravessa este prompt.

Ausentes de propósito, herdado da Etapa 7 (arquitetura, seção 2):
`JudgeVerdict.reasoning`, `ModelResponse.response_text`, `JudgeAttempt`,
`supporting_model_ratio`, `supporting_models`, lineage, usage/cost,
providers participantes, metadados brutos de quórum/processo,
`debate_limitations` em si (só a PRESENÇA, nunca o texto).

Segurança: como nenhum texto produzido por modelos de IA participantes do
debate (claim/explanation) chega mais a este prompt, o aviso de conteúdo
não confiável específico dessas claims deixou de ser necessário aqui --
a única entrada externa que resta é `question` (do usuário, não de um
participante do debate) e dados 100% estruturais (contagens/booleanos)
derivados deterministicamente pela aplicação. `system_prompt` continua
inteiramente escrito por este módulo, nunca deriva de texto de LLM.
"""

from __future__ import annotations

import json

from app.models.domain import JudgeVerdict
from app.models.provider_models import CompletionRequest, Message

# Provider-Neutral Request Provenance V1 -- contrato do request do
# Editor, montado por `build_editor_request` abaixo.
EDITOR_CONTRACT_VERSION = "editor_v1"


def build_editor_request(
    question: str,
    verdict: JudgeVerdict,
    max_output_tokens_per_call: int,
) -> CompletionRequest:
    verdict_counts: dict[str, int] = {}
    for assessment in verdict.claim_assessments:
        verdict_counts[assessment.verdict] = verdict_counts.get(assessment.verdict, 0) + 1

    system_prompt = (
        "Você é o planejador de apresentação final de um debate entre modelos de "
        "IA já julgado por um juiz. Você NÃO julga, não reavalia, não muda nenhum "
        "veredito, e não escreve nenhuma palavra do texto final que o usuário vai "
        "ler -- toda a prosa final é montada pela aplicação diretamente a partir "
        "dos dados estruturados do juiz. Sua única função é escolher, entre "
        "opções finitas, COMO essa apresentação determinística deve se estruturar. "
        'Responda SOMENTE com um JSON no formato {"opening_style": "...", '
        '"closing_style": "..."}, sem texto fora do JSON. '
        "opening_style deve ser exatamente 'direct' (vá direto ao resultado da "
        "avaliação, sem preâmbulo) ou 'contextual' (uma frase fixa de "
        "enquadramento reconecta a resposta à pergunta original antes do "
        "resultado) -- prefira 'contextual' quando a pergunta original for "
        "complexa/multifacetada ou os veredictos forem heterogêneos entre si, "
        "'direct' quando a pergunta for simples e os veredictos homogêneos. "
        "closing_style deve ser exatamente 'concise' (fechamento simples) ou "
        "'limitations_focused' (fechamento que chama atenção explicitamente pra "
        "limitações do debate antes de listá-las) -- prefira "
        "'limitations_focused' quando houver limitações registradas, 'concise' "
        "caso contrário. Você não recebe o texto das claims, das explicações do "
        "juiz, nem das limitações -- só contagens e presença/ausência -- porque "
        "sua escolha é só estrutural e não deve depender de nenhum conteúdo "
        "factual específico."
    )

    body = (
        f"PERGUNTA ORIGINAL:\n{question}\n\n"
        f"CONFIANCA_DO_JUIZ (0 a 1, subjetiva — só para calibrar tom): {verdict.confidence}\n\n"
        f"CONTAGEM_DE_VEREDITOS_POR_TIPO: {json.dumps(verdict_counts, ensure_ascii=False)}\n\n"
        f"HA_LIMITACOES_DE_DEBATE_REGISTRADAS: "
        f"{json.dumps(bool(verdict.debate_limitations))}"
    )

    return CompletionRequest(
        messages=[Message(role="user", content=body)],
        system_prompt=system_prompt,
        max_tokens=max_output_tokens_per_call,
    )
