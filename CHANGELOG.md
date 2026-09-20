# Dialeon 1.0.0

Primeira versão estável do Core Dialeon, após a estabilização em uso real da
v0.9.0. O pacote reúne API HTTP, CLI e frontend web compilado.

## Contrato público 1.x

- A compatibilidade de HTTP, CLI, configuração e leitura de runs históricas
  suportadas tem limites documentados no [README](README.md#compatibilidade-e-estabilidade-linha-1x).
- Requests continuam estritos. Objetos de resposta aceitam a evolução por
  campos opcionais; clientes devem ignorar chaves desconhecidas.
- OpenAPI publica os erros reais da aplicação e anuncia a versão do produto.
  Runs válidas da era v0.9 permanecem legíveis, inclusive seus registros de
  auditoria e contratos históricos.

## Uso e inspeção

- A experiência de perguntar e ler a resposta foi revista, com apresentação
  estruturada da resposta final, melhor histórico e inspeção de execuções.
- A inspeção destaca claims e perspectivas dos participantes. A interface
  torna mais claras a fonte fornecida pelo usuário e situações sem veredito,
  inclusive claims não avaliadas.

## Claims e execução

- A extração passou a produzir até 12 claims materiais compactas por resposta,
  com política de raciocínio mínimo, semântica de cobertura e proveniência
  mais confiáveis.
- Claims extraídas são a autoridade atual; somente uma revisão com
  `parent_claim_id` explícito aposenta a claim anterior. Agrupamento e
  reconciliação cross-round deixaram de fazer chamadas na execução corrente.
  Dados e operações históricos continuam legíveis para auditoria.
- O Judge usa o contrato `judge_v2` com intenção de raciocínio mínimo,
  timeout de 120 segundos e uma tentativa de transporte por completion.
  A cobertura exata das claims e os resultados estruturados sem veredito
  permanecem. Um workload real, não reduzido, de 50 claims foi validado com
  cobertura 50/50; isso não estabelece um limite ou garantia universal.
- O backoff de retries de transporte foi corrigido. Claims sem avaliação
  têm representação estruturada e persistida, e a proveniência de cada
  request registra a versão de contrato da operação (por exemplo
  `claim_extraction_v2`, `judge_v2`) para auditoria.

## Persistência e atualização

- Bancos SQLite criados pela v0.9 continuam abertos e legíveis. Colunas
  aditivas novas (blocos estruturados da resposta final e claims não
  avaliadas) são criadas automaticamente na primeira abertura, sem
  reescrever nem preencher retroativamente runs antigas.

## Limitações conhecidas

O Judge avalia as claims e seu histórico de revisão, não verifica verdade
externa. O texto de fonte do usuário não é autenticado. Limites de
tokens/custo podem ser ultrapassados por trabalho já em andamento, e uma
interrupção pode deixar trabalho parcial sem persistência terminal. Somente o
adapter da Anthropic mapeia `minimal_reasoning` para desabilitar thinking
explicitamente. A validação de 50 claims é evidência daquele workload, não
uma garantia para qualquer contagem. Veja [Limitações conhecidas](README.md#limitações-conhecidas).
