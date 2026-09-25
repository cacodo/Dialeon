# LLM Council (Dialeon)

Sistema que envia uma pergunta para vários modelos de linguagem
independentemente, faz eles debaterem em rodadas, extrai as
afirmações (claims) resultantes, e submete o resultado do debate a um
juiz (outro modelo). Quando uma fonte textual opcional é fornecida
pelo usuário, ela é comparada contra essas claims em um canal separado
(Source Analysis) — o Judge nunca recebe esse texto nem esse
resultado. Depois do Judge, uma etapa determinística reconcilia os
dois canais, e a resposta final estruturada é montada com base nesse
resultado. Cada execução é persistida para inspeção posterior.

Versão do pacote nesta árvore: **1.0.0**. O número de versão no código
não indica, por si só, que uma tag ou release já foi publicada. A política
da linha 1.x está em
[Compatibilidade e estabilidade](#compatibilidade-e-estabilidade-linha-1x).

**Importante sobre o que isto NÃO é**: concordância entre modelos não é
verdade, e a fonte fornecida pelo usuário não é validada como
objetivamente correta. Ver [Escopo epistêmico](#escopo-epistêmico)
abaixo.

## Sumário

- [O que já está implementado](#o-que-já-está-implementado)
- [Como o pipeline funciona](#como-o-pipeline-funciona)
- [Escopo epistêmico](#escopo-epistêmico)
- [Compatibilidade e estabilidade (linha 1.x)](#compatibilidade-e-estabilidade-linha-1x)
- [Limitações conhecidas](#limitações-conhecidas)
- [Notas da versão 1.0.0](CHANGELOG.md)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Rodando o projeto](#rodando-o-projeto)
- [Empacotamento de release](#empacotamento-de-release)
- [Configuração](#configuração)
- [Testes](#testes)
- [Possíveis direções futuras](#possíveis-direções-futuras)
- [Licença](#licença)

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
- CLI (`dialeon`), API HTTP (FastAPI) e frontend (React) para disparar
  execuções e inspecionar resultados.

## Como o pipeline funciona

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

## Rodando o projeto

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
dialeon run "sua pergunta aqui"         # executa e imprime o resultado
dialeon run "..." --providers openai,anthropic --source "texto de referência opcional"
dialeon list                             # lista execuções recentes
dialeon get <run_id>                     # detalhe de uma execução
dialeon audit <run_id>                   # auditoria completa (claims, vereditos, reconciliação, custo)
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
providers reais. As chaves configuradas continuam sendo carregadas
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
