"""Gold mínimo para la demo desplegada: solo los clientes de los escenarios.

El servidor de la demo (Railway) se construye desde GitHub y el gold completo
(214 MB, derivado del dataset del hackathon) no está en el repo. La demo no
lo necesita: la sesión siempre es la de un cliente de los escenarios, y la
herramienta solo lee las transacciones y reclamos de ese cliente. Este script
copia, para esos clientes, TODAS sus filas de las tres tablas que lee la API.
Son datos sintéticos del mismo tipo que el repo ya publica en sus eval sets.

    uv run python deploy/build_demo_gold.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "warehouse" / "gold"
OUT = Path(__file__).resolve().parent / "demo_gold"
TABLES = ["txn_lookup", "customer_360_min", "dispute_cases"]

sys.path.insert(0, str(ROOT / "app"))


def main() -> None:
    from demo import scenarios
    customers = sorted({s["customer"] for s in scenarios()})
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    ids = ", ".join(f"'{c}'" for c in customers)
    for table in TABLES:
        src = (GOLD / f"{table}.parquet").as_posix()
        dst = (OUT / f"{table}.parquet").as_posix()
        con.sql(f"COPY (SELECT * FROM read_parquet('{src}') WHERE customer_id IN ({ids})) "
                f"TO '{dst}' (FORMAT parquet)")
        rows = con.sql(f"SELECT count(*) FROM read_parquet('{dst}')").fetchone()[0]
        print(f"{table:<18} {rows:>5} filas")
    print(f"{len(customers)} clientes -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
