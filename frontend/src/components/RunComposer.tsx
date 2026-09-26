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

// Estado sob o qual cada modelo está na seleção: "met" (pré-seleção
// automática, ou escolha feita com a configuração local presente) ou
// "unknown" (escolha EXPLÍCITA do usuário enquanto o modelo estava
// "unknown"). É o que permite exigir que um modelo "unknown" só seja
// enviado por uma escolha feita nesse estado atual.
type SelectionBasis = 'met' | 'unknown'

interface Selection {
  ids: string[]
  basis: Readonly<Record<string, SelectionBasis>>
  // A pré-seleção já aconteceu (ou o usuário já escolheu): daqui em diante
  // uma nova lista só pode REMOVER escolhas, nunca acrescentar.
  settled: boolean
}

// Um modelo continua na seleção só se ainda existe na lista e: está "met",
// ou está "unknown" e foi escolhido explicitamente enquanto "unknown". Um
// "missing" nunca fica. Assim, um modelo pré-selecionado como "met" que
// passa a "unknown" sai da seleção (o usuário nunca o escolheu nesse
// estado), enquanto uma escolha explícita de um "unknown" sobrevive a
// recarregamentos em que ele continua "unknown".
function holdsInSelection(
  id: string,
  basis: SelectionBasis | undefined,
  providers: readonly string[],
  states: PrerequisiteStates,
): boolean {
  if (!providers.includes(id)) return false
  const state = prerequisiteOf(states, id)
  return state === 'met' || (state === 'unknown' && basis === 'unknown')
}

// Seleção diante de uma lista de modelos recém-recebida. A pré-seleção
// acontece UMA vez, na primeira lista que tenha algo a pré-selecionar, e só
// com modelos "met" (os do reuso que estão "met" ou, sem eles, todos os
// "met") -- nunca "unknown" nem "missing" automaticamente. Numa primeira
// execução sem nenhum "met", ela espera: depois de configurar, reiniciar a
// API e recarregar a lista, os "met" são pré-selecionados. Depois disso (ou
// de uma escolha do usuário), uma lista nova só REMOVE: fica o que ainda
// `holdsInSelection`, e o que saiu deixa a seleção de vez. Um "unknown"
// escolhido que passa a "met" fica, agora com base "met".
function reconcileSelection(
  current: Selection,
  providers: readonly string[],
  states: PrerequisiteStates,
  reuse: readonly string[],
): Selection {
  const isMet = (id: string) => providers.includes(id) && prerequisiteOf(states, id) === 'met'
  if (current.settled) {
    const kept = current.ids.filter((id) => holdsInSelection(id, current.basis[id], providers, states))
    const basis = Object.fromEntries(
      kept.map((id): [string, SelectionBasis] => [id, isMet(id) ? 'met' : 'unknown']),
    )
    const unchanged =
      kept.length === current.ids.length && kept.every((id) => basis[id] === current.basis[id])
    return unchanged ? current : { ids: kept, basis, settled: true }
  }
  const reused = reuse.filter(isMet)
  const initial = reused.length > 0 ? reused : providers.filter(isMet)
  return initial.length > 0
    ? { ids: initial, basis: Object.fromEntries(initial.map((id) => [id, 'met' as const])), settled: true }
    : current
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
  const [selection, setSelection] = useState<Selection>({ ids: [], basis: {}, settled: false })
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

  // Escolha do usuário no painel: cada modelo marcado fica com a base do
  // estado em que está AGORA -- marcar um "unknown" é a escolha explícita
  // que o torna enviável.
  function chooseModels(next: string[]) {
    const chosen = next.filter((id) => prerequisiteOf(localPrerequisites, id) !== 'missing')
    setSelection({
      ids: chosen,
      basis: Object.fromEntries(
        chosen.map((id): [string, SelectionBasis] => [
          id,
          prerequisiteOf(localPrerequisites, id) === 'met' ? 'met' : 'unknown',
        ]),
      ),
      settled: true,
    })
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
  const validSelected = selected.filter((id) =>
    holdsInSelection(id, selection.basis[id], exposedProviders, localPrerequisites),
  )
  // Primeira execução: a lista chegou, mas nenhum modelo tem a configuração
  // local CONFIRMADA ("met"). Isso não quer dizer que ela falte: só os
  // "missing" são ausência conhecida; os "unknown" não puderam ser
  // verificados.
  const withoutConfirmedConfiguration =
    exposedProviders.length > 0 &&
    !exposedProviders.some((id) => prerequisiteOf(localPrerequisites, id) === 'met')
  const missingProviders = exposedProviders.filter(
    (id) => prerequisiteOf(localPrerequisites, id) === 'missing',
  )
  const unknownProviders = exposedProviders.filter(
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

      {withoutConfirmedConfiguration && (
        // Explicação calma de primeira execução -- nunca "offline",
        // "indisponível" ou "quebrado": só o fato local que o servidor
        // conhece. "Falta configuração" só quando TODOS são ausência
        // conhecida ("missing"); com algum "unknown", o texto é neutro.
        // Nomes de variável/configuração ficam na documentação.
        <section
          aria-labelledby="first-run-heading"
          className="notice notice--neutral composer__first-run"
        >
          {unknownProviders.length === 0 ? (
            <>
              <h2 id="first-run-heading">Falta a configuração local dos modelos</h2>
              <p>
                O Dialeon oferece suporte a {formatModelList(missingProviders)}, mas esta instalação
                ainda não tem a configuração local necessária para usá-los.
              </p>
            </>
          ) : (
            <>
              <h2 id="first-run-heading">Nenhum modelo com configuração local confirmada</h2>
              <p>
                O Dialeon oferece suporte a {formatModelList(exposedProviders)}, mas o servidor não
                confirmou a configuração local de nenhum deles.
              </p>
              {missingProviders.length > 0 && (
                <p>
                  {formatModelList(missingProviders)}: falta a configuração local necessária nesta
                  instalação.
                </p>
              )}
              <p>
                {formatModelList(unknownProviders)}: não dá para verificar daqui se a configuração
                local está presente. {unknownProviders.length === 1 ? 'Ele pode' : 'Eles podem'} ser
                {unknownProviders.length === 1 ? ' escolhido' : ' escolhidos'} mesmo assim.
              </p>
            </>
          )}
          <p>
            A configuração é lida quando o servidor do Dialeon inicia: depois de ajustá-la (veja o
            README), reinicie o servidor e recarregue a lista. Recarregar só consulta o servidor de
            novo. Configuração presente não garante que o serviço de cada modelo aceite as
            credenciais.
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
