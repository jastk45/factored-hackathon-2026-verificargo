"""Verifica que la capa silver/gold cumple lo que promete.

Estos tests son el contrato ejecutable: si alguien cambia el pipeline y rompe
una garantía en la que el agente confía, fallan acá y no en producción.

    uv run pytest tests/test_contracts.py -v
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SILVER = REPO_ROOT / "warehouse" / "silver"
GOLD = REPO_ROOT / "warehouse" / "gold"

pytestmark = pytest.mark.skipif(
    not (SILVER / "transactions.parquet").exists(),
    reason="capa silver no construida; correr pipeline/silver.py",
)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def parquet(root: Path, name: str) -> str:
    return f"read_parquet('{(root / f'{name}.parquet').as_posix()}')"


# --- Garantías sobre las que el agente construye ------------------------

def test_no_duplicate_primary_keys(con) -> None:
    for table, pk in [
        ("customers", "customer_id"),
        ("products", "product_id"),
        ("transactions", "transaction_id"),
        ("complaints", "complaint_id"),
    ]:
        total, distinct = con.sql(
            f"SELECT count(*), count(DISTINCT {pk}) FROM {parquet(SILVER, table)}"
        ).fetchone()
        assert total == distinct, f"{table}: {total - distinct} PK duplicadas"


def test_every_transaction_has_an_owner(con) -> None:
    """Sin dueño no se puede autorizar el acceso: es la base del aislamiento."""
    orphans = con.sql(
        f"SELECT count(*) FROM {parquet(SILVER, 'transactions')} "
        "WHERE customer_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_transaction_product_belongs_to_same_customer(con) -> None:
    """A diferencia de complaints, transactions sí respeta la propiedad.

    Por eso `transactions` es el universo de hechos verificables del sistema.
    """
    crossed = con.sql(
        f"""
        SELECT count(*) FROM {parquet(SILVER, 'transactions')} t
        JOIN {parquet(SILVER, 'products')} p USING (product_id)
        WHERE t.customer_id <> p.customer_id
        """
    ).fetchone()[0]
    assert crossed == 0


def test_amounts_are_positive(con) -> None:
    """El signo va en transaction_type, nunca en el monto."""
    bad = con.sql(
        f"SELECT count(*) FROM {parquet(SILVER, 'transactions')} WHERE amount <= 0"
    ).fetchone()[0]
    assert bad == 0


def test_claimed_amount_always_has_currency(con) -> None:
    """Un monto sin moneda no es comparable contra un umbral de política."""
    bad = con.sql(
        f"""
        SELECT count(*) FROM {parquet(SILVER, 'complaints')}
        WHERE claimed_amount IS NOT NULL AND currency IS NULL
        """
    ).fetchone()[0]
    assert bad == 0, "CMP-07 debería haberlas mandado a cuarentena"


def test_country_catalog_is_normalized(con) -> None:
    countries = {
        r[0] for r in con.sql(
            f"SELECT DISTINCT country FROM {parquet(SILVER, 'customers')}"
        ).fetchall()
    }
    assert countries <= {"México", "Colombia", "Argentina"}, countries


def test_usd_conversion_is_almost_complete(con) -> None:
    """El 57% venía nulo; silver lo recalcula con las tasas diarias."""
    total, missing = con.sql(
        f"""
        SELECT count(*), count(*) FILTER (WHERE amount_usd IS NULL)
        FROM {parquet(SILVER, 'transactions')}
        """
    ).fetchone()
    assert missing / total < 0.001, f"{missing:,} de {total:,} sin USD"


def test_unconvertible_rows_are_flagged_not_guessed(con) -> None:
    """Lo que no se puede convertir se marca; nunca se inventa una tasa.

    Son transacciones del 2026-06-18, un día después de la última tasa
    disponible. Es un caso de frescura real y el sistema debe escalarlo.
    """
    rows = con.sql(
        f"""
        SELECT count(*) FROM {parquet(SILVER, 'transactions')}
        WHERE amount_usd_source = 'unavailable' AND amount_usd IS NOT NULL
        """
    ).fetchone()[0]
    assert rows == 0, "una fila sin tasa no puede tener amount_usd"


# --- Aislamiento por cliente (F-07) -------------------------------------

def test_dq_product_ownership_violation_is_recorded(con) -> None:
    """F-07: el 100% de affected_product_id apunta a un producto de otro cliente.

    El dato crudo está roto, así que el gold NO expone ese id. Este test fija
    el hallazgo: si una versión futura del dataset lo arreglara, falla y nos
    obliga a revisar la decisión.
    """
    total, flagged = con.sql(
        f"""
        SELECT count(*), count(*) FILTER (WHERE had_cross_customer_product_ref)
        FROM {parquet(GOLD, 'dispute_cases')}
        """
    ).fetchone()
    assert flagged > 0, "esperábamos referencias cruzadas marcadas"

    crossed = con.sql(
        f"""
        SELECT count(*) FROM {parquet(SILVER, 'complaints')} c
        JOIN {parquet(SILVER, 'products')} p
          ON c.affected_product_id = p.product_id
        WHERE c.affected_product_id IS NOT NULL
          AND p.customer_id = c.customer_id
        """
    ).fetchone()[0]
    assert crossed == 0, (
        "Alguna queja apunta ahora a un producto del mismo cliente: "
        "F-07 cambió y hay que revisar la decisión de no exponer el id."
    )


def test_gold_does_not_expose_affected_product_id(con) -> None:
    cols = con.sql(f"SELECT * FROM {parquet(GOLD, 'dispute_cases')} LIMIT 1").columns
    assert "affected_product_id" not in cols


def test_customer_360_min_excludes_pii(con) -> None:
    """Minimización de datos: el agente no necesita documento ni dirección."""
    cols = set(con.sql(
        f"SELECT * FROM {parquet(GOLD, 'customer_360_min')} LIMIT 1"
    ).columns)
    forbidden = {
        "document_number", "address", "email", "mobile_phone",
        "landline_phone", "date_of_birth", "estimated_monthly_income",
        "occupation", "last_name",
    }
    assert not (cols & forbidden), f"PII expuesta: {cols & forbidden}"
