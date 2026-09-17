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

  // Fragment (não `<div>` wrapper) -- botão e painel viram filhos diretos
  // de `.run-composer__toolbar`, o mesmo container flex que o controle de
  // Fonte usa, pra que os dois leiam como controles-irmãos genuínos (não
  // um aninhado dentro do outro) e o painel expandido quebre pra própria
  // linha do jeito previsível de `.run-composer__control-panel` (Polish
  // dos controles secundários do composer).
  return (
    <>
      <button
        type="button"
        className="run-composer__control-toggle provider-selector__summary"
        aria-expanded={expanded}
        aria-controls="provider-selector-panel"
        onClick={() => setExpanded((v) => !v)}
        disabled={disabled}
      >
        Participantes: {selected.length} selecionado{selected.length === 1 ? '' : 's'}
        <span className="run-composer__control-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {expanded && (
        <fieldset
          id="provider-selector-panel"
          className="run-composer__control-panel provider-selector__panel"
        >
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
    </>
  )
}
