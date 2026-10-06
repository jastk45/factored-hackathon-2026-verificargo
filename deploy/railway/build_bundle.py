"""Arma la carpeta que se despliega en Railway con `railway up`.

Por qué no desde GitHub: la API necesita `warehouse/gold` (214 MB, derivado
del dataset del hackathon) y esos datos no están en el repo. Esta carpeta
junta el código, el modelo de intención y el gold, con un Dockerfile que los
copia a la imagen. Se sube a Railway directamente: los datos no pasan por
GitHub ni quedan públicos.

    uv run python deploy/railway/build_bundle.py
    cd deploy/railway/bundle && railway up --service <servicio>
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "bundle"
INCLUDE = ["app", "policy", "models", "eval/reports", "frontend", "warehouse/gold",
           "pyproject.toml", "uv.lock", ".python-version", "Dockerfile"]
SKIP = shutil.ignore_patterns("node_modules", "dist", "__pycache__", "*.pyc", ".vite")


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

    # El Dockerfile del repo no lleva datos; el del bundle sí copia el gold.
    dockerfile = (OUT / "Dockerfile").read_text(encoding="utf-8")
    dockerfile = dockerfile.replace(
        "COPY eval/reports/ eval/reports/",
        "COPY eval/reports/ eval/reports/\nCOPY warehouse/gold/ warehouse/gold/")
    (OUT / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (OUT / ".dockerignore").write_text("**/node_modules\n**/__pycache__\n", encoding="utf-8")

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"-> {OUT.relative_to(ROOT)} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
