// Shell persistente do produto (UI Slice 2) -- header quieto compartilhado
// por todas as rotas de /app. Marca é só texto ("Dialeon") por enquanto --
// nenhuma arte de logo/dandelion é inventada aqui. O estado de rota atual
// nunca depende só de cor: `NavLink` já adiciona `aria-current="page"`
// automaticamente no link ativo, e o CSS correspondente (ver index.css)
// reforça isso com peso de fonte/sublinhado, não só uma troca de cor.

import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

interface AppShellProps {
  children: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <NavLink to="/" end className="app-shell__brand">
          Dialeon
        </NavLink>
        <nav className="app-shell__nav" aria-label="Navegação principal">
          <NavLink to="/" end className="app-shell__nav-link">
            Perguntar
          </NavLink>
          <NavLink to="/runs" className="app-shell__nav-link">
            Histórico
          </NavLink>
        </nav>
      </header>
      {children}
    </div>
  )
}
