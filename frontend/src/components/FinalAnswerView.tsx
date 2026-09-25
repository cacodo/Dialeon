// Resposta final em DUAS profundidades:
//
// - `FinalAnswerView` (profundidade 0): a resposta que o usuário lê, mais as
//   limitações/escopo necessários pra interpretá-la. Escolhe UMA apresentação
//   pela ordem de fallback já estabelecida: realização linguística (elegível)
//   -> resposta natural (elegível) -> resposta principal estruturada ->
//   avaliação completa. Nunca aninha outra resposta dentro de si.
// - `AnswerAssessmentDetails` (profundidade 1, dentro de "Como esta resposta
//   foi produzida"): como a resposta foi montada, a resposta principal
//   estruturada (quando a apresentação escolhida foi outra) e a avaliação
//   completa das afirmações, com a explicação de cada uma (profundidade 2).
//
// Conteúdo vindo de modelo é sempre texto inerte (sem Markdown/HTML). Nenhuma
// cor/selo codifica veredito -- o rótulo textual é o portador do sentido.

import type {
  AnswerBlockPublic,
  AnswerClaimItemPublic,
  FinalAnswerPublic,
  FinalAnswerStatus,
  LinguisticRealizationPublic,
  NaturalAnswerPublic,
  PrimaryAnswerPublic,
} from '../api/types'
import {
  formatFinalAnswerStatus,
  splitAnswerParagraphs,
  summarizeAnswerVerdicts,
  summarizeUnevaluatedClaims,
} from '../api/formatting'
import { CopyAnswerButton } from './CopyAnswerButton'

interface FinalAnswerViewProps {
  finalAnswer: FinalAnswerPublic
}

type HeadingLevel = 3 | 4

function Heading({ level, children, id }: { level: HeadingLevel; children: string; id?: string }) {
  return level === 3 ? <h3 id={id}>{children}</h3> : <h4 id={id}>{children}</h4>
}

// Mesma regra de sempre: só renderiza `answer_blocks` se TODO bloco for de um
// kind conhecido (tudo-ou-nada); caso contrário cai pro texto canônico.
const KNOWN_ANSWER_BLOCK_KINDS: ReadonlySet<string> = new Set<AnswerBlockPublic['kind']>([
  'paragraph',
  'claim_section',
])

function isSupportedAnswerBlocks(
  blocks: AnswerBlockPublic[] | null | undefined,
): blocks is AnswerBlockPublic[] {
  return (
    blocks != null &&
    blocks.length > 0 &&
    blocks.every((block) => KNOWN_ANSWER_BLOCK_KINDS.has(block.kind))
  )
}

// Status cujo `answer_text` (fallback de texto) já embute as limitações --
// a seção dedicada seria duplicação nesses casos.
const FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS: ReadonlySet<FinalAnswerStatus> = new Set([
  'llm_planned',
  'deterministic_from_verdict',
])

type Presentation =
  | { kind: 'realization'; realization: LinguisticRealizationPublic; primary: PrimaryAnswerPublic }
  | { kind: 'natural'; natural: NaturalAnswerPublic; primary: PrimaryAnswerPublic }
  | { kind: 'primary'; primary: PrimaryAnswerPublic }
  | { kind: 'complete' }

function selectPresentation(finalAnswer: FinalAnswerPublic): Presentation {
  if (
    finalAnswer.linguistic_realization != null &&
    finalAnswer.primary_answer != null &&
    finalAnswer.linguistic_realization_presentation_eligible === true
  ) {
    return {
      kind: 'realization',
      realization: finalAnswer.linguistic_realization,
      primary: finalAnswer.primary_answer,
    }
  }
  if (
    finalAnswer.natural_answer != null &&
    finalAnswer.primary_answer != null &&
    finalAnswer.natural_answer_presentation_eligible === true
  ) {
    return { kind: 'natural', natural: finalAnswer.natural_answer, primary: finalAnswer.primary_answer }
  }
  if (finalAnswer.primary_answer != null) {
    return { kind: 'primary', primary: finalAnswer.primary_answer }
  }
  return { kind: 'complete' }
}

function LimitationsList({
  heading,
  items,
  level,
}: {
  heading: string
  items: string[]
  level: HeadingLevel
}) {
  return (
    <div className="final-answer__limitations">
      <Heading level={level}>{heading}</Heading>
      <ul>
        {items.map((limitation, index) => (
          <li key={index}>{limitation}</li>
        ))}
      </ul>
    </div>
  )
}

function ClaimItemView({ item }: { item: AnswerClaimItemPublic }) {
  return (
    <li className="final-answer__claim-item">
      <p className="final-answer__claim-text">{item.claim_text}</p>
      <p className="final-answer__claim-verdict">
        <span className="final-answer__claim-verdict-label">Avaliação</span>
        <span className="final-answer__claim-verdict-value">{item.verdict_label}</span>
      </p>
      {/* Material SECUNDÁRIO do item atrás de disclosure nativo, fechada por
          padrão (o texto continua no DOM): explicação do juiz e nota de
          fonte. Claim e veredito NUNCA ficam escondidos. */}
      <details className="final-answer__claim-why">
        <summary>Por quê?</summary>
        <p className="final-answer__claim-explanation">{item.explanation}</p>
        {item.source_relationship_note && (
          <p className="final-answer__claim-source-note">
            <span className="final-answer__claim-source-note-label">Fonte</span>
            <span className="final-answer__claim-source-note-text">
              {item.source_relationship_note}
            </span>
          </p>
        )}
      </details>
    </li>
  )
}

function UnevaluatedClaimsDisclosure({ claims }: { claims: string[] }) {
  return (
    <div className="final-answer__unevaluated-claims">
      <p className="final-answer__unevaluated-claims-note">
        Estas afirmações não foram avaliadas e podem se sobrepor a outras ou permanecer sem
        verificação. Elas não são fatos verificados nem conclusões do Dialeon.
      </p>
      <details>
        <summary>Mostrar todas as {claims.length} afirmações não avaliadas</summary>
        <ul className="final-answer__unevaluated-claims-list">
          {claims.map((claim, index) => (
            <li key={index}>{claim}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}

function AnswerBlockView({ block, level }: { block: AnswerBlockPublic; level: HeadingLevel }) {
  if (block.kind === 'paragraph') {
    return <p>{block.text}</p>
  }
  if (block.kind === 'claim_section') {
    return (
      <div className="final-answer__claim-section">
        <Heading level={level}>{block.heading}</Heading>
        <ul className="final-answer__claim-list">
          {block.items.map((item, index) => (
            <ClaimItemView key={index} item={item} />
          ))}
        </ul>
      </div>
    )
  }
  return null
}

// Corpo da avaliação completa (answer_blocks / afirmações não avaliadas /
// texto canônico) -- usado como resposta de profundidade 0 quando não há
// resposta principal, e dentro dos detalhes (profundidade 1) quando há.
function CompleteAnswerBody({
  finalAnswer,
  level,
}: {
  finalAnswer: FinalAnswerPublic
  level: HeadingLevel
}) {
  const structuredBlocks = isSupportedAnswerBlocks(finalAnswer.answer_blocks)
    ? finalAnswer.answer_blocks
    : null

  const unevaluatedClaims =
    finalAnswer.status === 'deterministic_no_verdict' &&
    finalAnswer.unevaluated_claims != null &&
    finalAnswer.unevaluated_claims.length > 0
      ? finalAnswer.unevaluated_claims
      : null

  const showDedicatedLimitations =
    finalAnswer.limitations.length > 0 &&
    (structuredBlocks !== null ||
      !FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS.has(finalAnswer.status))

  const summary =
    structuredBlocks !== null
      ? summarizeAnswerVerdicts(structuredBlocks)
      : unevaluatedClaims !== null
        ? summarizeUnevaluatedClaims(unevaluatedClaims.length)
        : null

  return (
    <>
      {summary !== null && <p className="final-answer__summary">{summary}</p>}
      <div className="final-answer__text">
        {structuredBlocks !== null ? (
          structuredBlocks.map((block, index) => (
            <AnswerBlockView key={index} block={block} level={level} />
          ))
        ) : unevaluatedClaims !== null ? (
          <UnevaluatedClaimsDisclosure claims={unevaluatedClaims} />
        ) : (
          splitAnswerParagraphs(finalAnswer.answer_text).map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))
        )}
      </div>
      {showDedicatedLimitations && (
        <LimitationsList heading="Limitações" items={finalAnswer.limitations} level={level} />
      )}
    </>
  )
}

function PrimaryAnswerBody({ primary, level }: { primary: PrimaryAnswerPublic; level: HeadingLevel }) {
  return (
    <>
      <p className="final-answer__lead-in">{primary.lead_in}</p>
      <div className="final-answer__text">
        {primary.sections.map((section) => (
          <div className="final-answer__primary-section" key={section.role}>
            <Heading level={level}>{section.heading}</Heading>
            <ul className="final-answer__primary-list">
              {section.items.map((item) => (
                <li key={item.claim_id} className="final-answer__primary-item">
                  <span className="final-answer__primary-claim">{item.claim_text}</span>{' '}
                  <span className="final-answer__primary-verdict">({item.verdict_label})</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {primary.limitations.length > 0 && (
        <LimitationsList heading="Limitações registradas" items={primary.limitations} level={level} />
      )}
      <p className="final-answer__scope-note">{primary.scope_note}</p>
    </>
  )
}

function AnswerHeader({ copyText }: { copyText: string }) {
  return (
    <div className="final-answer__header">
      {/* tabIndex -1: destino programático do "Voltar à resposta" da inspeção. */}
      <h2 id="final-answer-heading" tabIndex={-1}>
        Resposta
      </h2>
      <CopyAnswerButton text={copyText} />
    </div>
  )
}

export function FinalAnswerView({ finalAnswer }: FinalAnswerViewProps) {
  const presentation = selectPresentation(finalAnswer)

  if (presentation.kind === 'realization') {
    const { realization } = presentation
    return (
      <section aria-labelledby="final-answer-heading" className="final-answer final-answer--natural">
        <AnswerHeader copyText={realization.rendered_text} />
        <div className="final-answer__text">
          {realization.blocks.map((block, index) => (
            <p key={index}>{block.text}</p>
          ))}
        </div>
        <p className="final-answer__scope-note">
          Texto redigido por um modelo a partir das afirmações selecionadas e avaliadas. Uma revisão
          indicou consistência com a resposta estruturada; não é verificação externa nem garantia de
          verdade.
        </p>
        {finalAnswer.limitations.length > 0 && (
          <LimitationsList heading="Limitações registradas" items={finalAnswer.limitations} level={3} />
        )}
      </section>
    )
  }

  if (presentation.kind === 'natural') {
    const { natural } = presentation
    // O texto natural já inclui as limitações registradas e o aviso de
    // escopo (renderização determinística da resposta principal).
    return (
      <section aria-labelledby="final-answer-heading" className="final-answer final-answer--natural">
        <AnswerHeader copyText={natural.rendered_text} />
        <div className="final-answer__text">
          {splitAnswerParagraphs(natural.rendered_text).map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>
      </section>
    )
  }

  if (presentation.kind === 'primary') {
    const { primary } = presentation
    return (
      <section aria-labelledby="final-answer-heading" className="final-answer final-answer--primary">
        <AnswerHeader copyText={primary.rendered_text} />
        <PrimaryAnswerBody primary={primary} level={3} />
      </section>
    )
  }

  return (
    <section aria-labelledby="final-answer-heading" className="final-answer">
      <AnswerHeader copyText={finalAnswer.answer_text} />
      <CompleteAnswerBody finalAnswer={finalAnswer} level={3} />
    </section>
  )
}

// Profundidade 1: o que está por trás da resposta mostrada, com o vocabulário
// preciso (juiz, avaliação, resposta principal) -- aqui o usuário pediu pra
// ver como a resposta foi produzida.
export function AnswerAssessmentDetails({ finalAnswer }: FinalAnswerViewProps) {
  const presentation = selectPresentation(finalAnswer)
  const showStructured = presentation.kind === 'realization' || presentation.kind === 'natural'

  return (
    <div className="answer-details">
      <p className="answer-details__provenance">
        <span className="answer-details__provenance-label">Como a resposta foi montada:</span>{' '}
        {formatFinalAnswerStatus(finalAnswer.status)}.
      </p>

      {showStructured && (
        <section aria-labelledby="structured-answer-heading" className="answer-details__section">
          <div className="final-answer__header">
            <h3 id="structured-answer-heading">Resposta principal (estruturada)</h3>
            <CopyAnswerButton text={presentation.primary.rendered_text} label="Copiar resposta principal" />
          </div>
          <PrimaryAnswerBody primary={presentation.primary} level={4} />
        </section>
      )}

      {presentation.kind !== 'complete' && (
        <section aria-labelledby="complete-assessment-heading" className="answer-details__section">
          <div className="final-answer__header">
            <h3 id="complete-assessment-heading">Avaliação completa</h3>
            <CopyAnswerButton text={finalAnswer.answer_text} label="Copiar avaliação completa" />
          </div>
          <details className="final-answer__complete">
            <summary>
              Ver avaliação completa ({presentation.primary.assessed_claim_count}{' '}
              {presentation.primary.assessed_claim_count === 1
                ? 'afirmação avaliada'
                : 'afirmações avaliadas'}
              )
            </summary>
            <CompleteAnswerBody finalAnswer={finalAnswer} level={4} />
          </details>
        </section>
      )}
    </div>
  )
}
