// T02.2 -- exibe o snapshot de política de transporte de provider
// (timeout + retry) vigente no momento em que o Run foi aceito. SIBLING
// de RunConfigPublic/AccountingView, nunca dentro deles -- autoridade de
// deployment distinta da configuração de execução/domínio do Run.
// `null` (execução histórica anterior a esta feature) renderiza
// honestamente como "não registrado", nunca inventa os defaults atuais.

import type { ProviderExecutionPolicy } from '../api/types'

interface ProviderExecutionPolicyViewProps {
  policy: ProviderExecutionPolicy | null
}

export function ProviderExecutionPolicyView({ policy }: ProviderExecutionPolicyViewProps) {
  if (policy === null) {
    return (
      <p className="provider-execution-policy provider-execution-policy--unknown">
        Política de execução do provider: não registrada (execução anterior a este registro).
      </p>
    )
  }

  return (
    <dl className="provider-execution-policy">
      <div className="provider-execution-policy__row">
        <dt>Timeout por tentativa</dt>
        <dd>{policy.attempt_timeout_seconds}s</dd>
      </div>
      <div className="provider-execution-policy__row">
        <dt>Tentativas de transporte (máx.)</dt>
        <dd>{policy.max_transport_attempts_per_completion}</dd>
      </div>
    </dl>
  )
}
