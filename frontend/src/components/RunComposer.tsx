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

import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { formatModelList, formatProviderName } from '../api/formatting'
import type { LocalPrerequisiteState } from '../api/types'
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
  // Estado dos pré-requisitos LOCAIS por provider (GET /providers). Sem
  // entrada (ou valor desconhecido) = "unknown": nunca tratado como "met".
  localPrerequisites?: Readonly<Record<string, LocalPrerequisiteState>>
  providersLoading: boolean
  providersError: string | null
  submitting: boolean
  initialInput?: ReuseInput | null
  onSubmit: (question: string, enabledProviders: string[], sourceText: string | null) => void
  onRetryProviders?: () => void
}

const SOURCE_PANEL_ID = 'run-composer-source-panel'
const MODELS_PANEL_ID = 'provider-selector-panel'

type PrerequisiteStates = Readonly<Record<string, LocalPrerequisiteState>> | undefined

function prerequisiteOf(states: PrerequisiteStates, id: string): LocalPrerequisiteState {
  const state = states !== undefined && Object.hasOwn(states, id) ? states[id] : undefined
  return state === 'met' || state === 'missing' ? state : 'unknown'
}

interface Selection {
  ids: string[]
  // A pré-seleção já aconteceu (ou o usuário já escolheu): daqui em diante
  // uma nova lista só pode REMOVER escolhas, nunca acrescentar.
  settled: boolean
}

// Seleção diante de uma lista de modelos recém-recebida. A pré-seleção
// acontece UMA vez, na primeira lista que tenha algo a pré-selecionar: os
// modelos do reuso que ainda existem e não estão sem configuração local ou,
// sem reuso, só os com configuração local presente ("met" -- nunca "unknown"
// nem "missing" automaticamente). Numa primeira execução sem nenhum "met",
// ela espera: depois de configurar, reiniciar a API e recarregar a lista, os
// "met" são pré-selecionados. Depois disso (ou de uma escolha do usuário),
// uma lista nova mantém só as escolhas que ainda existem e não estão sem
// configuração local; as que saíram deixam a seleção de vez.
function reconcileSelection(
  current: Selection,
  providers: readonly string[],
  states: PrerequisiteStates,
  reuse: readonly string[],
): Selection {
  const choosable = (id: string) => providers.includes(id) && prerequisiteOf(states, id) !== 'missing'
  if (current.settled) {
    const kept = current.ids.filter(choosable)
    return kept.length === current.ids.length ? current : { ids: kept, settled: true }
  }
  const reused = reuse.filter(choosable)
  const initial = reused.length > 0 ? reused : providers.filter((id) => prerequisiteOf(states, id) === 'met')
  return initial.length > 0 ? { ids: initial, settled: true } : current
}

export function RunComposer({
  providers,
  localPrerequisites,
  providersLoading,
  providersError,
  submitting,
  initialInput = null,
  onSubmit,
  onRetryProviders,
}: RunComposerProps) {
  const [question, setQuestion] = useState(initialInput?.question ?? '')
  const [selection, setSelection] = useState<Selection>({ ids: [], settled: false })
  // Lista de modelos com que a seleção foi reconciliada por último. Uma lista
  // nova (resposta de GET /providers) reconcilia durante o render -- sem
  // efeito, sem render intermediário com uma seleção desatualizada.
  const [reconciledWith, setReconciledWith] = useState<{
    providers: string[]
    states: PrerequisiteStates
  } | null>(null)
  if (
    reconciledWith === null ||
    reconciledWith.providers !== providers ||
    reconciledWith.states !== localPrerequisites
  ) {
    setReconciledWith({ providers, states: localPrerequisites })
    setSelection((current) =>
      reconcileSelection(current, providers, localPrerequisites, initialInput?.enabledProviders ?? []),
    )
  }
  const selected = selection.ids
  const [sourceExpanded, setSourceExpanded] = useState(initialInput?.sourceText != null)
  const [sourceText, setSourceText] = useState(initialInput?.sourceText ?? '')
  const [modelsExpanded, setModelsExpanded] = useState(false)

  function chooseModels(next: string[]) {
    setSelection({ ids: next, settled: true })
  }

  const modelOptions: ModelOption[] = providers.map((id) => ({
    id,
    label: formatProviderName(id),
    prerequisite: prerequisiteOf(localPrerequisites, id),
  }))
  // O que é mostrado, o que habilita o envio e o que é enviado são SEMPRE o
  // mesmo conjunto: a seleção restrita às opções que esta tela expõe agora
  // (inclusive no render entre uma nova lista e a reconciliação acima), sem
  // nenhuma sem configuração local. Sem lista exposta (carregando ou com
  // erro), não há escolha válida.
  const exposedProviders = providersLoading || providersError ? [] : providers
  const validSelected = selected.filter(
    (id) => exposedProviders.includes(id) && prerequisiteOf(localPrerequisites, id) !== 'missing',
  )
  // Primeira execução: a lista chegou, mas nenhum modelo tem a configuração
  // local presente.
  const withoutLocalConfiguration =
    exposedProviders.length > 0 &&
    !exposedProviders.some((id) => prerequisiteOf(localPrerequisites, id) === 'met')
  const missingProviders = exposedProviders.filter(
    (id) => prerequisiteOf(localPrerequisites, id) === 'missing',
  )
  const hasUnknownProviders = exposedProviders.some(
    (id) => prerequisiteOf(localPrerequisites, id) === 'unknown',
  )

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

      {withoutLocalConfiguration && (
        // Explicação calma de primeira execução -- nunca "offline",
        // "indisponível" ou "quebrado": só o fato local que o servidor
        // conhece. Nomes de variável/configuração ficam na documentação.
        <section
          aria-labelledby="first-run-heading"
          className="notice notice--neutral composer__first-run"
        >
          <h2 id="first-run-heading">Falta a configuração local dos modelos</h2>
          {missingProviders.length > 0 && (
            <p>
              O Dialeon oferece suporte a {formatModelList(missingProviders)}, mas esta instalação
              ainda não tem a configuração local necessária para usá-los.
            </p>
          )}
          {hasUnknownProviders && (
            <p>
              Para os modelos marcados com “Não foi possível verificar a configuração local”, não
              dá para saber daqui se ela está presente; eles podem ser escolhidos mesmo assim.
            </p>
          )}
          <p>
            A configuração é lida quando o servidor do Dialeon inicia: depois de ajustá-la (veja o
            README), reinicie o servidor e recarregue a lista. Configuração presente não garante que
            o serviço de cada modelo aceite as credenciais.
          </p>
          {onRetryProviders && (
            <button type="button" onClick={onRetryProviders}>
              Recarregar lista de modelos
            </button>
          )}
        </section>
      )}

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
            onChange={chooseModels}
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
          <p>Não foi possível carregar a lista de modelos: {providersError}</p>
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
