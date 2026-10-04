"""Carga incremental correcta frente a llegadas tardías.

El dataset llega particionado por fecha (year=/month=/day=) y advierte de
llegadas tardías: un registro puede aparecer después en una partición VIEJA.
Un incremental que solo lee "particiones posteriores al último watermark de
fecha" lo pierde sin avisar. Es la trampa clásica.

Este módulo procesa por **manifiesto de archivos**: recuerda qué archivos ya
leyó (ruta + tamaño + mtime) y en cada corrida toma todos los que no estén en
el manifiesto, estén en la partición que estén. Después hace upsert por clave
primaria quedándose con la versión más reciente.

Garantía que verifica tests/test_incremental.py:

    incremental(base) + incremental(delta)  ==  full_rebuild(base + delta)

incluyendo duplicados, actualizaciones, llegadas tardías y una columna nueva
(evolución de esquema).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

PK = "complaint_id"
ORDER = "process_date"


def discover(root: Path) -> list[Path]:
    return sorted(root.rglob("*.csv"))


def fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{path.as_posix()}|{st.st_size}|{int(st.st_mtime)}"


def _read(files: list[Path]) -> str:
    listing = ", ".join(f"'{f.as_posix()}'" for f in files)
    return (f"read_csv_auto([{listing}], union_by_name=true, filename=true, "
            "hive_partitioning=false)")


def full_rebuild(root: Path) -> duckdb.DuckDBPyRelation:
    """Lee todo y deduplica: la referencia contra la que se compara."""
    con = duckdb.connect()
    return con.sql(f"""
        SELECT * EXCLUDE (filename, _rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY {PK}
                   ORDER BY {ORDER} DESC, filename DESC) AS _rn
            FROM {_read(discover(root))})
        WHERE _rn = 1""")


def incremental(root: Path, state_dir: Path) -> dict:
    """Procesa solo archivos nuevos y hace upsert sobre el estado anterior."""
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = state_dir / "manifest.json"
    table_path = state_dir / "silver.parquet"

    manifest = set(json.loads(manifest_path.read_text(encoding="utf-8"))) \
        if manifest_path.exists() else set()
    files = discover(root)
    new = [f for f in files if fingerprint(f) not in manifest]
    if not new:
        return {"new_files": 0, "rows": None}

    con = duckdb.connect()
    batch = f"(SELECT * EXCLUDE (filename), filename AS _src FROM {_read(new)})"
    if table_path.exists():
        source = (f"(SELECT * FROM read_parquet('{table_path.as_posix()}') "
                  f"UNION ALL BY NAME SELECT * FROM {batch})")
    else:
        source = batch

    tmp = state_dir / "silver.tmp.parquet"
    con.sql(f"""
        COPY (
            SELECT * EXCLUDE (_rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY {PK}
                       ORDER BY {ORDER} DESC, _src DESC) AS _rn
                FROM {source})
            WHERE _rn = 1
        ) TO '{tmp.as_posix()}' (FORMAT parquet)""")
    tmp.replace(table_path)

    manifest |= {fingerprint(f) for f in new}
    manifest_path.write_text(json.dumps(sorted(manifest), indent=1), encoding="utf-8")
    rows = con.sql(f"SELECT count(*) FROM read_parquet('{table_path.as_posix()}')").fetchone()[0]
    return {"new_files": len(new), "rows": rows}


def watermark_incremental(root: Path, watermark: str) -> list[Path]:
    """El enfoque ingenuo: solo particiones con fecha posterior al watermark.

    Se deja acá para que el test demuestre qué se pierde.
    """
    keep = []
    for f in discover(root):
        parts = dict(p.split("=") for p in f.parent.as_posix().split("/") if "=" in p)
        day = f"{parts['year']}-{parts['month']}-{parts['day']}"
        if day > watermark:
            keep.append(f)
    return keep
