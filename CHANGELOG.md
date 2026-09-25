# Dialeon 1.1.0

Versão menor compatível com a linha 1.x. Acrescenta uma apresentação da
resposta em linguagem natural sobre a resposta estruturada e reforça
auditoria, persistência e a segurança da API local. Duas mudanças podem exigir
ação de quem opera o Dialeon fora do uso local padrão ou chama a API
diretamente: `ALLOWED_HOSTS` e o `Content-Type` do `POST /runs` (ver
[Segurança da API local](#segurança-da-api-local)).

## Resposta e apresentação

- **Resposta primária**: seleção limitada e tipada das claims avaliadas pelo
  Judge, organizada por papel (conclusão central, razões, trade-offs,
  condições, incertezas e ressalvas), sempre com o rótulo de veredito visível.
  É a autoridade do que a resposta apresenta; a coerência com o veredito é
  verificada ao salvar e ao reler.
- **Resposta natural**: renderização conversacional determinística da
  resposta primária -- sem chamada de modelo e sem proposições novas.
- **Realização linguística** (opcional): reescrita apresentacional escrita por
  modelo sobre a resposta primária, exibida só depois de validação estrutural
  determinística e de uma revisão semântica independente vinculada ao
  candidato exato. Aceitar a realização é evidência probabilística de
  revisão, não prova de fidelidade nem verificação externa. Usa até duas
  chamadas ao provider do Editor (realização) e até duas ao do Judge
  (revisão), respeitando o orçamento da execução e contabilizadas em
  uso/custo. Qualquer falha cai para a resposta natural/primária sem
  invalidar a execução.
- Nova superfície de resposta na interface: resumo, detalhamento, cópia e
  estado de espera; runs sem desfecho aparecem sem afirmar que estão ativas;
  nomes de provider reconhecíveis; atualizar uma run, reutilizar pergunta e
  fonte, ver a fonte enviada e limites de 20.000 caracteres visíveis. A fonte
  é enviada exatamente como digitada, como na API e na CLI.
- Os retries do Judge e do planejador da resposta primária recebem, após uma
  rejeição estruturada, um retorno limitado escrito pela aplicação.
- O orçamento padrão de tokens da execução (`DEFAULT_MAX_TOTAL_TOKENS`) passou
  de 50.000 para 150.000.

## Auditoria e confiabilidade

- Uma resposta já devolvida por um provider nunca some da auditoria: uma
  falha inesperada ao interpretá-la vira uma tentativa
  `interpretation_failed`, com texto bruto, uso e custo preservados, e o
  estágio segue o fallback que já aplica a uma saída malformada.
- Fragmentos brutos guardados só para auditoria (proposta numérica rejeitada,
  entrada rejeitada da análise de fonte) têm um limite de complexidade e
  precisam ser JSON interoperável. Fora disso, são omitidos com motivo
  explícito (`raw_proposal_omitted_reason`/`raw_entry_omitted_reason`); o
  texto integral do provider continua na tentativa correspondente. Isso
  impede que uma resposta patológica derrube a persistência ou a leitura da
  run.
- Se a gravação do resultado terminal falhar, a run fica `failed` com
  `failure_stage="terminal_persistence"`, distinta de uma falha de execução
  (`"execution"`), sem afirmar que o histórico detalhado foi preservado.
- Uma tentativa cancelada pelo timeout de dispatch da rodada agora conta o
  dispatch que já tinha acontecido.
- Falhas de conexão e timeouts do Gemini são retentáveis, como nos outros
  providers.
- A inspeção avisa quando um inteiro de um fragmento bruto pode ter sido
  arredondado pelo navegador.

## Segurança da API local

- `POST /runs` exige `Content-Type` JSON (`application/json` ou
  `application/*+json`). Sem ele, ou com `text/plain`, formulário ou
  multipart, a resposta é `422` antes de o corpo ser lido. Isso impede que uma
  página de outra origem dispare uma run paga com uma "simple request" do
  navegador. Clientes que já enviam JSON não mudam.
- **Autoridade de Host** (proteção contra DNS rebinding): a API só responde a
  requests cujo `Host` esteja permitido; os demais recebem
  `400 Invalid host header` antes de qualquer rota (execução, histórico,
  auditoria, frontend).
  - O uso local funciona sem configuração: o default aceita `localhost`,
    `127.0.0.1` e `[::1]`, em qualquer porta.
  - Acesso por IP de LAN ou hostname próprio exige declarar o nome em
    `ALLOWED_HOSTS` (lista separada por vírgula, sem porta), que substitui o
    default -- inclua `localhost` se também quiser acesso local.
  - Um reverse proxy que preserva o `Host` precisa ter esse hostname
    permitido. Um proxy que reescreve o `Host` só permite ao Dialeon validar
    o `Host` que recebe; validar a autoridade original passa a ser
    responsabilidade do proxy.
  - O endereço de bind não é um Host permitido: `--host 0.0.0.0` só escolhe
    onde o servidor escuta.
  - `ALLOWED_HOSTS=*` desliga conscientemente a validação.
  - Um valor malformado impede a API de iniciar em vez de desligar a
    proteção; os comandos da CLI não usam essa configuração.

## Instalação e dependências

- Os pisos mínimos declarados foram corrigidos para versões realmente
  compatíveis com o código: `fastapi>=0.116.2`, `starlette>=0.48.0`,
  `pydantic>=2.12.0`, `pydantic-settings>=2.15.0`,
  `sqlalchemy[asyncio]>=2.0.41`, `httpx>=0.28.1`, `openai>=1.55.3` e
  `google-genai>=1.21.0`. A instalação nos pisos exatos é verificada em
  Python 3.11, 3.13 e 3.14, inclusive servindo a API com uvicorn.
- O README documenta que o `.env` e o banco SQLite padrão são relativos ao
  diretório de trabalho atual.

## Persistência e atualização

- Bancos criados pela v1.0 e pela v0.9 continuam abertos e legíveis. As
  colunas aditivas novas (resposta primária, natural e realização
  linguística, motivos de omissão de fragmentos e `failure_stage`) são
  criadas automaticamente na primeira abertura, sem reescrever nem preencher
  retroativamente runs antigas.

## Compatibilidade

- As respostas ganharam campos opcionais novos (por exemplo
  `primary_answer`, `natural_answer`, `linguistic_realization` e seus
  indicadores de elegibilidade); clientes devem ignorar chaves desconhecidas.
- Enums fechados ganharam valores: `parse_status` das tentativas aceita
  `interpretation_failed`, e runs `failed` informam `failure_stage`. Clientes
  que tratam esses valores de forma exaustiva precisam reconhecê-los.
- Podem exigir ação: `ALLOWED_HOSTS` para acesso não local, `Content-Type`
  JSON no `POST /runs` e os novos pisos de dependência.

## Limitações conhecidas

As limitações da 1.0.0 continuam valendo. `POST /runs` é síncrono: se o
cliente desconectar, a run continua, e reenviar a pergunta cria outra run
(com custo próprio). Veja [Limitações conhecidas](README.md#limitações-conhecidas).

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
