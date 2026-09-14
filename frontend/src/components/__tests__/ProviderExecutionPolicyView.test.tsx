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

  it('T02.2: renderiza honestamente "não registrada" quando a política é null (histórico)', () => {
    render(<ProviderExecutionPolicyView policy={null} />)

    expect(screen.getByText(/não registrada/i)).toBeInTheDocument()
    // NUNCA inventa os defaults atuais (60s/3 tentativas)
    expect(screen.queryByText('60s')).not.toBeInTheDocument()
  })
})
