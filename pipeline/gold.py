"""Capa gold: tablas con la forma que el agente consulta.

`txn_lookup` es la tabla que responde "¿qué transacciones tiene este cliente
alrededor de esta fecha y este monto?". Lleva solo los campos que el servicio
necesita (minimización de datos) y está ordenada por cliente y fecha para que la
búsqueda por cliente sea barata.

    uv run python pipeline/gold.py
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
SILVER = REPO_ROOT / "warehouse" / "silver"
GOLD = REPO_ROOT / "warehouse" / "gold"


def silver(name: str) -> str:
    return f"read_parquet('{(SILVER / f'{name}.parquet').as_posix()}')"


def build_txn_lookup(con: duckdb.DuckDBPyConnection) -> int:
    """Transacciones por cliente, con lo justo para localizar una disputa."""
    out = GOLD / "txn_lookup.parquet"
    started = time.perf_counter()

    con.sql(
        f"""
        COPY (
            SELECT
                t.transaction_id,
                t.customer_id,
                t.product_id,
                CAST(t.transaction_date AS TIMESTAMP)   AS transaction_at,
                CAST(t.transaction_date AS DATE)        AS transaction_date,
                t.amount,
                t.currency,
                t.amount_usd,
                t.amount_usd_source,
                t.transaction_type,
                t.transaction_category,
                t.channel,
                t.merchant_name,
                t.merchant_category,
                t.transaction_status,
                t.response_code,
                t.transaction_country,
                t.transaction_city,
                -- is_fraud y fraud_score son etiquetas retrospectivas: viajan
                -- como evidencia para el handoff, nunca como feature del
                -- clasificador ni como criterio de elegibilidad.
                t.is_fraud   AS evidence_is_fraud,
                t.fraud_score AS evidence_fraud_score
            FROM {silver('transactions')} t
            ORDER BY t.customer_id, t.transaction_date DESC
        ) TO '{out.as_posix()}' (FORMAT parquet)
        """
    )
    n = con.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    print(f"  txn_lookup       {n:>9,} filas ({time.perf_counter() - started:.1f}s)")
    return n


def build_customer_360_min(con: duckdb.DuckDBPyConnection) -> int:
    """Solo los campos del cliente que el servicio necesita.

    Quedan fuera a propósito: documento de identidad, dirección, teléfonos,
    email, fecha de nacimiento, ingresos y ocupación. El agente no los necesita
    para tramitar una disputa, así que no entran al contexto del modelo.
    """
    out = GOLD / "customer_360_min.parquet"
    con.sql(
        f"""
        COPY (
            SELECT
                c.customer_id,
                c.first_name,
                c.country,
                c.segment,
                c.customer_status,
                c.detected_accent,
                CAST(c.registration_date AS DATE) AS customer_since,
                count(p.product_id)                      AS products_total,
                count(p.product_id) FILTER (
                    WHERE p.product_status = 'Active')   AS products_active,
                count(p.product_id) FILTER (
                    WHERE p.product_type ILIKE '%Card%') AS cards_total
            FROM {silver('customers')} c
            LEFT JOIN {silver('products')} p USING (customer_id)
            GROUP BY ALL
        ) TO '{out.as_posix()}' (FORMAT parquet)
        """
    )
    n = con.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    print(f"  customer_360_min {n:>9,} filas")
    return n


def build_dispute_cases(con: duckdb.DuckDBPyConnection) -> int:
    """Disputas históricas, para el análisis de demanda.

    `affected_product_id` NO se expone: F-07 demostró que el 100% apunta a un
    producto de otro cliente. Se conserva el flag de la violación para poder
    demostrarla, pero el id no viaja.
    """
    out = GOLD / "dispute_cases.parquet"
    con.sql(
        f"""
        COPY (
            SELECT
                c.complaint_id,
                c.customer_id,
                CAST(c.creation_date AS TIMESTAMP) AS created_at,
                c.subcategory,
                c.reception_channel,
                c.claimed_amount,
                c.currency,
                c.priority,
                c.status,
                c.sla_breached,
                c.resolution_days,
                (c.affected_product_id IS NOT NULL) AS had_cross_customer_product_ref
            FROM {silver('complaints')} c
            WHERE c.subcategory IN ('Cargo no reconocido', 'Cobro indebido')
        ) TO '{out.as_posix()}' (FORMAT parquet)
        """
    )
    n = con.sql(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    print(f"  dispute_cases    {n:>9,} filas")
    return n


def main() -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("SET preserve_insertion_order = false")

    print("Gold\n")
    build_txn_lookup(con)
    build_customer_360_min(con)
    build_dispute_cases(con)
    print(f"\n-> {GOLD.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
