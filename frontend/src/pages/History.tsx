// /app/runs -- histórico (Decision Delta secao 12). Job do produto:
// "encontrar, distinguir e reabrir uma investigação anterior" -- nunca
// analytics/comparação de provider/monitoramento/administração de runs.
// A pergunta literal original é a identidade PRIMÁRIA, reconhecível por
// humanos, de cada investigação (History Investigation-Identity V1) --
// nunca sintetizamos título/resumo, nunca promovemos o UUID a
// identidade visível.
//
// Paginação por PÁGINA (1-indexed) endereçável via URL (?page=N),
// nunca só estado interno -- normalizada honestamente pra 1 quando o
// valor é ausente/inválido/negativo/não-inteiro (nunca um erro visível
// pro usuário por causa de um parâmetro de URL malformado).
//
// PAGE_SIZE+1: pedimos um registro A MAIS do que exibimos, só pra saber
// se existe próxima página -- nunca expomos "Próxima" só porque a
// página atual veio com exatamente PAGE_SIZE registros (isso não prova
// nada sobre o que vem depois).
//
// Loading nunca é um `setState` síncrono dentro do efeito (isso é
// exatamente o padrão que aciona o lint `set-state-in-effect`) -- é
// DERIVADO em render, comparando `result.attemptKey` (a TENTATIVA que o
// ÚLTIMO fetch resolvido de fato atende) com `attemptKey` (a tentativa
// que o render atual pede). Só o desfecho (sucesso/erro) do fetch chama
// `setState`, sempre dentro de `.then()/.catch()`, nunca no topo do
// corpo do efeito.
//
// Estado de TENTATIVA DE REQUISIÇÃO, não só de página -- `attemptKey`
// combina `page` + `retryToken`. Isso importa pro retry: um clique em
// "Tentar novamente" NÃO muda `page`, só `retryToken` -- se o resultado
// "atual" fosse decidido só por página (like antes), o erro antigo
// continuaria "atual" (mesma página) até a nova busca resolver, sendo
// reexibido junto com/antes do novo loading. Chaveando por
// `page+retryToken`, o resultado antigo deixa de casar com a
// `attemptKey` do render IMEDIATAMENTE no re-render disparado pelo
// clique (antes mesmo do efeito rodar) -- loading aparece, erro
// desaparece, e o botão (que só existe dentro do bloco de erro) some
// junto, então não há como clicar "Tentar novamente" duas vezes pra
// gerar duas requisições concorrentes.
//
// `cancelled` (closure por instância de efeito) continua sendo quem
// garante correção contra ESCRITA tardia de uma tentativa já superada
// (ex.: página 1 → 2 → 1 com promises controladas resolvendo fora de
// ordem) -- `attemptKey` sozinho não previne isso (a chave pode se
// repetir ao voltar pra uma página já visitada com o mesmo
// `retryToken`), mas cada instância de efeito só escreve estado
// enquanto SUA PRÓPRIA `cancelled` continuar falsa, e o cleanup do
// React marca `cancelled=true` assim que `page`/`retryToken` mudam de
// novo -- então uma tentativa superada nunca vence uma mais nova,
// não importa a ordem de resolução.

import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunSummaryResponse } from '../api/types'
import { formatDateTime, formatErrorCode } from '../api/formatting'
import { isValidPage } from '../lib/safePage'

const PAGE_SIZE = 20

// Rótulos de LIFECYCLE apenas -- nunca implicam qualidade da resposta,
// verdade, consenso ou aprovação do juiz. "running" nunca diz "Em
// andamento" (isso implicaria progresso observável que não existe --
// ver AcceptedRunRow: em andamento e processo morto são
// indistinguíveis por design).
const STATUS_LABELS: Record<RunSummaryResponse['status'], string> = {
  completed: 'Concluída',
  insufficient_quorum: 'Quórum insuficiente',
  running: 'Sem desfecho registrado',
  failed: 'Falhou durante a execução',
}

// Payload malformado/histórico (nunca esperado pelo contrato TS, que já
// exige `question`) -- fallback neutro, nunca o UUID como título
// sintetizado.
function questionDisplay(run: RunSummaryResponse): string {
  return typeof run.question === 'string' && run.question.trim().length > 0
    ? run.question
    : 'Pergunta indisponível'
}

function normalizePage(raw: string | null): number {
  if (raw === null) return 1
  const parsed = Number(raw)
  return isValidPage(parsed) ? parsed : 1
}

type LoadResult =
  | { kind: 'success'; attemptKey: string; runs: RunSummaryResponse[]; hasNext: boolean }
  | { kind: 'error'; attemptKey: string; message: string }

export function History() {
  const [searchParams] = useSearchParams()
  const page = normalizePage(searchParams.get('page'))

  const [result, setResult] = useState<LoadResult | null>(null)
  // Incrementado só pelo botão "Tentar novamente" -- força o efeito a
  // rodar de novo pra ESTA MESMA página (nunca reseta pra página 1).
  const [retryToken, setRetryToken] = useState(0)
  const attemptKey = `${page}:${retryToken}`

  useEffect(() => {
    let cancelled = false
    const key = `${page}:${retryToken}`
    const offset = (page - 1) * PAGE_SIZE
    apiClient
      .listRuns(PAGE_SIZE + 1, offset)
      .then((response) => {
        if (cancelled) return
        setResult({
          kind: 'success',
          attemptKey: key,
          runs: response.runs.slice(0, PAGE_SIZE),
          hasNext: response.runs.length > PAGE_SIZE,
        })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setResult({
          kind: 'error',
          attemptKey: key,
          message: error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.',
        })
      })
    return () => {
      cancelled = true
    }
  }, [page, retryToken])

  // Resultado só é "atual" quando pertence à TENTATIVA (página +
  // retry) que o render pede agora -- nem uma página antiga, nem um
  // retry já superado da mesma página, nunca é mostrado como se fosse
  // a tentativa em curso; enquanto isso, a UI honestamente mostra
  // "carregando", nunca dados/erro obsoletos.
  const current = result !== null && result.attemptKey === attemptKey ? result : null

  return (
    <main className="history">
      <h1>Histórico</h1>

      {current === null && <p role="status">Carregando histórico…</p>}

      {current?.kind === 'error' && (
        <div role="alert" className="history__error">
          <p>{current.message}</p>
          <button type="button" onClick={() => setRetryToken((t) => t + 1)}>
            Tentar novamente
          </button>
        </div>
      )}

      {current?.kind === 'success' && (
        <>
          {current.runs.length === 0 ? (
            page === 1 ? (
              <p>Nenhuma execução ainda.</p>
            ) : (
              <div className="history__out-of-range">
                <p>Esta página não tem execuções — pode estar além do fim do histórico.</p>
                <Link to={`/runs?page=${page - 1}`}>Voltar para a página anterior</Link>
              </div>
            )
          ) : (
            <ul className="history__list">
              {current.runs.map((run) => (
                <li key={run.id} className="history__item">
                  <Link
                    className="history__link"
                    to={`/runs/${run.id}`}
                    // Origem de navegação -- ver RunDetail.tsx: o link
                    // "← Histórico" de lá preserva esta página EXATA
                    // quando a navegação começou aqui. Nunca afeta
                    // acesso direto a /runs/:id (sem este state).
                    state={{ fromHistoryPage: page }}
                  >
                    <span className="history__question">{questionDisplay(run)}</span>
                    <span className="history__meta">
                      <time dateTime={run.started_at}>{formatDateTime(run.started_at)}</time>
                      <span className="history__status">{STATUS_LABELS[run.status]}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <nav className="history__pagination" aria-label="Paginação do histórico">
            {page > 1 ? (
              <Link to={`/runs?page=${page - 1}`}>Anterior</Link>
            ) : (
              <span className="history__pagination-disabled">Anterior</span>
            )}
            <span className="history__pagination-current">Página {page}</span>
            {current.hasNext ? (
              <Link to={`/runs?page=${page + 1}`}>Próxima</Link>
            ) : (
              <span className="history__pagination-disabled">Próxima</span>
            )}
          </nav>
        </>
      )}
    </main>
  )
}
