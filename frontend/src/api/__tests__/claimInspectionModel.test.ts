import { describe, expect, it } from 'vitest'
import { buildClaimInspectionModel } from '../claimInspectionModel'
import type {
  ClaimAssessmentPublic,
  ClaimPublic,
  ClaimReconciliationOutcomePublic,
  JudgeVerdictPublic,
  RejectedSourceEntryPublic,
  SourceAnalysisOutcome,
  SourceJudgeReconciliationResultPublic,
  ValidSourceRelationPublic,
} from '../types'

function makeClaim(overrides: Partial<ClaimPublic>): ClaimPublic {
  return {
    id: 'claim-1',
    text: 'Brasília é a capital do Brasil.',
    source_model_response_id: 'mr-1',
    round_introduced: 1,
    parent_claim_id: null,
    merged_from_claim_ids: [],
    status: 'consensus',
    supporting_model_response_ids: [],
    supporting_models: [],
    total_models_in_round: 1,
    support_scope_model_count: null,
    confidence: null,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

function makeAssessment(overrides: Partial<ClaimAssessmentPublic>): ClaimAssessmentPublic {
  return {
    claim_id: 'claim-1',
    verdict: 'supported',
    explanation: 'justificativa',
    ...overrides,
  }
}

function makeVerdict(overrides: Partial<JudgeVerdictPublic> = {}): JudgeVerdictPublic {
  return {
    id: 'verdict-1',
    evaluated_through_round: 1,
    judge_model: 'claude-sonnet-5',
    judge_model_identity_source: 'provider_reported',
    claim_assessments: [],
    best_arguments_by: {},
    debate_limitations: [],
    confidence: 0.8,
    reasoning: 'justificativa',
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

function makeRelation(overrides: Partial<ValidSourceRelationPublic>): ValidSourceRelationPublic {
  return {
    kind: 'relation',
    id: 'rel-1',
    claim_id: 'claim-1',
    relation: 'supports',
    excerpt: 'trecho',
    excerpt_start: 0,
    excerpt_end: 6,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

function makeRejected(overrides: Partial<RejectedSourceEntryPublic>): RejectedSourceEntryPublic {
  return {
    kind: 'rejected',
    id: 'rej-1',
    claim_id: null,
    reason: 'omitted_by_model',
    raw_entry: null,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

function makeSourceAnalysis(overrides: Partial<SourceAnalysisOutcome> = {}): SourceAnalysisOutcome {
  return {
    skipped_reason: null,
    source_analyzer_provider: 'anthropic',
    cumulative_budget_exceeded: false,
    attempts: [],
    claim_results: [],
    ...overrides,
  }
}

function makeReconciliationOutcome(
  overrides: Partial<ClaimReconciliationOutcomePublic>,
): ClaimReconciliationOutcomePublic {
  return {
    claim_id: 'claim-1',
    judge_verdict_id: 'verdict-1',
    source_claim_result_ids: [],
    source_state: 'supports',
    channel_relationship: 'directionally_aligned',
    ...overrides,
  }
}

function makeReconciliation(
  claim_outcomes: ClaimReconciliationOutcomePublic[],
  status: SourceJudgeReconciliationResultPublic['status'] = 'complete',
): SourceJudgeReconciliationResultPublic {
  return { contract_version: 'source_judge_reconciliation_v1', status, claim_outcomes }
}

describe('buildClaimInspectionModel — identidade e ordenação', () => {
  it('cria uma unidade por claim, na mesma ordem de claims[]', () => {
    const claims = [
      makeClaim({ id: 'c1', text: 'Primeira.' }),
      makeClaim({ id: 'c2', text: 'Segunda.' }),
      makeClaim({ id: 'c3', text: 'Terceira.' }),
    ]

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units.map((u) => u.claim.id)).toEqual(['c1', 'c2', 'c3'])
  })

  it('embaralhar os arrays de entrada nunca muda nenhum join (só o Map por ID importa)', () => {
    const claims = [makeClaim({ id: 'c1', text: 'A' }), makeClaim({ id: 'c2', text: 'B' })]
    const assessments = [makeAssessment({ claim_id: 'c2' }), makeAssessment({ claim_id: 'c1' })]
    const judgeVerdict = makeVerdict({ claim_assessments: assessments })
    const relations = [makeRelation({ id: 'r2', claim_id: 'c2' }), makeRelation({ id: 'r1', claim_id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({ claim_results: relations })
    const outcomes = [
      makeReconciliationOutcome({ claim_id: 'c2' }),
      makeReconciliationOutcome({ claim_id: 'c1' }),
    ]
    const reconciliation = makeReconciliation(outcomes)

    const modelA = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis, reconciliation })
    const modelB = buildClaimInspectionModel({
      claims: [...claims].reverse(),
      judgeVerdict: makeVerdict({ claim_assessments: [...assessments].reverse() }),
      sourceAnalysis: makeSourceAnalysis({ claim_results: [...relations].reverse() }),
      reconciliation: makeReconciliation([...outcomes].reverse()),
    })

    const byId = (model: ReturnType<typeof buildClaimInspectionModel>) =>
      new Map(model.units.map((u) => [u.claim.id, u]))

    const unitsA = byId(modelA)
    const unitsB = byId(modelB)
    for (const id of ['c1', 'c2']) {
      expect(unitsA.get(id)?.judgeAssessments.map((a) => a.claim_id)).toEqual(
        unitsB.get(id)?.judgeAssessments.map((a) => a.claim_id),
      )
      expect(unitsA.get(id)?.sourceResults.map((r) => r.id)).toEqual(
        unitsB.get(id)?.sourceResults.map((r) => r.id),
      )
      expect(unitsA.get(id)?.reconciliationOutcomes.map((o) => o.claim_id)).toEqual(
        unitsB.get(id)?.reconciliationOutcomes.map((o) => o.claim_id),
      )
    }
    expect(modelA.integrityIssues).toHaveLength(0)
    expect(modelB.integrityIssues).toHaveLength(0)
  })

  it('claims com texto idêntico mas IDs diferentes permanecem unidades separadas', () => {
    const claims = [
      makeClaim({ id: 'c1', text: 'O céu é azul.' }),
      makeClaim({ id: 'c2', text: 'O céu é azul.' }),
    ]
    const judgeVerdict = makeVerdict({
      claim_assessments: [makeAssessment({ claim_id: 'c1', verdict: 'supported' })],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units).toHaveLength(2)
    const c1 = model.units.find((u) => u.claim.id === 'c1')!
    const c2 = model.units.find((u) => u.claim.id === 'c2')!
    expect(c1.judgeAssessments).toHaveLength(1)
    // A avaliação de c1 NUNCA vaza pra c2 só porque o texto é igual.
    expect(c2.judgeAssessments).toHaveLength(0)
  })
})

describe('buildClaimInspectionModel — canal: avaliações do juiz', () => {
  it('junta assessments só por claim_id, nunca por posição/ordem', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2' })]
    const judgeVerdict = makeVerdict({
      claim_assessments: [
        makeAssessment({ claim_id: 'c2', verdict: 'rejected' }),
        makeAssessment({ claim_id: 'c1', verdict: 'supported' }),
      ],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    const c1 = model.units.find((u) => u.claim.id === 'c1')!
    const c2 = model.units.find((u) => u.claim.id === 'c2')!
    expect(c1.judgeAssessments[0].verdict).toBe('supported')
    expect(c2.judgeAssessments[0].verdict).toBe('rejected')
  })

  it('judgeVerdict null é honestamente distinto de "avaliou e não achou nada pra dizer"', () => {
    const claims = [makeClaim({ id: 'c1' })]

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.judgeVerdictPresent).toBe(false)
    expect(model.units[0].judgeAssessments).toEqual([])
    expect(model.integrityIssues).toHaveLength(0)
  })

  it('veredito presente mas sem avaliação desta claim específica: array vazio, sem inferir nada', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2' })]
    const judgeVerdict = makeVerdict({ claim_assessments: [makeAssessment({ claim_id: 'c1' })] })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.judgeVerdictPresent).toBe(true)
    expect(model.units.find((u) => u.claim.id === 'c2')?.judgeAssessments).toEqual([])
  })

  it('assessment referenciando claim_id desconhecido vira integrity issue, nunca é anexada a uma claim adivinhada', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({
      claim_assessments: [makeAssessment({ claim_id: 'claim-fantasma' })],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units[0].judgeAssessments).toEqual([])
    expect(model.integrityIssues).toEqual([
      expect.objectContaining({ kind: 'unknown_claim_reference', claimId: 'claim-fantasma' }),
    ])
  })

  it('duas avaliações explícitas pra mesma claim_id: NENHUMA vira conclusão semântica (nunca last-write-wins, nunca "as duas valem") -- ambas em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const primeira = makeAssessment({ claim_id: 'c1', verdict: 'supported', explanation: 'primeira' })
    const segunda = makeAssessment({ claim_id: 'c1', verdict: 'rejected', explanation: 'segunda' })
    const judgeVerdict = makeVerdict({ claim_assessments: [primeira, segunda] })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    // Nem a primeira nem a última "vence" -- nenhuma conclusão semântica
    // do juiz é apresentada pra esta claim.
    expect(model.units[0].judgeAssessments).toEqual([])
    expect(
      model.integrityIssues.some((i) => i.kind === 'duplicate_claim_assessment' && i.claimId === 'c1'),
    ).toBe(true)
    // Os registros ORIGINAIS continuam inspecionáveis em quarentena --
    // nunca descartados de vez.
    expect(model.quarantinedAssessments).toHaveLength(2)
    expect(model.quarantinedAssessments.map((q) => q.assessment.explanation)).toEqual([
      'primeira',
      'segunda',
    ])
    expect(model.quarantinedAssessments.every((q) => q.reason === 'duplicate_claim_assessment')).toBe(
      true,
    )
  })
})

describe('buildClaimInspectionModel — canal: análise da fonte', () => {
  it('preserva múltiplos registros de fonte pra mesma claim', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [
        makeRelation({ id: 'r1', claim_id: 'c1', relation: 'supports' }),
        makeRelation({ id: 'r2', claim_id: 'c1', relation: 'unresolved' }),
      ],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults.map((r) => r.id)).toEqual(['r1', 'r2'])
  })

  it('estados ausente/pulado/falho permanecem distintos: sourceAnalysis null nunca produz resultados', () => {
    const claims = [makeClaim({ id: 'c1' })]

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toEqual([])
    expect(model.unattributedRejectedSourceEntries).toEqual([])
  })

  it('sourceAnalysis com skipped_reason (pulado/falho) nunca fabrica claim_results', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      skipped_reason: 'source_analysis_transport_failed',
      claim_results: [],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toEqual([])
  })

  it('entrada rejeitada com claim_id (atribuída) entra na unidade da claim, ao lado de relations', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRejected({ id: 'rej-1', claim_id: 'c1', reason: 'duplicate_claim_id' })],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toHaveLength(1)
    expect(model.unattributedRejectedSourceEntries).toEqual([])
  })

  it('entrada rejeitada com claim_id=null (não atribuída) fica fora de TODAS as unidades', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const rejected = makeRejected({ id: 'rej-1', claim_id: null, reason: 'omitted_by_model' })
    const sourceAnalysis = makeSourceAnalysis({ claim_results: [rejected] })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toEqual([])
    expect(model.unattributedRejectedSourceEntries).toEqual([rejected])
    expect(model.integrityIssues).toHaveLength(0)
  })

  it('relation referenciando claim_id desconhecido vira integrity issue, nunca anexada a outra claim', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRelation({ id: 'rel-x', claim_id: 'claim-que-nao-existe' })],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toEqual([])
    expect(model.integrityIssues).toEqual([
      expect.objectContaining({
        kind: 'unknown_claim_reference',
        claimId: 'claim-que-nao-existe',
        referenceId: 'rel-x',
      }),
    ])
  })

  it('entrada rejeitada com claim_id atribuído mas desconhecido também vira integrity issue (não some pro bucket "não atribuída")', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const rejected = makeRejected({ id: 'rej-x', claim_id: 'claim-fantasma', reason: 'invalid_entry' })
    const sourceAnalysis = makeSourceAnalysis({ claim_results: [rejected] })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.unattributedRejectedSourceEntries).toEqual([])
    expect(model.integrityIssues).toEqual([
      expect.objectContaining({ kind: 'unknown_claim_reference', claimId: 'claim-fantasma' }),
    ])
  })
})

describe('buildClaimInspectionModel — canal: reconciliação juiz<->fonte', () => {
  it('reconciliation null (histórico) nunca é inferido -- nenhum outcome, nenhuma issue', () => {
    const claims = [makeClaim({ id: 'c1' })]

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: makeVerdict(),
      sourceAnalysis: makeSourceAnalysis(),
      reconciliation: null,
    })

    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(model.integrityIssues).toHaveLength(0)
  })

  it('resolve source_claim_result_ids exatamente contra as entradas reais de análise de fonte', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRelation({ id: 'rel-1', claim_id: 'c1' })],
    })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', source_claim_result_ids: ['rel-1'] }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: makeVerdict({ id: 'verdict-1' }),
      sourceAnalysis,
      reconciliation,
    })

    expect(model.integrityIssues).toHaveLength(0)
    expect(model.units[0].reconciliationOutcomes[0].source_claim_result_ids).toEqual(['rel-1'])
  })

  it('claim_id desconhecido em reconciliation vira integrity issue, nunca anexado a outra claim, outcome inteiro em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation(
      [makeReconciliationOutcome({ claim_id: 'claim-desconhecida', judge_verdict_id: null })],
      'judge_unavailable',
    )

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'unknown_claim_reference' && i.claimId === 'claim-desconhecida',
      ),
    ).toBe(true)
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(1)
    expect(model.quarantinedReconciliationOutcomes[0].reasons).toContain('unknown_claim_reference')
  })

  it('judge_verdict_id que não bate com o veredito real vira integrity issue -- outcome INTEIRO fica fora de reconciliationOutcomes (nunca anexado parcialmente)', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-real' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-de-outra-execucao' }),
    ])

    const model = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis: null, reconciliation })

    // O claim_id em si resolve bem, mas a referência de veredito é
    // incoerente -- isso quarentena o outcome INTEIRO, nunca anexa
    // "a parte que ainda valeria".
    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'mismatched_judge_verdict_id' && i.referenceId === 'verdict-de-outra-execucao',
      ),
    ).toBe(true)
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(1)
    expect(model.quarantinedReconciliationOutcomes[0].outcome.claim_id).toBe('c1')
    expect(model.quarantinedReconciliationOutcomes[0].reasons).toEqual(['mismatched_judge_verdict_id'])
  })

  it('judge_verdict_id referenciado mas nenhum veredito existe nesta execução (envelope status=complete sem judgeVerdict): envelope incoerente, outcome em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(false)
    expect(
      model.integrityIssues.some((i) => i.kind === 'incoherent_reconciliation_envelope'),
    ).toBe(true)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('judge_verdict_id null com status judge_unavailable é coerente -- nunca tratado como mismatch, outcome anexa normalmente', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation(
      [makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: null, source_claim_result_ids: [] })],
      'judge_unavailable',
    )

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.integrityIssues.some((i) => i.kind === 'mismatched_judge_verdict_id')).toBe(false)
    expect(model.units[0].reconciliationOutcomes).toHaveLength(1)
  })

  it('judge_verdict_id null com status complete é incoerente (deveria referenciar o veredito real) -- vira integrity issue, outcome em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: null, source_claim_result_ids: [] }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(
      model.integrityIssues.some((i) => i.kind === 'mismatched_judge_verdict_id' && i.claimId === 'c1'),
    ).toBe(true)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('judge_verdict_id não-null com status judge_unavailable é incoerente (nenhum veredito deveria existir pra referenciar) -- vira integrity issue, outcome em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation(
      [
        makeReconciliationOutcome({
          claim_id: 'c1',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: [],
        }),
      ],
      'judge_unavailable',
    )

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(
      model.integrityIssues.some((i) => i.kind === 'mismatched_judge_verdict_id' && i.claimId === 'c1'),
    ).toBe(true)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('source_claim_result_ids referenciando id inexistente vira integrity issue e quarentena o outcome inteiro', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRelation({ id: 'rel-real', claim_id: 'c1' })],
    })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-real', 'rel-fantasma'],
      }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis,
      reconciliation,
    })

    const danglingIssues = model.integrityIssues.filter(
      (i) => i.kind === 'dangling_source_result_reference',
    )
    expect(danglingIssues).toHaveLength(1)
    expect(danglingIssues[0].referenceId).toBe('rel-fantasma')
    // rel-real por si só resolveria -- mas o outcome inteiro fica de
    // fora porque rel-fantasma não resolve (nunca anexa parcialmente).
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('reconciliation sem nenhuma fonte (sourceAnalysis null) sinaliza TODOS os source_claim_result_ids como dangling', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-1'],
      }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'dangling_source_result_reference' && i.referenceId === 'rel-1',
      ),
    ).toBe(true)
  })

  it('reconciliação referenciando um source-result de OUTRA claim é quarentenada (cross-claim reference)', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2', text: 'Outra afirmação.' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRelation({ id: 'rel-de-c2', claim_id: 'c2' })],
    })
    // outcome de c1 referencia um source-result que na verdade pertence
    // a c2 -- nunca deveria "pertencer" às duas.
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-de-c2'],
      }),
    ])

    const model = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis, reconciliation })

    expect(
      model.integrityIssues.some(
        (i) =>
          i.kind === 'cross_claim_source_result_reference' &&
          i.claimId === 'c1' &&
          i.referenceId === 'rel-de-c2',
      ),
    ).toBe(true)
    expect(model.units.find((u) => u.claim.id === 'c1')?.reconciliationOutcomes).toEqual([])
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(1)
    expect(model.quarantinedReconciliationOutcomes[0].reasons).toEqual([
      'cross_claim_source_result_reference',
    ])
  })

  it('reconciliação referenciando o mesmo source-result id duas vezes na mesma lista é quarentenada', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [makeRelation({ id: 'rel-1', claim_id: 'c1' })],
    })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-1', 'rel-1'],
      }),
    ])

    const model = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis, reconciliation })

    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'duplicate_source_result_reference' && i.referenceId === 'rel-1',
      ),
    ).toBe(true)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('duas claims, cada uma com reconciliação válida referenciando corretamente seu próprio source-result -- nenhum falso positivo cross-claim', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2', text: 'Outra afirmação.' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [
        makeRelation({ id: 'rel-c1', claim_id: 'c1' }),
        makeRelation({ id: 'rel-c2', claim_id: 'c2' }),
      ],
    })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-c1'],
      }),
      makeReconciliationOutcome({
        claim_id: 'c2',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-c2'],
      }),
    ])

    const model = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis, reconciliation })

    expect(model.integrityIssues).toHaveLength(0)
    expect(model.units.find((u) => u.claim.id === 'c1')?.reconciliationOutcomes).toHaveLength(1)
    expect(model.units.find((u) => u.claim.id === 'c2')?.reconciliationOutcomes).toHaveLength(1)
  })

  it('duas claims, cada uma com exatamente um outcome estruturalmente válido: ambas anexam normalmente (o caso comum nunca é penalizado)', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2', text: 'Outra afirmação.' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
      makeReconciliationOutcome({ claim_id: 'c2', judge_verdict_id: 'verdict-1' }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.integrityIssues).toHaveLength(0)
    expect(model.units.find((u) => u.claim.id === 'c1')?.reconciliationOutcomes).toHaveLength(1)
    expect(model.units.find((u) => u.claim.id === 'c2')?.reconciliationOutcomes).toHaveLength(1)
  })

  it('duas outcomes estruturalmente válidas pra MESMA claim_id: contrato é 1:1, nenhuma vira conclusão semântica -- ambas em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const primeiro = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: 'verdict-1',
      channel_relationship: 'directionally_aligned',
    })
    const segundo = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: 'verdict-1',
      channel_relationship: 'in_tension',
    })
    const reconciliation = makeReconciliation([primeiro, segundo])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'duplicate_reconciliation_outcome' && i.claimId === 'c1',
      ),
    ).toBe(true)
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(2)
    expect(
      model.quarantinedReconciliationOutcomes.every((q) => q.reasons.includes('duplicate_reconciliation_outcome')),
    ).toBe(true)
  })
})

describe('buildClaimInspectionModel — repair: cardinalidade de reconciliação usa ocorrências BRUTAS, nunca só as válidas', () => {
  it('1 outcome válido + 1 outcome inválido pra mesma claim_id (válido primeiro): NENHUM anexa, ambos em quarentena com seus motivos preservados', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const valido = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: 'verdict-1',
      source_claim_result_ids: [],
    })
    const invalido = makeReconciliationOutcome({
      claim_id: 'c1',
      // judge_verdict_id incoerente -- falha na validação INDIVIDUAL.
      judge_verdict_id: 'verdict-de-outra-execucao',
      source_claim_result_ids: [],
    })
    const reconciliation = makeReconciliation([valido, invalido])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    // O outcome individualmente válido NUNCA vira confiável só porque o
    // vizinho malformado "abriu espaço" -- cardinalidade bruta é 2,
    // então nenhum dos dois anexa.
    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'duplicate_reconciliation_outcome' && i.claimId === 'c1',
      ),
    ).toBe(true)
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(2)
    // AMBOS carregam o motivo de duplicidade...
    expect(
      model.quarantinedReconciliationOutcomes.every((q) =>
        q.reasons.includes('duplicate_reconciliation_outcome'),
      ),
    ).toBe(true)
    // ...e o que era individualmente inválido preserva SEU motivo
    // específico também (nunca substituído/perdido).
    const invalidQuarantined = model.quarantinedReconciliationOutcomes.find(
      (q) => q.outcome === invalido,
    )!
    expect(invalidQuarantined.reasons).toContain('mismatched_judge_verdict_id')
    // O válido, por sua vez, NÃO carrega um motivo individual (só o de
    // cardinalidade) -- prova de que ele PASSARIA sozinho, mas não passa
    // por causa da duplicidade.
    const validQuarantined = model.quarantinedReconciliationOutcomes.find((q) => q.outcome === valido)!
    expect(validQuarantined.reasons).toEqual(['duplicate_reconciliation_outcome'])
  })

  it('mesmo cenário em ordem INVERTIDA (inválido primeiro): resultado idêntico -- ordem de input nunca muda o resultado', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const valido = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: 'verdict-1',
      source_claim_result_ids: [],
    })
    const invalido = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: 'verdict-de-outra-execucao',
      source_claim_result_ids: [],
    })
    const reconciliation = makeReconciliation([invalido, valido])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(model.quarantinedReconciliationOutcomes).toHaveLength(2)
    expect(
      model.quarantinedReconciliationOutcomes.every((q) =>
        q.reasons.includes('duplicate_reconciliation_outcome'),
      ),
    ).toBe(true)
  })

  it('zero outcomes confiáveis sobrevivem quando há duplicidade bruta -- reconciliationOutcomes vazio pra essa claim em qualquer caso', () => {
    const claims = [makeClaim({ id: 'c1' }), makeClaim({ id: 'c2', text: 'Outra.' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
      // c2 nunca é tocada -- prova de que o repair é escopado por
      // claim_id, nunca um efeito colateral global.
      makeReconciliationOutcome({ claim_id: 'c2', judge_verdict_id: 'verdict-1' }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.units.find((u) => u.claim.id === 'c1')?.reconciliationOutcomes).toEqual([])
    expect(model.units.find((u) => u.claim.id === 'c2')?.reconciliationOutcomes).toHaveLength(1)
  })
})

describe('buildClaimInspectionModel — repair: coerência de envelope reconciliation.status <-> judgeVerdict', () => {
  it('judge_unavailable + veredito real presente + outcomes vazios: envelope incoerente, nenhum outcome (não há nenhum de qualquer forma), diagnóstico explícito', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([], 'judge_unavailable')

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(false)
    expect(
      model.integrityIssues.some((i) => i.kind === 'incoherent_reconciliation_envelope'),
    ).toBe(true)
  })

  it('judge_unavailable + veredito real presente + outcomes não vazios: NENHUM outcome anexa, todos em quarentena com incoherent_reconciliation_envelope', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const outcome = makeReconciliationOutcome({
      claim_id: 'c1',
      judge_verdict_id: null,
      source_claim_result_ids: [],
    })
    const reconciliation = makeReconciliation([outcome], 'judge_unavailable')

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(false)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
    expect(model.quarantinedReconciliationOutcomes).toEqual([
      { outcome, reasons: ['incoherent_reconciliation_envelope'] },
    ])
  })

  it('complete + nenhum veredito real: envelope incoerente (já coberto também no canal de reconciliação, reafirmado aqui)', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(false)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('complete + veredito real presente: envelope coerente, outcomes seguem validação individual normalmente', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: 'verdict-1' }),
    ])

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(true)
    expect(
      model.integrityIssues.some((i) => i.kind === 'incoherent_reconciliation_envelope'),
    ).toBe(false)
    expect(model.units[0].reconciliationOutcomes).toHaveLength(1)
  })

  it('judge_unavailable + nenhum veredito real: envelope coerente, outcomes seguem validação individual normalmente', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const reconciliation = makeReconciliation(
      [makeReconciliationOutcome({ claim_id: 'c1', judge_verdict_id: null, source_claim_result_ids: [] })],
      'judge_unavailable',
    )

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(true)
    expect(
      model.integrityIssues.some((i) => i.kind === 'incoherent_reconciliation_envelope'),
    ).toBe(false)
    expect(model.units[0].reconciliationOutcomes).toHaveLength(1)
  })

  it('reconciliation === null: reconciliationEnvelopeCoherent é true (vácuo, nada a avaliar) -- nunca inferido como incoerente', () => {
    const claims = [makeClaim({ id: 'c1' })]

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.reconciliationEnvelopeCoherent).toBe(true)
  })
})

describe('buildClaimInspectionModel — identidade de source-result ambígua', () => {
  it('id de source-result duplicado (duas entradas DIFERENTES) nunca resolve pra nenhuma claim -- ambas em quarentena', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const entryA = makeRelation({ id: 'rel-1', claim_id: 'c1', relation: 'supports' })
    const entryB = makeRelation({ id: 'rel-1', claim_id: 'c1', relation: 'contradicts' })
    const sourceAnalysis = makeSourceAnalysis({ claim_results: [entryA, entryB] })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toEqual([])
    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'duplicate_source_result_identity' && i.referenceId === 'rel-1',
      ),
    ).toBe(true)
    expect(model.quarantinedSourceResults).toHaveLength(2)
    expect(model.quarantinedSourceResults.every((q) => q.reason === 'duplicate_source_result_identity')).toBe(
      true,
    )
  })

  it('id de source-result duplicado não pode satisfazer resolução de reconciliação (nunca "resolve exatamente uma vez")', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const judgeVerdict = makeVerdict({ id: 'verdict-1' })
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [
        makeRelation({ id: 'rel-1', claim_id: 'c1', relation: 'supports' }),
        makeRelation({ id: 'rel-1', claim_id: 'c1', relation: 'contradicts' }),
      ],
    })
    const reconciliation = makeReconciliation([
      makeReconciliationOutcome({
        claim_id: 'c1',
        judge_verdict_id: 'verdict-1',
        source_claim_result_ids: ['rel-1'],
      }),
    ])

    const model = buildClaimInspectionModel({ claims, judgeVerdict, sourceAnalysis, reconciliation })

    expect(
      model.integrityIssues.some(
        (i) => i.kind === 'dangling_source_result_reference' && i.referenceId === 'rel-1',
      ),
    ).toBe(true)
    expect(model.units[0].reconciliationOutcomes).toEqual([])
  })

  it('multiplicidade de registros de fonte DISTINTOS pra uma claim continua permitida (não é ambiguidade)', () => {
    const claims = [makeClaim({ id: 'c1' })]
    const sourceAnalysis = makeSourceAnalysis({
      claim_results: [
        makeRelation({ id: 'rel-1', claim_id: 'c1', relation: 'supports' }),
        makeRelation({ id: 'rel-2', claim_id: 'c1', relation: 'unresolved' }),
      ],
    })

    const model = buildClaimInspectionModel({
      claims,
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.units[0].sourceResults).toHaveLength(2)
    expect(model.integrityIssues).toHaveLength(0)
    expect(model.quarantinedSourceResults).toEqual([])
  })

  it('entrada não atribuída (claim_id=null) com id ambíguo NUNCA aparece no bucket "não atribuída" -- a ambiguidade de identidade tem prioridade', () => {
    const entryA = makeRejected({ id: 'rej-1', claim_id: null, reason: 'omitted_by_model' })
    const entryB = makeRejected({ id: 'rej-1', claim_id: null, reason: 'invalid_entry' })
    const sourceAnalysis = makeSourceAnalysis({ claim_results: [entryA, entryB] })

    const model = buildClaimInspectionModel({
      claims: [makeClaim({ id: 'c1' })],
      judgeVerdict: null,
      sourceAnalysis,
      reconciliation: null,
    })

    expect(model.unattributedRejectedSourceEntries).toEqual([])
    expect(model.quarantinedSourceResults).toHaveLength(2)
  })
})

describe('buildClaimInspectionModel — identidade de claim ambígua (duplicate ClaimPublic.id)', () => {
  it('duas claims com o MESMO id e textos DIFERENTES: nem a primeira nem a última "vence" -- nenhuma unidade semântica é criada pra esse id', () => {
    const first = makeClaim({ id: 'c1', text: 'Primeira versão do texto.' })
    const second = makeClaim({ id: 'c1', text: 'Segunda versão, completamente diferente.' })

    const model = buildClaimInspectionModel({
      claims: [first, second],
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units).toEqual([])
    expect(
      model.integrityIssues.some((i) => i.kind === 'duplicate_claim_identity' && i.claimId === 'c1'),
    ).toBe(true)
    // Os DOIS registros originais continuam inspecionáveis -- nem
    // descartados, nem escolhido um "vencedor".
    expect(model.ambiguousClaims).toEqual([{ claimId: 'c1', claims: [first, second] }])
  })

  it('claim de id único ao lado de um id ambíguo: a única vira unidade normalmente, a ambígua não', () => {
    const unique = makeClaim({ id: 'c-unica', text: 'Única.' })
    const dupA = makeClaim({ id: 'c-ambigua', text: 'Versão A.' })
    const dupB = makeClaim({ id: 'c-ambigua', text: 'Versão B.' })

    const model = buildClaimInspectionModel({
      claims: [unique, dupA, dupB],
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units.map((u) => u.claim.id)).toEqual(['c-unica'])
    expect(model.ambiguousClaims).toHaveLength(1)
    expect(model.ambiguousClaims[0].claimId).toBe('c-ambigua')
  })

  it('avaliação do juiz referenciando um claim_id ambíguo é quarentenada com um motivo distinto de "desconhecido"', () => {
    const dupA = makeClaim({ id: 'c1', text: 'Versão A.' })
    const dupB = makeClaim({ id: 'c1', text: 'Versão B.' })
    const assessment = makeAssessment({ claim_id: 'c1' })
    const judgeVerdict = makeVerdict({ claim_assessments: [assessment] })

    const model = buildClaimInspectionModel({
      claims: [dupA, dupB],
      judgeVerdict,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(
      model.integrityIssues.some((i) => i.kind === 'ambiguous_claim_reference' && i.claimId === 'c1'),
    ).toBe(true)
    expect(model.quarantinedAssessments).toEqual([{ assessment, reason: 'ambiguous_claim_reference' }])
  })

  it('lineage nunca resolve pra um parent/merged-from ambíguo -- omitido honestamente, igual a um id ausente', () => {
    const dupA = makeClaim({ id: 'p1', text: 'Versão A do pai.' })
    const dupB = makeClaim({ id: 'p1', text: 'Versão B do pai.' })
    const child = makeClaim({ id: 'c1', text: 'Filha.', parent_claim_id: 'p1' })

    const model = buildClaimInspectionModel({
      claims: [dupA, dupB, child],
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    const childUnit = model.units.find((u) => u.claim.id === 'c1')!
    expect(childUnit.parentClaim).toBeNull()
  })
})

describe('buildClaimInspectionModel — lineage', () => {
  it('resolve parent_claim_id/merged_from_claim_ids só via claims[] deste audit (IDs explícitos, nunca texto)', () => {
    const parent = makeClaim({ id: 'p1', text: 'Versão anterior.' })
    const mergedA = makeClaim({ id: 'm1', text: 'Fundida A.' })
    const mergedB = makeClaim({ id: 'm2', text: 'Fundida B.' })
    const claim = makeClaim({
      id: 'c1',
      text: 'Versão atual.',
      parent_claim_id: 'p1',
      merged_from_claim_ids: ['m1', 'm2'],
    })

    const model = buildClaimInspectionModel({
      claims: [parent, mergedA, mergedB, claim],
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    const unit = model.units.find((u) => u.claim.id === 'c1')!
    expect(unit.parentClaim?.id).toBe('p1')
    expect(unit.mergedFromClaims.map((c) => c.id)).toEqual(['m1', 'm2'])
  })

  it('parent_claim_id/merged_from_claim_ids que não resolvem neste audit são omitidos honestamente, nunca inventados', () => {
    const claim = makeClaim({
      id: 'c1',
      parent_claim_id: 'p-nao-presente',
      merged_from_claim_ids: ['m-nao-presente'],
    })

    const model = buildClaimInspectionModel({
      claims: [claim],
      judgeVerdict: null,
      sourceAnalysis: null,
      reconciliation: null,
    })

    expect(model.units[0].parentClaim).toBeNull()
    expect(model.units[0].mergedFromClaims).toEqual([])
  })
})
