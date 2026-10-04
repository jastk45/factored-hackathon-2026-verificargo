"""El código fuente no contiene caracteres de control.

Durante el desarrollo, tres veces un `\b` de una expresión regular se convirtió
en un carácter de retroceso (0x08) al escribirse desde la terminal: el patrón
quedaba exigiendo un carácter invisible y nunca coincidía, sin ningún error.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(
    p for folder in ("app", "pipeline", "ml", "eval", "tests")
    for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_control_characters(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    bad = [(i + 1, hex(ord(c))) for i, line in enumerate(text.splitlines())
           for c in line if ord(c) < 32 and c != "\t"]
    assert not bad, f"caracteres de control en {path.name}: {bad[:5]}"
