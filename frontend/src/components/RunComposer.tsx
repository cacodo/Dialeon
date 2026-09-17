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
    // Accepted Question Size Boundary V1 (repair F1) -- `question` é
    // encaminhada VERBATIM (nunca `.trim()`ada aqui): o backend é a
    // única autoridade sobre o que conta como pergunta válida
    // (vazio/só-espaço-em-branco, teto de MAX_QUESTION_CHARACTERS -- ver
    // app/orchestrator/config.py::validate_question). `.trim()` acima em
    // `canSubmit` é só detecção de "em branco" pra UX (desabilitar o
    // botão), nunca uma transformação do valor realmente enviado -- um
    // valor originalmente inválido (ex.: >20.000 caracteres) nunca pode
    // se tornar válido por acaso de ser encurtado aqui antes de chegar
    // na validação canônica.
    const trimmedSource = sourceText.trim()
    onSubmit(question, selected, trimmedSource.length > 0 ? trimmedSource : null)
  }

  return (
    <form className="run-composer" onSubmit={handleSubmit}>
      <div className="run-composer__question">
        {/* Único heading primário da tela agora (Polimento visual UI
            Slice 2) -- "Nova pergunta"/"Faça uma pergunta" foram
            colapsados nesta única frase, pra remover a hierarquia
            redundante que existia antes. O label continua existindo
            (acessibilidade nunca é sacrificada por simplicidade visual),
            só visualmente oculto via `.sr-only`. */}
        <h1 className="run-composer__heading">O que você quer investigar?</h1>
        <label htmlFor="question-input" className="run-composer__label sr-only">
          Faça uma pergunta
        </label>
        <textarea
          id="question-input"
          className="run-composer__input run-composer__input--question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Escreva sua pergunta…"
          rows={4}
          disabled={submitting}
        />
      </div>

      {/* Barra de configuração secundária -- fonte opcional e seleção de
          participantes nunca competem visualmente com a pergunta em si
          (Decision Delta secao 6/7: provider selection nunca é a
          identidade principal da tela). */}
      <div className="run-composer__toolbar">
        <button
          type="button"
          className="run-composer__source-toggle"
          onClick={() => setSourceExpanded((expanded) => !expanded)}
          disabled={submitting}
          aria-expanded={sourceExpanded}
          aria-controls="run-composer-source-panel"
        >
          {sourceExpanded ? 'Ocultar fonte de texto' : '+ Adicionar fonte de texto (opcional)'}
        </button>

        {providersError && (
          <p role="alert" className="run-composer__error">
            Não foi possível carregar os participantes disponíveis: {providersError}
          </p>
        )}
        {providersLoading && (
          <p aria-live="polite" className="run-composer__providers-status">
            Carregando participantes disponíveis…
          </p>
        )}
        {!providersLoading && !providersError && (
          <ProviderSelector
            providers={providers}
            selected={selected}
            onChange={setSelected}
            disabled={submitting}
          />
        )}
      </div>

      {sourceExpanded && (
        // Colapsar só oculta este painel -- `sourceText` continua vivo no
        // estado do componente pai, nunca é limpo aqui, então reabrir
        // depois de fechar mostra exatamente o que já tinha sido digitado.
        <div id="run-composer-source-panel" className="run-composer__source">
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

      <button type="submit" disabled={!canSubmit} className="run-composer__submit">
        {submitting ? 'Investigando…' : 'Investigar'}
      </button>
    </form>
  )
}
