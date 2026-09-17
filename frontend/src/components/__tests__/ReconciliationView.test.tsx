// Claim-centered semantic inspection (UI Slice) -- ReconciliationView
// não renderiza mais claim_outcomes POR claim (isso migrou pra
// ClaimInspectionList.test.tsx, incluindo a cobertura do estado "mixed"
// neutro e de claim_id desconhecido, agora um integrity issue). Este
// arquivo cobre só as notas de nível de execução que sobraram aqui:
// reconciliation=null (histórico), envelope incoerente (repair
// pós-revisão adversarial nº2) e status=judge_unavailable coerente.

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReconciliationView } from '../ReconciliationView'
import ReconciliationViewSource from '../ReconciliationView.tsx?raw'
import type { SourceJudgeReconciliationResultPublic } from '../../api/types'

describe('ReconciliationView — notas de nível de execução', () => {
  it('trata reconciliation=null como execução histórica, sem inventar nenhum estado', () => {
    render(<ReconciliationView reconciliation={null} envelopeCoherent />)

    expect(screen.getByText(/execução anterior a este recurso/i)).toBeInTheDocument()
  })

  it('status judge_unavailable COERENTE explica por que o relacionamento não é comparável', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'judge_unavailable',
      claim_outcomes: [],
    }

    render(<ReconciliationView reconciliation={reconciliation} envelopeCoherent />)

    expect(screen.getByText(/o juiz não ficou disponível/i)).toBeInTheDocument()
  })

  it('status complete não renderiza nada standalone (o detalhamento por claim vive em cada unidade)', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [
        {
          claim_id: 'c1',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: ['rel-1'],
          source_state: 'supports',
          channel_relationship: 'directionally_aligned',
        },
      ],
    }

    const { container } = render(<ReconciliationView reconciliation={reconciliation} envelopeCoherent />)

    expect(container).toBeEmptyDOMElement()
  })

  it('nunca usa dangerouslySetInnerHTML/innerHTML (texto vindo do backend é sempre renderizado como texto)', () => {
    expect(ReconciliationViewSource).not.toContain('dangerouslySetInnerHTML')
    expect(ReconciliationViewSource).not.toContain('innerHTML')
  })
})

describe('ReconciliationView — repair pós-revisão adversarial nº2: envelope incoerente nunca declara "juiz indisponível"', () => {
  it('status=judge_unavailable mas envelope INCOERENTE (esta execução tem veredito real): NUNCA diz que o juiz ficou indisponível', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'judge_unavailable',
      claim_outcomes: [],
    }

    render(<ReconciliationView reconciliation={reconciliation} envelopeCoherent={false} />)

    expect(screen.queryByText(/o juiz não ficou disponível/i)).not.toBeInTheDocument()
    expect(screen.getByText(/não é internamente coerente/i)).toBeInTheDocument()
  })

  it('status=complete mas envelope INCOERENTE (esta execução não tem veredito real): nunca declara o status como confiável', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [
        {
          claim_id: 'c1',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: [],
          source_state: 'supports',
          channel_relationship: 'directionally_aligned',
        },
      ],
    }

    render(<ReconciliationView reconciliation={reconciliation} envelopeCoherent={false} />)

    expect(screen.getByText(/não é internamente coerente/i)).toBeInTheDocument()
    expect(screen.queryByText(/o juiz não ficou disponível/i)).not.toBeInTheDocument()
  })
})
