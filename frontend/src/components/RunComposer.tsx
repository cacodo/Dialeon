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
// Direct Answer Execution V1 -- um controle de modo escolhe entre o
// Conselho (padrão, comportamento de sempre) e a resposta direta (UM modelo
// responde sozinho, sem as etapas do conselho e sem fonte). As duas seleções
// de modelo são estados separados: trocar de modo nunca fabrica nem apaga a
// escolha do outro, e o envio só leva o que vale no modo atual.
//
// Council Local Execution Readiness & Admission V1 -- no Conselho, a seleção
// atual (e se há fonte) é conferida por uma prévia SEM efeito
// (`previewReadiness`). O caminho feliz não muda: sem ausência conhecida,
// nada aparece e o envio pede admissão estrita (o servidor reavalia e recusa
// se a configuração tiver mudado). Com ausência local CONHECIDA em alguma
// etapa, um aviso compacto diz qual etapa e qual modelo, e perguntar exige a
// escolha deliberada de seguir mesmo assim (envio padrão com reconhecimento).
// O reconhecimento vale só para ESTA entrada (pergunta, fonte, modelos) e ESTA
// degradação (a identidade que o servidor derivou e mostrou); o envio manda
// essa identidade, e o servidor recusa se a degradação no aceite for outra.
// "Não verificável" (unknown) é incerteza, nunca falha: só uma nota neutra.
// A resposta direta não usa nada disso.
//
// Enter continua inserindo nova linha (a pergunta pode ser longa e
// multi-linha); Ctrl/⌘+Enter envia. O atalho é anunciado por
// `aria-keyshortcuts` e por uma dica discreta ligada ao campo por
// `aria-describedby` -- nunca só visual.

import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  formatModelList,
  formatProviderName,
  formatReadinessRoles,
  groupReadinessByProvider,
} from '../api/formatting'
import type {
  CouncilReadiness,
  CouncilReadinessRequest,
  LocalPrerequisiteState,
} from '../api/types'
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
  // `kind` só é passado pra resposta direta; `admission` só pro Conselho.
  onSubmit: (
    question: string,
    enabledProviders: string[],
    sourceText: string | null,
    kind?: 'direct',
    admission?: CouncilAdmissionChoice,
  ) => void
  onRetryProviders?: () => void
  // Prévia de prontidão local do Conselho (sem efeito). Ausente = nenhuma
  // prévia: o envio continua pedindo admissão estrita.
  previewReadiness?: (request: CouncilReadinessRequest) => Promise<CouncilReadiness>
  // Avaliação devolvida por uma recusa de admissão estrita -- mais recente
  // que qualquer prévia; mostrada no aviso da seleção atual.
  rejectedReadiness?: CouncilReadiness | null
}

// Como o Conselho é enviado: estrito (o servidor recusa com ausência local
// conhecida) ou padrão COM reconhecimento explícito da degradação.
export type CouncilAdmissionChoice =
  | { readiness_admission: 'strict' }
  | {
      readiness_admission: 'standard'
      acknowledge_known_degradation: true
      acknowledged_degradation_fingerprint: string
    }

// Sem resultado pra chave atual = prévia em andamento (ou indisponível).
type ReadinessState =
  | { key: string; status: 'error' }
  | { key: string; status: 'ready'; readiness: CouncilReadiness }

export type RunMode = 'council' | 'direct'

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

// Resposta direta: o modelo escolhido (no máximo um), com a mesma regra de
// base da seleção do Conselho -- "met" pode ser escolhido automaticamente;
// "unknown" só por escolha explícita nesse estado; "missing" nunca.
interface DirectChoice {
  id: string | null
  basis: SelectionBasis | null
  // A escolha já aconteceu (automática ou do usuário): uma lista nova só
  // pode REMOVER, nunca trocar de modelo sozinha.
  settled: boolean
}

function reconcileDirectChoice(
  current: DirectChoice,
  providers: readonly string[],
  states: PrerequisiteStates,
  preferred: readonly string[],
  fallbackToAnyMet: boolean,
): DirectChoice {
  const isMet = (id: string) => providers.includes(id) && prerequisiteOf(states, id) === 'met'
  if (current.settled) {
    if (current.id === null) return current
    if (!holdsInSelection(current.id, current.basis ?? undefined, providers, states)) {
      return { id: null, basis: null, settled: true }
    }
    const basis: SelectionBasis = isMet(current.id) ? 'met' : 'unknown'
    return basis === current.basis ? current : { ...current, basis }
  }
  const pick = preferred.find(isMet) ?? (fallbackToAnyMet ? providers.find(isMet) : undefined)
  if (pick !== undefined) return { id: pick, basis: 'met', settled: true }
  // Reuso de uma run direta cujo modelo não pode ser marcado agora: nenhum
  // outro é escolhido no lugar dele.
  return fallbackToAnyMet ? current : { id: null, basis: null, settled: providers.length > 0 }
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
  previewReadiness,
  rejectedReadiness = null,
}: RunComposerProps) {
  const [question, setQuestion] = useState(initialInput?.question ?? '')
  const reusedDirectProvider = initialInput?.kind === 'direct' ? initialInput.enabledProviders[0] : null
  const [mode, setMode] = useState<RunMode>(initialInput?.kind === 'direct' ? 'direct' : 'council')
  const [selection, setSelection] = useState<Selection>({ ids: [], basis: {}, settled: false })
  const [directChoice, setDirectChoice] = useState<DirectChoice>({
    id: null,
    basis: null,
    settled: false,
  })
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
      reconcileSelection(
        current,
        providers,
        localPrerequisites,
        // o reuso de uma run direta não pré-seleciona nada no Conselho
        initialInput?.kind === 'direct' ? [] : (initialInput?.enabledProviders ?? []),
      ),
    )
    setDirectChoice((current) =>
      current.settled || mode === 'direct'
        ? reconcileDirectChoice(
            current,
            providers,
            localPrerequisites,
            reusedDirectProvider !== null ? [reusedDirectProvider] : [],
            reusedDirectProvider === null,
          )
        : current,
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

  function chooseDirect(next: string[]) {
    const id = next[0]
    const state = id === undefined ? 'missing' : prerequisiteOf(localPrerequisites, id)
    if (id === undefined || state === 'missing') return
    setDirectChoice({ id, basis: state === 'met' ? 'met' : 'unknown', settled: true })
  }

  function changeMode(next: RunMode) {
    if (next === 'direct' && !directChoice.settled) {
      // Primeira vez no modo direto: só um modelo "met" é escolhido
      // automaticamente, de preferência um que já estava escolhido no Conselho.
      setDirectChoice((current) =>
        reconcileDirectChoice(current, providers, localPrerequisites, validSelected, true),
      )
    }
    setMode(next)
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
  const validDirect =
    directChoice.id !== null &&
    holdsInSelection(directChoice.id, directChoice.basis ?? undefined, exposedProviders, localPrerequisites)
      ? [directChoice.id]
      : []
  const isDirect = mode === 'direct'
  // Reuso de uma run direta cujo modelo não pôde ser marcado de novo: dito
  // com clareza, sem trocar de modelo por conta própria.
  const reuseNotRestored =
    isDirect && reusedDirectProvider !== null && directChoice.id === null && !providersLoading
      ? reusedDirectProvider
      : null
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

  // Prévia de prontidão da seleção ATUAL do Conselho. A chave identifica o
  // que decide as dependências (participantes e presença de fonte); uma
  // resposta de outra chave nunca é mostrada.
  const readinessKey =
    isDirect || validSelected.length === 0 ? null : JSON.stringify([validSelected, !sourceBlank])
  const [readinessState, setReadinessState] = useState<ReadinessState | null>(null)
  useEffect(() => {
    if (readinessKey === null || previewReadiness === undefined) return
    const [enabledProviders, sourceSupplied] = JSON.parse(readinessKey) as [string[], boolean]
    let cancelled = false
    previewReadiness({ enabled_providers: enabledProviders, source_supplied: sourceSupplied })
      .then((readiness) => {
        if (!cancelled) setReadinessState({ key: readinessKey, status: 'ready', readiness })
      })
      .catch(() => {
        // Sem prévia, nada é afirmado: o envio segue estrito e o servidor decide.
        if (!cancelled) setReadinessState({ key: readinessKey, status: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [readinessKey, previewReadiness])
  // Uma recusa do servidor (admissão estrita, ou reconhecimento de outra
  // degradação) traz a avaliação AUTORITATIVA feita no aceite, pra entrada
  // atual. Ela tem precedência sobre QUALQUER prévia (consultiva) da mesma
  // seleção enquanto essa entrada durar -- então uma prévia pedida antes, que
  // chegue depois, nunca passa por cima dela.
  const [authoritative, setAuthoritative] = useState<{
    key: string
    readiness: CouncilReadiness
  } | null>(null)
  // Toda avaliação mostrada (autoritativa ou prévia) vale só pela VISITA
  // contínua à entrada em que foi obtida: sair dessa entrada a descarta, e
  // voltar a ela depois é uma visita nova, com prévia nova -- a mesma chave
  // não ressuscita uma avaliação antiga. Prévias pendentes da visita anterior
  // já são descartadas pelo `cancelled` do efeito.
  const [visitedKey, setVisitedKey] = useState<string | null>(readinessKey)
  if (readinessKey !== visitedKey) {
    setVisitedKey(readinessKey)
    setAuthoritative(null)
    setReadinessState(null)
  }
  const [seenRejection, setSeenRejection] = useState<CouncilReadiness | null>(null)
  if (rejectedReadiness !== seenRejection) {
    setSeenRejection(rejectedReadiness)
    setAuthoritative(
      rejectedReadiness !== null && readinessKey !== null
        ? { key: readinessKey, readiness: rejectedReadiness }
        : null,
    )
  }
  const previewed =
    readinessState !== null && readinessState.status === 'ready' && readinessState.key === readinessKey
      ? readinessState.readiness
      : null
  const readiness =
    authoritative !== null && authoritative.key === readinessKey ? authoritative.readiness : previewed
  const missingGroups =
    readiness === null ? [] : groupReadinessByProvider(readiness, 'missing', { includeParticipants: true })
  // Só etapas internas: um participante "não verificável" já foi escolhido
  // explicitamente nesse estado (ver ModelSelectionPanel).
  const unknownGroups =
    readiness === null ? [] : groupReadinessByProvider(readiness, 'unknown', { includeParticipants: false })
  // A identidade da degradação vem sempre do servidor; o cliente nunca a
  // calcula nem a reaproveita de outra avaliação.
  const degradationFingerprint =
    readiness !== null && readiness.summary === 'some_missing'
      ? (readiness.known_degradation_fingerprint ?? null)
      : null
  const knownDegradation = readiness !== null && readiness.summary === 'some_missing'
  // O reconhecimento vale só para a entrada EXATA em que foi dado (pergunta,
  // fonte, modelos) e para a degradação mostrada: qualquer mudança em uma
  // delas pede a escolha de novo. Fica só na memória da página.
  const acknowledgementScope =
    degradationFingerprint === null
      ? null
      : JSON.stringify([question, sourceBlank ? null : sourceText, validSelected, degradationFingerprint])
  const [acknowledgedScope, setAcknowledgedScope] = useState<string | null>(null)
  const degradationAcknowledged =
    acknowledgementScope !== null && acknowledgedScope === acknowledgementScope

  const canSubmit =
    question.trim().length > 0 &&
    (isDirect ? validDirect.length === 1 : validSelected.length > 0) &&
    !questionTooLong &&
    // a fonte nunca é enviada na resposta direta
    (isDirect || !sourceTooLong) &&
    !submitting &&
    !providersLoading &&
    // degradação local conhecida: só com a escolha deliberada de seguir
    (isDirect || !knownDegradation || degradationAcknowledged)

  function submit() {
    if (!canSubmit) return
    // Accepted Question Size Boundary V1 (repair F1) -- `question` é
    // encaminhada VERBATIM (nunca `.trim()`ada aqui): o backend é a única
    // autoridade sobre o que conta como pergunta válida. `.trim()` acima em
    // `canSubmit` é só detecção de "em branco" pra UX. Fonte: whitespace só
    // decide se ela está vazia; conteúdo não vazio segue VERBATIM, igual à
    // API e à CLI.
    if (isDirect) {
      onSubmit(question, validDirect, null, 'direct')
      return
    }
    onSubmit(
      question,
      validSelected,
      sourceBlank ? null : sourceText,
      undefined,
      degradationAcknowledged && degradationFingerprint !== null
        ? {
            readiness_admission: 'standard',
            acknowledge_known_degradation: true,
            acknowledged_degradation_fingerprint: degradationFingerprint,
          }
        : { readiness_admission: 'strict' },
    )
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
      {/* Depende do modo: comparação/concordância/divergência só existem no
          Conselho -- nunca são prometidas para a resposta direta. */}
      <p className="composer__lede">
        {isDirect
          ? 'O modelo escolhido responde sozinho, e o Dialeon mostra essa resposta.'
          : 'Os modelos escolhidos respondem de forma independente; o Dialeon organiza a resposta e mostra onde eles concordam, onde divergem e o que continua incerto.'}
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

      <fieldset className="composer__mode" disabled={submitting}>
        <legend className="composer__mode-legend">Como responder</legend>
        <label className="composer__mode-option">
          <input
            type="radio"
            name="run-mode"
            value="council"
            checked={!isDirect}
            onChange={() => changeMode('council')}
            aria-describedby="run-mode-council-hint"
          />
          <span>Conselho de modelos</span>
        </label>
        <label className="composer__mode-option">
          <input
            type="radio"
            name="run-mode"
            value="direct"
            checked={isDirect}
            onChange={() => changeMode('direct')}
            aria-describedby="run-mode-direct-hint"
          />
          <span>Resposta direta</span>
        </label>
        <p id="run-mode-council-hint" className="composer__mode-hint" hidden={isDirect}>
          Vários modelos respondem; o Dialeon compara, avalia e organiza a resposta.
        </p>
        <p id="run-mode-direct-hint" className="composer__mode-hint" hidden={!isDirect}>
          Um modelo responde sozinho, sem as etapas do conselho e sem fonte. A resposta é a
          desse modelo: não é consenso nem verificação.
          {!sourceBlank && ' O texto de fonte que você colou não é enviado neste modo.'}
        </p>
      </fieldset>

      {reuseNotRestored !== null && (
        <p role="status" className="notice notice--neutral composer__reuse-note">
          O modelo usado antes ({formatProviderName(reuseNotRestored)}) não foi marcado:{' '}
          {!providers.includes(reuseNotRestored)
            ? 'ele não está mais na lista de modelos desta instalação.'
            : prerequisiteOf(localPrerequisites, reuseNotRestored) === 'missing'
              ? 'falta a configuração local dele nesta instalação.'
              : 'não foi possível verificar a configuração local dele; você pode marcá-lo em “Modelo”.'}
        </p>
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
          {!isDirect && (
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
          )}

          {providersLoading && (
            <p aria-live="polite" className="composer__status">
              Carregando modelos…
            </p>
          )}
          {!providersLoading && !providersError && (
            <ModelSummaryButton
              options={modelOptions}
              selected={isDirect ? validDirect : validSelected}
              expanded={modelsExpanded}
              onToggle={() => setModelsExpanded((expanded) => !expanded)}
              disabled={submitting}
              panelId={MODELS_PANEL_ID}
              single={isDirect}
            />
          )}

          <button type="submit" disabled={!canSubmit} className="composer__submit">
            {submitting ? 'Perguntando…' : 'Perguntar'}
          </button>
        </div>

        {!isDirect && sourceExpanded && (
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
            selected={isDirect ? validDirect : validSelected}
            onChange={isDirect ? chooseDirect : chooseModels}
            disabled={submitting}
            panelId={MODELS_PANEL_ID}
            single={isDirect}
          />
        )}
      </div>

      {!isDirect && knownDegradation && (
        <section
          aria-labelledby="readiness-heading"
          className="notice notice--warning composer__readiness"
        >
          <h2 id="readiness-heading">Falta configuração local para parte do Conselho</h2>
          <ul className="composer__readiness-list">
            {missingGroups.map((group) => (
              <li key={group.provider}>
                {formatReadinessRoles(group.roles)}: {formatProviderName(group.provider)}{' '}
                <span className="composer__readiness-model">
                  (modelo configurado: {group.configuredModel})
                </span>
              </li>
            ))}
          </ul>
          <p>
            Nesta instalação, essas etapas não conseguem chamar o modelo: se forem alcançadas, falham
            e a resposta sai incompleta (por exemplo, sem afirmações extraídas ou avaliadas). O
            Dialeon não troca de modelo por conta própria. Depois de ajustar a configuração, reinicie
            o servidor.
          </p>
          <label className="composer__readiness-ack">
            <input
              type="checkbox"
              checked={degradationAcknowledged}
              disabled={submitting || acknowledgementScope === null}
              onChange={(event) => setAcknowledgedScope(event.target.checked ? acknowledgementScope : null)}
            />
            <span>Perguntar mesmo assim, sabendo que a resposta pode sair incompleta</span>
          </label>
        </section>
      )}

      {!isDirect && !knownDegradation && unknownGroups.length > 0 && (
        <p className="notice notice--neutral composer__readiness-note">
          Não dá para verificar daqui a configuração local de{' '}
          {unknownGroups
            .map((group) => `${formatReadinessRoles(group.roles).toLowerCase()} (${formatProviderName(group.provider)})`)
            .join('; ')}
          . Isso não quer dizer que falte: a pergunta pode ser feita normalmente.
        </p>
      )}

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

      {!isDirect && sourceTooLong && (
        // Fora do painel recolhível: o erro nunca fica escondido se a fonte
        // estiver recolhida. (Na resposta direta a fonte não é enviada.)
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
