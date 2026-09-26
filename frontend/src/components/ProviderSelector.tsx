// Seleção dos modelos que respondem a uma pergunta -- controle SECUNDÁRIO do
// composer (nunca a identidade principal da tela).
//
// Recebe opções `{ id, label }` em vez de uma lista fixa de nomes: a lista
// vem de GET /providers e pode crescer. O resumo mostra NOMES (mais útil que
// uma contagem abstrata) e continua curto com muitos modelos ("+N"). Nada
// aqui afirma prontidão/credenciais de um modelo -- isso não é conhecido
// pela interface.
//
// Pré-requisitos LOCAIS (vindos do backend, sem rede): "missing" continua
// visível, mas desabilitado e explicado; "unknown" é escolhível, com uma
// nota neutra; "met" não ganha selo nenhum -- configuração local presente
// não é serviço disponível nem credencial válida.

import type { LocalPrerequisiteState } from '../api/types'

export interface ModelOption {
  id: string
  label: string
  prerequisite: LocalPrerequisiteState
}

const PREREQUISITE_NOTES: Partial<Record<LocalPrerequisiteState, string>> = {
  missing: 'Falta configuração local nesta instalação',
  unknown: 'Não foi possível verificar a configuração local',
}

const SUMMARY_NAME_LIMIT = 3

function modelSummaryText(
  options: readonly ModelOption[],
  selected: readonly string[],
  single: boolean,
): string {
  const names = options.filter((option) => selected.includes(option.id)).map((option) => option.label)
  // Resposta direta: exatamente um modelo responde.
  const prefix = single ? 'Modelo' : 'Modelos'
  if (names.length === 0) return `${prefix}: nenhum`
  const shown = names.slice(0, SUMMARY_NAME_LIMIT).join(', ')
  const hidden = names.length - SUMMARY_NAME_LIMIT
  return hidden > 0 ? `${prefix}: ${shown} +${hidden}` : `${prefix}: ${shown}`
}

interface ModelSummaryButtonProps {
  options: readonly ModelOption[]
  selected: readonly string[]
  expanded: boolean
  onToggle: () => void
  disabled?: boolean
  panelId: string
  single?: boolean
}

export function ModelSummaryButton({
  options,
  selected,
  expanded,
  onToggle,
  disabled,
  panelId,
  single = false,
}: ModelSummaryButtonProps) {
  return (
    <button
      type="button"
      className="composer__control composer__models"
      aria-expanded={expanded}
      aria-controls={panelId}
      onClick={onToggle}
      disabled={disabled}
    >
      <span className="composer__models-text">{modelSummaryText(options, selected, single)}</span>
      <span className="composer__control-chevron" aria-hidden="true">
        ▾
      </span>
    </button>
  )
}

interface ModelSelectionPanelProps {
  options: readonly ModelOption[]
  selected: readonly string[]
  onChange: (selected: string[]) => void
  disabled?: boolean
  panelId: string
  // Resposta direta: escolha ÚNICA (radio) -- um modelo responde sozinho.
  single?: boolean
}

export function ModelSelectionPanel({
  options,
  selected,
  onChange,
  disabled,
  panelId,
  single = false,
}: ModelSelectionPanelProps) {
  function toggle(id: string) {
    if (single) {
      onChange([id])
      return
    }
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id])
  }

  return (
    <fieldset id={panelId} className="composer__panel model-selection">
      <legend>{single ? 'Modelo que vai responder' : 'Modelos que vão responder'}</legend>
      <div className="model-selection__options">
        {options.map((option) => {
          const note = PREREQUISITE_NOTES[option.prerequisite]
          const noteId = `${panelId}-${option.id}-note`
          // A nota fica FORA do <label>: o nome acessível é só o do modelo; a
          // nota é a descrição do controle.
          return (
            <div
              key={option.id}
              className={`model-selection__item model-selection__item--${option.prerequisite}`}
            >
              <label className="model-selection__option">
                <input
                  type={single ? 'radio' : 'checkbox'}
                  name={single ? `${panelId}-single` : undefined}
                  checked={selected.includes(option.id)}
                  onChange={() => toggle(option.id)}
                  disabled={disabled || option.prerequisite === 'missing'}
                  aria-describedby={note ? noteId : undefined}
                />
                <span>{option.label}</span>
              </label>
              {note && (
                <span id={noteId} className="model-selection__note">
                  {note}
                </span>
              )}
            </div>
          )
        })}
      </div>
      {selected.length === 0 && options.some((option) => option.prerequisite !== 'missing') && (
        <p className="model-selection__hint">
          {single ? 'Escolha um modelo para perguntar.' : 'Escolha pelo menos um modelo para perguntar.'}
        </p>
      )}
    </fieldset>
  )
}
