"""Los datos en disco son exactamente los que registra docs/data_manifest.json.

Si alguien reconstruye el warehouse y cambia una fila, los eval sets congelados
dejarían de corresponder a los datos: este test lo detecta antes de evaluar.

    uv run pytest tests/test_data_manifest.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

MANIFEST = REPO_ROOT / "docs" / "data_manifest.json"
pytestmark = pytest.mark.skipif(not (REPO_ROOT / "warehouse" / "gold").exists(),
                                reason="warehouse no construido")


def test_published_artifacts_match_the_manifest() -> None:
    from manifest import sha256
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["git_commit"] != "no-commit"
    for name, info in manifest["artifacts"].items():
        path = REPO_ROOT / "warehouse" / name
        assert path.exists(), name
        assert sha256(path) == info["sha256"], f"{name} cambió respecto del manifiesto"
