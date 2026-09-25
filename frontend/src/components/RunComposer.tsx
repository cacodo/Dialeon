// Composer da pergunta -- UMA superfície coerente (moldura única):
//
//   ┌──────────────────────────────────────────────┐
//   │ pergunta…                                    │
//   ├──────────────────────────────────────────────┤
//   │ Fonte   Modelos: GPT, Claude, Gemini  [Perguntar] │
//   └──────────────────────────────────────────────┘
//
// Só capacidades que EXISTEM hoje: fonte opcional (à esquerda), seleção de
// modelos (resumo por nome) e o envio. A barra é o ponto natural de
// extensão futura, mas nada além disso é renderizado aqui -- sem
// placeholders, sem controles "em breve".
//
// Enter continua inserindo nova linha (a pergunta pode ser longa e
// multi-linha); Ctrl/⌘+Enter envia. O atalho é anunciado por
// `aria-keyshortcuts` e por uma dica discreta ligada ao campo por
// `aria-describedby` -- nunca só visual.

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { formatProviderName } from '../api/formatting'
import { ModelSelectionPanel, ModelSummaryButton, type ModelOption } from './ProviderSelector'
import {
  MAX_QUESTION_CHARACTERS,
  MAX_SOURCE_TEXT_CHARACTERS,
  characterCount,
  isBlankLikeBackend,
  formatCharacterLimit,
} from '../lib/inputLimits'
import type { ReuseInput } from '../lib/reuseInput'

interface RunComposerProps {
  providers: string[]
  providersLoading: boolean
  providersError: string | null
  submitting: boolean
  initialInput?: ReuseInput | null
  onSubmit: (question: string, enabledProviders: string[], sourceText: string | null) => void
  onRetryProviders?: () => void
}

const SOURCE_PANEL_ID = 'run-composer-source-panel'
const MODELS_PANEL_ID = 'provider-selector-panel'

export function RunComposer({
  providers,
  providersLoading,
  providersError,
  submitting,
  initialInput = null,
  onSubmit,
  onRetryProviders,
}: RunComposerProps) {
  const [question, setQuestion] = useState(initialInput?.question ?? '')
  const [selected, setSelected] = useState<string[]>([])
  // A pré-seleção (todos, ou os do reuso que ainda existem) acontece UMA vez,
  // na primeira lista de modelos recebida -- nunca reverte uma escolha do
  // usuário depois.
  const hasInitializedSelection = useRef(false)
  const [sourceExpanded, setSourceExpanded] = useState(initialInput?.sourceText != null)
  const [sourceText, setSourceText] = useState(initialInput?.sourceText ?? '')
  const [modelsExpanded, setModelsExpanded] = useState(false)

  useEffect(() => {
    if (providers.length > 0 && !hasInitializedSelection.current) {
      const reused = (initialInput?.enabledProviders ?? []).filter((p) => providers.includes(p))
      setSelected(reused.length > 0 ? reused : providers)
      hasInitializedSelection.current = true
    } else if (hasInitializedSelection.current) {
      // Nova descoberta (ex.: "Recarregar modelos"): mantém só as escolhas
      // que ainda existem na lista devolvida. As que sumiram saem da seleção
      // de vez -- nunca voltam sozinhas se reaparecerem depois.
      setSelected((current) => {
        const kept = current.filter((id) => providers.includes(id))
        return kept.length === current.length ? current : kept
      })
    }
  }, [providers, initialInput])

  const modelOptions: ModelOption[] = providers.map((id) => ({ id, label: formatProviderName(id) }))
  // O que é mostrado, o que habilita o envio e o que é enviado são SEMPRE o
  // mesmo conjunto: a seleção restrita às opções que esta tela expõe agora
  // (inclusive no render entre uma nova lista e a reconciliação acima). Sem
  // lista exposta (carregando ou com erro), não há escolha válida.
  const exposedProviders = providersLoading || providersError ? [] : providers
  const validSelected = selected.filter((id) => exposedProviders.includes(id))

  // Limites estáticos (espelham o backend -- ver lib/inputLimits.ts). O
  // backend segue a autoridade; isto só evita um round-trip inútil. A fonte
  // é contada VERBATIM, exatamente como é enviada; só uma fonte vazia (pelo
  // mesmo critério do backend) é tratada como ausente e não conta pro limite.
  const questionCount = characterCount(question)
  const sourceCount = characterCount(sourceText)
  const sourceBlank = isBlankLikeBackend(sourceText)
  const questionTooLong = questionCount > MAX_QUESTION_CHARACTERS
  const sourceTooLong = !sourceBlank && sourceCount > MAX_SOURCE_TEXT_CHARACTERS

  const canSubmit =
    question.trim().length > 0 &&
    validSelected.length > 0 &&
    !questionTooLong &&
    !sourceTooLong &&
    !submitting &&
    !providersLoading

  function submit() {
    if (!canSubmit) return
    // Accepted Question Size Boundary V1 (repair F1) -- `question` é
    // encaminhada VERBATIM (nunca `.trim()`ada aqui): o backend é a única
    // autoridade sobre o que conta como pergunta válida. `.trim()` acima em
    // `canSubmit` é só detecção de "em branco" pra UX. Fonte: whitespace só
    // decide se ela está vazia; conteúdo não vazio segue VERBATIM, igual à
    // API e à CLI.
    onSubmit(question, validSelected, sourceBlank ? null : sourceText)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  function handleQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit} aria-busy={submitting}>
      <h1 className="composer__heading">O que você quer saber?</h1>
      <p className="composer__lede">
        Os modelos escolhidos respondem de forma independente; o Dialeon organiza a resposta e
        mostra onde eles concordam, onde divergem e o que continua incerto.
      </p>

      <div className="composer__frame">
        <label htmlFor="question-input" className="sr-only">
          Faça uma pergunta
        </label>
        <textarea
          id="question-input"
          className="composer__input"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleQuestionKeyDown}
          placeholder="Escreva sua pergunta…"
          rows={4}
          disabled={submitting}
          aria-invalid={questionTooLong}
          aria-describedby="question-limit question-shortcut"
          aria-keyshortcuts="Control+Enter Meta+Enter"
        />

        <div className="composer__bar">
          <button
            type="button"
            className="composer__control composer__source-toggle"
            onClick={() => setSourceExpanded((expanded) => !expanded)}
            disabled={submitting}
            aria-expanded={sourceExpanded}
            aria-controls={SOURCE_PANEL_ID}
          >
            Fonte (opcional)
            <span className="composer__control-chevron" aria-hidden="true">
              ▾
            </span>
          </button>

          {providersLoading && (
            <p aria-live="polite" className="composer__status">
              Carregando modelos…
            </p>
          )}
          {!providersLoading && !providersError && (
            <ModelSummaryButton
              options={modelOptions}
              selected={validSelected}
              expanded={modelsExpanded}
              onToggle={() => setModelsExpanded((expanded) => !expanded)}
              disabled={submitting}
              panelId={MODELS_PANEL_ID}
            />
          )}

          <button type="submit" disabled={!canSubmit} className="composer__submit">
            {submitting ? 'Perguntando…' : 'Perguntar'}
          </button>
        </div>

        {sourceExpanded && (
          <div id={SOURCE_PANEL_ID} className="composer__panel composer__source">
            <label htmlFor="source-input" className="composer__label">
              Fonte de texto (opcional)
            </label>
            <textarea
              id="source-input"
              className="composer__source-input"
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder="Cole um trecho de texto para comparar com as afirmações do debate…"
              rows={4}
              disabled={submitting}
              aria-invalid={sourceTooLong}
              aria-describedby="source-limit source-hint"
            />
            <p
              id="source-limit"
              className={`composer__limit${sourceTooLong ? ' composer__limit--exceeded' : ''}`}
            >
              {formatCharacterLimit(sourceCount)} / {formatCharacterLimit(MAX_SOURCE_TEXT_CHARACTERS)}{' '}
              caracteres
            </p>
            <p id="source-hint" className="composer__hint">
              O Dialeon compara este texto com as afirmações identificadas durante o debate entre os
              modelos e indica se ele apoia, contradiz ou não permite decidir cada uma. A comparação é
              mostrada à parte: não altera a avaliação das afirmações, e a fonte não é verificada como
              verdadeira.
            </p>
          </div>
        )}

        {modelsExpanded && !providersLoading && !providersError && (
          <ModelSelectionPanel
            options={modelOptions}
            selected={validSelected}
            onChange={setSelected}
            disabled={submitting}
            panelId={MODELS_PANEL_ID}
          />
        )}
      </div>

      <div className="composer__meta">
        <p id="question-shortcut" className="composer__hint">
          Ctrl/⌘ + Enter para perguntar
        </p>
        <p
          id="question-limit"
          className={`composer__limit${questionTooLong ? ' composer__limit--exceeded' : ''}`}
        >
          {formatCharacterLimit(questionCount)} / {formatCharacterLimit(MAX_QUESTION_CHARACTERS)}{' '}
          caracteres
        </p>
      </div>

      {questionTooLong && (
        <p role="alert" className="notice notice--validation">
          A pergunta passa do limite de {formatCharacterLimit(MAX_QUESTION_CHARACTERS)} caracteres (
          {formatCharacterLimit(questionCount)}). Reduza o texto para perguntar.
        </p>
      )}

      {sourceTooLong && (
        // Fora do painel recolhível: o erro nunca fica escondido se a fonte
        // estiver recolhida.
        <p role="alert" className="notice notice--validation">
          A fonte passa do limite de {formatCharacterLimit(MAX_SOURCE_TEXT_CHARACTERS)} caracteres (
          {formatCharacterLimit(sourceCount)}). Abra “Fonte (opcional)” e reduza o texto.
        </p>
      )}

      {providersError && (
        <div role="alert" className="notice notice--error composer__providers-error">
          <p>Não foi possível carregar os modelos disponíveis: {providersError}</p>
          {onRetryProviders && (
            <button type="button" onClick={onRetryProviders}>
              Tentar novamente
            </button>
          )}
        </div>
      )}
    </form>
  )
}
