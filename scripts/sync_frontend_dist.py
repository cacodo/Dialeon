"""Copia `frontend/dist/` (build de produção já gerado por `npm run build`)
para `app/frontend_dist/` -- Release Packaging Contract V1.

Passo intermediário do build order de release, ANTES de `python -m build`
(ver README, secao "Empacotamento de release"). Nunca roda automaticamente
(nem em `pip install -e .`, nem na suíte de testes do backend) -- rodar
testes/backend sozinho nunca precisa de Node nem deste script.

`app/frontend_dist/` é puramente um artefato de build -- nunca versionado
em Git (ver .gitignore); este script é a ÚNICA cópia programática dele,
nunca uma duplicata mantida manualmente. `frontend/dist/` (fonte da cópia)
continua a única saída real do build de frontend; este script nunca decide
como construir o frontend, só onde colocar o resultado já construído pra
`app/api/frontend_serving.py`/o empacotamento do wheel/sdist o encontrarem
dentro do pacote Python.

Identidade do bundle servido -- `app/frontend_dist/`, quando existe, tem
PRECEDÊNCIA sobre `frontend/dist/` em `/app` (é o local que existe num
pacote instalado). Num checkout, um `app/frontend_dist/` de uma sincronização
anterior continua sendo servido depois de um `npm run build` novo, até ser
sincronizado de novo. Por isso:

- depois de copiar, o script CONFERE que o destino é byte a byte idêntico à
  fonte (mesmo conjunto de arquivos, mesmos bytes) e falha se não for;
- `--check` só confere (não copia nada): sai com 1 e lista as diferenças se
  o bundle que `/app` serviria não for o build atual de `frontend/dist/`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "frontend" / "dist"
TARGET = REPO_ROOT / "app" / "frontend_dist"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def bundle_differences(source: Path, target: Path) -> list[str]:
    """Diferenças entre o build (`source`) e o bundle sincronizado
    (`target`): arquivo só de um lado ou com bytes diferentes. Lista vazia
    = idênticos. Um lado sem `index.html` nunca é considerado idêntico."""
    problems = [
        f"sem index.html: {root}" for root in (source, target) if not (root / "index.html").is_file()
    ]
    if problems:
        return problems
    built, synced = _files(source), _files(target)
    return (
        [f"só no build: {name}" for name in sorted(built.keys() - synced.keys())]
        + [f"só no bundle sincronizado: {name}" for name in sorted(synced.keys() - built.keys())]
        + [
            f"conteúdo diferente: {name}"
            for name in sorted(built.keys() & synced.keys())
            if built[name] != synced[name]
        ]
    )


def sync(source: Path, target: Path) -> list[str]:
    """Substitui `target` inteiro por uma cópia de `source` e devolve as
    diferenças que restarem (sempre vazia se a cópia deu certo)."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return bundle_differences(source, target)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check_only = args == ["--check"]
    if args and not check_only:
        print("uso: python scripts/sync_frontend_dist.py [--check]", file=sys.stderr)
        return 2

    if not (SOURCE / "index.html").is_file():
        print(
            f"erro: {SOURCE} não contém um build de frontend "
            "(rode `npm install && npm run build` em frontend/ primeiro)",
            file=sys.stderr,
        )
        return 1

    differences = bundle_differences(SOURCE, TARGET) if check_only else sync(SOURCE, TARGET)
    if differences:
        print(
            f"erro: {TARGET} não é idêntico ao build em {SOURCE}"
            + (" (rode `python scripts/sync_frontend_dist.py`)" if check_only else ""),
            file=sys.stderr,
        )
        for difference in differences:
            print(f"  - {difference}", file=sys.stderr)
        return 1
    print(f"OK: {TARGET} idêntico a {SOURCE}" if check_only else f"OK: {SOURCE} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
