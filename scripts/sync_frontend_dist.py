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
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "frontend" / "dist"
TARGET = REPO_ROOT / "app" / "frontend_dist"


def main() -> int:
    if not (SOURCE / "index.html").is_file():
        print(
            f"erro: {SOURCE} não contém um build de frontend "
            "(rode `npm install && npm run build` em frontend/ primeiro)",
            file=sys.stderr,
        )
        return 1

    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"OK: {SOURCE} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
