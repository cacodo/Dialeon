import { Navigate, Route, Routes } from 'react-router-dom'
import { Home } from './pages/Home'
import { History } from './pages/History'
import { RunDetail } from './pages/RunDetail'

// Rotas relativas ao basename "/app" (configurado no BrowserRouter em
// main.tsx) -- servido pelo FastAPI sob /app (Decision Delta secao 4).
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/runs" element={<History />} />
      <Route path="/runs/:runId" element={<RunDetail />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
