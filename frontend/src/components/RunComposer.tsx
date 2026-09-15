// Composer da pergunta -- protagonista da home (Decision Delta secao 7).
// Pelo menos um provider precisa continuar selecionado pra submit válido
// (prevenção de UX; o backend continua autoridade de validação real).
//
// Seleção inicial: quando GET /providers completa com sucesso pela
// primeira vez, TODOS os providers retornados vêm pré-selecionados --
// sem hardcode, usando exatamente os IDs reais do backend -- pra que a
// experiência Standard funcione sem exigir interação manual antes da
// primeira Run. Só acontece UMA vez (via `hasInitializedSelection`):
// rerenders posteriores nunca sobrescrevem uma escolha manual do
// usuário.

import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ProviderSelector } from './ProviderSelector'

interface RunComposerProps {
  providers: string[]
  providersLoading: boolean
  providersError: string | null
  submitting: boolean
  onSubmit: (question: string, enabledProviders: string[], sourceText: string | null) => void
}

export function RunComposer({
  providers,
  providersLoading,
  providersError,
  submitting,
  onSubmit,
}: RunComposerProps) {
  const [question, setQuestion] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const hasInitializedSelection = useRef(false)
  // Etapa 16 -- divulgação progressiva: o campo de fonte só aparece
  // depois de um clique explícito, pra não sugerir que toda pergunta
  // precisa de uma fonte (a resposta continua answer-first mesmo sem
  // nenhuma fonte fornecida).
  const [sourceExpanded, setSourceExpanded] = useState(false)
  const [sourceText, setSourceText] = useState('')

  useEffect(() => {
    if (providers.length > 0 && !hasInitializedSelection.current) {
      setSelected(providers)
      hasInitializedSelection.current = true
    }
  }, [providers])

  const canSubmit =
    question.trim().length > 0 && selected.length > 0 && !submitting && !providersLoading

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    const trimmedSource = sourceText.trim()
    onSubmit(question.trim(), selected, trimmedSource.length > 0 ? trimmedSource : null)
  }

  return (
    <form className="run-composer" onSubmit={handleSubmit}>
      <label htmlFor="question-input" className="run-composer__label">
        Faça uma pergunta
      </label>
      <textarea
        id="question-input"
        className="run-composer__input"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="O que você quer investigar?"
        rows={3}
        disabled={submitting}
      />

      {!sourceExpanded && (
        <button
          type="button"
          className="run-composer__source-toggle"
          onClick={() => setSourceExpanded(true)}
          disabled={submitting}
        >
          + Adicionar fonte de texto (opcional)
        </button>
      )}
      {sourceExpanded && (
        <div className="run-composer__source">
          <label htmlFor="source-input" className="run-composer__label">
            Fonte de texto (opcional)
          </label>
          <textarea
            id="source-input"
            className="run-composer__input"
            value={sourceText}
            onChange={(e) => setSourceText(e.target.value)}
            placeholder="Cole um trecho de texto para comparar com as claims do debate…"
            rows={4}
            disabled={submitting}
          />
          <p className="run-composer__source-hint">
            A fonte é comparada com as afirmações do debate como um canal independente do
            julgamento -- não altera a avaliação do juiz, mas o relacionamento entre os dois pode
            aparecer na resposta final.
          </p>
        </div>
      )}

      {providersError && (
        <p role="alert" className="run-composer__error">
          Não foi possível carregar os participantes disponíveis: {providersError}
        </p>
      )}
      {providersLoading && <p aria-live="polite">Carregando participantes disponíveis…</p>}
      {!providersLoading && !providersError && (
        <ProviderSelector
          providers={providers}
          selected={selected}
          onChange={setSelected}
          disabled={submitting}
        />
      )}

      <button type="submit" disabled={!canSubmit} className="run-composer__submit">
        {submitting ? 'Executando…' : 'Perguntar'}
      </button>
    </form>
  )
}
