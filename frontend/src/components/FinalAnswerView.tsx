// Resposta final -- protagonista absoluta (ANSWER FIRST). limitations
// aparecem logo abaixo, sempre que existirem (nunca escondidas).
//
// UI Slice 3 (Structured Final Answer) -- quando `answer_blocks` existe
// (runs novos), a resposta é renderizada como elementos semânticos reais
// (h3/ul/li) diretamente a partir do JSON tipado do backend -- NUNCA por
// parsing de `answer_text`. `answer_blocks === null` (runs históricos,
// ou o caminho sem veredito) cai de volta pro comportamento anterior
// (`splitAnswerParagraphs` sobre `answer_text`), inalterado.
//
// `claim_text`/`explanation`/`source_relationship_note` continuam NÃO
// CONFIÁVEIS (modelo participante do debate/Judge/fonte fornecida pelo
// usuário) -- sempre interpolados como filhos de texto do React
// (`{item.claim_text}`), NUNCA via `dangerouslySetInnerHTML`: o React já
// escapa texto por padrão, então mesmo conteúdo malicioso (`<script>`,
// `# Heading`, `- item forjado`, `\n\n`) permanece texto visível inerte,
// nunca é interpretado como marcação/estrutura nova.
//
// Repair (revisão adversarial, achados 4 e 5) -- `answer_blocks` é
// TUDO OU NADA (`isSupportedAnswerBlocks`): `null`/ausente/vazio/algum
// `kind` desconhecido (mesmo misturado com blocos conhecidos) sempre
// caem pro MESMO fallback completo de texto, nunca uma renderização
// parcial que descarta blocos silenciosamente. A seção dedicada de
// `limitations` é suprimida especificamente quando o fallback de texto
// é usado E `status` é um dos dois que SEMPRE embutem esse mesmo
// conteúdo em `answer_text` (`FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS`)
// -- decisão baseada só em `status`, nunca por inspecionar o texto.

import type {
  AnswerBlockPublic,
  AnswerClaimItemPublic,
  FinalAnswerPublic,
  FinalAnswerStatus,
} from '../api/types'
import { formatFinalAnswerStatus, splitAnswerParagraphs } from '../api/formatting'

interface FinalAnswerViewProps {
  finalAnswer: FinalAnswerPublic
}

// Repair (revisão adversarial, achado 4) -- os únicos `kind`s que este
// frontend sabe renderizar hoje. A checagem abaixo é DELIBERADAMENTE em
// tempo de EXECUÇÃO (não só o tipo `AnswerBlockPublic['kind']`, que o
// TypeScript trataria como sempre-fechado): o JSON vem de uma resposta
// HTTP não validada em tempo de execução -- um backend futuro/mais novo
// que o frontend, ou um dado corrompido, pode legitimamente conter um
// `kind` que esta versão não conhece.
const KNOWN_ANSWER_BLOCK_KINDS: ReadonlySet<string> = new Set<AnswerBlockPublic['kind']>([
  'paragraph',
  'claim_section',
])

// Renderização estruturada é TUDO OU NADA -- nunca um meio-termo onde
// blocos conhecidos são renderizados e blocos desconhecidos são
// silenciosamente descartados (isso apresentaria uma resposta
// INCOMPLETA como se fosse completa, sem nenhum sinal pro usuário).
// `null`/ausente/`undefined`/vazio/qualquer `kind` desconhecido (mesmo
// misturado com blocos conhecidos) -- todos caem pro MESMO fallback
// completo (`splitAnswerParagraphs` sobre `answer_text`, que sempre
// contém o conteúdo INTEIRO e já é o comportamento testado/aceito de
// antes desta slice).
function isSupportedAnswerBlocks(
  blocks: AnswerBlockPublic[] | null | undefined,
): blocks is AnswerBlockPublic[] {
  return (
    blocks != null &&
    blocks.length > 0 &&
    blocks.every((block) => KNOWN_ANSWER_BLOCK_KINDS.has(block.kind))
  )
}

// Repair (revisão adversarial, achado 5) -- política explícita de
// NÃO-parsing pra decidir se a seção dedicada de limitações duplicaria
// conteúdo já presente no fallback de texto (`answer_text`). Decisão
// baseada INTEIRAMENTE em `status` (nunca inspecionando/tentando
// reconhecer substring de `answer_text`):
//
// - 'llm_planned'/'deterministic_from_verdict': `_compose_answer`
//   (app/editor/compose.py) SEMPRE embute `debate_limitations` dentro
//   de `answer_text`, textualmente, sempre que a lista é não-vazia --
//   em AMBOS os `closing_style`s. Quando o fallback de texto é usado
//   pra um destes dois status (só pode acontecer numa linha histórica
//   persistida antes desta coluna existir -- ver
//   `FinalAnswer.answer_blocks`, app/editor/result.py), o texto JÁ
//   contém a mesma informação -- a seção dedicada duplicaria.
// - 'deterministic_no_verdict': o texto de limitações é uma frase
//   SINTÉTICA própria ("Avaliação final não realizada: ..."), nunca
//   embutida em `answer_text` (que usa uma frase diferente) -- nunca
//   duplica, continua mostrando a seção dedicada.
// - 'llm_composed' (só histórico -- nenhum código novo produz este
//   status): prosa livre escrita por uma LLM no passado -- nenhum
//   campo aqui garante se ela já menciona as limitações ou não.
//   Comportamento honesto: preserva o que sempre foi mostrado (a seção
//   dedicada), nunca tenta adivinhar a partir do texto.
const FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS: ReadonlySet<FinalAnswerStatus> = new Set([
  'llm_planned',
  'deterministic_from_verdict',
])

// Visual polish (Structured Final Answer) -- "Avaliação"/"Fonte" abaixo
// são rótulos ESTÁTICOS escritos por este componente (nunca vindos do
// backend); só existem pra dar ao rótulo/valor um tratamento tipográfico
// separado (rótulo quieto vs. valor). `verdict_label`/`explanation`/
// `source_relationship_note` continuam interpolados como filhos de texto
// puro do React, cada um em seu próprio nó -- nenhuma reinterpretação de
// conteúdo, nenhuma mudança de palavra, só mais estrutura de
// apresentação em cima do mesmo texto.
function ClaimItemView({ item }: { item: AnswerClaimItemPublic }) {
  return (
    <li className="final-answer__claim-item">
      <p className="final-answer__claim-text">{item.claim_text}</p>
      <p className="final-answer__claim-verdict">
        <span className="final-answer__claim-verdict-label">Avaliação</span>
        <span className="final-answer__claim-verdict-value">{item.verdict_label}</span>
      </p>
      <p className="final-answer__claim-explanation">{item.explanation}</p>
      {item.source_relationship_note && (
        <p className="final-answer__claim-source-note">
          <span className="final-answer__claim-source-note-label">Fonte</span>
          <span className="final-answer__claim-source-note-text">{item.source_relationship_note}</span>
        </p>
      )}
    </li>
  )
}

function AnswerBlockView({ block }: { block: AnswerBlockPublic }) {
  if (block.kind === 'paragraph') {
    return <p>{block.text}</p>
  }
  if (block.kind === 'claim_section') {
    return (
      <div className="final-answer__claim-section">
        <h3>{block.heading}</h3>
        <ul className="final-answer__claim-list">
          {block.items.map((item, index) => (
            <ClaimItemView key={index} item={item} />
          ))}
        </ul>
      </div>
    )
  }
  // Inalcançável em produção -- `isSupportedAnswerBlocks` já garante que
  // só chega aqui com blocos de `kind` conhecido (ver seu uso abaixo).
  // Mantido só como defesa em profundidade: se essa garantia algum dia
  // for violada por um refactor, ainda assim nunca renderiza estrutura
  // pra um bloco que não sabe interpretar.
  return null
}

export function FinalAnswerView({ finalAnswer }: FinalAnswerViewProps) {
  const structuredBlocks = isSupportedAnswerBlocks(finalAnswer.answer_blocks)
    ? finalAnswer.answer_blocks
    : null

  const showDedicatedLimitations =
    finalAnswer.limitations.length > 0 &&
    (structuredBlocks !== null ||
      !FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS.has(finalAnswer.status))

  return (
    <section aria-labelledby="final-answer-heading" className="final-answer">
      <h2 id="final-answer-heading">Resposta</h2>
      <div className="final-answer__text">
        {structuredBlocks !== null
          ? structuredBlocks.map((block, index) => <AnswerBlockView key={index} block={block} />)
          : splitAnswerParagraphs(finalAnswer.answer_text).map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
      </div>

      {showDedicatedLimitations && (
        <div className="final-answer__limitations">
          <h3>Limitações</h3>
          <ul>
            {finalAnswer.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="final-answer__status">{formatFinalAnswerStatus(finalAnswer.status)}</p>
    </section>
  )
}
