"""Verifica el fixture de disputas vinculadas.

La promesa del fixture es que **la transacción disputada existe de verdad**:
lo sintético es el reclamo, no el hecho. Estos tests la sostienen, y además
comprueban la separación dev/eval que exige D-11.

    uv run pytest tests/test_fixture.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "linked_disputes"
GOLD = REPO_ROOT / "warehouse" / "gold"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "eval.jsonl").exists(),
    reason="fixture no construido; correr pipeline/build_fixture.py",
)


def load(profile: str) -> list[dict]:
    path = FIXTURES / f"{profile}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


@pytest.fixture(scope="module")
def dev() -> list[dict]:
    return load("dev")


@pytest.fixture(scope="module")
def ev() -> list[dict]:
    return load("eval")


# --- La promesa central -------------------------------------------------

@pytest.mark.parametrize("profile", ["dev", "eval"])
def test_every_disputed_transaction_exists(con, profile: str) -> None:
    """Cada caso apunta a una transacción real del mismo cliente.

    Es justamente lo que el dataset NO tiene (F-06): 0 de 8.125 disputas se
    enlazan con una transacción del cliente.
    """
    cases = load(profile)
    txn = f"read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')"

    for case in cases:
        gt = case["ground_truth"]
        row = con.sql(
            f"""
            SELECT customer_id, amount, currency
            FROM {txn} WHERE transaction_id = '{gt["transaction_id"]}'
            """
        ).fetchone()
        assert row is not None, f"{case['case_id']}: transacción inexistente"
        assert row[0] == case["customer_id"], (
            f"{case['case_id']}: la transacción es de otro cliente"
        )
        assert abs(float(row[1]) - gt["amount"]) < 0.01
        assert row[2] == gt["currency"]


@pytest.mark.parametrize("profile", ["dev", "eval"])
def test_all_within_regulatory_window(con, profile: str) -> None:
    """Ninguna transacción supera el plazo de reclamo de 90 días."""
    for case in load(profile):
        days = case["ground_truth"]["days_since_transaction"]
        assert 0 <= days <= 90, f"{case['case_id']}: {days} días"


@pytest.mark.parametrize("profile", ["dev", "eval"])
def test_outcome_matches_its_stated_reason(profile: str) -> None:
    """La etiqueta es auditable: cada una trae la regla que la produjo."""
    for case in load(profile):
        outcome = case["expected_outcome"]
        reason = case["expected_outcome_reason"]
        assert outcome in {"RESOLVE", "CLARIFY", "ESCALATE"}
        assert reason, f"{case['case_id']}: etiqueta sin motivo"
        if outcome == "ESCALATE":
            assert "plazo" in reason or "umbral" in reason
        if outcome == "CLARIFY":
            assert "comercio" in reason


# --- Anticircularidad (D-11) -------------------------------------------

def test_dev_and_eval_share_no_transactions(dev, ev) -> None:
    """Si compartieran transacciones, el matching se afinaría contra su test."""
    dev_ids = {c["ground_truth"]["transaction_id"] for c in dev}
    eval_ids = {c["ground_truth"]["transaction_id"] for c in ev}
    overlap = dev_ids & eval_ids
    assert not overlap, f"{len(overlap)} transacciones compartidas"


def test_eval_is_harder_than_dev(dev, ev) -> None:
    """El perfil de evaluación tiene más ruido: más redondeo y más fechas vagas.

    Si se volvieran equivalentes, la evaluación dejaría de ser exigente y este
    test lo detecta.
    """
    def noise(cases: list[dict]) -> float:
        rounded = sum(c["stated"]["amount_was_rounded"] for c in cases)
        vague = sum(c["stated"]["date_is_vague"] for c in cases)
        return (rounded + vague) / (2 * len(cases))

    assert noise(ev) > noise(dev), "el perfil eval debe ser más ruidoso que dev"


# --- Trazabilidad -------------------------------------------------------

@pytest.mark.parametrize("profile", ["dev", "eval"])
def test_every_case_declares_its_origin(profile: str) -> None:
    """El reto pide identificar qué es real y qué generó el equipo."""
    for case in load(profile):
        assert case["origin"] == "team-generated"


@pytest.mark.parametrize("profile", ["dev", "eval"])
def test_both_languages_present(profile: str) -> None:
    langs = {c["language"] for c in load(profile)}
    assert langs == {"es", "pt"}


def test_portuguese_messages_are_in_portuguese(ev) -> None:
    """Detecta mezcla de idiomas: un mensaje pt no debe traer artículos en es."""
    for case in ev:
        if case["language"] != "pt":
            continue
        text = case["customer_message"]
        assert "reconheço" in text
        for spanish in (" el ", " la ", " una tienda", " el súper"):
            assert spanish not in text, f"{case['case_id']}: castellano en pt"
