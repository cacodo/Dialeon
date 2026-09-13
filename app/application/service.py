"""
`CouncilExecutionService` -- Etapa 10 (lifecycle durável adicionado na
T02.4).

A boundary entre `CouncilRunner` (lógica pura de pipeline, Etapa 8) e
`CouncilRepository` (persistência, Etapa 10) -- nenhum dos dois conhece o
outro. Não se chama "Coordinator" de propósito: `CouncilRunner` já é quem
coordena/sequencia as etapas do pipeline; este objeto faz uma coisa
diferente e mais externa -- executar e then registrar o resultado (ou a
falha) para auditoria.

Fluxo (T02.4 -- substitui o fluxo da Decision Delta §5, ver justificativa
abaixo):

    caller
      -> valida TODA autoridade de provider do RunConfig -- participantes
         + os 4 papéis internos (claim processor, judge, editor, source
         analyzer) -- via run_config.all_provider_authorities
         (UnknownProviderError se houver algum desconhecido -- nenhum
         registro é criado nesse caso; T02.4 repair, achado MEDIUM da
         revisão independente: a validação original só cobria
         enabled_providers, deixando os 4 papéis internos escaparem pra
         serem descobertos só em runtime profundo, depois de aceite
         durável e possível consumo de provider)
      -> minta run_id + started_at (autoritativos a partir daqui)
      -> persiste o registro de aceite (repo.save_accepted) -- esta
         transação PRECISA completar antes de qualquer chamada ao runner
      -> chama CouncilRunner.run(run_config, run_id=..., started_at=...)
      -> sucesso: repo.save_success(...) (mesma id)
      -> quorum failure: repo.save_quorum_failure(..., run_id=...) (mesma id)
      -> qualquer outra exceção: repo.save_unexpected_failure(...) (mesma
         id, informação sanitizada) e relança a exceção ORIGINAL intacta
      -> retorna o resultado, ou relança a exceção

Decision Delta §5 dizia explicitamente "não minta run_id antes de chamar
CouncilRunner.run()" -- essa regra é revertida aqui, deliberadamente
(T02.4): o motivo original (evitar autoridade duplicada de id) continua
válido, mas a implementação anterior tinha um efeito colateral não
percebido -- uma execução validada que já pode consumir providers só
ganhava QUALQUER identidade durável (a) no fim do pipeline inteiro
(sucesso) ou (b) dentro de `save_quorum_failure` (falha de quórum). Toda
exceção inesperada nem chegava a ser mencionada em disco -- um bug real,
um erro de provider não capturado, ou o processo morrendo no meio,
faziam a execução (e o custo real já incorrido) desaparecer sem deixar
rastro algum pra auditoria (gap de auditabilidade/reparabilidade, não de
corrupção de dado -- ver relatório de reconciliação T02.4). A correção
preserva o PRINCÍPIO original (autoridade única de run-id -- nunca dois
lugares mintando ids competindo) só que movendo o ponto de mintagem pra
ANTES da execução, e garantindo que CouncilRunner/`save_quorum_failure`
reusem essa MESMA identidade em vez de mintar a própria."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.application.errors import UnknownProviderError
from app.council.result import CouncilRunResult
from app.council.runner import CouncilRunner
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.storage.repository import CouncilRepository

# Mensagem sanitizada FIXA -- nunca `str(exc)`, nunca traceback, nunca
# repr da exceção (ver `_sanitize_unexpected_failure`). Mesmo texto
# genérico já usado pelos catch-alls existentes de
# `app/api/error_handlers.py`/`app/cli/main.py` -- este slice não inventa
# um segundo vocabulário de erro genérico, reusa o que já existe.
_UNEXPECTED_FAILURE_MESSAGE = "Erro interno inesperado durante a execução."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


def _sanitize_unexpected_failure(exc: Exception) -> tuple[str, str]:
    """Classificação interna estável + mensagem genérica sanitizada --
    NUNCA `str(exc)`/`repr(exc)`/traceback (item 6 do contrato T02.4:
    nenhuma API key, segredo, credencial, valor de ambiente cru, saída
    de LLM, ou texto controlado pelo usuário pode chegar a
    `failure_classification`/`failure_message`). `type(exc).__name__` é
    sempre um identificador Python fixo definido pelo código da própria
    aplicação/bibliotecas -- nunca contém dado do request, do provider,
    ou de um traceback. A mensagem é uma constante fixa, deliberadamente
    idêntica em TODO failure inesperado -- diferenciar mensagens por
    exceção exigiria tocar `str(exc)`, exatamente o que este contrato
    proíbe."""
    return type(exc).__name__, _UNEXPECTED_FAILURE_MESSAGE


class CouncilExecutionService:
    """Executa um `CouncilRunner` e persiste o desfecho -- sucesso, falha
    de quórum, ou falha inesperada -- numa única chamada. `CouncilRunner`/
    `Orchestrator` nunca precisam saber que isso existe.

    `known_providers` (Etapa 14, T19A.1): o conjunto de nomes de provider
    realmente construídos (mesma fonte que `AppComponents.providers`) --
    é contra ele que TODA autoridade de provider do `RunConfig`
    (`run_config.all_provider_authorities` -- Etapa 14 validava só
    `enabled_providers`; T02.4 repair, achado MEDIUM da revisão
    independente, estendeu pros 4 papéis internos também) é validada,
    ANTES de qualquer chamada ao `CouncilRunner`. Essa checagem morava só
    em `app/api/routes.py` até a Etapa 14; movida pra cá porque é a
    boundary reutilizável de verdade -- API e CLI (e qualquer cliente
    futuro) compartilham a MESMA regra por construção, em vez de cada um
    reimplementá-la (achado do Repo Evidence Pack de T19A.1). Esta
    validação NUNCA depende de `bootstrap._validate_internal_provider_config`
    ter rodado -- `CouncilExecutionService` é a boundary de aceite
    arquitetural e pode ser construído/testado independentemente da
    composition root normal (achado da revisão independente do T02.4)."""

    def __init__(
        self,
        runner: CouncilRunner,
        repository: CouncilRepository,
        known_providers: frozenset[str] | set[str],
    ):
        self._runner = runner
        self._repository = repository
        self._known_providers = frozenset(known_providers)

    async def run(self, run_config: RunConfig) -> CouncilRunResult:
        """Valida, aceita (persiste ANTES de qualquer chamada ao runner),
        executa, e persiste o desfecho terminal.

        `UnknownProviderError` (Etapa 14; escopo estendido no T02.4
        repair): levantada ANTES de qualquer mintagem/persistência --
        nenhum registro é criado, exatamente como o comportamento HTTP
        anterior (rejeitado antes do service ser chamado) exceto que
        agora a checagem em si mora aqui, não em `routes.py` (item 1 do
        contrato T02.4: validação nunca é movida pra dentro de lógica
        controlada por LLM/persistência). Cobre TODA autoridade de
        provider do `RunConfig` -- participantes E os 4 papéis internos
        (claim processor, judge, editor, source analyzer) -- nunca só
        `enabled_providers`.

        `InsufficientQuorumError`: persiste o registro de falha de
        quórum sob a MESMA identidade aceita, anexa o id em
        `exc.persisted_failure_id` (campo formal, Etapa 11 — não um
        atributo dinâmico) e RELANÇA a MESMA instância — o contrato de
        erro do chamador não muda por causa da persistência existir.

        Qualquer OUTRA exceção (bug real, erro de provider não
        capturado em nenhuma camada anterior): persiste um estado
        terminal FAILED sob a MESMA identidade aceita, com informação
        JÁ SANITIZADA (nunca traceback/segredo/texto cru -- ver
        `_sanitize_unexpected_failure`), e relança a exceção ORIGINAL
        intacta -- o boundary de erro genérico existente (API 500 /
        CLI exit 1) continua funcionando sem modificação, porque nunca
        muda O QUE é relançado, só passa a deixar rastro recuperável
        antes de relançar.

        Se a PRÓPRIA persistência terminal falhar (ex.: erro de banco
        durante `save_success`/`save_quorum_failure`/
        `save_unexpected_failure`), essa nova exceção propaga no lugar
        (item 7 do contrato: nunca fabrica um desfecho terminal falso) --
        o registro de aceite/running já commitado antes do runner rodar
        nunca é apagado por uma falha aqui, porque o DELETE/UPDATE que o
        finalizaria está na MESMA transação atômica que falhou."""
        unknown = sorted(
            p for p in run_config.all_provider_authorities if p not in self._known_providers
        )
        if unknown:
            raise UnknownProviderError(
                unknown_providers=unknown, known_providers=sorted(self._known_providers)
            )

        run_id = _new_id()
        started_at = _now()
        await self._repository.save_accepted(
            run_id, run_config=run_config, started_at=started_at
        )

        try:
            result = await self._runner.run(run_config, run_id=run_id, started_at=started_at)
        except InsufficientQuorumError as exc:
            failed_at = _now()
            failure_id = await self._repository.save_quorum_failure(
                exc,
                run_config=run_config,
                started_at=started_at,
                failed_at=failed_at,
                run_id=run_id,
            )
            exc.persisted_failure_id = failure_id
            raise
        except Exception as exc:
            failed_at = _now()
            classification, message = _sanitize_unexpected_failure(exc)
            await self._repository.save_unexpected_failure(
                run_id,
                failed_at=failed_at,
                failure_classification=classification,
                failure_message=message,
            )
            raise

        await self._repository.save_success(result)
        return result
