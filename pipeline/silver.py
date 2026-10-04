"""Capa silver: tipado, contratos, cuarentena y conversión de moneda.

Lee los CSV crudos, aplica el contrato de cada tabla y escribe dos salidas por
tabla en `warehouse/silver/`:

    <tabla>.parquet              filas que cumplen el contrato
    <tabla>_quarantine.parquet   filas que no, con el ID de la regla violada

Nada se corrige en silencio. Lo que no cumple se aparta y se cuenta.

Solo pasan las 4 tablas que el agente consulta (D-12); las otras 9 se quedan en
bronze a propósito.

    uv run python pipeline/silver.py
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from contracts import CONTRACT_VERSION, CONTRACTS

REPO_ROOT = Path(__file__).resolve().parent.parent
SILVER_DIR = REPO_ROOT / "warehouse" / "silver"
MANIFEST_PATH = REPO_ROOT / "docs" / "build_manifest.json"

SOURCES = {
    "customers": "read_csv_auto('data/customers.csv')",
    "products": "read_csv_auto('data/products.csv')",
    "transactions": (
        "read_csv_auto('data/transactions/*/*/*/*.csv', "
        "union_by_name=true, ignore_errors=true)"
    ),
    "complaints": (
        "read_csv_auto('data/complaints/*/*/*/*.csv', "
        "union_by_name=true, ignore_errors=true)"
    ),
}

# Normalización de catálogos: el mismo país aparece escrito de varias formas.
COUNTRY_FIX = """
    CASE
        WHEN lower(strip_accents(country)) IN ('mexico', 'méxico', 'mx') THEN 'México'
        WHEN lower(strip_accents(country)) IN ('colombia', 'co') THEN 'Colombia'
        WHEN lower(strip_accents(country)) IN ('argentina', 'ar') THEN 'Argentina'
        ELSE country
    END
"""


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 - el repo puede no tener commits aún
        return "no-commit"


def select_clause(table: str, con: duckdb.DuckDBPyConnection) -> str:
    """Columnas de la tabla, con las normalizaciones que correspondan."""
    cols = con.sql(f"SELECT * FROM {SOURCES[table]} LIMIT 1").columns
    parts = []
    for col in cols:
        if col == "country":
            parts.append(f"{COUNTRY_FIX} AS country")
        else:
            parts.append(f'"{col}"')
    return ", ".join(parts)


def build_table(con: duckdb.DuckDBPyConnection, table: str) -> dict:
    contract = CONTRACTS[table]
    src = SOURCES[table]
    started = time.perf_counter()

    con.sql(
        f"""
        CREATE OR REPLACE VIEW raw_{table} AS
        SELECT {select_clause(table, con)} FROM {src}
        """
    )
    total = con.sql(f"SELECT count(*) FROM raw_{table}").fetchone()[0]

    # Deduplicación por clave natural: se conserva la fila más reciente. El
    # dataset anuncia ~2% de duplicados; medimos cuántos hay de verdad.
    pk = contract.primary_key
    order_col = next(
        (c for c in ("last_updated", "process_date", "creation_date")
         if c in con.sql(f"SELECT * FROM raw_{table} LIMIT 1").columns),
        pk,
    )
    con.sql(
        f"""
        CREATE OR REPLACE VIEW dedup_{table} AS
        SELECT * EXCLUDE (_rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY {pk} ORDER BY {order_col} DESC
            ) AS _rn
            FROM raw_{table}
        ) WHERE _rn = 1
        """
    )
    deduped = con.sql(f"SELECT count(*) FROM dedup_{table}").fetchone()[0]

    bad = contract.quarantine_predicate()
    reason = contract.reason_expression()

    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    clean_path = SILVER_DIR / f"{table}.parquet"
    quarantine_path = SILVER_DIR / f"{table}_quarantine.parquet"

    con.sql(
        f"""
        COPY (SELECT * FROM dedup_{table} WHERE NOT ({bad}))
        TO '{clean_path.as_posix()}' (FORMAT parquet)
        """
    )
    con.sql(
        f"""
        COPY (
            SELECT *, {reason} AS dq_rule_violated
            FROM dedup_{table} WHERE {bad}
        ) TO '{quarantine_path.as_posix()}' (FORMAT parquet)
        """
    )

    clean = con.sql(
        f"SELECT count(*) FROM read_parquet('{clean_path.as_posix()}')"
    ).fetchone()[0]
    quarantined = deduped - clean

    # Desglose de cuarentena por regla, para el informe de calidad.
    by_rule: dict[str, int] = {}
    if quarantined:
        rows = con.sql(
            f"""
            SELECT dq_rule_violated, count(*)
            FROM read_parquet('{quarantine_path.as_posix()}')
            GROUP BY 1 ORDER BY 2 DESC
            """
        ).fetchall()
        by_rule = {str(r[0]): r[1] for r in rows}

    # Reglas 'warn': no apartan filas, pero se cuentan.
    warnings: dict[str, int] = {}
    for rule in contract.rules:
        if rule.severity != "warn":
            continue
        n = con.sql(
            f"SELECT count(*) FROM dedup_{table} "
            f"WHERE NOT coalesce({rule.expr}, TRUE)"
        ).fetchone()[0]
        if n:
            warnings[rule.id] = n

    elapsed = time.perf_counter() - started
    print(
        f"  {table:14s} {total:>9,} -> dedup {deduped:>9,} "
        f"-> limpias {clean:>9,} · cuarentena {quarantined:,} "
        f"({elapsed:.1f}s)"
    )
    for rule_id, n in by_rule.items():
        print(f"      cuarentena {rule_id}: {n:,}")
    for rule_id, n in warnings.items():
        print(f"      aviso      {rule_id}: {n:,}")

    return {
        "rows_raw": total,
        "rows_after_dedup": deduped,
        "duplicates_removed": total - deduped,
        "rows_clean": clean,
        "rows_quarantined": quarantined,
        "quarantine_by_rule": by_rule,
        "warnings": warnings,
        "seconds": round(elapsed, 2),
    }


def add_usd(con: duckdb.DuckDBPyConnection) -> dict:
    """Recalcula amount_usd con las tasas diarias (57% viene nulo)."""
    path = SILVER_DIR / "transactions.parquet"
    rates = "read_csv_auto('data/daily_exchange_rates.csv')"
    tmp = SILVER_DIR / "transactions_usd.parquet"

    con.sql(
        f"""
        COPY (
            SELECT t.* EXCLUDE (amount_usd),
                   CASE
                       WHEN t.currency = 'USD' THEN t.amount
                       WHEN r.exchange_rate IS NOT NULL
                           THEN round(t.amount * r.exchange_rate, 2)
                       ELSE t.amount_usd
                   END AS amount_usd,
                   CASE
                       WHEN t.currency = 'USD' THEN 'identity'
                       WHEN r.exchange_rate IS NOT NULL THEN 'daily_rate'
                       WHEN t.amount_usd IS NOT NULL THEN 'source'
                       ELSE 'unavailable'
                   END AS amount_usd_source
            FROM read_parquet('{path.as_posix()}') t
            LEFT JOIN {rates} r
              ON r.date = CAST(t.transaction_date AS DATE)
             AND r.source_currency = t.currency
             AND r.target_currency = 'USD'
        ) TO '{tmp.as_posix()}' (FORMAT parquet)
        """
    )
    tmp.replace(path)

    rows = con.sql(
        f"""
        SELECT amount_usd_source, count(*)
        FROM read_parquet('{path.as_posix()}')
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    coverage = {str(r[0]): r[1] for r in rows}
    print("  conversión USD: " + " · ".join(f"{k}={v:,}" for k, v in coverage.items()))
    return coverage


def main() -> None:
    con = duckdb.connect()
    con.sql("SET preserve_insertion_order = false")  # menos memoria en 4,4M filas

    print(f"Silver · contrato v{CONTRACT_VERSION}\n")
    stats = {table: build_table(con, table) for table in SOURCES}

    print()
    usd_coverage = add_usd(con)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "contract_version": CONTRACT_VERSION,
        "git_commit": git_commit(),
        "tables_in_silver": list(SOURCES),
        "tables_left_in_bronze": [
            "branches", "service_agents", "marketing_campaigns",
            "daily_exchange_rates", "call_center_interactions",
            "call_transcripts", "satisfaction_surveys", "campaign_sends",
            "digital_events",
        ],
        "stats": stats,
        "usd_coverage": usd_coverage,
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nManifiesto -> {MANIFEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
