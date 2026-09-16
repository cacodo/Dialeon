"""Release Packaging Contract V1 -- cobertura determinística de
`_resolve_default_frontend_dist` (app/api/frontend_serving.py).

Nunca depende de um build de frontend real nem de instalação de pacote --
usa `tmp_path` fabricado, pra que a ordem de candidatos (bundle empacotado
antes do checkout de desenvolvimento) e o fallback gracioso continuem
corretos independente do ambiente rodando o teste."""

from __future__ import annotations

import inspect

from app.api.frontend_serving import (
    _DEV_CHECKOUT_FRONTEND_DIST,
    _PACKAGE_BUNDLED_FRONTEND_DIST,
    DEFAULT_FRONTEND_DIST,
    _resolve_default_frontend_dist,
)


def _make_build(path):
    path.mkdir(parents=True)
    (path / "index.html").write_text("<html></html>")


def test_prefers_package_bundled_dist_when_both_present(tmp_path):
    bundled = tmp_path / "bundled"
    dev = tmp_path / "dev"
    _make_build(bundled)
    _make_build(dev)

    resolved = _resolve_default_frontend_dist((bundled, dev))

    assert resolved == bundled


def test_falls_back_to_dev_checkout_when_bundle_absent(tmp_path):
    bundled = tmp_path / "bundled-nao-existe"
    dev = tmp_path / "dev"
    _make_build(dev)

    resolved = _resolve_default_frontend_dist((bundled, dev))

    assert resolved == dev


def test_falls_back_to_last_candidate_when_nothing_built(tmp_path):
    """Nenhum build em lugar nenhum -- devolve o último candidato
    inalterado (comportamento histórico), sem levantar exceção;
    `mount_frontend` é quem trata "sem build" graciosamente."""
    bundled = tmp_path / "bundled-nao-existe"
    dev = tmp_path / "dev-nao-existe"

    resolved = _resolve_default_frontend_dist((bundled, dev))

    assert resolved == dev


def test_default_candidate_order_is_package_bundled_then_dev_checkout():
    """A ordem real usada por DEFAULT_FRONTEND_DIST -- bundle empacotado
    (dentro do próprio pacote `app`) checado ANTES do checkout de
    desenvolvimento (`frontend/dist` sibling da raiz do repo)."""
    default_candidates = inspect.signature(_resolve_default_frontend_dist).parameters[
        "candidates"
    ].default
    assert default_candidates == (_PACKAGE_BUNDLED_FRONTEND_DIST, _DEV_CHECKOUT_FRONTEND_DIST)


def test_both_candidates_are_relative_to_this_module_not_a_hardcoded_machine_path():
    """Os dois candidatos são derivados de `Path(__file__)` deste módulo
    -- nunca de um caminho absoluto fixo de máquina de desenvolvedor.
    Ambos compartilham o mesmo ancestral comum (a raiz do
    checkout/pacote), o que só é verdade se forem calculados
    relativamente ao módulo, nunca hardcoded independentemente."""
    package_bundled_repo_root = _PACKAGE_BUNDLED_FRONTEND_DIST.parent.parent
    dev_checkout_repo_root = _DEV_CHECKOUT_FRONTEND_DIST.parent.parent
    assert package_bundled_repo_root == dev_checkout_repo_root
    assert _PACKAGE_BUNDLED_FRONTEND_DIST.name == "frontend_dist"
    assert _DEV_CHECKOUT_FRONTEND_DIST == dev_checkout_repo_root / "frontend" / "dist"


def test_default_frontend_dist_is_one_of_the_two_known_candidates():
    assert DEFAULT_FRONTEND_DIST in (_PACKAGE_BUNDLED_FRONTEND_DIST, _DEV_CHECKOUT_FRONTEND_DIST)
