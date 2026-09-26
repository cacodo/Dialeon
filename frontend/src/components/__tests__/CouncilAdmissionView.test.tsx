import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CouncilAdmissionView } from '../CouncilAdmissionView'
import type { CouncilAdmission } from '../../api/types'

const admission: CouncilAdmission = {
  contract_version: 'council_admission_v2',
  mode: 'standard',
  known_degradation_acknowledged: true,
  acknowledged_degradation_fingerprint: `sha256:${'a'.repeat(64)}`,
  readiness: {
    contract_version: 'council_local_readiness_v1',
    summary: 'some_missing',
    strict_admission: 'blocked',
    known_degradation_fingerprint: `sha256:${'a'.repeat(64)}`,
    dependencies: [
      { role: 'participant', provider: 'gemini', configured_default_model: 'gemini-configured', local_prerequisite: 'unknown', applicability: 'selected' },
      { role: 'claim_extraction', provider: 'anthropic', configured_default_model: 'claude-configured', local_prerequisite: 'missing', applicability: 'potential' },
      { role: 'source_analysis', provider: 'anthropic', configured_default_model: 'claude-configured', local_prerequisite: 'missing', applicability: 'not_applicable' },
      { role: 'judge', provider: 'anthropic', configured_default_model: 'claude-configured', local_prerequisite: 'missing', applicability: 'potential' },
    ],
  },
}

describe('CouncilAdmissionView', () => {
  it('run anterior a este registro: "não registrada", nunca presente nem ausente', () => {
    render(<CouncilAdmissionView admission={null} />)
    expect(screen.getByText(/Prontidão local no aceite: não registrada/)).toBeInTheDocument()
  })

  it('mostra o resumo, o modo, o reconhecimento e as etapas afetadas do caminho pedido', () => {
    render(<CouncilAdmissionView admission={admission} />)

    expect(screen.getByText(/faltava configuração local em etapas do caminho pedido/)).toHaveTextContent(
      'Admissão padrão, enviada sabendo exatamente desta configuração local ausente.',
    )
    expect(screen.getByText(/^Extração de afirmações e juiz: Claude/)).toHaveTextContent(
      'modelo configurado: claude-configured',
    )
    // análise da fonte fora do caminho (sem fonte): não listada
    expect(screen.queryByText(/análise da fonte/)).not.toBeInTheDocument()
    expect(screen.getByText(/não quer dizer que faltasse/)).toBeInTheDocument()
    expect(screen.getByText(/^Participante: Gemini/)).toBeInTheDocument()
  })

  it('registro v1 (desenvolvimento): o reconhecimento aparece sem afirmar qual situação foi vista', () => {
    render(
      <CouncilAdmissionView
        admission={{ ...admission, contract_version: 'council_admission_v1', acknowledged_degradation_fingerprint: null }}
      />,
    )

    expect(screen.getByText(/qual situação foi reconhecida não foi registrado/)).toBeInTheDocument()
    expect(screen.queryByText(/exatamente desta/)).not.toBeInTheDocument()
  })
})
