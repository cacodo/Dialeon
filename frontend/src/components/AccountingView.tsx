// Accounting -- resumo secundário na experiência principal, detalhado
// na inspeção. UNKNOWN != ZERO em todo lugar que isso aparece.

import type { AccountingSummary, RoundAccountingPublic } from '../api/types'
import { formatEstimatedCost, formatTokenCount } from '../api/formatting'

interface AccountingViewProps {
  accounting: AccountingSummary | RoundAccountingPublic
  compact?: boolean
}

export function AccountingView({ accounting, compact }: AccountingViewProps) {
  return (
    <dl className={compact ? 'accounting accounting--compact' : 'accounting'}>
      <div className="accounting__row">
        <dt>Custo estimado</dt>
        <dd>
          {formatEstimatedCost(
            accounting.estimated_cost_usd,
            accounting.has_unknown_accounting_components,
          )}
        </dd>
      </div>
      {!compact && (
        <>
          <div className="accounting__row">
            <dt>Tokens de entrada</dt>
            <dd>{formatTokenCount(accounting.total_input_tokens)}</dd>
          </div>
          <div className="accounting__row">
            <dt>Tokens de saída</dt>
            <dd>{formatTokenCount(accounting.total_output_tokens)}</dd>
          </div>
        </>
      )}
    </dl>
  )
}
