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

  // Judge Transport Execution Policy V1 -- quando o snapshot registra um
  // override do Judge, os dois valores de topo valem só pras DEMAIS
  // operações; os rótulos dizem isso explicitamente. Sem override
  // registrado (histórico), os rótulos originais permanecem.
  const judge = policy.judge_override ?? null
  const defaultSuffix = judge === null ? '' : ' (padrão, exceto Juiz)'

  return (
    <dl className="provider-execution-policy">
      <div className="provider-execution-policy__row">
        <dt>Timeout por tentativa{defaultSuffix}</dt>
        <dd>{policy.attempt_timeout_seconds}s</dd>
      </div>
      <div className="provider-execution-policy__row">
        <dt>Tentativas de transporte (máx.){defaultSuffix}</dt>
        <dd>{policy.max_transport_attempts_per_completion}</dd>
      </div>
      {judge !== null && (
        <>
          <div className="provider-execution-policy__row">
            <dt>Timeout por tentativa (Juiz)</dt>
            <dd>{judge.attempt_timeout_seconds}s</dd>
          </div>
          <div className="provider-execution-policy__row">
            <dt>Tentativas de transporte (máx., Juiz)</dt>
            <dd>{judge.max_transport_attempts_per_completion}</dd>
          </div>
        </>
      )}
    </dl>
  )
}
