# Dialeon 1.3.0

Versão menor compatível com a linha 1.x. Acrescenta um segundo modo de
execução, a **resposta direta**: a pergunta vai a um único provider, com o
modelo que o deployment tem configurado para ele, sem nenhuma etapa do
Conselho. O Conselho continua sendo o padrão em todo lugar: requests sem
`kind`, `dialeon run` sem `--direct` e todas as runs históricas mantêm o
comportamento de sempre. A resposta direta é a resposta daquele modelo: não é
consenso, veredito de juiz, verificação nem evidência independente, e não usa
fonte.

## Resposta direta

- Disponível na interface web, na API e na CLI, sempre por escolha explícita.
- Exatamente um provider por run. O modelo padrão configurado no deployment
  para esse provider é congelado no aceite e enviado explicitamente como o
  modelo solicitado; o modelo que o provider reportar fica registrado ao lado,
  com a origem dessa identidade (`requested_model`, `model`,
  `model_identity_source`). Os dois podem diferir.
- Uma única chamada lógica ao provider (as tentativas de transporte seguem a
  política de execução vigente), sem extração de afirmações, crítica, análise
  de fonte, juiz, reconciliação, editor nem realização linguística.
- O provider precisa estar com a configuração local presente: `missing` é
  recusado antes do aceite, sem nenhuma chamada; `unknown` só por escolha
  explícita. Isso não garante que a chamada funcione.
- Fonte não é aceita neste modo.
- Resultado e falha ficam persistidos: a resposta do provider com modelo
  solicitado e reportado, uso de tokens, custo estimado e sua proveniência de
  preço, tentativas, incerteza de tentativas anteriores e proveniência do
  request (contrato `direct_answer_v1`, com o modelo incluído no digest).
  Quando a contabilização está incompleta, o custo aparece como o subtotal
  conhecido.
- Erro, timeout ou resposta vazia/malformada viram uma run direta `failed`,
  com o registro da chamada. Nunca há troca de provider ou de modelo, nem
  queda para o Conselho. Uma execução interrompida continua honestamente
  `running`.
- As mensagens de falha não afirmam o que o modelo produziu remotamente:
  quando a chamada terminou sem resposta aproveitável, dizem que nenhuma
  resposta utilizável foi recebida; quando a execução ou o registro final
  falharam, dizem que nenhuma resposta foi registrada, lembrando que o
  provider pode ter respondido.

## Interface web

- Nova escolha “Como responder”: **Conselho de modelos** (padrão) ou
  **Resposta direta**, com seleção de um único modelo que segue as mesmas
  regras de configuração local. No modo direto a fonte não é enviada, e o
  texto de introdução não promete comparação entre modelos.
- Página própria da run direta: pergunta, resposta, resumo curto, “Perguntar
  de novo”/“Nova pergunta” e os detalhes da chamada em “Como esta resposta foi
  produzida” (ou “O que aconteceu nesta pergunta”, quando nenhuma resposta foi
  registrada).
- O Histórico marca as runs diretas, que reabrem na página própria.
  “Perguntar de novo” numa run direta volta ao modo direto com a mesma
  pergunta e o mesmo provider; a run nova usa o modelo configurado no
  momento, não o da run anterior.

## API

- `POST /runs` aceita `"kind": "direct"`, com exatamente um item em
  `enabled_providers` e sem `source_text`. Sem `kind` (ou com
  `"kind": "council"`), a run é do Conselho, como sempre.
- Os detalhes e a auditoria de uma run direta sempre trazem `"kind": "direct"`
  e têm forma própria (`config`, `answer`, `response`, `accounting`,
  `provider_execution_policy`; em falha, `failure_stage`, `failure_reason` e
  `message`), sem `final_answer`. As respostas do Conselho não mudaram e não
  trazem `kind`.
- Cada item de `GET /runs` ganhou `kind` (`council`/`direct`).
- Novo código de erro `provider_prerequisites_missing` (422) quando o provider
  escolhido para uma run direta está `missing`.
- No OpenAPI, as uniões de resposta de run passaram a ser publicadas em dois
  níveis nomeados: o ramo do Conselho é a mesma união por `status` da 1.2.0,
  e o ramo direto é uma união por `status` cujos membros exigem `kind`.

## CLI

- `dialeon run "pergunta" --direct --providers <um provider>`: saída humana
  com a resposta primeiro e `kind` no `--json`. `--source` é recusado com
  `--direct`, inclusive vazio.
- Novo código de saída 5: a run direta foi registrada, mas a chamada ao
  provider terminou sem resposta utilizável.
- `dialeon get`, `dialeon audit` e `dialeon list` reconhecem runs diretas;
  `dialeon list --json` ganhou `kind` em cada item.

## Instalação e dependências

- Sem mudanças de dependências nem de versão mínima do Python (3.11).
- Artefatos da release: `llm_council-1.3.0-py3-none-any.whl` e
  `llm_council-1.3.0.tar.gz`, com a interface web já compilada. Não há
  publicação no PyPI.

## Persistência e atualização

- Mudança aditiva de banco: `accepted_runs` ganha a coluna `run_kind`,
  criada automaticamente na primeira abertura, sem preenchimento retroativo
  (vazia é Conselho, o que vale para toda run anterior), e a nova tabela
  `direct_runs` guarda o desfecho das runs diretas. Bancos criados pela v1.2,
  v1.1, v1.0 e v0.9 continuam abertos e legíveis; runs antigas, inclusive as
  do Conselho com um único provider, continuam sendo do Conselho.
- Um `run_kind` desconhecido não é lido como Conselho: detalhe, auditoria e
  listagem falham com erro interno, sem reescrever o registro.

## Compatibilidade

- Tudo é aditivo e opt-in: campos opcionais novos (`kind` na criação e na
  listagem), a forma nova das runs diretas, um código de erro e um código de
  saída novos. Clientes devem ignorar chaves desconhecidas.
- Clientes que tratam a listagem ou os detalhes de forma exaustiva precisam
  reconhecer runs com `"kind": "direct"`, que não têm `final_answer`.
- Clientes gerados a partir do OpenAPI podem ver a estrutura das uniões de
  resposta mudar; o JSON do Conselho em runtime é o mesmo.

## Limitações conhecidas

As limitações da 1.2.0 continuam valendo. A resposta direta depende
inteiramente de um modelo: não é verificada, não é comparada com outros
modelos nem com uma fonte. Não há teto de custo para ela além do limite de
tokens de saída por chamada (`DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL`).
Configuração local presente não garante que o provider aceite a chamada.

# Dialeon 1.2.0

Versão menor compatível com a linha 1.x. A interface web e a saída humana de
`dialeon run`/`dialeon get` passam a mostrar a resposta primeiro, com os
detalhes técnicos logo abaixo, e `GET /providers` passa a informar o estado
dos pré-requisitos locais de cada provider, usado na primeira execução para
orientar a escolha dos modelos. Nenhuma mudança de banco, de dependências ou
dos formatos `--json` da CLI.

## Pré-requisitos locais dos providers

- `GET /providers` ganhou o campo aditivo `local_prerequisites`: para cada
  provider da lista `providers`, `met`, `missing` ou `unknown`, a partir da
  configuração com que a API iniciou.
  - `met`: os pré-requisitos locais que o Dialeon sabe verificar parecem
    presentes (hoje, uma chave de API não vazia). Não quer dizer que a
    credencial tenha sido validada no fornecedor, nem garante que o provider
    ou o modelo estejam disponíveis: nada disso é testado antes de uma
    pergunta de verdade.
  - `missing`: falta um pré-requisito local conhecido (chave ausente, vazia
    ou só com espaços).
  - `unknown`: não dá para determinar localmente; não é o mesmo que
    `missing`.
- O campo nunca inclui chaves, partes delas, tamanhos, nomes de variáveis ou
  caminhos, e não faz nenhuma chamada de rede. A configuração é lida quando a
  API inicia: depois de mudar o `.env`, é preciso reiniciar a API.
- Na interface, todos os providers continuam visíveis. Só os `met` são
  pré-selecionados; os `missing` aparecem desabilitados, com explicação; os
  `unknown` só entram numa pergunta por escolha explícita. Quando nenhum está
  `met`, a tela explica a situação, lembra de reiniciar a API depois de
  configurar e oferece “Recarregar lista de modelos”, que só repete
  `GET /providers`.

## Resposta primeiro na interface web

- A página de uma pergunta mostra, nesta ordem: a pergunta, a resposta com as
  limitações necessárias, um resumo curto (modelos, custo estimado, duração),
  as ações “Perguntar de novo” e “Nova pergunta”, e “Como esta resposta foi
  produzida”, onde ficam a resposta principal estruturada, a avaliação
  completa, as respostas de cada modelo e a auditoria técnica.
- Uma pergunta concluída abre em `/runs/:id`, a mesma página de uma pergunta
  reaberta pelo Histórico, já com o resultado em mãos. Quórum insuficiente
  também abre o registro salvo.
- O composer foi reorganizado (“O que você quer saber?”, fonte opcional,
  modelos por nome, Ctrl/⌘ + Enter para perguntar).
- Quando o resultado de uma pergunta não pode ser confirmado (erro do servidor
  ou de rede depois do envio), a interface não oferece reenvio de um clique:
  a pergunta pode ter sido processada, então ela aponta o Histórico. A lista
  de modelos pode ser recarregada com segurança.
- Avisos com gravidade distinta (validação, atenção, neutro, erro), temas claro
  e escuro, contraste do botão principal e dos controles, leiaute em telas
  estreitas, texto longo sem quebra e movimento reduzido.

## Resposta primeiro na CLI

- A saída humana de uma run concluída (`dialeon run` e `dialeon get`) começa
  pela resposta (mesma ordem de escolha de sempre: realização linguística,
  resposta natural, resposta principal, avaliação completa), seguida das
  limitações, de um bloco `detalhes:` (status, run_id, providers solicitados,
  custo estimado, conclusão), de “como a resposta foi montada” e de onde ver o
  resumo da auditoria (`dialeon audit <id>`) e os dados estruturados
  (`dialeon audit <id> --json`).
- `dialeon get` de uma run com quórum insuficiente mostra primeiro o desfecho.
- Todo `--json` da CLI continua idêntico; `dialeon providers --json` continua
  devolvendo só a lista de identificadores.

## Correções e reforços

- Uma chave de API vazia ou só com espaços conta como ausente: a chamada ao
  provider nem é tentada (falha local, custo conhecido zero), como já
  acontecia com uma chave ausente. O valor de uma chave presente é enviado
  exatamente como configurado.
- A seleção de modelos é reconciliada a cada recarga da lista: modelos que
  somem ou passam a `missing` saem da seleção, e um modelo marcado
  automaticamente que passa a `unknown` é desmarcado até ser escolhido de
  novo. “Perguntar de novo” só restaura modelos `met`.
- A página de uma pergunta segue sempre o id da URL: navegação rápida entre
  perguntas não mostra conteúdo de outra, e um resultado recebido incompleto
  cai para a busca normal.
- O texto sobre a fonte diz que ela é comparada com as afirmações
  identificadas durante o debate, separadamente da avaliação delas.
- CLI: os providers pedidos para a run aparecem como “providers solicitados”
  (não como modelos que responderam), as indicações de auditoria descrevem o
  que cada comando mostra, e as limitações não são repetidas quando o texto
  mostrado como resposta já as traz.

## Instalação e dependências

- Sem mudanças de dependências nem de versão mínima do Python (3.11).
- Artefatos da release: `llm_council-1.2.0-py3-none-any.whl` e
  `llm_council-1.2.0.tar.gz`, com a interface web já compilada. Não há
  publicação no PyPI. O README descreve o primeiro uso a partir do wheel da
  release.

## Persistência e atualização

- Nenhuma mudança de banco. Bancos criados pela v1.1, v1.0 e v0.9 continuam
  abertos e legíveis sem alteração.

## Compatibilidade

- `GET /providers` ganhou `local_prerequisites`; `providers` não mudou.
  Clientes devem ignorar chaves desconhecidas.
- A saída humana da CLI foi reorganizada; ela não faz parte do contrato
  estável. Os formatos `--json` não mudaram.
- A mensagem registrada quando a credencial local está ausente passou a ser
  “configuração local obrigatória ausente”.

## Limitações conhecidas

As limitações da 1.1.0 continuam valendo. O estado de pré-requisitos cobre só
os modelos escolhidos: as etapas internas (extração de afirmações, juiz,
editor, análise da fonte) usam por padrão a Anthropic, e a interface não
mostra se a configuração local dela está presente -- sem ela, as respostas
dos modelos são coletadas, mas as afirmações não são extraídas nem avaliadas
(ver o README). Nenhum provider atual informa `unknown`; o estado existe para
adapters que não possam se avaliar localmente.

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
