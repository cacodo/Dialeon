"""M3 -- autoridade de Host contra DNS rebinding.

Depois de um DNS rebinding, a página do atacante fala com o Dialeon local
como same-origin: `Host` e `Origin` são `attacker.example:<porta>`, o JSON
passa sem preflight e a guarda de Content-Type não bloqueia. Estes testes
usam uvicorn REAL em loopback (porta efêmera), então o `Host` chega ao app
exatamente como o navegador o enviaria; `Host` ausente/duplicado vai por
socket cru. Nenhum provider real: componentes fake, banco em memória.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn

from app.api.app import create_app
from app.config import Settings
from app.host_authority import DEFAULT_ALLOWED_HOSTS, parse_allowed_hosts, request_host
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result

BODY = {"question": "Pergunta nova do atacante", "enabled_providers": ["openai", "anthropic"]}
ATTACKER = "attacker.example"


# ---------------------------------------------------------------------------
# Parsing do header Host e da configuração
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost", "localhost"),
        ("localhost:8000", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1:8000", "127.0.0.1"),
        ("[::1]", "::1"),
        ("[::1]:8000", "::1"),
        ("[0:0:0:0:0:0:0:1]:8000", "::1"),
        ("LOCALHOST:8000", "localhost"),
        ("localhost.:8000", "localhost"),
        ("attacker.example:8000", "attacker.example"),
        ("localhost.attacker.example", "localhost.attacker.example"),
        ("attacker-localhost", "attacker-localhost"),
        ("my_service:8000", "my_service"),
        ("", None),
        ("localhost:", None),
        ("localhost:abc", None),
        ("localhost:8000:9", None),
        ("localhost:99999", None),
        ("::1", None),  # IPv6 no Host exige colchetes
        ("[::1", None),
        ("[localhost]:8000", None),
        ("[::1]x", None),
        ("localhost@attacker.example", None),
        ("local host", None),
        ("ação.example", None),
        ("a" * 64 + ".example", None),
    ],
)
def test_request_host_parsing(value, expected):
    assert request_host(value) == expected


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (["dialeon.lan"], ("dialeon.lan",)),
        (["  Dialeon.LAN.  ", "192.168.1.20", "dialeon.lan"], ("dialeon.lan", "192.168.1.20")),
        (["[::1]", "::1", "0:0:0:0:0:0:0:1"], ("::1",)),
        (["*"], ("*",)),
        ([" * "], ("*",)),
    ],
)
def test_allowed_hosts_are_normalized_and_deduplicated(entries, expected):
    assert parse_allowed_hosts(entries) == expected


@pytest.mark.parametrize(
    "entries",
    [[], [""], ["a", ""], ["localhost:8000"], ["[::1]:8000"], ["*.lan"], ["*", "localhost"], ["dia leon"], ["ação.lan"], ["a/b"]],
)
def test_malformed_allowed_hosts_are_rejected_never_silently_disabled(entries):
    with pytest.raises(ValueError):
        parse_allowed_hosts(entries)


def test_settings_default_is_loopback_only(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    assert Settings(_env_file=None).allowed_hosts == DEFAULT_ALLOWED_HOSTS == ("localhost", "127.0.0.1", "::1")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("dialeon.lan", ("dialeon.lan",)),
        ("dialeon.lan, 192.168.1.20 ,[::1]", ("dialeon.lan", "192.168.1.20", "::1")),
        ("*", ("*",)),
    ],
)
def test_settings_reads_a_comma_separated_env_list(monkeypatch, env, expected):
    monkeypatch.setenv("ALLOWED_HOSTS", env)
    assert Settings(_env_file=None).allowed_hosts == expected


@pytest.mark.parametrize("env", ["", " , ", "localhost:8000", '["dialeon.lan"]', "*,localhost"])
def test_settings_rejects_malformed_env(monkeypatch, env):
    monkeypatch.setenv("ALLOWED_HOSTS", env)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# ---------------------------------------------------------------------------
# uvicorn real
# ---------------------------------------------------------------------------


class _Instance:
    def __init__(self, app, port):
        self.app = app
        self.port = port
        components = app.state.components
        self.service_run = AsyncMock(wraps=components.service.run)
        components.service.run = self.service_run
        self.debate = components.service._runner._debate_engine

    def client(self, host_header: str | None = None, **headers) -> httpx.AsyncClient:
        if host_header is not None:
            headers["Host"] = host_header
        return httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.port}", headers=headers, timeout=10)

    async def run_ids(self, host: str = "127.0.0.1") -> list[str]:
        async with self.client(f"{host}:{self.port}") as client:
            return [run["id"] for run in (await client.get("/runs")).json()["runs"]]


@contextlib.asynccontextmanager
async def _serve(*, allowed_hosts=None, frontend_dist=None, bind="127.0.0.1", seed=True):
    kwargs = {} if allowed_hosts is None else {"allowed_hosts": allowed_hosts}
    result = full_council_run_result()
    app = create_app(
        settings=Settings(_env_file=None, **kwargs),
        components_factory=make_components_factory(
            debate_result=result.debate_result,
            judge_result=result.judge_result,
            editor_result=result.editor_result,
        ),
        frontend_dist=frontend_dist,
    )
    server = uvicorn.Server(uvicorn.Config(app, host=bind, port=0, log_level="critical"))
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(15):
            while not server.started:
                assert not serving.done(), "uvicorn exited during startup"
                await asyncio.sleep(0.02)
        instance = _Instance(app, server.servers[0].sockets[0].getsockname()[1])
        if seed:
            await app.state.components.repository.save_success(full_council_run_result())
        yield instance
    finally:
        server.should_exit = True
        await serving


@pytest.fixture
def default_policy(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)


def _sensitive_strings(run_id: str) -> list[str]:
    return [run_id, full_council_run_result().run_config.question]


async def test_dns_rebinding_cannot_start_a_paid_run(default_policy):
    async with _serve() as instance:
        before = await instance.run_ids()
        evil = f"{ATTACKER}:{instance.port}"
        async with instance.client(evil, Origin=f"http://{evil}") as client:
            response = await client.post("/runs", json=BODY)

        assert response.status_code == 400
        assert response.text == "Invalid host header"
        assert ATTACKER not in response.text
        instance.service_run.assert_not_called()
        assert instance.debate.calls == []  # nenhum dispatch
        assert await instance.run_ids() == before  # nenhuma Run nova


async def test_dns_rebinding_cannot_read_history(default_policy):
    async with _serve() as instance:
        [run_id] = await instance.run_ids()
        evil = f"{ATTACKER}:{instance.port}"
        async with instance.client(evil, Origin=f"http://{evil}") as client:
            responses = [
                await client.get(path)
                for path in ("/runs", f"/runs/{run_id}", f"/runs/{run_id}/audit", "/providers", "/openapi.json")
            ]

        for response in responses:
            assert response.status_code == 400
            assert response.text == "Invalid host header"
            for secret in _sensitive_strings(run_id):
                assert secret not in response.text


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:{port}",
        "127.0.0.1",
        "127.0.0.1:{port}",
        "[::1]:{port}",
        "LOCALHOST:{port}",
        "localhost.:{port}",
    ],
)
async def test_legitimate_local_hosts_read_and_act_normally(default_policy, host):
    async with _serve() as instance:
        [run_id] = await instance.run_ids()
        async with instance.client(host.format(port=instance.port)) as client:
            listing = await client.get("/runs")
            detail = await client.get(f"/runs/{run_id}")
            audit = await client.get(f"/runs/{run_id}/audit")
            created = await client.post("/runs", json=BODY)

        assert [r["id"] for r in listing.json()["runs"]] == [run_id]
        assert detail.status_code == audit.status_code == 200
        assert created.status_code == 201
        instance.service_run.assert_awaited_once()


@pytest.mark.parametrize(
    "host",
    [
        ATTACKER,
        f"{ATTACKER}:{{port}}",
        "localhost.attacker.example:{port}",
        "attacker-localhost:{port}",
        "127.0.0.1.attacker.example:{port}",
        "0.0.0.0:{port}",
        "localhost:{port}:1",
        "[::1:{port}",
        "dialeon.lan:{port}",
    ],
)
async def test_other_hosts_are_rejected_before_any_route(default_policy, host):
    async with _serve() as instance:
        async with instance.client(host.format(port=instance.port)) as client:
            post = await client.post("/runs", json=BODY)
            get = await client.get("/runs")

        assert post.status_code == get.status_code == 400
        instance.service_run.assert_not_called()
        assert instance.debate.calls == []


async def _raw_request(port: int, head: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    return data


@pytest.mark.parametrize(
    "head",
    [
        b"GET /runs HTTP/1.0\r\n\r\n",  # sem Host
        b"GET /runs HTTP/1.1\r\nHost: localhost\r\nHost: attacker.example\r\n\r\n",
        b"GET /runs HTTP/1.1\r\nHost: attacker.example\r\nHost: localhost\r\n\r\n",
    ],
    ids=["absent", "duplicate_local_first", "duplicate_attacker_first"],
)
async def test_absent_or_duplicate_host_never_reaches_history(default_policy, head):
    async with _serve() as instance:
        [run_id] = await instance.run_ids()
        data = await _raw_request(instance.port, head)

    assert data.split(b"\r\n", 1)[0].split(b" ")[1] == b"400"
    assert run_id.encode() not in data


async def test_ipv6_loopback_bind_serves_its_own_host(default_policy):
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback indisponível neste ambiente")
    async with _serve(bind="::1", seed=False) as instance:
        async with httpx.AsyncClient(base_url=f"http://[::1]:{instance.port}", timeout=10) as client:
            assert (await client.get("/providers")).status_code == 200
            assert (
                await client.get("/providers", headers={"Host": f"{ATTACKER}:{instance.port}"})
            ).status_code == 400


async def test_explicit_custom_host_is_allowed_and_replaces_the_local_default():
    async with _serve(allowed_hosts=("dialeon.lan",)) as instance:
        [run_id] = await instance.run_ids("dialeon.lan")
        async with instance.client(f"dialeon.lan:{instance.port}") as client:
            listing = await client.get("/runs")
            created = await client.post("/runs", json=BODY)
        async with instance.client(f"{ATTACKER}:{instance.port}") as client:
            attacker = await client.get("/runs")
        async with instance.client(f"localhost:{instance.port}") as client:
            local = await client.get("/runs")

    assert run_id in listing.text
    assert created.status_code == 201
    instance.service_run.assert_awaited_once()
    assert attacker.status_code == local.status_code == 400


async def test_custom_host_is_rejected_without_configuration(default_policy):
    async with _serve() as instance:
        async with instance.client(f"dialeon.lan:{instance.port}") as client:
            assert (await client.get("/runs")).status_code == 400


async def test_wildcard_consciously_disables_host_validation():
    async with _serve(allowed_hosts=("*",)) as instance:
        async with instance.client(f"{ATTACKER}:{instance.port}") as client:
            assert (await client.get("/runs")).status_code == 200


async def test_same_origin_frontend_keeps_working(default_policy, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Dialeon</body></html>")
    (dist / "assets" / "app.js").write_text("console.log('ok')")
    async with _serve(frontend_dist=dist, seed=False) as instance:
        async with instance.client(f"localhost:{instance.port}") as client:
            page = await client.get("/app/")
            asset = await client.get("/app/assets/app.js")
            api = await client.get("/providers")
        async with instance.client(f"{ATTACKER}:{instance.port}") as client:
            evil_page = await client.get("/app/")

    assert page.status_code == asset.status_code == api.status_code == 200
    assert "Dialeon" in page.text
    assert evil_page.status_code == 400 and "Dialeon" not in evil_page.text


def test_rejection_message_is_constant_and_never_echoes_the_host():
    body = json.dumps("Invalid host header")
    assert ATTACKER not in body
