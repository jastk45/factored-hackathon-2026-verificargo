"""Manifiesto de trazabilidad de los artefactos de datos publicados.

Registra, para cada parquet de silver y gold que usa el sistema: hash del
archivo, filas, columnas y rango de fechas (si tiene), junto con el commit del
código y la fecha de referencia del sistema. `tests/test_data_manifest.py`
comprueba que los datos en disco sean exactamente los registrados: si alguien
reconstruye el warehouse y cambia algo, el test lo detecta antes de evaluar.

Por qué un manifiesto aparte. `docs/build_manifest.json` lo escribió silver.py
el 28 de septiembre, antes del primer commit (por eso dice "no-commit"). No se
reconstruye el warehouse encima de los eval sets congelados: se registra lo
que hay y se verifica que no cambie.

    uv run python pipeline/manifest.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = REPO_ROOT / "warehouse"
OUT = REPO_ROOT / "docs" / "data_manifest.json"
ARTIFACTS = [
    "gold/txn_lookup.parquet", "gold/customer_360_min.parquet", "gold/dispute_cases.parquet",
    "silver/customers.parquet", "silver/products.parquet", "silver/transactions.parquet",
    "silver/complaints.parquet",
]
DATE_COLUMNS = ("transaction_date", "created_at", "customer_since")
# El "hoy" del sistema: la última fecha con datos (app/tools.py).
REFERENCE_DATE = "2026-06-18"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    src = f"read_parquet('{path.as_posix()}')"
    cols = [c[0] for c in con.sql(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    info = {
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "rows": con.sql(f"SELECT count(*) FROM {src}").fetchone()[0],
        "columns": len(cols),
        "modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
    }
    for col in DATE_COLUMNS:
        if col in cols:
            lo, hi = con.sql(f"SELECT min({col}), max({col}) FROM {src}").fetchone()
            info["date_range"] = {"column": col, "min": str(lo), "max": str(hi)}
            break
    return info


def git_commit() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> None:
    con = duckdb.connect()
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "reference_date": REFERENCE_DATE,
        "note": ("Artefactos construidos el 27-28 sep (antes del primer commit). Se registran "
                 "y verifican; no se reconstruyen encima de los eval sets congelados."),
        "artifacts": {name: describe(con, WAREHOUSE / name) for name in ARTIFACTS
                      if (WAREHOUSE / name).exists()},
    }
    OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, info in manifest["artifacts"].items():
        print(f"{name:<34} {info['rows']:>10,} filas  {info['sha256'][:12]}")
    print(f"-> {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
