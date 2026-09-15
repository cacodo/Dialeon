"""Erros de domínio da reconciliação Source<->Judge -- FAIL-CLOSED:
levantados quando o dado de entrada (Claim/JudgeVerdict/SourceAnalysisResult)
viola uma premissa estrutural da qual a reconciliação depende pra produzir
um resultado honesto (ex.: JudgeVerdict referenciando uma claim que não é
mais corrente, ou análise de fonte "concluída" sem cobrir uma claim
corrente). Nunca corrigido silenciosamente escolhendo um valor arbitrário
-- ver `app/reconciliation/reconcile.py`."""

from __future__ import annotations


class ReconciliationError(ValueError):
    pass
