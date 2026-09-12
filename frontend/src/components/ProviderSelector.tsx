// Controle secundário/expansível (Decision Delta secao 6) -- provider
// selection nunca é a identidade principal da tela. IDs vêm SEMPRE de
// GET /providers (prop `providers`), nunca hardcoded aqui.

import { useState } from 'react'

interface ProviderSelectorProps {
  providers: string[]
  selected: string[]
  onChange: (selected: string[]) => void
  disabled?: boolean
}

function displayName(providerId: string): string {
  // Formatação de nome pra display, sem alterar o ID enviado ao backend
  // (Decision Delta secao 6: "nomes podem ser formatados para display
  // sem alterar IDs enviados").
  return providerId.charAt(0).toUpperCase() + providerId.slice(1)
}

export function ProviderSelector({ providers, selected, onChange, disabled }: ProviderSelectorProps) {
  const [expanded, setExpanded] = useState(false)

  function toggle(providerId: string) {
    if (selected.includes(providerId)) {
      onChange(selected.filter((p) => p !== providerId))
    } else {
      onChange([...selected, providerId])
    }
  }

  return (
    <div className="provider-selector">
      <button
        type="button"
        className="provider-selector__summary"
        aria-expanded={expanded}
        aria-controls="provider-selector-panel"
        onClick={() => setExpanded((v) => !v)}
        disabled={disabled}
      >
        Participantes: {selected.length} selecionado{selected.length === 1 ? '' : 's'} ▾
      </button>
      {expanded && (
        <fieldset id="provider-selector-panel" className="provider-selector__panel">
          <legend>Escolha os participantes</legend>
          {providers.map((providerId) => (
            <label key={providerId} className="provider-selector__option">
              <input
                type="checkbox"
                checked={selected.includes(providerId)}
                onChange={() => toggle(providerId)}
                disabled={disabled}
              />
              {displayName(providerId)}
            </label>
          ))}
        </fieldset>
      )}
    </div>
  )
}
