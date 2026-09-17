// Claim-centered semantic inspection -- adapter PURO (sem I/O, sem
// estado) que reorganiza o MESMO payload de audit já recebido em uma
// unidade product-facing por `ClaimPublic.id`. Nenhuma chamada nova,
// nenhum dado inventado -- só reagrupamento.
//
// IDENTIDADE: `ClaimPublic.id` é a ÚNICA chave de join usada aqui.
// NUNCA por texto da claim, similaridade semântica, posição no array ou
// ordem de exibição -- duas claims com o mesmo texto mas IDs diferentes
// permanecem unidades SEPARADAS, e embaralhar qualquer array de entrada
// (claims/assessments/claim_results/claim_outcomes) nunca muda nenhum
// join (todo lookup é por Map, nunca por índice).
//
// FRONTEIRA DE INTEGRIDADE (repair pós-revisão adversarial) -- só
// registros ESTRUTURALMENTE confiáveis entram na semântica product-facing:
// - NUNCA last-write-wins: quando uma identidade que o contrato trata
//   como 1:1 aparece ambígua (claim_id duplicado em `claims[]`, mais de
//   uma avaliação do juiz pra mesma claim, mais de um outcome de
//   reconciliação estruturalmente válido pra mesma claim, um id de
//   source-result duplicado), NENHUM registro dessa identidade ambígua
//   vira uma unidade/conclusão semântica -- nem o primeiro, nem o
//   último. TODOS os registros envolvidos são preservados em
//   quarentena (`ambiguousClaims`/`quarantined*`), nunca descartados.
// - Um `ClaimReconciliationOutcomePublic` só é anexado a
//   `ClaimInspectionUnit.reconciliationOutcomes` depois de validar o
//   envelope de referência COMPLETO: claim_id resolve sem ambiguidade,
//   judge_verdict_id coerente com o veredito real E com
//   `reconciliation.status`, cada source_claim_result_id resolve
//   exatamente uma vez, pertence à MESMA claim, e não se repete dentro
//   da própria lista. Qualquer violação -- mesmo que o claim_id em si
//   resolva perfeitamente -- quarentena o outcome INTEIRO (nunca anexa
//   parcialmente, nunca infere qual parte "ainda vale").
// - Nada disso reconstrói/recalcula reconciliação nem verdade
//   semântica -- só decide, por integridade estrutural, se um registro
//   JÁ PRODUZIDO pode entrar na camada semântica ou não. A camada
//   técnica (`ClaimInspectionModel.integrityIssues` + as coleções de
//   quarentena) preserva os registros ORIGINAIS pra auditoria, nunca só
//   um resumo textual.
//
// NUNCA infere o que não está explicitamente no audit:
// - reconciliation ausente (null) nunca vira um outcome "not_comparable"
//   inventado;
// - ausência de suporte de um participante nunca vira "discordância";
// - uma referência malformada/ambígua NUNCA é anexada a uma claim
//   "adivinhada" -- vira um item em `integrityIssues` + um registro nas
//   coleções de quarentena, sempre separado do conteúdo semântico normal.
//
// Este módulo NUNCA reproduz o filtro de "claims correntes" que o
// backend aplica em `app/reconciliation/reconcile.py` -- expõe a
// população de claims exatamente como o audit trouxe (`audit.claims`,
// histórico incluído), e a lineage (parent/merged-from) é honesta: só
// resolve o que EXISTE, SEM AMBIGUIDADE, no mesmo array de claims deste
// audit, nunca inventa um texto pra um ID que não está presente nem
// escolhe entre duplicatas.

import type {
  ClaimAssessmentPublic,
  ClaimPublic,
  ClaimReconciliationOutcomePublic,
  JudgeVerdictPublic,
  RejectedSourceEntryPublic,
  SourceAnalysisOutcome,
  SourceClaimAnalysisResultPublic,
  SourceJudgeReconciliationResultPublic,
} from './types'

export type AuditIntegrityIssueKind =
  | 'unknown_claim_reference'
  | 'ambiguous_claim_reference'
  | 'duplicate_claim_identity'
  | 'duplicate_claim_assessment'
  | 'duplicate_source_result_identity'
  | 'mismatched_judge_verdict_id'
  | 'dangling_source_result_reference'
  | 'cross_claim_source_result_reference'
  | 'duplicate_source_result_reference'
  | 'duplicate_reconciliation_outcome'
  | 'incoherent_reconciliation_envelope'

// Diagnóstico TÉCNICO (audit-integrity), nunca reinterpretado como
// desacordo/ausência/rejeição semântica de nenhum canal -- ver
// `ClaimInspectionModel.integrityIssues`. `detail` é texto de
// diagnóstico pra apresentação técnica (Auditoria técnica), não um
// rótulo product-facing.
export interface AuditIntegrityIssue {
  kind: AuditIntegrityIssueKind
  // Melhor esforço -- a claim_id que a referência problemática CITA,
  // mesmo quando essa claim_id não existe (ou é ambígua) em `claims[]`.
  // `null` só quando a própria referência/registro não carrega claim_id
  // nenhum.
  claimId: string | null
  // O identificador bruto envolvido no problema (source-result id,
  // judge_verdict_id, etc.), quando aplicável.
  referenceId: string | null
  detail: string
}

// Grupo de `ClaimPublic` que compartilham o mesmo `id` -- por
// construção, `claims.length > 1` aqui sempre (um grupo de 1 nunca é
// ambíguo, nunca aparece nesta lista). Nenhum dos registros vira uma
// unidade semântica; todos ficam disponíveis pra inspeção técnica.
export interface AmbiguousClaimGroup {
  claimId: string
  claims: ClaimPublic[]
}

export interface QuarantinedAssessment {
  assessment: ClaimAssessmentPublic
  reason: AuditIntegrityIssueKind
}

export interface QuarantinedSourceResult {
  entry: SourceClaimAnalysisResultPublic
  reason: AuditIntegrityIssueKind
}

export interface QuarantinedReconciliationOutcome {
  outcome: ClaimReconciliationOutcomePublic
  // Um outcome pode falhar mais de uma validação ao mesmo tempo (ex.:
  // judge_verdict_id incoerente E um source-result cross-claim) --
  // array, nunca um único motivo escolhido arbitrariamente.
  reasons: AuditIntegrityIssueKind[]
}

export interface ClaimInspectionUnit {
  claim: ClaimPublic
  // Lineage honesta: só resolvida contra claims de id ÚNICO no MESMO
  // audit -- nunca escolhe entre duplicatas ambíguas. IDs que não
  // resolvem (ausentes OU ambíguos) são omitidos silenciosamente (mesma
  // disciplina que a UI já aplicava antes desta slice) -- não são
  // referências cross-channel, então não viram `AuditIntegrityIssue`.
  parentClaim: ClaimPublic | null
  mergedFromClaims: ClaimPublic[]
  // Array, nunca um único valor -- mas só contém uma entrada quando
  // exatamente UMA avaliação explícita aponta pra esta claim_id (ver
  // `duplicate_claim_assessment`: 2+ avaliações pra mesma claim_id nunca
  // entram aqui, ficam inteiramente em quarentena).
  judgeAssessments: ClaimAssessmentPublic[]
  // Mistura relation/rejected (SourceClaimAnalysisResultPublic é a união
  // dos dois) -- só entradas cujo `claim_id` bate com esta claim E cujo
  // `id` não é ambíguo neste audit. Multiplicidade de registros de fonte
  // pra UMA claim continua permitida (nunca colapsada) -- só o ID de
  // CADA registro precisa ser inequívoco.
  sourceResults: SourceClaimAnalysisResultPublic[]
  // Array, mas só contém uma entrada quando exatamente UM outcome
  // estruturalmente válido (envelope de referência completo -- ver
  // docstring do módulo) aponta pra esta claim_id.
  reconciliationOutcomes: ClaimReconciliationOutcomePublic[]
}

export interface ClaimInspectionModel {
  // Uma unidade por `ClaimPublic` de id ÚNICO em `claims[]`, na MESMA
  // ordem -- toda a população de claims NÃO ambíguas do audit, nunca um
  // subconjunto "corrente" recalculado aqui. Claims com id duplicado
  // nunca produzem unidade nenhuma (ver `ambiguousClaims`).
  units: ClaimInspectionUnit[]
  // Entradas rejeitadas da análise de fonte com `claim_id === null` --
  // por definição nunca pertencem a nenhuma unidade de claim (ver
  // docstring do módulo de origem, app/presentation/schemas.py:
  // `RejectedSourceEntryPublic.claim_id` distingue "não endereçou
  // nenhuma claim específica" de uma referência que apenas não resolve).
  // Uma entrada com id AMBÍGUO (duplicado) nunca entra aqui mesmo com
  // claim_id null -- vai pra `quarantinedSourceResults` (a ambiguidade
  // de identidade do registro em si tem prioridade).
  unattributedRejectedSourceEntries: RejectedSourceEntryPublic[]
  // true quando esta execução teve um `JudgeVerdictPublic` (mesmo que
  // ele não tenha avaliado ESTA claim específica -- ver
  // `judgeAssessments` por unidade). false é o único estado honesto de
  // "juiz indisponível nesta execução" -- nunca inferido de outro campo.
  judgeVerdictPresent: boolean
  // Coerência do ENVELOPE de reconciliação como um todo --
  // `reconciliation.status === 'judge_unavailable'` precisa corresponder
  // exatamente a `judgeVerdict === null`, e `'complete'` precisa
  // corresponder a um veredito real presente. `true` quando
  // `reconciliation === null` (não há envelope pra avaliar -- o
  // consumidor já trata esse caso separadamente, nunca lê este campo
  // nesse cenário). Quando `false`, NENHUM outcome desta reconciliação
  // pode virar relacionamento product-facing, mesmo que o outcome em si
  // pareça individualmente válido -- e nenhuma superfície (ex.:
  // ReconciliationView) pode declarar "juiz indisponível"/"juiz
  // avaliou" com base só em `reconciliation.status` sem checar este
  // campo primeiro.
  reconciliationEnvelopeCoherent: boolean
  integrityIssues: AuditIntegrityIssue[]
  // Registros ORIGINAIS em quarentena -- nunca só um resumo textual --
  // preservados pra `Auditoria técnica` continuar inspecionável mesmo
  // pro que foi removido da camada semântica.
  ambiguousClaims: AmbiguousClaimGroup[]
  quarantinedAssessments: QuarantinedAssessment[]
  quarantinedSourceResults: QuarantinedSourceResult[]
  quarantinedReconciliationOutcomes: QuarantinedReconciliationOutcome[]
}

export interface ClaimInspectionModelInput {
  claims: ClaimPublic[]
  judgeVerdict: JudgeVerdictPublic | null
  sourceAnalysis: SourceAnalysisOutcome | null
  reconciliation: SourceJudgeReconciliationResultPublic | null
}

function isRejectedEntry(
  entry: SourceClaimAnalysisResultPublic,
): entry is RejectedSourceEntryPublic {
  return entry.kind === 'rejected'
}

type ClaimReferenceResolution =
  | { ok: true }
  | { ok: false; kind: 'unknown_claim_reference' | 'ambiguous_claim_reference' }

export function buildClaimInspectionModel({
  claims,
  judgeVerdict,
  sourceAnalysis,
  reconciliation,
}: ClaimInspectionModelInput): ClaimInspectionModel {
  const integrityIssues: AuditIntegrityIssue[] = []

  // --- Identidade de claim: ambiguidade detectada ANTES de qualquer
  //     unidade ser construída -- nenhum primeiro/último "vence". ------
  const claimGroupsById = new Map<string, ClaimPublic[]>()
  for (const claim of claims) {
    const group = claimGroupsById.get(claim.id)
    if (group) group.push(claim)
    else claimGroupsById.set(claim.id, [claim])
  }

  const ambiguousClaims: AmbiguousClaimGroup[] = []
  const ambiguousClaimIds = new Set<string>()
  const claimsById = new Map<string, ClaimPublic>()
  for (const [claimId, group] of claimGroupsById) {
    if (group.length > 1) {
      ambiguousClaimIds.add(claimId)
      ambiguousClaims.push({ claimId, claims: group })
      integrityIssues.push({
        kind: 'duplicate_claim_identity',
        claimId,
        referenceId: null,
        detail: `${group.length} registros de claim compartilham o mesmo id (${claimId}) -- nenhuma unidade semântica foi criada pra este id; nenhum registro "vence" sobre os outros.`,
      })
    } else {
      claimsById.set(claimId, group[0])
    }
  }

  function resolveClaimRef(id: string | null): ClaimPublic | null {
    if (id === null) return null
    return claimsById.get(id) ?? null
  }

  function resolveTargetClaim(claimId: string): ClaimReferenceResolution {
    if (claimsById.has(claimId)) return { ok: true }
    if (ambiguousClaimIds.has(claimId)) return { ok: false, kind: 'ambiguous_claim_reference' }
    return { ok: false, kind: 'unknown_claim_reference' }
  }

  const unitsById = new Map<string, ClaimInspectionUnit>()
  const unitOrder: string[] = []
  for (const claim of claims) {
    if (ambiguousClaimIds.has(claim.id) || unitsById.has(claim.id)) continue
    unitOrder.push(claim.id)
    unitsById.set(claim.id, {
      claim,
      parentClaim: resolveClaimRef(claim.parent_claim_id),
      mergedFromClaims: claim.merged_from_claim_ids
        .map((id) => resolveClaimRef(id))
        .filter((c): c is ClaimPublic => c !== null),
      judgeAssessments: [],
      sourceResults: [],
      reconciliationOutcomes: [],
    })
  }

  // --- Canal: avaliações do juiz -----------------------------------
  const quarantinedAssessments: QuarantinedAssessment[] = []
  const assessmentGroupsByClaimId = new Map<string, ClaimAssessmentPublic[]>()

  for (const assessment of judgeVerdict?.claim_assessments ?? []) {
    const resolution = resolveTargetClaim(assessment.claim_id)
    if (!resolution.ok) {
      integrityIssues.push({
        kind: resolution.kind,
        claimId: assessment.claim_id,
        referenceId: null,
        detail:
          resolution.kind === 'ambiguous_claim_reference'
            ? `Avaliação do juiz referencia claim_id ${assessment.claim_id}, que é ambíguo neste audit (múltiplos registros de claim compartilham este id).`
            : `Avaliação do juiz referencia claim_id desconhecido neste audit: ${assessment.claim_id}.`,
      })
      quarantinedAssessments.push({ assessment, reason: resolution.kind })
      continue
    }
    const group = assessmentGroupsByClaimId.get(assessment.claim_id)
    if (group) group.push(assessment)
    else assessmentGroupsByClaimId.set(assessment.claim_id, [assessment])
  }

  for (const [claimId, group] of assessmentGroupsByClaimId) {
    if (group.length === 1) {
      unitsById.get(claimId)!.judgeAssessments.push(group[0])
      continue
    }
    // Contrato trata avaliação do juiz por claim como 1:1 -- 2+
    // registros explícitos pra mesma claim_id são ambíguos, NENHUM vira
    // conclusão semântica do juiz pra esta claim (nunca last-write-wins,
    // nunca "as duas valem").
    integrityIssues.push({
      kind: 'duplicate_claim_assessment',
      claimId,
      referenceId: null,
      detail: `${group.length} avaliações do juiz referenciam a mesma claim_id (${claimId}) -- nenhuma foi anexada como conclusão do juiz pra esta afirmação; nenhuma "vence" sobre as outras.`,
    })
    for (const assessment of group) {
      quarantinedAssessments.push({ assessment, reason: 'duplicate_claim_assessment' })
    }
  }

  // --- Canal: análise da fonte ---------------------------------------
  // Identidade de source-result também precisa ser inequívoca -- ids
  // duplicados nunca resolvem "exatamente uma vez", então NENHUM
  // registro com id duplicado entra numa unidade de claim (mesmo que o
  // claim_id individual dele resolvesse bem). Multiplicidade de
  // registros DIFERENTES (ids distintos) pra uma mesma claim continua
  // inteiramente permitida.
  const sourceResultGroupsById = new Map<string, SourceClaimAnalysisResultPublic[]>()
  for (const entry of sourceAnalysis?.claim_results ?? []) {
    const group = sourceResultGroupsById.get(entry.id)
    if (group) group.push(entry)
    else sourceResultGroupsById.set(entry.id, [entry])
  }

  const duplicateSourceResultIds = new Set<string>()
  // Só ids ÚNICOS entram aqui -- usado pela validação de reconciliação
  // abaixo pra decidir se um source_claim_result_id "resolve exatamente
  // uma vez" e a QUAL claim ele pertence.
  const uniqueSourceResultClaimId = new Map<string, string | null>()
  for (const [id, group] of sourceResultGroupsById) {
    if (group.length > 1) {
      duplicateSourceResultIds.add(id)
      integrityIssues.push({
        kind: 'duplicate_source_result_identity',
        claimId: null,
        referenceId: id,
        detail: `${group.length} entradas da análise de fonte compartilham o mesmo id (${id}) -- nenhuma resolve de forma inequívoca; todas ficam fora das unidades de claim.`,
      })
    } else {
      uniqueSourceResultClaimId.set(id, group[0].claim_id)
    }
  }

  const unattributedRejectedSourceEntries: RejectedSourceEntryPublic[] = []
  const quarantinedSourceResults: QuarantinedSourceResult[] = []

  for (const entry of sourceAnalysis?.claim_results ?? []) {
    if (duplicateSourceResultIds.has(entry.id)) {
      quarantinedSourceResults.push({ entry, reason: 'duplicate_source_result_identity' })
      continue
    }

    if (isRejectedEntry(entry) && entry.claim_id === null) {
      // Nunca pertence a uma unidade de claim -- por definição.
      unattributedRejectedSourceEntries.push(entry)
      continue
    }

    const claimId = entry.claim_id as string
    const resolution = resolveTargetClaim(claimId)
    if (!resolution.ok) {
      integrityIssues.push({
        kind: resolution.kind,
        claimId,
        referenceId: entry.id,
        detail:
          resolution.kind === 'ambiguous_claim_reference'
            ? `Entrada da análise de fonte (id=${entry.id}, kind=${entry.kind}) referencia claim_id ${claimId}, que é ambíguo neste audit.`
            : `Entrada da análise de fonte (id=${entry.id}, kind=${entry.kind}) referencia claim_id desconhecido neste audit: ${claimId}.`,
      })
      quarantinedSourceResults.push({ entry, reason: resolution.kind })
      continue
    }

    unitsById.get(claimId)!.sourceResults.push(entry)
  }

  // --- Canal: reconciliação juiz<->fonte -----------------------------
  // Um outcome só é anexado depois de validar o envelope de referência
  // COMPLETO -- qualquer violação quarentena o outcome INTEIRO (nunca
  // anexa parcialmente).
  const quarantinedReconciliationOutcomes: QuarantinedReconciliationOutcome[] = []

  // Coerência de ENVELOPE (repair pós-revisão adversarial nº2) -- ANTES
  // de validar outcome por outcome, o próprio `reconciliation.status`
  // precisa corresponder à realidade desta execução: 'judge_unavailable'
  // exige `judgeVerdict === null`, 'complete' exige um veredito real
  // presente. Quando o envelope é incoerente, NENHUM outcome desta
  // reconciliação pode virar relacionamento product-facing -- mesmo que
  // um outcome individual pareça perfeitamente válido -- porque a
  // premissa de status que cada outcome individualmente assume já não é
  // confiável. Nunca infere um status substituto; só recusa confiar no
  // que foi produzido.
  const reconciliationEnvelopeCoherent =
    reconciliation === null ||
    (reconciliation.status === 'judge_unavailable') === (judgeVerdict === null)

  if (reconciliation !== null && !reconciliationEnvelopeCoherent) {
    integrityIssues.push({
      kind: 'incoherent_reconciliation_envelope',
      claimId: null,
      referenceId: null,
      detail:
        reconciliation.status === 'judge_unavailable'
          ? 'Reconciliação tem status judge_unavailable, mas esta execução TEM um veredito de juiz real -- envelope de nível de execução incoerente; nenhum outcome desta reconciliação é confiável o suficiente pra virar relacionamento product-facing.'
          : 'Reconciliação tem status complete, mas esta execução NÃO tem veredito de juiz -- envelope de nível de execução incoerente; nenhum outcome desta reconciliação é confiável o suficiente pra virar relacionamento product-facing.',
    })
    for (const outcome of reconciliation.claim_outcomes) {
      quarantinedReconciliationOutcomes.push({ outcome, reasons: ['incoherent_reconciliation_envelope'] })
    }
  } else if (reconciliation !== null) {
    // Envelope coerente -- validação por outcome continua, incluindo
    // cardinalidade 1:1 por claim_id. Repair pós-revisão adversarial nº1
    // -- a cardinalidade precisa ser calculada sobre TODOS os outcomes
    // BRUTOS que citam uma claim_id (válidos + inválidos), nunca só
    // entre os que já passaram na validação individual -- senão um
    // outcome malformado poderia "abrir espaço" pra um outcome válido
    // vizinho da MESMA claim_id ser tratado como se fosse o único,
    // confiável por eliminação. Só depois de agrupar por claim_id é que
    // decidimos: exatamente 1 outcome bruto E individualmente válido ->
    // anexa; qualquer outra combinação (2+ brutos, ou o único bruto
    // sendo inválido) -> quarentena TODOS os da claim_id.
    interface ProcessedOutcome {
      outcome: ClaimReconciliationOutcomePublic
      issues: AuditIntegrityIssueKind[]
    }
    const outcomesByClaimId = new Map<string, ProcessedOutcome[]>()

    function judgeVerdictMismatchDetail(outcome: ClaimReconciliationOutcomePublic): string | null {
      if (reconciliation!.status === 'judge_unavailable') {
        if (outcome.judge_verdict_id !== null) {
          return `Reconciliação (claim_id=${outcome.claim_id}) tem status judge_unavailable mas referencia judge_verdict_id=${outcome.judge_verdict_id} -- nenhum veredito deveria existir pra referenciar.`
        }
        return null
      }
      // status === 'complete' (e o envelope já foi confirmado coerente,
      // então `judgeVerdict` aqui nunca é null).
      if (outcome.judge_verdict_id === null) {
        return `Reconciliação (claim_id=${outcome.claim_id}) tem status complete mas judge_verdict_id é null -- deveria referenciar o veredito real desta execução.`
      }
      if (outcome.judge_verdict_id !== judgeVerdict!.id) {
        return `Reconciliação (claim_id=${outcome.claim_id}) referencia judge_verdict_id=${outcome.judge_verdict_id}, que não bate com o veredito real desta execução (id=${judgeVerdict!.id}).`
      }
      return null
    }

    for (const outcome of reconciliation.claim_outcomes) {
      const resolution = resolveTargetClaim(outcome.claim_id)
      if (!resolution.ok) {
        integrityIssues.push({
          kind: resolution.kind,
          claimId: outcome.claim_id,
          referenceId: null,
          detail:
            resolution.kind === 'ambiguous_claim_reference'
              ? `Reconciliação referencia claim_id ${outcome.claim_id}, que é ambíguo neste audit.`
              : `Reconciliação referencia claim_id desconhecido neste audit: ${outcome.claim_id}.`,
        })
        // claim_id não resolve -- não existe unidade pra agrupar
        // cardinalidade, então quarentena direto, sem passar pelo
        // agrupamento por claim_id abaixo.
        quarantinedReconciliationOutcomes.push({ outcome, reasons: [resolution.kind] })
        continue
      }

      const issues: AuditIntegrityIssueKind[] = []

      const verdictMismatchDetail = judgeVerdictMismatchDetail(outcome)
      if (verdictMismatchDetail !== null) {
        integrityIssues.push({
          kind: 'mismatched_judge_verdict_id',
          claimId: outcome.claim_id,
          referenceId: outcome.judge_verdict_id,
          detail: verdictMismatchDetail,
        })
        issues.push('mismatched_judge_verdict_id')
      }

      // Cada source_claim_result_id precisa: (a) resolver exatamente
      // uma vez neste audit, (b) pertencer à MESMA claim deste outcome,
      // (c) nunca se repetir dentro da própria lista.
      const seenInThisOutcome = new Set<string>()
      for (const sourceResultId of outcome.source_claim_result_ids) {
        if (seenInThisOutcome.has(sourceResultId)) {
          integrityIssues.push({
            kind: 'duplicate_source_result_reference',
            claimId: outcome.claim_id,
            referenceId: sourceResultId,
            detail: `Reconciliação (claim_id=${outcome.claim_id}) referencia o mesmo source-result id (${sourceResultId}) mais de uma vez na mesma entrada.`,
          })
          issues.push('duplicate_source_result_reference')
          continue
        }
        seenInThisOutcome.add(sourceResultId)

        if (!uniqueSourceResultClaimId.has(sourceResultId)) {
          integrityIssues.push({
            kind: 'dangling_source_result_reference',
            claimId: outcome.claim_id,
            referenceId: sourceResultId,
            detail: `Reconciliação (claim_id=${outcome.claim_id}) referencia uma entrada de análise de fonte que não resolve de forma inequívoca neste audit (id=${sourceResultId}).`,
          })
          issues.push('dangling_source_result_reference')
          continue
        }

        const owningClaimId = uniqueSourceResultClaimId.get(sourceResultId)!
        if (owningClaimId !== outcome.claim_id) {
          integrityIssues.push({
            kind: 'cross_claim_source_result_reference',
            claimId: outcome.claim_id,
            referenceId: sourceResultId,
            detail: `Reconciliação (claim_id=${outcome.claim_id}) referencia uma entrada de análise de fonte (id=${sourceResultId}) que pertence a outra claim (claim_id=${owningClaimId ?? 'nenhuma'}).`,
          })
          issues.push('cross_claim_source_result_reference')
        }
      }

      const group = outcomesByClaimId.get(outcome.claim_id)
      if (group) group.push({ outcome, issues })
      else outcomesByClaimId.set(outcome.claim_id, [{ outcome, issues }])
    }

    for (const [claimId, group] of outcomesByClaimId) {
      if (group.length > 1) {
        // Cardinalidade BRUTA (válidos + inválidos) > 1 -- contrato
        // trata reconciliação por claim como 1:1, então nenhum outcome
        // desta claim_id vira conclusão semântica, mesmo que algum
        // deles fosse individualmente válido. Cada um preserva seus
        // próprios motivos de validação, MAIS o motivo de cardinalidade.
        integrityIssues.push({
          kind: 'duplicate_reconciliation_outcome',
          claimId,
          referenceId: null,
          detail: `${group.length} outcomes de reconciliação (válidos ou não) referenciam a mesma claim_id (${claimId}) -- nenhum foi anexado como conclusão da aplicação pra esta afirmação; nenhum "vence" sobre os outros, mesmo que algum fosse individualmente válido.`,
        })
        for (const { outcome, issues } of group) {
          quarantinedReconciliationOutcomes.push({
            outcome,
            reasons: [...issues, 'duplicate_reconciliation_outcome'],
          })
        }
        continue
      }

      const { outcome, issues } = group[0]
      if (issues.length > 0) {
        quarantinedReconciliationOutcomes.push({ outcome, reasons: issues })
      } else {
        unitsById.get(claimId)!.reconciliationOutcomes.push(outcome)
      }
    }
  }

  return {
    units: unitOrder.map((id) => unitsById.get(id)!),
    unattributedRejectedSourceEntries,
    judgeVerdictPresent: judgeVerdict !== null,
    reconciliationEnvelopeCoherent,
    integrityIssues,
    ambiguousClaims,
    quarantinedAssessments,
    quarantinedSourceResults,
    quarantinedReconciliationOutcomes,
  }
}
