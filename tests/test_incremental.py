"""Corrección de la carga incremental (el reto lo pide con datos estáticos).

Usa el fixture etiquetado fixtures/incremental_v2/ (ver su README).

    uv run pytest tests/test_incremental.py -v
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from incremental import full_rebuild, incremental, watermark_incremental  # noqa: E402

FIXTURE = ROOT / "fixtures" / "incremental_v2"
pytestmark = pytest.mark.skipif(not (FIXTURE / "base").exists(),
                                reason="correr pipeline/build_incremental_fixture.py")


def snapshot(rel) -> list[tuple]:
    cols = ["complaint_id", "status", "priority", "process_date"]
    return sorted(tuple(str(r[c]) for c in cols) for r in rel.df().to_dict("records"))


@pytest.fixture
def landing(tmp_path) -> Path:
    """Zona de aterrizaje: primero llega la base; el delta llega después."""
    root = tmp_path / "landing"
    shutil.copytree(FIXTURE / "base", root)
    return root


def deliver_delta(root: Path) -> None:
    for f in (FIXTURE / "delta").rglob("*.csv"):
        dest = root / f.relative_to(FIXTURE / "delta")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)


def test_incremental_equals_full_rebuild(landing, tmp_path) -> None:
    state = tmp_path / "state"
    first = incremental(landing, state)
    assert first["new_files"] == 2 and first["rows"] == 80

    deliver_delta(landing)
    second = incremental(landing, state)
    assert second["new_files"] == 2, "debe tomar el día nuevo Y el archivo tardío"

    import duckdb
    inc = duckdb.connect().sql(f"SELECT * FROM read_parquet('{(state / 'silver.parquet').as_posix()}')")
    assert snapshot(inc) == snapshot(full_rebuild(landing))
    assert second["rows"] == 80 + 45 + 3   # las 5 actualizaciones no suman filas


def test_updates_keep_the_latest_version(landing, tmp_path) -> None:
    state = tmp_path / "state"
    incremental(landing, state)
    deliver_delta(landing)
    incremental(landing, state)
    import duckdb
    statuses = duckdb.connect().sql(f"""
        SELECT status FROM read_parquet('{(state / 'silver.parquet').as_posix()}')
        WHERE complaint_id IN (SELECT complaint_id FROM read_csv_auto(
            '{(FIXTURE / 'base' / 'year=2026' / 'month=06' / 'day=10' / 'complaints_20260610.csv').as_posix()}')
            LIMIT 5)""").fetchall()
    assert {s[0] for s in statuses} == {"Resolved"}


def test_late_arrival_is_captured(landing, tmp_path) -> None:
    state = tmp_path / "state"
    incremental(landing, state)
    deliver_delta(landing)
    incremental(landing, state)
    import duckdb
    late = duckdb.connect().sql(f"""SELECT count(*) FROM read_parquet(
        '{(state / 'silver.parquet').as_posix()}') WHERE _src LIKE '%_late.csv'""").fetchone()[0]
    assert late == 3


def test_new_column_survives_the_merge(landing, tmp_path) -> None:
    """Evolución de esquema: la columna nueva aparece; las filas viejas la tienen nula."""
    state = tmp_path / "state"
    incremental(landing, state)
    deliver_delta(landing)
    incremental(landing, state)
    import duckdb
    rel = duckdb.connect().sql(f"SELECT * FROM read_parquet('{(state / 'silver.parquet').as_posix()}')")
    assert "case_channel_detail" in rel.columns


def test_rerunning_without_new_files_is_a_no_op(landing, tmp_path) -> None:
    state = tmp_path / "state"
    incremental(landing, state)
    assert incremental(landing, state)["new_files"] == 0


def test_naive_date_watermark_loses_the_late_arrival(landing) -> None:
    """La trampa documentada: un watermark por fecha no ve la partición vieja."""
    deliver_delta(landing)
    picked = watermark_incremental(landing, watermark="2026-06-11")
    assert any(p.name.endswith("20260612.csv") for p in picked)
    assert not any(p.name.endswith("_late.csv") for p in picked), (
        "el watermark ingenuo no debería ver el archivo tardío: si lo viera, "
        "el test dejaría de demostrar la trampa")
