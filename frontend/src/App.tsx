import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { Home } from './pages/Home'
import { History } from './pages/History'
import { RunDetail } from './pages/RunDetail'

// Rotas relativas ao basename "/app" (configurado no BrowserRouter em
// main.tsx) -- servido pelo FastAPI sob /app (Decision Delta secao 4).
// `AppShell` (UI Slice 2) é o único lugar que sabe sobre navegação entre
// rotas -- cada página continua sem conhecimento do shell, exatamente
// como antes (testes de página seguem renderizando cada página isolada).
export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/runs" element={<History />} />
        <Route path="/runs/:runId" element={<RunDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}
