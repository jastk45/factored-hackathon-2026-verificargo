"""Genera fixtures/incremental_v2/: un FIXTURE DE PRUEBA, no datos de producción.

Parte de quejas reales del silver y fabrica una segunda entrega con todo lo
que rompe a un incremental ingenuo:

  base/   2 días particionados (2026-06-10 y 2026-06-11), 40 quejas cada uno
  delta/  - un día nuevo (2026-06-12) con 45 quejas nuevas
          - 5 actualizaciones de quejas ya cargadas (cambia el status)
          - 3 quejas que llegan TARDE a la partición vieja 2026-06-10
          - una columna nueva, `case_channel_detail` (evolución de esquema)

    uv run python pipeline/build_incremental_fixture.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "warehouse" / "silver" / "complaints.parquet"
OUT = ROOT / "fixtures" / "incremental_v2"
COLS = ("complaint_id, CAST(creation_date AS DATE) AS creation_date, process_date, "
        "customer_id, subcategory, claimed_amount, currency, priority, status")


def part(stage: str, day: str) -> Path:
    y, m, d = day.split("-")
    p = OUT / stage / f"year={y}" / f"month={m}" / f"day={d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def main() -> None:
    con = duckdb.connect()
    con.sql(f"CREATE VIEW c AS SELECT {COLS} FROM read_parquet('{SILVER.as_posix()}') "
            "ORDER BY complaint_id")
    rows = con.sql("SELECT * FROM c LIMIT 131").df()

    def write(df, stage, day, name):
        df = df.copy()
        df["process_date"] = day
        df.to_csv(part(stage, day) / name, index=False)

    write(rows.iloc[0:40], "base", "2026-06-10", "complaints_20260610.csv")
    write(rows.iloc[40:80], "base", "2026-06-11", "complaints_20260611.csv")

    new_day = rows.iloc[80:125].copy()
    updates = rows.iloc[0:5].copy()
    updates["status"] = "Resolved"                       # cambio de estado
    delta = __import__("pandas").concat([new_day, updates])
    delta["case_channel_detail"] = "app_v2"              # columna nueva
    write(delta, "delta", "2026-06-12", "complaints_20260612.csv")

    late = rows.iloc[125:128]
    write(late, "delta", "2026-06-10", "complaints_20260610_late.csv")

    (OUT / "README.md").write_text(
        "# FIXTURE DE PRUEBA — no son datos de producción\n\n"
        "Generado por `pipeline/build_incremental_fixture.py` a partir de quejas "
        "reales del silver. Simula una segunda entrega con 45 altas, 5 "
        "actualizaciones, 3 llegadas tardías a una partición vieja y una columna "
        "nueva. Lo usa `tests/test_incremental.py`.\n", encoding="utf-8")
    print(f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
