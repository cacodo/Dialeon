import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

// O timeout padrão de `findBy*`/`waitFor` (1000 ms) é curto demais quando a
// suíte inteira roda em paralelo (o primeiro teste de um arquivo paga o custo
// frio de import/render) e gerava falhas intermitentes. Só aumenta a TOLERÂNCIA
// de espera -- nenhuma asserção muda.
configure({ asyncUtilTimeout: 4000 })
