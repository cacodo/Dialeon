// UI Slice 2 -- a resposta final ganhou quebra tipográfica em parágrafos
// (splitAnswerParagraphs), preservando exatamente o texto/limitations/
// status já existentes -- nunca reinterpretando o conteúdo.
//
// UI Slice 3 -- quando `answer_blocks` existe, a resposta renderiza
// elementos semânticos reais (h3/ul/li) diretamente do JSON tipado --
// cobertura de heading/lista genuínos, conteúdo NÃO CONFIÁVEL inerte, e
// fallback pra `splitAnswerParagraphs` quando `answer_blocks` é `null`.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AnswerAssessmentDetails, FinalAnswerView } from '../FinalAnswerView'
import type { AnswerBlockPublic, AnswerVerdictLabel, FinalAnswerPublic } from '../../api/types'

function makeFinalAnswer(overrides: Partial<FinalAnswerPublic> = {}): FinalAnswerPublic {
  return {
    answer_text: 'Brasília é a capital do Brasil.',
    answer_blocks: null,
    limitations: [],
    status: 'llm_planned',
    editor_model: 'claude-sonnet-5',
    editor_model_identity_source: 'provider_reported',
    judge_confidence: 0.8,
    ...overrides,
  }
}

describe('FinalAnswerView', () => {
  it('renderiza uma resposta de bloco único como um único parágrafo', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer()} />)

    const paragraph = screen.getByText('Brasília é a capital do Brasil.')
    expect(paragraph.tagName).toBe('P')
  })

  it('quebra blocos separados por linha em branco em parágrafos distintos, sem alterar o texto', () => {
    const answerText =
      'Resultado da avaliação do debate:\n\n' +
      'Conclusões sustentadas pelo debate:\n- A receita cresceu 12% em 2025.\n\n' +
      'Limitações do debate:\n- Só uma rodada de debate.'
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_text: answerText })} />)

    expect(screen.getByText('Resultado da avaliação do debate:').tagName).toBe('P')
    expect(
      screen.getByText((_, node) => node?.textContent === 'Conclusões sustentadas pelo debate:\n- A receita cresceu 12% em 2025.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText((_, node) => node?.textContent === 'Limitações do debate:\n- Só uma rodada de debate.'),
    ).toBeInTheDocument()
  })

  it('nunca inventa parágrafo quando a resposta é uma única linha (nenhum \\n\\n presente)', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_text: 'Uma linha só.' })} />)

    expect(screen.getAllByText('Uma linha só.')).toHaveLength(1)
  })

  it('continua mostrando limitações quando presentes (status histórico llm_composed, sem garantia sobre o que answer_text já contém)', () => {
    // Achado 5 do repair -- status explícito 'llm_composed' de propósito:
    // é o único caso (prosa livre histórica) onde a seção dedicada
    // SEMPRE deve continuar aparecendo no fallback de texto, porque não
    // há como saber se answer_text já a menciona. Ver
    // FALLBACK_TEXT_STATUSES_THAT_ALREADY_INCLUDE_LIMITATIONS.
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'llm_composed',
          limitations: ['Só uma rodada de debate.'],
        })}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Limitações' })).toBeInTheDocument()
    expect(screen.getByText('Só uma rodada de debate.')).toBeInTheDocument()
  })

  it('nunca mostra a seção de limitações quando a lista está vazia', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ limitations: [] })} />)

    expect(screen.queryByRole('heading', { name: 'Limitações' })).not.toBeInTheDocument()
  })
})

describe('FinalAnswerView -- answer_blocks (UI Slice 3)', () => {
  const blocks: AnswerBlockPublic[] = [
    { kind: 'paragraph', text: 'Resultado da avaliação do debate:' },
    {
      kind: 'claim_section',
      heading: 'Conclusões sustentadas pelo debate:',
      items: [
        {
          claim_text: 'A receita cresceu 12% em 2025.',
          verdict_label: 'sustentada pelo debate',
          explanation: 'Múltiplos participantes concordam.',
          source_relationship_note:
            'Relação com a fonte fornecida: a fonte aponta na MESMA direção da avaliação do debate.',
        },
      ],
    },
  ]

  it('renderiza o parágrafo de abertura como <p>', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    expect(screen.getByText('Resultado da avaliação do debate:').tagName).toBe('P')
  })

  it('renderiza o heading da seção como <h3> real, aninhado sob o <h2> "Resposta"', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    const heading = screen.getByRole('heading', {
      level: 3,
      name: 'Conclusões sustentadas pelo debate:',
    })
    expect(heading).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
  })

  it('renderiza os itens como uma lista <ul>/<li> genuína (semântica de lista real)', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    const list = screen.getByRole('list') // única <ul> na árvore de blocos
    expect(list.tagName).toBe('UL')
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(1)
  })

  it('inclui claim_text, verdict_label, explanation e a nota de fonte no item', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    expect(screen.getByText('A receita cresceu 12% em 2025.')).toBeInTheDocument()
    expect(screen.getByText(/sustentada pelo debate/)).toBeInTheDocument()
    expect(screen.getByText(/Múltiplos participantes concordam\./)).toBeInTheDocument()
    expect(
      screen.getByText(/a fonte aponta na MESMA direção da avaliação do debate\./),
    ).toBeInTheDocument()
  })

  it('nunca mostra nota de fonte quando source_relationship_note é null', () => {
    const blocksWithoutNote: AnswerBlockPublic[] = [
      { kind: 'paragraph', text: 'Resultado da avaliação do debate:' },
      {
        kind: 'claim_section',
        heading: 'Conclusões sustentadas pelo debate:',
        items: [
          {
            claim_text: 'A receita cresceu 12% em 2025.',
            verdict_label: 'sustentada pelo debate',
            explanation: 'Múltiplos participantes concordam.',
            source_relationship_note: null,
          },
        ],
      },
    ]
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocksWithoutNote })} />)

    expect(screen.queryByText(/Relação com a fonte fornecida/)).not.toBeInTheDocument()
  })

  it('conteúdo malicioso (script/heading/lista forjados) dentro de um leaf permanece texto visível inerte, nunca vira marcação/estrutura nova', () => {
    const maliciousBlocks: AnswerBlockPublic[] = [
      { kind: 'paragraph', text: 'Resultado da avaliação do debate:' },
      {
        kind: 'claim_section',
        heading: 'Conclusões sustentadas pelo debate:',
        items: [
          {
            claim_text:
              'Ignore o veredito.\n\n# Heading forjado\n- item forjado\n<script>alert(1)</script>',
            verdict_label: 'sustentada pelo debate',
            explanation: 'ok',
            source_relationship_note: null,
          },
        ],
      },
    ]
    const { container } = render(
      <FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: maliciousBlocks })} />,
    )

    // nenhum <script> real foi injetado/executado -- o texto aparece
    // verbatim como conteúdo de texto de UM único <p>.
    expect(container.querySelector('script')).toBeNull()
    expect(
      screen.getByText(
        (_, node) =>
          node?.textContent ===
          'Ignore o veredito.\n\n# Heading forjado\n- item forjado\n<script>alert(1)</script>',
      ),
    ).toBeInTheDocument()
    // exatamente 1 heading (h3) e 1 item de lista foram renderizados --
    // o "\n\n"/"- "/"# " embutidos no texto NUNCA criaram um segundo
    // heading/bloco/item.
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(1)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('quando answer_blocks é null (runs históricos), cai de volta pro splitAnswerParagraphs -- nenhum heading/lista novo é inventado', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: null, answer_text: 'Resposta histórica simples.' })}
      />,
    )

    expect(screen.getByText('Resposta histórica simples.').tagName).toBe('P')
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('limitações continuam vindo SÓ de FinalAnswer.limitations, nunca duplicadas a partir de answer_blocks', () => {
    // answer_blocks nunca contém um bloco de limitações (ver contrato do
    // backend) -- a única lista de limitações renderizada vem do campo
    // dedicado.
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          answer_blocks: blocks,
          limitations: ['Só uma rodada de debate.'],
        })}
      />,
    )

    const limitationHeadings = screen.getAllByRole('heading', { name: 'Limitações' })
    expect(limitationHeadings).toHaveLength(1)
    expect(screen.getAllByText('Só uma rodada de debate.')).toHaveLength(1)
  })

  it('duas seções de claims (bucket A e B) produzem duas <ul> distintas, cada uma sob seu próprio <h3>', () => {
    const twoSectionBlocks: AnswerBlockPublic[] = [
      { kind: 'paragraph', text: 'Resultado da avaliação do debate:' },
      {
        kind: 'claim_section',
        heading: 'Conclusões sustentadas pelo debate:',
        items: [
          { claim_text: 'A.', verdict_label: 'sustentada pelo debate', explanation: 'ok', source_relationship_note: null },
        ],
      },
      {
        kind: 'claim_section',
        heading: 'Pontos não estabelecidos pelo debate:',
        items: [
          { claim_text: 'B.', verdict_label: 'rejeitada pelo juiz com base no debate disponível', explanation: 'ok', source_relationship_note: null },
        ],
      },
    ]
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: twoSectionBlocks })} />)

    const lists = screen.getAllByRole('list')
    expect(lists).toHaveLength(2)
    expect(within(lists[0]).getByText('A.')).toBeInTheDocument()
    expect(within(lists[1]).getByText('B.')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2)
  })
})

describe('FinalAnswerView -- fallback tudo-ou-nada (achado 4 do repair)', () => {
  it('answer_blocks vazio ([]) cai pro fallback completo de texto, nunca é tratado como "sem blocos" silencioso nem renderizado como estrutura vazia', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: [], answer_text: 'Texto de fallback.' })}
      />,
    )

    expect(screen.getByText('Texto de fallback.').tagName).toBe('P')
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('um bloco de kind desconhecido misturado com blocos conhecidos derruba TUDO pro fallback de texto -- nunca renderiza parcialmente os blocos conhecidos', () => {
    const mixedBlocks = [
      { kind: 'paragraph', text: 'Parágrafo conhecido.' },
      { kind: 'quote', text: 'Um bloco de um contrato futuro que este frontend não entende.' },
    ] as unknown as AnswerBlockPublic[]
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: mixedBlocks, answer_text: 'Texto de fallback completo.' })}
      />,
    )

    // NUNCA o parágrafo conhecido isolado -- o fallback INTEIRO substitui
    // a tentativa de renderização parcial.
    expect(screen.queryByText('Parágrafo conhecido.')).not.toBeInTheDocument()
    expect(screen.getByText('Texto de fallback completo.').tagName).toBe('P')
  })

  it('answer_blocks contendo só um kind desconhecido também cai pro fallback completo', () => {
    const unknownOnlyBlocks = [
      { kind: 'quote', text: 'Bloco de contrato futuro.' },
    ] as unknown as AnswerBlockPublic[]
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: unknownOnlyBlocks, answer_text: 'Texto de fallback.' })}
      />,
    )

    expect(screen.getByText('Texto de fallback.').tagName).toBe('P')
    expect(screen.queryByText('Bloco de contrato futuro.')).not.toBeInTheDocument()
  })
})

describe('FinalAnswerView -- política de limitações no fallback de texto (achado 5 do repair)', () => {
  it('suprime a seção dedicada quando status=llm_planned e answer_blocks está ausente (answer_text já embute o mesmo conteúdo)', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'llm_planned',
          answer_blocks: null,
          limitations: ['Só uma rodada de debate.'],
        })}
      />,
    )

    expect(screen.queryByRole('heading', { name: 'Limitações' })).not.toBeInTheDocument()
  })

  it('suprime a seção dedicada quando status=deterministic_from_verdict e answer_blocks está ausente', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'deterministic_from_verdict',
          answer_blocks: null,
          limitations: ['Só uma rodada de debate.'],
        })}
      />,
    )

    expect(screen.queryByRole('heading', { name: 'Limitações' })).not.toBeInTheDocument()
  })

  it('NUNCA suprime quando status=deterministic_no_verdict (texto sintético diferente, nunca embutido em answer_text)', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'deterministic_no_verdict',
          answer_blocks: null,
          limitations: ['Avaliação final não realizada: motivo X.'],
        })}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Limitações' })).toBeInTheDocument()
  })

  it('nunca suprime quando answer_blocks estruturados estão presentes, mesmo pra um status que suprimiria no fallback de texto', () => {
    const structuredBlocks: AnswerBlockPublic[] = [{ kind: 'paragraph', text: 'Abertura.' }]
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'llm_planned',
          answer_blocks: structuredBlocks,
          limitations: ['Só uma rodada de debate.'],
        })}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Limitações' })).toBeInTheDocument()
  })
})

describe('FinalAnswerView -- unevaluated_claims (Structured Unevaluated Claims + Progressive Disclosure)', () => {
  const claims = [
    'Afirmação A do modelo participante.',
    'Afirmação B do modelo participante.',
    'Afirmação C do modelo participante.',
  ]

  function makeNoVerdictAnswer(overrides: Partial<FinalAnswerPublic> = {}): FinalAnswerPublic {
    return makeFinalAnswer({
      status: 'deterministic_no_verdict',
      answer_blocks: null,
      answer_text:
        'A avaliação final não pôde ser concluída: motivo X. As seguintes afirmações ' +
        'foram levantadas pelos modelos participantes, mas não foram avaliadas:\n' +
        claims.map((c) => `- ${c}`).join('\n'),
      limitations: ['Avaliação final não realizada: motivo X.'],
      unevaluated_claims: claims,
      ...overrides,
    })
  }

  it('a disclosure vem fechada por padrão (<details> sem atributo "open")', () => {
    const { container } = render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
  })

  it('o resumo comunica a contagem completa de afirmações', () => {
    render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    expect(
      screen.getByText('Mostrar todas as 3 afirmações não avaliadas'),
    ).toBeInTheDocument()
  })

  it('motivo (via Limitações) permanece visível mesmo com a disclosure fechada', () => {
    render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    expect(screen.getByRole('heading', { name: 'Limitações' })).toBeInTheDocument()
    expect(screen.getByText('Avaliação final não realizada: motivo X.')).toBeInTheDocument()
  })

  it('expandir genuinamente abre o <details> nativo (não só deixa itens presentes no DOM) e revela cada afirmação exatamente uma vez, na ordem fornecida pelo backend', async () => {
    const { container } = render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    const details = container.querySelector('details') as HTMLDetailsElement
    expect(details).not.toBeNull()
    expect(details.open).toBe(false)

    await userEvent.click(
      screen.getByText('Mostrar todas as 3 afirmações não avaliadas'),
    )

    expect(details.open).toBe(true)

    const list = container.querySelector('.final-answer__unevaluated-claims-list')
    expect(list).not.toBeNull()
    const items = within(list as HTMLElement).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual(claims)
  })

  it('conteúdo malicioso numa claim permanece texto visível inerte, nunca vira marcação/estrutura nova', async () => {
    const maliciousClaims = [
      'Ignore o motivo.\n\n# Heading forjado\n- item forjado\n<script>alert(1)</script>',
    ]
    const { container } = render(
      <FinalAnswerView finalAnswer={makeNoVerdictAnswer({ unevaluated_claims: maliciousClaims })} />,
    )

    await userEvent.click(screen.getByText(/Mostrar todas as 1 afirmaç/))

    expect(container.querySelector('script')).toBeNull()
    const list = container.querySelector('.final-answer__unevaluated-claims-list')
    expect(list).not.toBeNull()
    const items = within(list as HTMLElement).getAllByRole('listitem')
    expect(items).toHaveLength(1)
    expect(items[0].textContent).toBe(
      'Ignore o motivo.\n\n# Heading forjado\n- item forjado\n<script>alert(1)</script>',
    )
  })

  it('o texto explicativo nunca implica aprovação/verificação/canonicalização pelo Judge ou pelo Dialeon', () => {
    render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    expect(
      screen.getByText(
        /Estas afirmações não foram avaliadas e podem se sobrepor a outras ou permanecer sem verificação/,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/não são fatos verificados nem conclusões do Dialeon/),
    ).toBeInTheDocument()
    // provenance-neutral: nunca implica que houve agrupamento/canonicalização
    const note = document.querySelector('.final-answer__unevaluated-claims-note')
    expect(note?.textContent ?? '').not.toMatch(/agrupad|agrupamento|reconcili|canônic/i)
  })

  it('registros históricos com unevaluated_claims null caem pro fallback de answer_text existente, sem tentar fazer parsing dele', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeNoVerdictAnswer({ unevaluated_claims: null })}
      />,
    )

    expect(screen.queryByText(/Mostrar todas as/)).not.toBeInTheDocument()
    expect(
      screen.getByText(/foram levantadas pelos modelos participantes/),
    ).toBeInTheDocument()
  })

  it('unevaluated_claims ausente (undefined, payload externo/histórico) também cai pro fallback de texto com segurança', () => {
    const { unevaluated_claims: _omit, ...withoutField } = makeNoVerdictAnswer()
    render(<FinalAnswerView finalAnswer={withoutField as FinalAnswerPublic} />)

    expect(screen.queryByText(/Mostrar todas as/)).not.toBeInTheDocument()
    expect(
      screen.getByText(/foram levantadas pelos modelos participantes/),
    ).toBeInTheDocument()
  })

  it('respostas normais com veredito continuam inalteradas mesmo que unevaluated_claims esteja presente por engano', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'llm_planned',
          answer_text: 'Resposta normal com veredito.',
          unevaluated_claims: claims,
        })}
      />,
    )

    expect(screen.getByText('Resposta normal com veredito.').tagName).toBe('P')
    expect(screen.queryByText(/Mostrar todas as/)).not.toBeInTheDocument()
  })

  it('nunca renderiza answer_text e a lista estruturada ao mesmo tempo (sem duplicação)', () => {
    render(<FinalAnswerView finalAnswer={makeNoVerdictAnswer()} />)

    expect(
      screen.queryByText(/As seguintes afirmações foram levantadas pelos modelos participantes/),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText('Mostrar todas as 3 afirmações não avaliadas'),
    ).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Chat-first answer surface: resumo determinístico, achados escaneáveis,
// "Por quê?" recolhido, Copiar resposta.
// ---------------------------------------------------------------------------

type Verdict = AnswerVerdictLabel

function item(claim_text: string, verdict_label: Verdict, note: string | null = null) {
  return {
    claim_text,
    verdict_label,
    explanation: `Explicação de: ${claim_text}`,
    source_relationship_note: note,
  }
}

function sectionsBlocks(
  a: ReturnType<typeof item>[],
  b: ReturnType<typeof item>[] = [],
): AnswerBlockPublic[] {
  const blocks: AnswerBlockPublic[] = [{ kind: 'paragraph', text: 'Resultado da avaliação do debate:' }]
  if (a.length > 0) {
    blocks.push({ kind: 'claim_section', heading: 'Conclusões sustentadas pelo debate:', items: a })
  }
  if (b.length > 0) {
    blocks.push({ kind: 'claim_section', heading: 'Pontos não estabelecidos pelo debate:', items: b })
  }
  return blocks
}

describe('FinalAnswerView -- resumo determinístico', () => {
  const summaryText = (container: HTMLElement) =>
    container.querySelector('.final-answer__summary')?.textContent ?? null

  it('conta os vereditos na ordem fixa do vocabulário, omitindo zeros', () => {
    const blocks = sectionsBlocks(
      [
        item('A1', 'sustentada pelo debate'),
        item('A2', 'sustentada pelo debate'),
        item('A3', 'parcialmente sustentada, com ressalvas'),
      ],
      [
        item('B1', 'sem informação suficiente para decidir'),
        item('B2', 'rejeitada pelo juiz com base no debate disponível'),
        item('B3', 'com posições conflitantes, não resolvida'),
      ],
    )
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    expect(summaryText(container)).toBe(
      '6 afirmações avaliadas: 2 sustentadas, 1 parcialmente sustentada, 1 rejeitada pelo juiz, 1 com posições conflitantes, 1 sem informação suficiente.',
    )
  })

  it('usa o singular para uma única afirmação', () => {
    const { container } = render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: sectionsBlocks([item('A', 'sustentada pelo debate')]) })}
      />,
    )

    expect(summaryText(container)).toBe('1 afirmação avaliada: 1 sustentada.')
  })

  it('é só contagem: nunca escolhe "conclusão principal", nem ranqueia, nem inventa confiança', () => {
    const { container } = render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          answer_blocks: sectionsBlocks([item('A', 'sustentada pelo debate'), item('B', 'sustentada pelo debate')]),
          judge_confidence: 0.93,
        })}
      />,
    )

    expect(summaryText(container)).not.toMatch(/principal|mais importante|confiança|93|%/i)
  })

  it('rótulo fora do vocabulário conhecido => resumo omitido (nunca adivinha)', () => {
    const blocks = sectionsBlocks([item('A', 'sustentada pelo debate')])
    ;(blocks[1] as { items: { verdict_label: string }[] }).items.push({
      claim_text: 'X',
      verdict_label: 'rótulo novo inesperado',
      explanation: 'e',
      source_relationship_note: null,
    } as never)
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    expect(container.querySelector('.final-answer__summary')).toBeNull()
  })

  it('sem veredito: resume a contagem de afirmações sem avaliação, sem alegar nada sobre elas', () => {
    const { container } = render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({
          status: 'deterministic_no_verdict',
          answer_blocks: null,
          unevaluated_claims: ['Um.', 'Dois.', 'Três.'],
        })}
      />,
    )

    expect(summaryText(container)).toBe('Sem avaliação final — 3 afirmações ficaram sem avaliação.')
  })

  it('resposta histórica sem answer_blocks e sem lista estruturada não ganha resumo', () => {
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer()} />)

    expect(container.querySelector('.final-answer__summary')).toBeNull()
    expect(screen.getByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
  })
})

describe('FinalAnswerView -- achados escaneáveis', () => {
  const blocks = sectionsBlocks(
    [
      item('Primeira claim.', 'sustentada pelo debate', 'Relação com a fonte: mesma direção.'),
      item('Segunda claim.', 'parcialmente sustentada, com ressalvas'),
    ],
    [item('Terceira claim.', 'sem informação suficiente para decidir')],
  )

  it('mantém ordem e texto exatos das claims e sempre mostra claim + veredito', () => {
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)

    const texts = [...container.querySelectorAll('.final-answer__claim-text')].map((n) => n.textContent)
    expect(texts).toEqual(['Primeira claim.', 'Segunda claim.', 'Terceira claim.'])
    const verdicts = [...container.querySelectorAll('.final-answer__claim-verdict-value')].map((n) => n.textContent)
    expect(verdicts).toEqual([
      'sustentada pelo debate',
      'parcialmente sustentada, com ressalvas',
      'sem informação suficiente para decidir',
    ])
  })

  it('explicação e nota de fonte ficam num <details> nativo recolhido; abrir revela o texto exato', async () => {
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: blocks })} />)
    const details = container.querySelectorAll('details.final-answer__claim-why')
    expect(details).toHaveLength(3)
    details.forEach((d) => expect(d).not.toHaveAttribute('open'))
    expect(screen.getAllByText('Por quê?')).toHaveLength(3)

    await userEvent.click(screen.getAllByText('Por quê?')[0])

    expect(details[0]).toHaveAttribute('open')
    expect(details[1]).not.toHaveAttribute('open')
    expect(within(details[0] as HTMLElement).getByText('Explicação de: Primeira claim.')).toBeVisible()
    expect(within(details[0] as HTMLElement).getByText(/mesma direção/)).toBeVisible()
  })

  it('a explicação fica oculta antes de abrir, mas claim, veredito e limitações continuam visíveis', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ answer_blocks: blocks, limitations: ['Limitação global do debate.'] })}
      />,
    )

    expect(screen.getByText('Explicação de: Primeira claim.')).not.toBeVisible()
    expect(screen.getByText('Primeira claim.')).toBeVisible()
    expect(screen.getAllByText('sustentada pelo debate')[0]).toBeVisible()
    expect(screen.getByText('Limitação global do debate.')).toBeVisible()
    expect(screen.getByRole('heading', { level: 3, name: 'Limitações' })).toBeInTheDocument()
  })

  it('conteúdo não confiável na explicação continua texto inerte (sem marcação nova)', () => {
    const evil = sectionsBlocks([
      {
        claim_text: '<img src=x onerror=alert(1)> **negrito** [link](http://x)',
        verdict_label: 'sustentada pelo debate',
        explanation: '<script>alert(1)</script> # heading',
        source_relationship_note: null,
      },
    ])
    const { container } = render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: evil })} />)

    expect(container.querySelector('img, script, a, strong')).toBeNull()
    expect(container.querySelector('.final-answer__claim-text')?.textContent).toBe(
      '<img src=x onerror=alert(1)> **negrito** [link](http://x)',
    )
    expect(container.querySelector('.final-answer__claim-explanation')?.textContent).toBe(
      '<script>alert(1)</script> # heading',
    )
  })
})

describe('FinalAnswerView -- Copiar resposta', () => {
  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')

  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as unknown as { clipboard?: unknown }).clipboard
  })

  function setClipboard(value: unknown) {
    Object.defineProperty(navigator, 'clipboard', { value, configurable: true })
  }

  const answer = () =>
    makeFinalAnswer({
      answer_text: 'TEXTO CANÔNICO\n\ncom limitações embutidas e <b>tags</b> literais.',
      answer_blocks: sectionsBlocks([item('Outra coisa renderizada.', 'sustentada pelo debate')]),
    })

  it('copia EXATAMENTE o answer_text canônico (não os blocos renderizados) e anuncia sucesso', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    render(<FinalAnswerView finalAnswer={answer()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith('TEXTO CANÔNICO\n\ncom limitações embutidas e <b>tags</b> literais.')
    expect(await screen.findByText('Resposta copiada.')).toBeInTheDocument()
    expect(screen.getByText('Resposta copiada.').closest('[role="status"]')).not.toBeNull()
  })

  it('falha do clipboard mostra feedback e nunca quebra a resposta', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('negado')) })
    render(<FinalAnswerView finalAnswer={answer()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))

    expect(await screen.findByText(/não foi possível copiar automaticamente/i)).toBeInTheDocument()
    expect(screen.getByText('Outra coisa renderizada.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
  })

  it('sem API de clipboard (contexto inseguro) também cai no feedback de falha', async () => {
    setClipboard(undefined)
    render(<FinalAnswerView finalAnswer={answer()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))

    expect(await screen.findByText(/não foi possível copiar automaticamente/i)).toBeInTheDocument()
  })

  it('também existe (copiando answer_text) para resposta histórica sem answer_blocks', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    render(<FinalAnswerView finalAnswer={makeFinalAnswer()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))

    expect(writeText).toHaveBeenCalledWith('Brasília é a capital do Brasil.')
  })
})

// ---------------------------------------------------------------------------
// Resposta PRINCIPAL (seleção tipada por ids, renderizada no backend)
// ---------------------------------------------------------------------------

import type { PrimaryAnswerPublic } from '../../api/types'

function makePrimary(overrides: Partial<PrimaryAnswerPublic> = {}): PrimaryAnswerPublic {
  return {
    contract_version: 'primary_answer_plan_v1',
    based_on_verdict_id: 'v-1',
    lead_in: 'Resposta principal, restrita ao que o debate e o Judge avaliaram (não é verificação externa):',
    sections: [
      {
        role: 'central_conclusion',
        heading: 'Conclusão central:',
        items: [
          { claim_id: 'c1', claim_text: 'Um SaaS é a melhor escolha.', verdict_label: 'sustentada pelo debate' },
        ],
      },
      {
        role: 'uncertainties',
        heading: 'Incertezas e ressalvas:',
        items: [
          {
            claim_id: 'c2',
            claim_text: 'O fornecedor pode falir.',
            verdict_label: 'sem informação suficiente para decidir',
          },
        ],
      },
    ],
    limitations: ['Sem dados empíricos.'],
    assessed_claim_count: 3,
    selected_claim_count: 2,
    omitted_not_established_count: 0,
    scope_note: 'Seleção apresentacional: 2 de 3 afirmações avaliadas pelo Judge. A avaliação completa lista todas.',
    rendered_text: 'TEXTO CANÔNICO DA RESPOSTA PRINCIPAL\n\n- Um SaaS é a melhor escolha. (sustentada pelo debate)',
    ...overrides,
  }
}

describe('FinalAnswerView -- resposta principal', () => {
  const completeBlocks = sectionsBlocks(
    [item('Claim da avaliação completa.', 'sustentada pelo debate')],
    [item('Claim não estabelecida.', 'sem informação suficiente para decidir')],
  )
  const withPrimary = (primary: PrimaryAnswerPublic | null = makePrimary()) =>
    makeFinalAnswer({
      answer_text: 'AVALIAÇÃO COMPLETA CANÔNICA',
      answer_blocks: completeBlocks,
      limitations: ['Sem dados empíricos.'],
      primary_answer: primary,
    })

  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as unknown as { clipboard?: unknown }).clipboard
  })

  it('a resposta principal domina: título "Resposta", seções, rótulo do veredito junto da claim e escopo', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withPrimary()} />)

    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Conclusão central:' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 3, name: 'Incertezas e ressalvas:' })).toBeVisible()
    expect(screen.getByText('Um SaaS é a melhor escolha.')).toBeVisible()
    expect(screen.getByText('(sustentada pelo debate)')).toBeVisible()
    expect(screen.getByText('(sem informação suficiente para decidir)')).toBeVisible()
    expect(screen.getByText(/Seleção apresentacional: 2 de 3/)).toBeVisible()
    expect(screen.getByText(/não é verificação externa/)).toBeVisible()
    // limitações continuam visíveis (a cópia dentro da avaliação completa fica recolhida)
    const limitations = container.querySelector('.final-answer--primary > .final-answer__limitations')
    expect(limitations).not.toBeNull()
    expect(within(limitations as HTMLElement).getByText('Sem dados empíricos.')).toBeVisible()
  })

  it('não despeja o muro de claims sob a resposta principal: a avaliação completa fica fora da resposta (profundidade 1) e recolhida', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withPrimary()} />)

    // Profundidade 0: só a resposta -- a avaliação completa não está ali.
    expect(container.querySelector('details.final-answer__complete')).toBeNull()
    expect(screen.queryByText('Claim da avaliação completa.')).not.toBeInTheDocument()

    // Profundidade 1: presente, mas recolhida até um gesto explícito.
    const details = render(<AnswerAssessmentDetails finalAnswer={withPrimary()} />).container.querySelector(
      'details.final-answer__complete',
    ) as HTMLDetailsElement
    expect(details).not.toBeNull()
    expect(details.open).toBe(false)
    expect(screen.getByText('Claim da avaliação completa.')).not.toBeVisible()
    expect(screen.getByText(/Ver avaliação completa \(3 afirmações avaliadas\)/)).toBeVisible()
  })

  it('a investigação completa continua alcançável: abrir revela a avaliação completa intacta', async () => {
    const { container } = render(<AnswerAssessmentDetails finalAnswer={withPrimary()} />)

    await userEvent.click(screen.getByText(/Ver avaliação completa/))

    expect((container.querySelector('details.final-answer__complete') as HTMLDetailsElement).open).toBe(true)
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
    expect(screen.getByText('Claim não estabelecida.')).toBeVisible()
    expect(screen.getAllByText('Por quê?').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Copiar avaliação completa' })).toBeInTheDocument()
  })

  it('sem resposta principal (null/ausente/histórico) o comportamento de fallback é o de sempre', () => {
    for (const primary of [null, undefined]) {
      const { container, unmount } = render(
        <FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: completeBlocks, primary_answer: primary })} />,
      )
      expect(container.querySelector('.final-answer__complete')).toBeNull()
      expect(container.querySelector('.final-answer--primary')).toBeNull()
      expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
      expect(screen.getByRole('button', { name: 'Copiar resposta' })).toBeInTheDocument()
      unmount()
    }
  })

  it('"Copiar resposta" copia EXATAMENTE o texto canônico da resposta principal; a avaliação completa tem seu próprio botão', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(
      <>
        <FinalAnswerView finalAnswer={withPrimary()} />
        <AnswerAssessmentDetails finalAnswer={withPrimary()} />
      </>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))
    expect(writeText).toHaveBeenLastCalledWith(makePrimary().rendered_text)
    expect(await screen.findByText('Resposta copiada.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Copiar avaliação completa' }))
    expect(writeText).toHaveBeenLastCalledWith('AVALIAÇÃO COMPLETA CANÔNICA')
  })

  it('conteúdo não confiável na resposta principal continua texto inerte (sem Markdown/HTML/links)', () => {
    const evil = makePrimary({
      sections: [
        {
          role: 'central_conclusion',
          heading: 'Conclusão central:',
          items: [
            {
              claim_id: 'c1',
              claim_text: '<img src=x onerror=alert(1)> **negrito** [link](http://x) # título\n- item',
              verdict_label: 'sustentada pelo debate',
            },
          ],
        },
      ],
      limitations: ['<script>alert(1)</script>'],
    })
    const { container } = render(<FinalAnswerView finalAnswer={withPrimary(evil)} />)

    expect(container.querySelector('img, script, a, strong')).toBeNull()
    expect(container.querySelector('.final-answer__primary-claim')?.textContent).toBe(
      '<img src=x onerror=alert(1)> **negrito** [link](http://x) # título\n- item',
    )
    const primaryHeadings = screen
      .getAllByRole('heading', { level: 3 })
      .filter((h) => !h.closest('details'))
      .map((h) => h.textContent)
    expect(primaryHeadings).toEqual([
      'Conclusão central:',
      'Limitações registradas',
    ])
  })
})

// ---------------------------------------------------------------------------
// Resposta NATURAL (renderização conversacional determinística do
// PrimaryAnswer, backend: app/editor/natural_answer.py)
// ---------------------------------------------------------------------------

import type { NaturalAnswerPublic } from '../../api/types'

function makeNatural(overrides: Partial<NaturalAnswerPublic> = {}): NaturalAnswerPublic {
  return {
    renderer_contract_version: 'natural_answer_v1',
    based_on_verdict_id: 'v-1',
    rendered_text:
      'Um SaaS é a melhor escolha. (sustentada pelo debate).\n\n' +
      'Ficam registradas as seguintes incertezas e ressalvas: O fornecedor pode falir. ' +
      '(sem informação suficiente para decidir).\n\n' +
      'Seleção apresentacional: 2 de 3 afirmações avaliadas pelo Judge. A avaliação completa lista todas.',
    ...overrides,
  }
}

describe('FinalAnswerView -- resposta natural', () => {
  const completeBlocks = sectionsBlocks(
    [item('Claim da avaliação completa.', 'sustentada pelo debate')],
    [item('Claim não estabelecida.', 'sem informação suficiente para decidir')],
  )
  const withNatural = (
    natural: NaturalAnswerPublic | null = makeNatural(),
    primary: PrimaryAnswerPublic | null = makePrimary(),
  ) =>
    makeFinalAnswer({
      answer_text: 'AVALIAÇÃO COMPLETA CANÔNICA',
      answer_blocks: completeBlocks,
      limitations: ['Sem dados empíricos.'],
      primary_answer: primary,
      natural_answer: natural,
      natural_answer_presentation_eligible: natural != null,
    })

  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as unknown as { clipboard?: unknown }).clipboard
  })

  it('quando presente, a resposta natural domina: título "Resposta", texto conversacional em parágrafos, sem headings de seção', () => {
    render(<FinalAnswerView finalAnswer={withNatural()} />)

    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
    expect(screen.getByText('Um SaaS é a melhor escolha. (sustentada pelo debate).').tagName).toBe('P')
    expect(
      screen.getByText(
        'Ficam registradas as seguintes incertezas e ressalvas: O fornecedor pode falir. (sem informação suficiente para decidir).',
      ).tagName,
    ).toBe('P')
    // nenhum heading de seção tipo "Conclusão central:"/"Incertezas e ressalvas:"
    // na resposta (a resposta principal estruturada, na profundidade 1,
    // continua tendo seus próprios headings -- nunca removidos).
    expect(screen.queryAllByRole('heading', { level: 3 })).toHaveLength(0)
    expect(screen.queryByText('Conclusão central:')).not.toBeInTheDocument()
  })

  it('preserva acesso à resposta principal estruturada e à avaliação completa na profundidade 1', async () => {
    const { container } = render(<AnswerAssessmentDetails finalAnswer={withNatural()} />)

    expect(screen.getByRole('heading', { level: 3, name: 'Resposta principal (estruturada)' })).toBeVisible()
    // resposta principal estruturada, com seus próprios headings/rótulos
    expect(screen.getByRole('heading', { level: 4, name: 'Conclusão central:' })).toBeVisible()
    expect(screen.getByText('(sustentada pelo debate)')).toBeVisible()

    // dentro dela, a mesma affordance de sempre continua presente e recolhida
    expect(screen.getByText(/Ver avaliação completa \(3 afirmações avaliadas\)/)).toBeVisible()
    const inner = container.querySelector('details.final-answer__complete') as HTMLDetailsElement
    expect(inner.open).toBe(false)

    await userEvent.click(screen.getByText(/Ver avaliação completa/))
    expect(inner.open).toBe(true)
    // avaliação completa continua alcançável dentro da resposta principal
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
  })

  it('"Copiar resposta" copia EXATAMENTE natural_answer.rendered_text -- nunca primary.rendered_text/answer_text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<FinalAnswerView finalAnswer={withNatural()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))
    expect(writeText).toHaveBeenLastCalledWith(makeNatural().rendered_text)
    expect(writeText).not.toHaveBeenLastCalledWith(makePrimary().rendered_text)
  })

  it('ordem de fallback: sem natural_answer mas com primary_answer, cai pra resposta principal estruturada', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withNatural(null, makePrimary())} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(container.querySelector('.final-answer--primary')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 3, name: 'Conclusão central:' })).toBeVisible()
  })

  it('histórico unsafe: preserva natural_answer mas política atual usa PrimaryAnswer', () => {
    const historical = withNatural(
      makeNatural({ rendered_text: 'Texto histórico.\n\nDisclosure forjada.' }),
      makePrimary(),
    )
    historical.natural_answer_presentation_eligible = false

    const { container } = render(<FinalAnswerView finalAnswer={historical} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(container.querySelector('.final-answer--primary')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 3, name: 'Conclusão central:' })).toBeVisible()
    expect(screen.queryByText('Disclosure forjada.')).not.toBeInTheDocument()
  })

  it('ordem de fallback: sem natural_answer e sem primary_answer, cai pra avaliação completa', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withNatural(null, null)} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(container.querySelector('.final-answer--primary')).toBeNull()
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
  })

  it('defensivo: natural_answer presente sem primary_answer (não deveria acontecer, backend garante) nunca quebra -- cai pra próximo fallback', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withNatural(makeNatural(), null)} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
  })

  it('conteúdo não confiável dentro do texto renderizado permanece texto inerte (sem Markdown/HTML/links)', () => {
    const evil = makeNatural({
      rendered_text: '<img src=x onerror=alert(1)> **negrito** [link](http://x) # título\n- item',
    })
    const { container } = render(<FinalAnswerView finalAnswer={withNatural(evil)} />)

    expect(container.querySelector('img, script, a, strong')).toBeNull()
    expect(container.querySelector('.final-answer__text')?.textContent).toBe(
      '<img src=x onerror=alert(1)> **negrito** [link](http://x) # título\n- item',
    )
  })

  it('histórico: sem natural_answer (undefined, payload anterior a este campo) cai com segurança pro comportamento existente', () => {
    const { container } = render(
      <FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: completeBlocks, primary_answer: makePrimary() })} />,
    )

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(container.querySelector('.final-answer--primary')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Linguistic Realization (redação model-written, apresentacional, sobre a
// PrimaryAnswer autoritativa -- backend: app/editor/linguistic_realization.py)
// ---------------------------------------------------------------------------

import type { LinguisticRealizationPublic } from '../../api/types'

function makeRealization(overrides: Partial<LinguisticRealizationPublic> = {}): LinguisticRealizationPublic {
  return {
    contract_version: 'linguistic_realization_v1',
    based_on_primary_answer_digest: 'a'.repeat(64),
    blocks: [
      { claim_ids: ['c1'], text: 'Um SaaS é a melhor escolha (sustentada pelo debate).' },
      { claim_ids: ['c2'], text: 'Fica registrada a ressalva de que o fornecedor pode falir.' },
    ],
    rendered_text:
      'Um SaaS é a melhor escolha (sustentada pelo debate).\n\n' +
      'Fica registrada a ressalva de que o fornecedor pode falir.',
    ...overrides,
  }
}

describe('FinalAnswerView -- realização linguística', () => {
  const completeBlocks = sectionsBlocks(
    [item('Claim da avaliação completa.', 'sustentada pelo debate')],
    [item('Claim não estabelecida.', 'sem informação suficiente para decidir')],
  )
  const withRealization = (
    realization: LinguisticRealizationPublic | null = makeRealization(),
    primary: PrimaryAnswerPublic | null = makePrimary(),
    natural: NaturalAnswerPublic | null = null,
  ) =>
    makeFinalAnswer({
      answer_text: 'AVALIAÇÃO COMPLETA CANÔNICA',
      answer_blocks: completeBlocks,
      limitations: ['Sem dados empíricos.'],
      primary_answer: primary,
      linguistic_realization: realization,
      linguistic_realization_presentation_eligible: realization != null,
      natural_answer: natural,
      natural_answer_presentation_eligible: natural != null,
    })

  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as unknown as { clipboard?: unknown }).clipboard
  })

  it('quando presente e elegível, domina: título "Resposta", um parágrafo por bloco, disclosure de origem model-written', () => {
    render(<FinalAnswerView finalAnswer={withRealization()} />)

    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
    expect(screen.getByText('Um SaaS é a melhor escolha (sustentada pelo debate).').tagName).toBe('P')
    expect(
      screen.getByText('Fica registrada a ressalva de que o fornecedor pode falir.').tagName,
    ).toBe('P')
    expect(screen.getByText(/Texto redigido por um modelo/)).toBeInTheDocument()
    expect(screen.getByText(/não é verificação externa nem garantia de verdade/)).toBeInTheDocument()
  })

  it('mostra as limitações registradas ao lado da redação', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withRealization()} />)

    // A resposta principal aninhada (recolhida na disclosure) tem sua
    // PRÓPRIA seção "Limitações registradas" -- pega só a de nível
    // superior, que vem ANTES do <details> no documento.
    const topLevelLimitations = container.querySelector(
      '.final-answer > .final-answer__limitations',
    ) as HTMLElement
    expect(topLevelLimitations).not.toBeNull()
    expect(within(topLevelLimitations).getByRole('heading', { name: 'Limitações registradas' })).toBeInTheDocument()
    expect(within(topLevelLimitations).getByText('Sem dados empíricos.')).toBeInTheDocument()
  })

  it('preserva acesso à resposta principal estruturada e à avaliação completa na profundidade 1', async () => {
    const { container } = render(<AnswerAssessmentDetails finalAnswer={withRealization()} />)

    expect(screen.getByRole('heading', { level: 3, name: 'Resposta principal (estruturada)' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 4, name: 'Conclusão central:' })).toBeVisible()
    const inner = container.querySelector('details.final-answer__complete') as HTMLDetailsElement
    expect(inner.open).toBe(false)
    await userEvent.click(screen.getByText(/Ver avaliação completa/))
    expect(inner.open).toBe(true)
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
  })

  it('"Copiar resposta" copia EXATAMENTE linguistic_realization.rendered_text -- nunca primary/answer_text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<FinalAnswerView finalAnswer={withRealization()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Copiar resposta' }))
    expect(writeText).toHaveBeenLastCalledWith(makeRealization().rendered_text)
    expect(writeText).not.toHaveBeenLastCalledWith(makePrimary().rendered_text)
  })

  it('tem precedência sobre a resposta natural quando as duas estão presentes e elegíveis', () => {
    const { container } = render(
      <FinalAnswerView finalAnswer={withRealization(makeRealization(), makePrimary(), makeNatural())} />,
    )

    expect(screen.getByText(/Texto redigido por um modelo/)).toBeInTheDocument()
    expect(container.querySelector('.final-answer__scope-note')).not.toBeNull()
    // o texto da resposta natural não aparece -- a realização o substitui
    expect(screen.queryByText('Um SaaS é a melhor escolha. (sustentada pelo debate).')).not.toBeInTheDocument()
    expect(screen.getByText('Um SaaS é a melhor escolha (sustentada pelo debate).')).toBeInTheDocument()
  })

  it('ordem de fallback: não elegível, cai pra resposta natural quando ela está presente e elegível', () => {
    const withNatural = withRealization(makeRealization(), makePrimary(), makeNatural())
    withNatural.linguistic_realization_presentation_eligible = false

    render(<FinalAnswerView finalAnswer={withNatural} />)

    expect(screen.queryByText(/Texto redigido por um modelo/)).not.toBeInTheDocument()
    expect(screen.getByText('Um SaaS é a melhor escolha. (sustentada pelo debate).')).toBeInTheDocument()
  })

  it('ordem de fallback: sem realização, cai pra resposta principal estruturada', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withRealization(null)} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(container.querySelector('.final-answer--primary')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 3, name: 'Conclusão central:' })).toBeVisible()
  })

  it('defensivo: realização presente sem primary_answer nunca quebra -- cai pro próximo fallback', () => {
    const { container } = render(<FinalAnswerView finalAnswer={withRealization(makeRealization(), null)} />)

    expect(container.querySelector('.final-answer--natural')).toBeNull()
    expect(screen.getByText('Claim da avaliação completa.')).toBeVisible()
  })

  it('conteúdo não confiável dentro dos blocos permanece texto inerte (sem Markdown/HTML/links)', () => {
    const evil = makeRealization({
      blocks: [
        {
          claim_ids: ['c1'],
          text: '<img src=x onerror=alert(1)> **negrito** [link](http://x) # título',
        },
      ],
    })
    const { container } = render(<FinalAnswerView finalAnswer={withRealization(evil)} />)

    expect(container.querySelector('img, script, a, strong')).toBeNull()
    expect(
      screen.getByText('<img src=x onerror=alert(1)> **negrito** [link](http://x) # título'),
    ).toBeInTheDocument()
  })

  it('histórico: sem linguistic_realization (undefined, payload anterior a este campo) cai com segurança pro comportamento existente', () => {
    const { container } = render(
      <FinalAnswerView finalAnswer={makeFinalAnswer({ answer_blocks: completeBlocks, primary_answer: makePrimary() })} />,
    )

    expect(screen.queryByText(/Texto redigido por um modelo/)).not.toBeInTheDocument()
    expect(container.querySelector('.final-answer--primary')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Divisão por profundidade: a resposta (profundidade 0) não carrega a
// proveniência técnica; AnswerAssessmentDetails (profundidade 1) carrega.
// ---------------------------------------------------------------------------

import { formatFinalAnswerStatus } from '../../api/formatting'

describe('FinalAnswerView / AnswerAssessmentDetails -- profundidade', () => {
  it('a resposta não mostra a linha de status/proveniência; a profundidade 1 mostra', () => {
    const answer = makeFinalAnswer()
    const statusLabel = formatFinalAnswerStatus(answer.status)
    const { unmount } = render(<FinalAnswerView finalAnswer={answer} />)
    expect(screen.queryByText(statusLabel, { exact: false })).not.toBeInTheDocument()
    unmount()

    const { container } = render(<AnswerAssessmentDetails finalAnswer={answer} />)
    const provenance = container.querySelector('.answer-details__provenance')
    expect(provenance?.textContent).toBe(`Como a resposta foi montada: ${statusLabel}.`)
  })

  it('sem resposta principal, a avaliação completa JÁ é a resposta: a profundidade 1 não a duplica', () => {
    const { container } = render(<AnswerAssessmentDetails finalAnswer={makeFinalAnswer()} />)

    expect(container.querySelector('details.final-answer__complete')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Resposta principal (estruturada)' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /copiar/i })).not.toBeInTheDocument()
  })
})
