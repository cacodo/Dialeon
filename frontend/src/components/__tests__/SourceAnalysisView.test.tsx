// Claim-centered semantic inspection (UI Slice) -- SourceAnalysisView
// não renderiza mais claim_results POR claim (isso migrou pra
// ClaimInspectionList.test.tsx, incluindo toda a cobertura de
// segurança/XSS de excerpt hostil). Este arquivo cobre só o que
// permanece aqui: status de nível de execução (ausente/pulada/falha) e
// a apresentação de `unattributedRejectedSourceEntries`.
//
// Repair pós-revisão adversarial (nº3) -- este componente NUNCA mais
// re-deriva a lista de entradas não atribuídas filtrando
// `sourceAnalysis.claim_results` por conta própria -- ele só CONFIA no
// que `unattributedRejectedSourceEntries` (já validado por
// `buildClaimInspectionModel`) manda renderizar. A prova de que
// registros com id ambíguo NUNCA voltam a aparecer aqui mesmo estando
// em `claim_results` vive em InspectionPanel.test.tsx (integração
// completa com o adapter real); aqui testamos só que o componente
// confia no prop, nunca no dado bruto.

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SourceAnalysisView } from '../SourceAnalysisView'
import SourceAnalysisViewSource from '../SourceAnalysisView.tsx?raw'
import type { RejectedSourceEntryPublic, SourceAnalysisOutcome } from '../../api/types'

function makeOutcome(overrides: Partial<SourceAnalysisOutcome>): SourceAnalysisOutcome {
  return {
    skipped_reason: null,
    source_analyzer_provider: 'anthropic',
    cumulative_budget_exceeded: false,
    attempts: [],
    claim_results: [],
    ...overrides,
  }
}

describe('SourceAnalysisView — status de nível de execução', () => {
  it('G: nenhuma fonte fornecida degrada honestamente, sem fingir análise completa', () => {
    render(<SourceAnalysisView sourceAnalysis={null} unattributedRejectedSourceEntries={[]} />)

    expect(screen.getByText(/nenhuma fonte foi fornecida/i)).toBeInTheDocument()
    expect(screen.queryByText(/concluída/i)).not.toBeInTheDocument()
  })

  it('G2: análise pulada por orçamento nunca aparece como sucesso', () => {
    const outcome = makeOutcome({ skipped_reason: 'budget_exhausted_before_source_analysis' })

    render(<SourceAnalysisView sourceAnalysis={outcome} unattributedRejectedSourceEntries={[]} />)

    expect(screen.getByText(/não concluída/i)).toBeInTheDocument()
    expect(screen.getByText(/orçamento esgotado/i)).toBeInTheDocument()
  })

  it('análise concluída sem entradas não-atribuídas não renderiza nada (evita UI vazia enganosa)', () => {
    const outcome = makeOutcome({})

    const { container } = render(
      <SourceAnalysisView sourceAnalysis={outcome} unattributedRejectedSourceEntries={[]} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})

describe('SourceAnalysisView — renderiza exatamente o que unattributedRejectedSourceEntries manda, nunca re-deriva de claim_results', () => {
  it('F2: entrada não atribuída passada via prop é rotulada como não identificada', () => {
    const outcome = makeOutcome({})
    const entries: RejectedSourceEntryPublic[] = [
      {
        kind: 'rejected',
        id: 'rej-1',
        claim_id: null,
        reason: 'omitted_by_model',
        raw_entry: null,
        created_at: '2026-09-06T00:00:00Z',
      },
    ]

    render(<SourceAnalysisView sourceAnalysis={outcome} unattributedRejectedSourceEntries={entries} />)

    expect(screen.getByText(/afirmação não identificada/i)).toBeInTheDocument()
    expect(screen.getByText(/a análise não endereçou esta afirmação/i)).toBeInTheDocument()
  })

  it('claim_results bruto tendo uma entrada rejeitada NUNCA a faz aparecer aqui se o prop validado não a incluir (nunca re-deriva por conta própria)', () => {
    const outcome = makeOutcome({
      claim_results: [
        {
          kind: 'rejected',
          id: 'rej-1',
          claim_id: null,
          reason: 'omitted_by_model',
          raw_entry: null,
          created_at: '2026-09-06T00:00:00Z',
        },
      ],
    })

    // O adapter decidiu (por qualquer motivo de integridade) que
    // NENHUMA entrada não atribuída deve ser apresentada -- o
    // componente precisa respeitar isso, mesmo com uma entrada
    // aparentemente elegível presente em claim_results.
    const { container } = render(
      <SourceAnalysisView sourceAnalysis={outcome} unattributedRejectedSourceEntries={[]} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('I: SourceAnalysisView não usa nenhuma API de renderização insegura de HTML', () => {
    // Prova estrutural (fonte real do componente, importada via ?raw),
    // não só comportamental: se alguém reintroduzir
    // dangerouslySetInnerHTML no futuro, este teste quebra mesmo que
    // nenhum caso de excerpt específico o capture.
    expect(SourceAnalysisViewSource).not.toContain('dangerouslySetInnerHTML')
    expect(SourceAnalysisViewSource).not.toContain('innerHTML')
  })
})
