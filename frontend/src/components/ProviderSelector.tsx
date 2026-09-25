// Seleção dos modelos que respondem a uma pergunta -- controle SECUNDÁRIO do
// composer (nunca a identidade principal da tela).
//
// Recebe opções `{ id, label }` em vez de uma lista fixa de nomes: a lista
// vem de GET /providers e pode crescer. O resumo mostra NOMES (mais útil que
// uma contagem abstrata) e continua curto com muitos modelos ("+N"). Nada
// aqui afirma prontidão/credenciais de um modelo -- isso não é conhecido
// pela interface.

export interface ModelOption {
  id: string
  label: string
}

const SUMMARY_NAME_LIMIT = 3

function modelSummaryText(options: readonly ModelOption[], selected: readonly string[]): string {
  const names = options.filter((option) => selected.includes(option.id)).map((option) => option.label)
  if (names.length === 0) return 'Modelos: nenhum'
  const shown = names.slice(0, SUMMARY_NAME_LIMIT).join(', ')
  const hidden = names.length - SUMMARY_NAME_LIMIT
  return hidden > 0 ? `Modelos: ${shown} +${hidden}` : `Modelos: ${shown}`
}

interface ModelSummaryButtonProps {
  options: readonly ModelOption[]
  selected: readonly string[]
  expanded: boolean
  onToggle: () => void
  disabled?: boolean
  panelId: string
}

export function ModelSummaryButton({
  options,
  selected,
  expanded,
  onToggle,
  disabled,
  panelId,
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
      <span className="composer__models-text">{modelSummaryText(options, selected)}</span>
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
}

export function ModelSelectionPanel({
  options,
  selected,
  onChange,
  disabled,
  panelId,
}: ModelSelectionPanelProps) {
  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id])
  }

  return (
    <fieldset id={panelId} className="composer__panel model-selection">
      <legend>Modelos que vão responder</legend>
      <div className="model-selection__options">
        {options.map((option) => (
          <label key={option.id} className="model-selection__option">
            <input
              type="checkbox"
              checked={selected.includes(option.id)}
              onChange={() => toggle(option.id)}
              disabled={disabled}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      {selected.length === 0 && (
        <p className="model-selection__hint">Escolha pelo menos um modelo para perguntar.</p>
      )}
    </fieldset>
  )
}
