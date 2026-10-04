"""Impide que las credenciales lleguen al repo.

La regla "las credenciales nunca entran al repo" solo sirve si se puede
verificar. Este test falla si algun archivo versionado contiene un Access Key
de AWS, o si .env dejara de estar ignorado.

    uv run pytest tests/test_no_secrets.py
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Formato de un Access Key ID de AWS: AKIA + 16 alfanumericos en mayusculas.
AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")

# .env.example documenta los nombres de las variables sin valores reales.
ALLOWED = {".env.example", "tests/test_no_secrets.py"}


def tracked_files() -> list[str]:
    """Archivos que git versionaria (sin los ignorados)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_env_is_ignored() -> None:
    """.env existe pero git no debe verlo."""
    if not (REPO_ROOT / ".env").exists():
        pytest.skip(".env no existe en este entorno")
    assert ".env" not in tracked_files(), (
        ".env aparece entre los archivos versionados. "
        "Revisa .gitignore antes de hacer commit."
    )


def test_no_aws_keys_in_tracked_files() -> None:
    """Ningun archivo versionado contiene un Access Key de AWS."""
    offenders: list[str] = []
    for rel in tracked_files():
        if rel in ALLOWED:
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue  # binario o ilegible: no puede contener el patron en claro
        if AWS_KEY_RE.search(text):
            offenders.append(rel)

    assert not offenders, (
        "Access Key de AWS encontrado en archivos versionados: "
        + ", ".join(offenders)
    )
