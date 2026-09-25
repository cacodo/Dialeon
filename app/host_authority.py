"""
Autoridade de Host (M3 -- DNS rebinding).

Uma página maliciosa pode fazer seu próprio hostname (`attacker.example`)
resolver pra 127.0.0.1 depois de carregada. Pro navegador, requests dela ao
Dialeon local continuam same-origin: CORS não se aplica, o JSON passa sem
preflight e a guarda de Content-Type do POST /runs (M1) não bloqueia. O único
sinal confiável que sobra é o header `Host`, que carrega o nome que a PÁGINA
usou -- `attacker.example:8000`, nunca `localhost`. Sem validá-lo, a página
dispara Runs pagas e lê todo o histórico.

Por isso toda request HTTP passa por uma allowlist de hosts ANTES de
qualquer rota (execução, histórico, audit, OpenAPI, frontend estático):

- default: só os nomes de loopback (`localhost`, `127.0.0.1`, `::1`) --
  exatamente como a API é documentada pra rodar (uvicorn em 127.0.0.1);
- `ALLOWED_HOSTS` explícito SUBSTITUI o default (LAN, hostname custom,
  reverse proxy que preserva `Host`); inclua `localhost` se também quiser
  acesso local;
- `ALLOWED_HOSTS=*` desliga a proteção -- decisão consciente do operador.

A configuração é validada na borda HTTP (`create_app`), não ao carregar
Settings: um valor malformado impede a API de ser criada, sem afetar os
comandos da CLI, que não servem HTTP.

`Origin`, `Referer` e `X-Forwarded-Host` nunca são autoridade: sob DNS
rebinding o `Origin` é o do atacante e bate com o `Host`, e headers
encaminhados só teriam valor atrás de um proxy confiável (fora de escopo).
O endereço de bind (`--host 0.0.0.0`) também não é um Host: é onde o
servidor escuta, não o nome com que clientes legítimos o chamam.

Por que não `starlette.middleware.trustedhost.TrustedHostMiddleware`
(testado em Starlette 0.48 e 1.6): rejeita todo Host IPv6 (`[::1]:8000`
vira `[` por `split(":")`), compara com diferenciação de maiúsculas, aceita
`localhost:8000:9`, e valida padrões com `assert` (desligado por `-O`).

A comparação é port-agnostic e usa a forma normalizada: minúsculas, sem
ponto final (`localhost.` é o mesmo nome), IPv6 na forma comprimida sem
colchetes. Host ausente, duplicado ou malformado é rejeitado.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
ANY_HOST = "*"

# Rótulos DNS (LDH) mais `_`, comum em nomes de serviço internos (ex.:
# containers). Também cobre IPv4 na forma pontuada.
_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")
_PORT = re.compile(r"[0-9]{1,5}")


def _normalize_name(name: str) -> str | None:
    """Hostname/IPv4/IPv6 -> forma canônica, ou None se malformado."""
    if not name.isascii():
        return None
    candidate = name.lower()
    if candidate.endswith("."):
        candidate = candidate[:-1]
    if len(candidate) > 253:
        return None
    if ":" in candidate:
        try:
            return ipaddress.IPv6Address(candidate).compressed
        except ValueError:
            return None
    if _HOSTNAME.fullmatch(candidate) is None:
        return None
    return candidate


def request_host(value: str) -> str | None:
    """Valor do header Host (`host[:porta]`, `[ipv6][:porta]`) -> nome
    normalizado, ou None se malformado. A porta é validada e descartada."""
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        literal, rest = value[1:end], value[end + 1 :]
        if ":" not in literal:
            return None
        name = literal
    else:
        name, colon, port = value.partition(":")
        rest = colon + port
    if rest:
        if not rest.startswith(":") or _PORT.fullmatch(rest[1:]) is None or int(rest[1:]) > 65535:
            return None
    return _normalize_name(name)


def parse_allowed_hosts(entries: Iterable[str]) -> tuple[str, ...]:
    """Configuração -> allowlist normalizada (sem duplicatas, na ordem dada).

    Recusa, em vez de ignorar, o que poderia desligar ou enfraquecer a
    proteção em silêncio: lista vazia, item vazio, porta, curinga parcial,
    `*` misturado com hosts, não-ASCII (use a forma IDNA `xn--`), ou
    qualquer nome malformado."""
    items = [entry.strip() for entry in entries]
    if not items:
        raise ValueError("allowed_hosts não pode ser vazio (omita a configuração para o default local)")
    if ANY_HOST in items:
        if items != [ANY_HOST]:
            raise ValueError("'*' desliga a validação de Host e precisa ser o único item")
        return (ANY_HOST,)
    normalized: list[str] = []
    for item in items:
        if not item:
            raise ValueError("allowed_hosts contém um item vazio")
        if "*" in item:
            raise ValueError(f"curinga parcial não é suportado: {item!r} (use '*' sozinho)")
        name = item[1:-1] if item.startswith("[") and item.endswith("]") else item
        if name.count(":") == 1:
            raise ValueError(f"allowed_hosts não leva porta: {item!r}")
        host = _normalize_name(name)
        if host is None:
            raise ValueError(f"host inválido em allowed_hosts: {item!r}")
        if host not in normalized:
            normalized.append(host)
    return tuple(normalized)


class HostAuthorityMiddleware:
    """ASGI puro: rejeita (400, texto fixo) toda request HTTP cujo Host não
    esteja na allowlist, antes de qualquer rota ou leitura de corpo. O Host
    recebido nunca é ecoado na resposta.

    Só escopo `http`: o Dialeon não tem WebSocket. Um endpoint WebSocket
    futuro precisaria ser coberto aqui também."""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        allowed = parse_allowed_hosts(allowed_hosts)
        self._allow_any = allowed == (ANY_HOST,)
        self._allowed = frozenset(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._allow_any:
            await self.app(scope, receive, send)
            return
        values = [value for key, value in scope["headers"] if key == b"host"]
        host = request_host(values[0].decode("latin-1")) if len(values) == 1 else None
        if host is None or host not in self._allowed:
            await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
            return
        await self.app(scope, receive, send)
