# LLM Council

Sistema de debate multi-LLM: envia uma pergunta para várias LLMs
independentemente, compara as respostas, faz elas debaterem, e usa
um juiz para chegar a uma resposta final com consenso auditável —
distinguindo sempre "modelos concordam" de "existe evidência externa".

Arquitetura completa em `docs/` (ou nos documentos de arquitetura já
trocados na conversa que originou este projeto).

## Estado atual: Etapa 1 — estrutura + configuração

Este commit contém **só** estrutura de diretórios e configuração.
Nenhuma lógica de LLM, orquestração, debate ou juiz foi implementada
ainda — isso vem nas próximas etapas, uma de cada vez.

```
llm-council/
├── app/
│   ├── main.py                # (vazio ainda — Etapa 4+)
│   ├── config.py               # ✅ settings via env vars
│   ├── models/                 # schemas Pydantic — Etapa 3
│   ├── providers/               # LLMProvider + implementações — Etapa 2
│   ├── orchestrator/            # coordenação de fases — Etapa 4
│   ├── context/                 # gerenciamento de contexto — Etapa 6+
│   ├── debate/                  # rodadas de crítica — Etapa 6
│   ├── judge/                   # JudgeStrategy — Etapa 7
│   ├── verification/             # stub no MVP — Etapa 8
│   ├── cost/                     # cost tracker/budget guard — Etapa 4
│   └── storage/                  # SQLAlchemy/SQLite — Etapa 9
├── frontend/
│   └── streamlit_app.py         # (vazio ainda — Etapa 10)
├── tests/
├── .env.example                 # ✅ copie para .env e preencha as chaves
├── pyproject.toml                # ✅ dependências
└── README.md
```

## Setup local (o que já dá pra fazer nesta etapa)

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env            # depois preencha suas API keys no .env
```

Não há nada pra rodar ainda — `main.py` está vazio de propósito. O
objetivo desta etapa é só confirmar que a estrutura, o `config.py` e
a instalação de dependências funcionam antes de entrarmos na Etapa 2
(Provider Layer).

### Como confirmar que esta etapa está OK

```bash
python -c "from app.config import settings; print(settings.model_dump())"
```

Isso deve imprimir as configurações padrão (com as API keys como
`None` até você preencher o `.env`), sem erro nenhum. Se der erro de
import, é sinal de que falta `pip install -e .`.
