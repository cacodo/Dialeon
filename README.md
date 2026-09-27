# LLM Council (Dialeon)

Dialeon é uma aplicação local para fazer perguntas a modelos de linguagem
(hoje GPT, Claude e Gemini, cada um pela API do seu fornecedor) de dois jeitos:

- **Conselho de modelos** (padrão): vários modelos respondem à mesma pergunta
  e você recebe uma resposta organizada: onde os modelos concordam, onde
  divergem, o que foi avaliado e o que continua incerto.
- **Resposta direta**: um único modelo responde sozinho, sem as etapas do
  Conselho (ver [Resposta direta ou Conselho](#resposta-direta-ou-conselho)).

Nos dois casos cada pergunta fica registrada com a sua proveniência e
auditoria. Você usa pelo navegador (ou pela CLI `dialeon`), com as suas
próprias chaves de API; cada pergunta faz chamadas pagas aos fornecedores.
Para começar, veja [Primeiros passos](#primeiros-passos-release-publicada).

Em detalhe, o Conselho envia uma pergunta para vários modelos de linguagem
independentemente, faz eles debaterem em rodadas, extrai as
afirmações (claims) resultantes, e submete o resultado do debate a um
juiz (outro modelo). Quando uma fonte textual opcional é fornecida
pelo usuário, ela é comparada contra essas claims em um canal separado
(Source Analysis) — o Judge nunca recebe esse texto nem esse
resultado. Depois do Judge, uma etapa determinística reconcilia os
dois canais, e a resposta final estruturada é montada com base nesse
resultado. Cada execução é persistida para inspeção posterior.

Versão do pacote nesta árvore: **1.5.0**. O número de versão no código
não indica, por si só, que uma tag ou release já foi publicada. A política
da linha 1.x está em
[Compatibilidade e estabilidade](#compatibilidade-e-estabilidade-linha-1x).

**Importante sobre o que isto NÃO é**: concordância entre modelos não é
verdade, e a fonte fornecida pelo usuário não é validada como
objetivamente correta. Ver [Escopo epistêmico](#escopo-epistêmico)
abaixo.

## Sumário

- [Primeiros passos (release publicada)](#primeiros-passos-release-publicada)
- [O que já está implementado](#o-que-já-está-implementado)
- [Como o pipeline funciona](#como-o-pipeline-funciona)
- [Escopo epistêmico](#escopo-epistêmico)
- [Compatibilidade e estabilidade (linha 1.x)](#compatibilidade-e-estabilidade-linha-1x)
- [Limitações conhecidas](#limitações-conhecidas)
- [Notas de versão](CHANGELOG.md)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Rodando a partir do código-fonte (desenvolvimento)](#rodando-a-partir-do-código-fonte-desenvolvimento)
- [Empacotamento de release](#empacotamento-de-release)
- [Configuração](#configuração)
- [Testes](#testes)
- [Possíveis direções futuras](#possíveis-direções-futuras)
- [Licença](#licença)

## Primeiros passos (release publicada)

Para usar o Dialeon não é preciso clonar o repositório, nem ter Node: o wheel
publicado em cada [release](https://github.com/cacodo/Dialeon/releases) já
inclui a API, a CLI e a interface web compilada. É preciso Python 3.11 ou mais
novo.

**1. Instale num diretório de trabalho fixo.** A API e a CLI leem o `.env` do
diretório ATUAL e criam o banco (`llm_council.db`) nele; use sempre o mesmo
diretório (ver [Configuração](#configuração)).

```bash
mkdir dialeon && cd dialeon
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install https://github.com/cacodo/Dialeon/releases/download/v1.5.0/llm_council-1.5.0-py3-none-any.whl
```

A página da release mostra o SHA-256 de cada arquivo, se você quiser conferir
o que baixou.

**2. Configure as chaves.** Crie um arquivo `.env` nesse diretório com as
chaves de API dos fornecedores que você vai usar (as que não tiver podem
ficar de fora):

```dotenv
OPENAI_API_KEY=sua-chave-da-openai
ANTHROPIC_API_KEY=sua-chave-da-anthropic
GOOGLE_API_KEY=sua-chave-do-google-gemini
```

Por padrão, as etapas internas de cada pergunta do Conselho (extração das
afirmações, juiz, editor e análise da fonte) usam a Anthropic; uma resposta
direta não passa por elas. Sem `ANTHROPIC_API_KEY`,
as respostas dos modelos escolhidos ainda são coletadas, mas as afirmações
não são extraídas nem avaliadas. Para usar outro fornecedor nessas etapas,
defina no mesmo `.env` `DEFAULT_CLAIM_PROCESSOR_PROVIDER`,
`DEFAULT_JUDGE_PROVIDER`, `DEFAULT_EDITOR_PROVIDER` e
`DEFAULT_SOURCE_ANALYZER_PROVIDER` (`openai`, `anthropic` ou `gemini`). Numa
pergunta do Conselho, a interface avisa antes do envio quando falta a
configuração local de alguma dessas etapas (ver
[Configuração local das etapas do Conselho](#configuração-local-das-etapas-do-conselho)).

**3. Inicie a API**, a partir do mesmo diretório:

```bash
uvicorn app.api.app:create_app --factory
```

**4. Abra <http://localhost:8000/app>**, escreva a pergunta, escolha em
“Como responder” entre **Conselho de modelos** (padrão) e **Resposta direta**,
confira quais modelos vão responder e clique em **Perguntar** (ou Ctrl/⌘ +
Enter). Uma resposta do Conselho pode levar alguns minutos.

### O que a lista de modelos indica

A partir da v1.2.0 (na v1.1.0 a lista mostra os modelos sem essa indicação),
cada modelo aparece com o estado da **configuração local** que o servidor
encontrou ao iniciar:

- **presente** (`met`, sem nenhuma marca na tela): tudo que o Dialeon sabe
  verificar localmente está lá, por exemplo uma chave não vazia. Não quer
  dizer que a chave seja válida, que o serviço esteja no ar, que haja cota ou
  que o modelo configurado exista: nada disso é testado antes de uma pergunta
  de verdade.
- **ausente** (`missing`, “Falta configuração local nesta instalação”): o
  Dialeon sabe que falta algo local obrigatório. O modelo continua na lista,
  mas não pode ser escolhido. Uma chave vazia ou só com espaços conta como
  ausente.
- **não verificável** (`unknown`, “Não foi possível verificar a configuração
  local”): o Dialeon não consegue determinar localmente se a configuração
  está presente -- isso não quer dizer que ela falte. O modelo nunca é
  marcado automaticamente; só entra na pergunta se você o marcar enquanto
  ele está nesse estado (um modelo marcado automaticamente que passe a esse
  estado depois de reiniciar a API é desmarcado).

Só modelos com configuração local presente são marcados automaticamente. Se
nenhum estiver, a tela explica a situação: diz que a configuração falta só
quando todos os modelos estão como ausentes; se algum não puder ser
verificado, diz apenas que nenhum tem a configuração local confirmada.

A mesma informação está em `GET /providers`, no campo `local_prerequisites`
(ao lado da lista `providers`, que não mudou). Ela nunca inclui chaves,
partes delas, tamanhos, nomes de variáveis ou caminhos. `dialeon providers
--json` continua devolvendo só a lista.

### Mudou o `.env`? Reinicie a API

A configuração é lida quando a API inicia. Depois de editar o `.env`, pare a
API (Ctrl+C) e inicie de novo; só então recarregue a página ou use
“Recarregar lista de modelos”. Recarregar a lista só consulta o que o
servidor já carregou: não relê o `.env` nem testa os fornecedores.

### Custo

Cada pergunta faz chamadas reais e pagas aos modelos escolhidos e, no
Conselho, ao fornecedor das etapas internas. Uma resposta direta é uma única
chamada, limitada só pelo teto de saída por chamada
(`DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL`). No Conselho, `DEFAULT_MAX_COST_USD`
(padrão 1,00 dólar) é um limite de interrupção **flexível**, baseado no custo já conhecido: ele é
verificado entre chamadas, e uma pergunta pode terminar acima dele. Uso sem
preço conhecido pelo Dialeon não entra nessa conta. Não é
uma estimativa prévia, nem um teto garantido de cobrança. O custo mostrado
depois da resposta é uma estimativa calculada a partir do uso reportado. Ver
[Orçamento e contabilização](#orçamento-e-contabilização).

### Resposta direta ou Conselho

A partir da v1.3.0 (a v1.2.0 só tem o Conselho), cada pergunta pode ser feita
de dois jeitos:

- **Conselho de modelos** (padrão, o comportamento de sempre): os modelos
  escolhidos respondem, e o Dialeon extrai as afirmações, as compara, avalia
  (juiz) e organiza a resposta.
- **Resposta direta**: **um** modelo responde sozinho. É uma única chamada ao
  provider escolhido, usando o modelo padrão que este deployment tem
  configurado para ele ou, opcionalmente, um modelo escolhido para esta
  pergunta (ver [Modelo específico da resposta direta](#modelo-específico-da-resposta-direta-avançado);
  congelado no momento da pergunta; o modelo que o provider reportar fica
  registrado ao lado). Não passa por nenhuma etapa do
  Conselho -- nada de afirmações, juiz, fonte ou editor --, então a resposta é
  a desse modelo: não é consenso nem verificação. Fonte não é aceita neste
  modo. O provider precisa estar com a configuração local presente (`met`;
  `missing` é recusado antes de qualquer chamada, `unknown` só por escolha
  explícita); isso não garante que a chamada funcione -- erro, timeout ou
  resposta vazia continuam possíveis e ficam registrados como uma resposta
  direta sem resposta, sem troca de modelo e sem cair no Conselho. Os
  detalhes da chamada (modelo solicitado e reportado, tokens, custo
  estimado, tentativas, proveniência do pedido) ficam em “Como esta resposta
  foi produzida” (ou “O que aconteceu nesta pergunta”, quando nenhuma resposta
  foi registrada) e na auditoria.

Na interface, escolha em “Como responder”. Pela CLI:
`dialeon run "pergunta" --direct --providers openai` (exatamente um
provider; sai com código 5 se a chamada terminar sem resposta registrada).
Pela API: `POST /runs` com `"kind": "direct"` e exatamente um item em
`enabled_providers`; sem `kind`, a run é do Conselho, como sempre.

### Modelo específico da resposta direta (avançado)

Na versão em desenvolvimento desta árvore (ainda não publicada em release; a
v1.5.0 não tem), uma resposta direta pode pedir um modelo específico ao
provider escolhido. Sem escolha, ela usa o modelo padrão configurado nesta
instalação, como sempre. Continua sendo uma resposta direta: uma única
chamada, sem nenhuma etapa do Conselho.

- A escolha é um identificador do fornecedor, enviado exatamente como
  digitado, com a mesma regra de forma da escolha dos participantes do
  Conselho (não vazio, sem espaços, sem caracteres de controle ou invisíveis,
  até 256 caracteres). Não há catálogo de modelos: o Dialeon não confere se o
  modelo existe, está disponível ou aceita a chamada. Se o fornecedor recusar,
  a resposta direta fica registrada sem resposta (falha do provider), sem
  troca por outro modelo nem pelo padrão.
- No aceite, o Dialeon congela o modelo pedido e a origem dele em
  `config.requested_model` e `config.requested_model_origin`
  (`configured_default` ou `run_override`; escrever o próprio padrão conta
  como escolha). Toda tentativa da chamada usa esse modelo, e mudar o padrão
  depois não altera runs já aceitas. O modelo que o fornecedor reportou
  continua registrado ao lado do pedido. Runs diretas anteriores mostram
  `configured_default`: até aqui, a resposta direta sempre usava o padrão
  configurado. Com escolha explícita, o padrão configurado da época não é
  registrado.
- Um modelo sem preço conhecido tem custo desconhecido, nunca zero.

Na interface: com “Resposta direta”, “Modelo” → “Modelo específico
(avançado)”; em branco é o padrão. “Perguntar de novo” só traz o modelo quando
ele foi escolhido explicitamente, e só para o mesmo provider. Pela CLI:
`dialeon run "..." --direct --providers openai --model openai=gpt-x` (no
máximo um `--model`, com o provider de `--providers`). Pela API:
`"requested_model": "gpt-x"` em `POST /runs` com `"kind": "direct"`; o
Conselho recusa esse campo (lá é `participant_model_overrides`).

### Configuração local das etapas do Conselho

A partir da v1.4.0 (a v1.3.0 não tem), o Dialeon confere, antes de uma
pergunta do Conselho, a configuração **local** de cada dependência dela: os
modelos escolhidos e as etapas internas (extração de afirmações, análise da
fonte, juiz, editor e revisão da redação, que hoje usa o fornecedor do juiz).
Isso resolve um caso concreto: com OpenAI e Gemini configurados e sem
`ANTHROPIC_API_KEY`, os modelos escolhidos respondem (e cobram), mas as
etapas internas, que usam a Anthropic por padrão, falham em seguida.

- **O que é**: o mesmo estado local da lista de modelos (`met` presente,
  `missing` ausente, `unknown` não verificável), agora por etapa, com o
  fornecedor e o modelo configurados para ela.
- **O que não é**: não testa credenciais, serviços nem modelos (nenhuma
  chamada de rede), não é autorização e não garante que uma etapa vá rodar.
  As etapas internas só **podem** ser alcançadas: quórum, afirmações
  extraídas, veredito, orçamento e falhas anteriores decidem isso. A análise
  da fonte só entra quando há fonte. O modelo mostrado é o configurado nesta
  instalação, não prova de que exista no fornecedor.
- **`unknown` não é `missing`**: não verificável é incerteza, nunca falha.

Na interface, o caminho normal não muda. Se faltar configuração local em
alguma etapa, um aviso diz qual etapa e qual modelo, e perguntar exige marcar
“Perguntar mesmo assim” (a resposta pode sair incompleta: uma etapa sem
configuração falha sem chamar o fornecedor, e o Dialeon não troca de modelo).
A escolha vale só para aquele aviso e aquela pergunta: mudar a pergunta, a
fonte ou os modelos pede a escolha de novo. Sem ela, a interface envia com
**admissão estrita**. Se a configuração local tiver mudado entre o aviso e o
envio, o servidor recusa sem criar nada e a interface mostra a situação nova.

Pela CLI: `dialeon readiness --providers openai,gemini` mostra a avaliação
sem executar nada (`--source` e o default de `--providers` são os mesmos de
`dialeon run`); `dialeon run "..." --strict-readiness` recusa a pergunta
(código 2, nada criado, nenhuma chamada) se faltar configuração local em
alguma etapa do caminho pedido. Sem a opção, `dialeon run` aceita como
sempre, e a saída mostra a situação registrada no aceite.

Pela API: `POST /runs/readiness` (opcional, sem efeito) com
`enabled_providers` e `source_supplied`; em `POST /runs`,
`"readiness_admission": "strict"` recusa com `422`
`council_prerequisites_missing` (a avaliação vai em `error.details.readiness`).
Para seguir sabendo de uma degradação, mande `"acknowledge_known_degradation":
true` com `"acknowledged_degradation_fingerprint"` igual ao
`known_degradation_fingerprint` da avaliação mostrada (uma identidade das
etapas sem configuração, não uma credencial): se a degradação avaliada no
aceite for outra, a resposta é `409` `council_readiness_changed`, sem criar
nada, com a avaliação nova em `error.details.readiness`.
Sem esses campos, o aceite é o mesmo da v1.3.0. Toda run nova do Conselho
guarda a avaliação feita no aceite, o modo de admissão e o reconhecimento em
`council_admission` (detalhe e auditoria); runs anteriores têm `null` (não
registrado), nunca reconstruído da configuração atual.

### Modelo de cada participante (avançado)

A partir da v1.5.0 (a v1.4.0 não tem), uma pergunta do Conselho pode pedir um
modelo específico para um participante. Sem escolha, cada participante usa o modelo padrão
configurado nesta instalação (`OPENAI_DEFAULT_MODEL` etc.), como sempre.

- A escolha é um identificador do fornecedor, enviado exatamente como
  digitado. O Dialeon só confere a forma (não vazio, sem espaços nem
  caracteres de controle ou invisíveis, até 256 caracteres) e que o provider
  é um participante selecionado: não tem catálogo de modelos e não confere se
  o modelo existe, está disponível ou aceita a chamada. Um modelo recusado pelo fornecedor aparece como falha
  registrada daquela resposta, sem troca por outro modelo.
- Vale só para o participante: resposta inicial e crítica. Extração de
  afirmações, análise da fonte, juiz, editor e revisão continuam com o modelo
  padrão do provider deles, mesmo quando é o mesmo provider.
- No aceite, o Dialeon congela o modelo pedido a **cada** participante e a
  origem dele (`configured_default` ou `run_override`) em
  `config.participant_models`; mudar o padrão depois não altera runs já
  aceitas. O modelo que o fornecedor reportou continua em cada resposta, ao
  lado do pedido. Runs anteriores mostram `participant_models: null` (não
  registrado).
- O mesmo identificador em dois providers são dois pedidos diferentes. Um
  modelo sem preço conhecido tem custo desconhecido (nunca zero) e, como
  antes, não entra na conta do `DEFAULT_MAX_COST_USD`.

Na interface: “Modelos” → “Modelo de cada participante (avançado)”; em branco
é o padrão. A avaliação de configuração local mostra o modelo que será pedido
e “Perguntar de novo” só traz escolhas explícitas. Pela CLI: `dialeon run
"..." --model openai=gpt-x` (repetível; também em `dialeon readiness`). Pela
API: `participant_model_overrides` (`{"openai": "gpt-x"}`) em `POST /runs` e
em `POST /runs/readiness`; a resposta direta recusa esse campo (o modelo dela
vai em `requested_model`, ver
[Modelo específico da resposta direta](#modelo-específico-da-resposta-direta-avançado)).

## O que já está implementado

- Execução concorrente de uma pergunta contra múltiplos providers/modelos
  (OpenAI, Anthropic, Gemini), com política de quórum configurável
  (mínimo de respostas pra seguir com debate vs. mínimo pra ainda
  retornar algo).
- Debate em rodadas: resposta inicial de cada modelo e, quando há
  quórum suficiente, uma rodada de crítica.
- Extração de afirmações (claims) a partir das respostas. As claims
  extraídas são as claims autoritativas: não há agrupamento nem
  reconciliação de claims entre modelos ou rodadas (essas operações
  consultivas foram removidas da execução; runs antigas que as
  registraram continuam legíveis). Só uma revisão explícita feita na
  própria extração (`parent_claim_id`) substitui uma claim.
- Verificação determinística de expressões aritméticas simples
  encontradas nas claims (sem chamada a modelo).
- Análise de fonte (Source Analysis) opcional: quando o usuário fornece
  um texto de referência, cada claim corrente é comparada contra esse
  texto (apoia / contradiz / não resolvida), de forma independente do
  debate.
- Avaliação de um juiz (Judge) sobre o conjunto de claims correntes do
  debate — avaliação escopada ao debate, não ao texto de fonte.
- Reconciliação determinística (sem chamada a modelo) entre o canal do
  Judge e o canal da Source Analysis: classifica como os dois se
  relacionam por claim (alinhados, em tensão, etc.), nunca produz um
  veredito de verdade novo.
- Composição de uma resposta final estruturada: a LLM do Editor
  continua cega a fonte e a reconciliação (nunca recebe o texto de
  fonte, o `SourceAnalysisResult` nem o resultado da reconciliação); a
  composição determinística da aplicação, essa sim, usa o resultado da
  reconciliação e pode incluir na resposta final as relações/excertos
  de Source Analysis por ele referenciados — sem que isso signifique
  que a reconciliação escolhe qual dos dois canais está certo.
- Resposta principal (aditiva e opcional): uma segunda chamada do Editor
  apenas SELECIONA, por id, claims já avaliadas pelo Judge e as organiza em
  papéis finitos (conclusão central, razões, contrapontos, condições,
  incertezas). A aplicação valida o plano (ids atuais e avaliados, sem
  duplicata, papel compatível com o veredito) e o renderiza
  deterministicamente; o modelo nunca escreve texto. É uma seleção
  apresentacional, não verificação externa: a avaliação completa
  determinística segue sempre disponível e é o fallback quando o plano
  falha ou não se aplica (por exemplo, sem veredito).
- Persistência via SQLite (SQLAlchemy assíncrono), com ciclo de vida de
  execução (`running` / `completed` / `failed` / `insufficient_quorum`).
  Uma execução `completed` é persistida em detalhe suficiente pra
  reconstrução/auditoria completa do resultado (claims, vereditos,
  tentativas, reconciliação, custo); `running`/`failed` persistem
  identidade, configuração, lifecycle e o snapshot de provenance do
  aceite — não um registro incremental de claims/tentativas/vereditos
  parciais em andamento, e uma falha inesperada de processo não
  garante que todo o trabalho parcial anterior seja reconstruível.
- Contabilização de tokens/custo por execução, com orçamento monetário e
  orçamento agregado de tokens (ambos soft caps — ver
  [Orçamento e contabilização](#orçamento-e-contabilização)).
- Provenance preservada na auditoria, em quatro dimensões distintas,
  nenhuma delas prova de execução bem-sucedida ou de conteúdo real:
  - **identidade de modelo** (`model_identity_source`): se o identificador
    de modelo foi reportado pelo próprio provider ou é um fallback pro
    modelo solicitado (o identificador solicitado nem sempre é igual ao
    reportado);
  - **autoridade de modelo padrão** (`DefaultModelAuthoritySnapshot`):
    snapshot, tomado no aceite da execução, de qual modelo padrão/fallback
    estava configurado por provider — não significa que esse modelo foi
    de fato solicitado ou executado;
  - **política de execução de provider** (`ProviderExecutionPolicy`):
    snapshot do timeout/número de tentativas de transporte vigente no
    deployment quando a execução foi aceita;
  - **request provider-neutro** (`RequestProvenance`): versão do contrato
    de request da operação + digest determinístico (SHA-256) do
    `CompletionRequest` provider-neutro finalizado — nunca o payload
    exato enviado ao provider, nunca prova de aceite remoto.
- Resposta direta (opcional, a partir da v1.3.0): uma única chamada a um
  provider escolhido, com o modelo configurado no deployment (ou, na versão
  em desenvolvimento desta árvore, um modelo escolhido para a pergunta), sem
  nenhuma etapa do Conselho; resultado ou falha registrados com a mesma
  proveniência de modelo, uso, custo e request (ver
  [Resposta direta ou Conselho](#resposta-direta-ou-conselho)).
- Configuração local das etapas do Conselho (a partir da v1.4.0): prévia sem
  efeito, admissão estrita opcional e registro, no aceite, da configuração
  local de cada etapa (ver
  [Configuração local das etapas do Conselho](#configuração-local-das-etapas-do-conselho)).
- Modelo de cada participante (a partir da v1.5.0): escolha opcional do
  identificador de modelo por participante do Conselho, congelada no aceite
  junto com os padrões usados (ver
  [Modelo de cada participante](#modelo-de-cada-participante-avançado)).
- CLI (`dialeon`), API HTTP (FastAPI) e frontend (React) para disparar
  execuções e inspecionar resultados.

## Como o pipeline funciona

Pipeline do Conselho (uma resposta direta não passa por nenhuma destas etapas):

```
Debate (rodadas + extração de claims)
  → Source Analysis (só se uma fonte foi fornecida)
  → Judge (avaliação escopada ao debate)
  → Reconciliação determinística Source↔Judge
  → Editor (resposta final estruturada)
```

Nenhuma chamada de modelo é adicionada pela etapa de reconciliação — é
uma função pura sobre o que os dois canais anteriores já produziram. O
Judge nunca recebe o texto de fonte nem o resultado da Source Analysis.
A LLM do Editor também nunca recebe o texto de fonte, o
`SourceAnalysisResult` ou o resultado da reconciliação — quem usa o
resultado da reconciliação (e resolve as relações/excertos de Source
Analysis por ele referenciados) é só a camada de composição
determinística da aplicação, ao montar a resposta final.

## Escopo epistêmico

- **O veredito do Judge é escopado ao debate**: reflete o que os
  modelos participantes discutiram, não uma verificação externa.
- **A Source Analysis é um canal de comparação independente**, não uma
  fonte de verdade — o texto fornecido pelo usuário nunca é verificado,
  só comparado contra as claims.
- **A reconciliação classifica um relacionamento entre dois canais**
  (ex.: "o Judge e a fonte apontam na mesma direção"), nunca decide qual
  dos dois está certo, e nunca é um terceiro veredito de verdade.
- Consenso entre modelos nunca é tratado como confirmação de veracidade.

## Compatibilidade e estabilidade (linha 1.x)

Política de compatibilidade da **linha 1.x**, aplicável a partir da versão
1.0.0 do pacote. A publicação de uma tag/release é um passo separado.

**Estável durante 1.x:** o significado dos endpoints HTTP documentados,
dos campos de request e dos campos centrais de resposta e de auditoria já
existentes; os códigos de status/erro HTTP (`error.code`); os comandos,
opções, formatos de `--json` e códigos de saída documentados da CLI; os
nomes das variáveis de ambiente documentadas; o entrypoint instalado
`dialeon` e o entrypoint Uvicorn `app.api.app:create_app`; e a leitura de
runs históricas válidas e suportadas (a partir da era v0.9). Por
segurança, requests cujo `Host` não esteja em `ALLOWED_HOSTS` (default: só
loopback) são rejeitadas com 400 antes de qualquer endpoint — acesso por
LAN, hostname próprio ou proxy exige declarar o nome (ver
[Configuração](#configuração)).

**Evolução compatível durante 1.x:** novos endpoints, novos campos
**opcionais** de resposta, informação adicional de auditoria e novos
comandos/opções de CLI que não alterem o comportamento existente.
**Clientes devem ignorar chaves de resposta desconhecidas** (o OpenAPI
gerado não publica objetos de resposta como fechados). **Requests
continuam estritos**: campos desconhecidos no corpo de um request são
rejeitados. Enums fechados de resposta (ex.: `status`, `verdict`) têm
significados estáveis, mas um valor novo pode quebrar clientes que os
tratam de forma exaustiva -- não é evolução automaticamente compatível
como um campo opcional novo.

Runs diretas (ver [Resposta direta ou Conselho](#resposta-direta-ou-conselho))
só existem quando pedidas com `"kind": "direct"`. Os detalhes delas sempre
trazem `"kind": "direct"` e têm forma própria (`answer`, `response`, sem
`final_answer`); respostas sem `kind` continuam sendo do Conselho, com a forma
de sempre. A listagem ganhou `kind` (`council`/`direct`) em cada item.

A partir da v1.4.0 (ver
[Configuração local das etapas do Conselho](#configuração-local-das-etapas-do-conselho)):
`POST /runs/readiness` é novo e opcional; `readiness_admission`,
`acknowledge_known_degradation` e `acknowledged_degradation_fingerprint` são
campos opcionais de `POST /runs` (só do Conselho); as respostas do Conselho
ganharam `council_admission`; o código `council_prerequisites_missing` só
aparece com `"readiness_admission": "strict"`, e `council_readiness_changed`
só com um reconhecimento de degradação.

A partir da v1.5.0 (ver
[Modelo de cada participante](#modelo-de-cada-participante-avançado)):
`participant_model_overrides` é um campo opcional de `POST /runs` e de
`POST /runs/readiness` (só do Conselho); `config` ganhou `participant_models`
(`null` em runs anteriores); a avaliação de configuração local passou a
`council_local_readiness_v2`, com `planned_model`/`planned_model_origin` nos
participantes (avaliações `v1` gravadas antes continuam legíveis e com a
mesma identidade de degradação). Os requests dos participantes passaram a
levar o modelo explícito (`initial_response_v2`/`critique_v2` na
proveniência); os registros `v1` mantêm o significado original.

**Não fazem parte da API estável:** o texto exato de mensagens da CLI e de
erros; o schema SQLite bruto e as classes ORM; os módulos internos `app.*`,
adapters de provider e detalhes internos do domínio; componentes
React/TypeScript, CSS, DOM e navegação do frontend; prompts internos e
payloads exatos enviados aos providers; e a reprodução idêntica de saídas de
modelo.

Nomes de contrato de operação como `judge_v1`/`judge_v2` são **proveniência
de auditoria**: mantêm sua interpretação histórica, mas execuções futuras
podem usar versões mais novas -- não congelam o algoritmo de execução.

A versão anunciada em `info.version` do OpenAPI é a versão do produto (a
de `pyproject.toml`); não existe uma versão de API HTTP independente.

## Limitações conhecidas

- O Judge avalia as claims e o histórico de revisão (lineage) que recebe; ele
  não estabelece verdade externa e não revisa o debate bruto como um oráculo
  de verdade.
- O texto de fonte fornecido pelo usuário não é verificado automaticamente.
- Limites de tokens/custo são portões operacionais e podem ser **soft**: o uso
  só é conhecido depois que uma chamada termina, então uma execução pode
  terminar acima do limite.
- Um processo interrompido pode não deixar um registro terminal persistido, e
  o trabalho parcial não é necessariamente persistido de forma incremental.
- `minimal_reasoning` é uma intenção expressa pela operação (hoje: extração
  de claims e Judge); atualmente só o adapter da Anthropic a mapeia para
  desabilitação explícita de thinking -- nos demais providers ela não tem efeito.
- A validação real do Judge com 50 claims foi bem-sucedida, mas é evidência
  para aquele workload, não uma garantia universal de cardinalidade.

## Estrutura do repositório

```
llm-council/
├── app/
│   ├── config.py            # Settings (variáveis de ambiente)
│   ├── providers/           # adapters dos providers (OpenAI/Anthropic/Gemini)
│   ├── orchestrator/        # dispatch concorrente + política de quórum/budget
│   ├── debate/               # rodadas e extração de claims
│   ├── source_analysis/      # comparação claim × texto de fonte
│   ├── judge/                 # avaliação escopada ao debate
│   ├── reconciliation/        # relacionamento determinístico Source↔Judge
│   ├── editor/                 # composição da resposta final
│   ├── council/                 # orquestração do pipeline completo (CouncilRunner)
│   ├── application/              # serviço de execução (aceite/persistência/erros)
│   ├── storage/                    # modelos SQLAlchemy + repositório
│   ├── presentation/                # schemas públicos (API/CLI compartilhados)
│   ├── api/                          # FastAPI (rotas, app, serving do frontend)
│   ├── cli/                           # entry point `dialeon`
│   └── bootstrap.py                    # composition root (providers → pipeline → storage)
├── frontend/                 # React + Vite + TypeScript
├── tests/                     # pytest (espelha a estrutura de app/)
├── .env.example                # exemplo das principais variáveis de ambiente (sem chaves reais)
└── pyproject.toml
```

## Rodando a partir do código-fonte (desenvolvimento)

Caminho para quem vai alterar o Dialeon, a partir de um clone do repositório.
Para só usar, veja [Primeiros passos](#primeiros-passos-release-publicada).

### Backend

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # depois preencha suas API keys no .env
```

Subir a API (porta 8000, padrão do uvicorn):

```bash
uvicorn app.api.app:create_app --factory --reload
```

Usar a CLI diretamente, sem subir a API:

```bash
dialeon providers                       # lista os identificadores de provider disponíveis
dialeon run "sua pergunta aqui"         # executa e imprime a resposta (detalhes logo abaixo)
dialeon run "..." --providers openai,anthropic --source "texto de referência opcional"
dialeon run "..." --direct --providers openai   # resposta direta de um único provider
dialeon readiness --providers openai,gemini     # configuração local de cada etapa do Conselho, sem executar
dialeon run "..." --strict-readiness            # recusa se faltar configuração local em alguma etapa
dialeon run "..." --model openai=gpt-x          # modelo específico para um participante (repetível)
dialeon list                             # lista execuções recentes
dialeon get <run_id>                     # resposta e detalhes de uma execução
dialeon audit <run_id>                   # resumo legível da auditoria
dialeon audit <run_id> --json            # dados estruturados da auditoria
```

Qualquer um dos comandos acima que dispare uma execução real (`dialeon
run`, ou uma chamada a `POST /runs` pela API) faz chamadas de verdade
aos providers configurados e pode gerar custo.

### Frontend

```bash
cd frontend
npm install
npm run dev          # servidor de desenvolvimento, com proxy pra API em :8000
npm run build         # build de produção — servido pela API em /app quando presente
npm test               # suíte de testes (vitest)
```

## Empacotamento de release

Dialeon é distribuído atualmente como **um único produto**: o artefato
Python de release (`llm-council`, instalável via wheel/sdist) inclui
backend, CLI (`dialeon`) **e** o frontend web já compilado. Quem instala
esse artefato não precisa da árvore de código-fonte do frontend nem de
Node pra que a API sirva a UI já empacotada em `/app` — só pra
CONSTRUIR o frontend a partir do fonte, que é responsabilidade de quem
prepara o release, não de quem instala.

O frontend continua um pacote interno/privado (`frontend/package.json`
tem `"private": true`) — nunca publicado separadamente.

Build order mínimo de um release (nesta ordem, sempre):

```bash
# 1. instala as dependências de frontend já declaradas (frontend/package-lock.json)
cd frontend && npm install && npm run build && cd ..

# 2. copia frontend/dist/ pra dentro do pacote Python (app/frontend_dist/,
#    nunca versionado em Git -- só existe durante o build de release)
python scripts/sync_frontend_dist.py

# 3. constrói wheel + sdist normalmente (setuptools via pyproject.toml)
python -m build
```

Rodar a suíte de testes do backend sozinha (`pytest`) **nunca** requer
Node nem este build order — `app/frontend_dist/` ausente preserva
exatamente o comportamento gracioso de sempre (API funciona normalmente
sem frontend montado, ver `app/api/frontend_serving.py`).

## Configuração

Veja [`.env.example`](.env.example) para as principais variáveis de
configuração e exemplos (nunca chaves reais) — não é uma lista
exaustiva de tudo que `Settings` aceita (ex.: `OPENAI_DEFAULT_MODEL`,
`ANTHROPIC_DEFAULT_MODEL`, `GEMINI_DEFAULT_MODEL` e
`DEFAULT_SOURCE_ANALYZER_PROVIDER` também são aceitas, mas não
aparecem lá); consulte `app/config.py` pra lista completa. Copie
`.env.example` para `.env` e preencha suas próprias chaves — `.env` já
está no `.gitignore` deste repositório e nunca deve ser commitado.

**Diretório de trabalho.** Tanto a CLI (`dialeon`) quanto a API
(`uvicorn app.api.app:create_app --factory`) procuram o `.env` no
diretório de trabalho ATUAL, e o `DATABASE_URL` padrão
(`sqlite+aiosqlite:///./llm_council.db`) é relativo a esse mesmo
diretório. Rodar os comandos a partir de diretórios diferentes usa
configurações e bancos diferentes (um banco vazio novo é criado se não
existir). Para um histórico único, rode sempre do mesmo diretório ou
defina `DATABASE_URL` com caminho absoluto (ex.:
`sqlite+aiosqlite:////home/voce/dialeon/llm_council.db`) — variáveis de
ambiente têm precedência sobre o `.env`.

**Hosts aceitos pela API (`ALLOWED_HOSTS`).** A API só responde a requests
cujo header `Host` seja um nome permitido; qualquer outro recebe
`400 Invalid host header` antes de qualquer rota (execução, histórico,
audit, frontend). Isso impede que uma página maliciosa, fazendo o próprio
hostname apontar pra sua máquina (DNS rebinding), dispare Runs pagas ou
leia o histórico. O default aceita só `localhost`, `127.0.0.1` e `[::1]`,
em qualquer porta — o uso local documentado acima (inclusive o proxy do
`npm run dev`) funciona sem configuração.

Para acessar por outro nome, declare-o: `ALLOWED_HOSTS` é uma lista
separada por vírgula, sem porta, que **substitui** o default (inclua
`localhost` se também quiser acesso local). O endereço de bind não é um
Host: `--host 0.0.0.0` só escolhe onde o servidor escuta; os clientes
continuam chamando por um nome ou IP, e é esse que precisa estar na lista.

```bash
# LAN: clientes usam http://192.168.1.20:8000
ALLOWED_HOSTS=192.168.1.20,localhost uvicorn app.api.app:create_app --factory --host 0.0.0.0
# reverse proxy que preserva o Host original (ex.: nginx com
# `proxy_set_header Host $host`)
ALLOWED_HOSTS=dialeon.example.internal
```

Um proxy que reescreve o `Host` para o upstream (ex.: `127.0.0.1:8000`)
funciona com o default, mas aí o Dialeon só consegue validar o `Host` que
recebe do proxy: validar o `Host` original do cliente (e assim barrar DNS
rebinding) passa a ser responsabilidade do proxy. `X-Forwarded-Host`,
`Origin` e `Referer` nunca são consultados. `ALLOWED_HOSTS=*` desliga a
validação — só faz sentido quando outra camada já garante o Host.
Configuração malformada (item vazio, porta, curinga parcial) impede a API
de iniciar em vez de desligar a proteção; os comandos da CLI, que não
servem HTTP, não usam essa configuração.

**Nunca** imprima, logue ou serialize o objeto de configurações inteiro
(por exemplo `Settings().model_dump()`) — isso inclui as API keys em
texto plano quando configuradas. Para confirmar que a instalação/CLI
está funcional sem imprimir, serializar ou transmitir as chaves
configuradas, use:

```bash
dialeon providers --json
```

Isso só lista os identificadores de provider que a aplicação conhece
(nunca a instância do provider, o objeto de configurações ou as
próprias chaves), e não chama nenhum provider real pra montar essa
lista. Não confirma se uma API key específica é válida — isso só é
verificável executando uma pergunta de verdade, o que chama os
providers reais. Com a API rodando, `GET /providers` mostra também, por
provider, se a configuração local está presente (`local_prerequisites`,
ver [O que a lista de modelos indica](#o-que-a-lista-de-modelos-indica)) --
o mesmo limite vale: presente não é válida. As chaves configuradas continuam sendo carregadas
normalmente na memória do processo durante o bootstrap da CLI; este
comando especificamente só nunca as imprime, serializa ou transmite.

## Testes

Backend:

```bash
pytest
```

Por padrão, testes marcados como `integration` (que fazem chamadas
reais a providers e exigem API keys) são excluídos automaticamente. Pra
rodá-los explicitamente:

```bash
pytest -m integration
```

Frontend:

```bash
cd frontend && npm test
```

## Orçamento e contabilização

Dois orçamentos independentes por execução (nomes de campo em runtime,
`RunConfig`):

- **Orçamento monetário** (`max_cost_usd`): teto de custo estimado da
  execução.
- **Orçamento agregado de tokens** (`max_total_tokens`): soma de
  input+output de todos os providers já concluídos — é um **soft cap**,
  verificado entre chamadas, nunca cancela trabalho já em andamento; a
  execução pode legitimamente terminar acima desse número.

Distintos dos tetos de **output por chamada** (`max_output_tokens_per_call`/
`max_output_tokens_judge`), que limitam quanto texto uma única chamada a
um único provider pode gerar — não têm relação com o orçamento agregado
acima. (`max_output_tokens_grouping` é mantido só por compatibilidade
com execuções históricas: nenhuma chamada atual o usa.)

Esses valores vêm de `Settings` (configuráveis via `.env`) sob nomes de
variável distintos dos nomes de campo acima:

| Campo em runtime (`RunConfig`) | Variável de ambiente |
| --- | --- |
| `max_cost_usd` | `DEFAULT_MAX_COST_USD` |
| `max_total_tokens` | `DEFAULT_MAX_TOTAL_TOKENS` |
| `max_output_tokens_per_call` | `DEFAULT_MAX_OUTPUT_TOKENS_PER_CALL` |
| `max_output_tokens_grouping` | `DEFAULT_MAX_OUTPUT_TOKENS_GROUPING` |
| `max_output_tokens_judge` | `DEFAULT_MAX_OUTPUT_TOKENS_JUDGE` |

## Possíveis direções futuras

Sem roteiro formal/autoritativo neste repositório. Áreas identificadas
como possíveis próximos passos, sem compromisso de implementação:

- múltiplos juízes/estratégias de consenso do Judge;
- suporte a mais providers;
- interface de inspeção mais rica no frontend.

## Licença

Dialeon é licenciado sob a [Apache License, Version 2.0](LICENSE).
