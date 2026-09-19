import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProviderExecutionPolicyView } from '../ProviderExecutionPolicyView'

describe('ProviderExecutionPolicyView', () => {
  it('T02.2: mostra os dois valores quando a política é conhecida', () => {
    render(
      <ProviderExecutionPolicyView
        policy={{ attempt_timeout_seconds: 45, max_transport_attempts_per_completion: 3 }}
      />,
    )

    expect(screen.getByText('45s')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('Judge override: mostra política do Juiz distinta da padrão e rotula a padrão como "exceto Juiz"', () => {
    render(
      <ProviderExecutionPolicyView
        policy={{
          attempt_timeout_seconds: 60,
          max_transport_attempts_per_completion: 3,
          judge_override: { attempt_timeout_seconds: 120, max_transport_attempts_per_completion: 1 },
        }}
      />,
    )

    expect(screen.getByText('Timeout por tentativa (padrão, exceto Juiz)')).toBeInTheDocument()
    expect(screen.getByText('Tentativas de transporte (máx.) (padrão, exceto Juiz)')).toBeInTheDocument()
    expect(screen.getByText('Timeout por tentativa (Juiz)')).toBeInTheDocument()
    expect(screen.getByText('Tentativas de transporte (máx., Juiz)')).toBeInTheDocument()
    expect(screen.getByText('60s')).toBeInTheDocument()
    expect(screen.getByText('120s')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('Judge override null/ausente (snapshot histórico): rótulos originais, nenhuma linha do Juiz inventada', () => {
    const { rerender } = render(
      <ProviderExecutionPolicyView
        policy={{
          attempt_timeout_seconds: 60,
          max_transport_attempts_per_completion: 3,
          judge_override: null,
        }}
      />,
    )
    expect(screen.getByText('Timeout por tentativa')).toBeInTheDocument()
    expect(screen.queryByText(/Juiz/)).not.toBeInTheDocument()

    rerender(
      <ProviderExecutionPolicyView
        policy={{ attempt_timeout_seconds: 60, max_transport_attempts_per_completion: 3 }}
      />,
    )
    expect(screen.getByText('Timeout por tentativa')).toBeInTheDocument()
    expect(screen.queryByText(/Juiz/)).not.toBeInTheDocument()
  })

  it('T02.2: renderiza honestamente "não registrada" quando a política é null (histórico)', () => {
    render(<ProviderExecutionPolicyView policy={null} />)

    expect(screen.getByText(/não registrada/i)).toBeInTheDocument()
    // NUNCA inventa os defaults atuais (60s/3 tentativas)
    expect(screen.queryByText('60s')).not.toBeInTheDocument()
  })
})
