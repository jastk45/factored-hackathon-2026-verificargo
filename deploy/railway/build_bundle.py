"""Arma la carpeta que se despliega en Railway con `railway up`: SOLO el backend.

Por qué no desde GitHub: la API necesita `warehouse/gold` (214 MB, derivado
del dataset del hackathon) y esos datos no están en el repo. Esta carpeta
junta la API, la política, el modelo de intención y el gold, con el Dockerfile
de deploy/railway/ (sin frontend: el frontend está en Netlify). Se sube a
Railway directamente: los datos no pasan por GitHub ni quedan públicos.

    uv run python deploy/railway/build_bundle.py
    cd deploy/railway/bundle && railway up --service <servicio>
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "bundle"
INCLUDE = ["app", "policy", "models", "eval/reports", "warehouse/gold",
           "pyproject.toml", "uv.lock", ".python-version"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for item in INCLUDE:
        src, dst = ROOT / item, OUT / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=SKIP)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    shutil.copy2(HERE / "Dockerfile", OUT / "Dockerfile")
    (OUT / ".dockerignore").write_text("**/__pycache__\n", encoding="utf-8")

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"-> {OUT.relative_to(ROOT)} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
