// Provenance-Preserving Human-Readable Run Export -- ação secundária da
// página de uma pergunta CONCLUÍDA: baixa um documento de texto gerado pelo
// servidor com a resposta e o mínimo de proveniência pra lê-la fora do
// Dialeon. A resposta vai inteira; o texto completo da fonte, as respostas
// individuais dos modelos e o custo não entram como partes separadas. Só é
// renderizado por quem sabe que a run está concluída.

import { runExportUrl } from '../api/client'

export function ExportAnswerLink({ runId }: { runId: string }) {
  return (
    <>
      <a href={runExportUrl(runId)} download aria-describedby="export-description">
        Exportar resposta (.txt)
      </a>
      <p id="export-description" className="sr-only">
        Baixa um arquivo de texto com a pergunta, a resposta inteira e como ela foi produzida. Não
        inclui, como partes separadas, o texto completo da fonte, as respostas individuais dos
        modelos nem o custo.
      </p>
    </>
  )
}
