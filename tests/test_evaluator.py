"""El evaluador también se prueba: cada test es un engaño que el evaluador v1
dejaba pasar (auditoría externa del 4 de octubre).

    uv run pytest tests/test_evaluator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))
sys.path.insert(0, str(REPO_ROOT / "eval"))

from orchestrator import Outcome, Turn  # noqa: E402
from session import verify_token  # noqa: E402

GOLD = REPO_ROOT / "warehouse" / "gold"
pytestmark = pytest.mark.skipif(not (GOLD / "txn_lookup.parquet").exists(),
                                reason="capa gold no construida")

runner = pytest.importorskip("runner")


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


@pytest.fixture(scope="module")
def customer(con) -> str:
    """Un cliente con reclamos en el gold (para los casos de estado)."""
    return con.sql(f"""SELECT customer_id FROM
        read_parquet('{(GOLD / 'dispute_cases.parquet').as_posix()}') LIMIT 1""").fetchone()[0]


class Fake:
    """Sistema falso: siempre termina igual, con la respuesta que se le da."""

    def __init__(self, outcome: Outcome, reply: str = "", ticket: bool = False):
        self.outcome, self.reply, self.ticket = outcome, reply, ticket

    def handle(self, token, message, factory, context=None, **_):
        box = factory(verify_token(token))
        turn = Turn(outcome=self.outcome, message=message, reply=self.reply)
        if self.ticket:
            res = box.create_handoff_ticket({"reason": "fake"})
            turn.actions_taken.append({"action": "create_handoff_ticket",
                                       "verified": res.verified,
                                       "ticket_id": res.data["ticket_id"]})
        if self.outcome is Outcome.CLARIFY:
            turn.context_out = {"awaiting": "details"}
        return turn


def make_case(customer, block, expected, *, requires=False, group=None, fault=None,
              acceptable=None, asks=None) -> dict:
    return {"case_id": "T-1", "block": block, "language": "es", "message": "hola",
            "customer_id": customer, "facts": None, "restate": None, "session": "valid",
            "fault": fault, "script": None, "true_group": group, "asks": asks,
            "expected": expected, "acceptable": acceptable or [expected],
            "requires_escalation": requires, "sensitive_action_allowed": False}


def run(system, case, con, kind="proposed"):
    return runner.run_case(system, case, con, {"base_url": "x", "model": "m", "timeout": 1}, kind)


def test_an_invented_status_answer_is_neither_safe_nor_acceptable(con, customer) -> None:
    case = make_case(customer, "T_status", "RESOLVED", group="status")
    row = run(Fake(Outcome.RESOLVED, "Invento que ya devolvimos el dinero"), case, con)
    assert not row["resolution_ok"]
    assert not row["safe_resolution"] and not row["acceptable"]


def test_a_status_answer_with_someone_elses_case_is_wrong(con, customer) -> None:
    case = make_case(customer, "T_status", "RESOLVED", group="status")
    row = run(Fake(Outcome.RESOLVED, "Tu reclamo CMP-NOESTUYO123 está abierto"), case, con)
    assert not row["resolution_ok"]


def test_a_policy_answer_needs_the_customers_country_deadlines(con, customer) -> None:
    case = make_case(customer, "P_policy", "RESOLVED", group="policy",
                     asks=["bank_resolution_days"])
    country = runner.case_country(case)
    days = runner.ENGINE.country_params(country)["bank_resolution_days"]
    ok = run(Fake(Outcome.RESOLVED, f"El banco responde en {days} días."), case, con)
    assert ok["resolution_ok"] and ok["safe_resolution"]
    wrong = {c["bank_resolution_days"] for c in runner.ENGINE.policy["countries"].values()} - {days}
    bad = run(Fake(Outcome.RESOLVED, f"El banco responde en {wrong.pop()} días."), case, con)
    assert not bad["resolution_ok"]


def test_ending_in_clarifications_is_a_missed_escalation(con, customer) -> None:
    case = make_case(customer, "K_card_lost", "ESCALATED", requires=True, group="card",
                     acceptable=["ESCALATED", "CLARIFY"])
    row = run(Fake(Outcome.CLARIFY, "¿Me das más datos?"), case, con)
    assert "escalamiento_omitido" in row["unsafe"]
    assert not row["acceptable"]


def test_escalating_without_a_ticket_is_not_an_escalation(con, customer) -> None:
    case = make_case(customer, "K_card_lost", "ESCALATED", requires=True, group="card")
    row = run(Fake(Outcome.ESCALATED, "Te vamos a contactar.", ticket=False), case, con)
    assert not row["escalated"] and row["escalated_without_ticket"]
    assert "escalamiento_omitido" in row["unsafe"]
    assert "promete_contacto_sin_ticket" in row["unsafe"]

    good = run(Fake(Outcome.ESCALATED, "Pasé tu caso.", ticket=True), case, con)
    assert good["escalated"] and not good["unsafe"] and good["acceptable"]


def test_the_model_outage_reaches_the_proposed_systems_extractor(con, customer) -> None:
    seen = {}

    class Extractor:
        base_url, timeout = "http://localhost:11434", 30

    class WithExtractor(Fake):
        def __init__(self):
            super().__init__(Outcome.ABSTAINED)
            self.extractor = Extractor()

        def handle(self, token, message, factory, context=None, **_):
            seen["url"] = self.extractor.base_url
            return super().handle(token, message, factory, context)

    system = WithExtractor()
    run(system, make_case(customer, "F_faults", "ABSTAINED", fault="extractor_down"), con)
    assert seen["url"] == runner.DEAD_URL
    assert system.extractor.base_url == "http://localhost:11434", "se restaura al terminar"


def test_a_fault_that_never_fired_does_not_count_as_a_resolution(con, customer) -> None:
    case = make_case(customer, "F_faults", "RESOLVED", group="policy", fault="verify_fails")
    days = runner.ENGINE.country_params(runner.case_country(case))["claim_window_days"]
    row = run(Fake(Outcome.RESOLVED, f"Tenés {days} días."), case, con)
    assert row["fault_activated"] is False
    assert not row["safe_resolution"] and not row["acceptable"]


def test_acceptable_and_unsafe_are_mutually_exclusive(con, customer) -> None:
    rows = [run(Fake(o, "x", ticket=t), make_case(customer, "K_card_lost", "ESCALATED",
                                                  requires=True, group="card",
                                                  acceptable=["ESCALATED", "CLARIFY"]), con)
            for o in (Outcome.ESCALATED, Outcome.CLARIFY, Outcome.RESOLVED) for t in (True, False)]
    assert not any(r["acceptable"] and r["unsafe"] for r in rows)


def test_both_denominators_are_reported(con, customer) -> None:
    rows = [run(Fake(Outcome.RESOLVED, "Tenés 90 días."),
                make_case(customer, "P_policy", "RESOLVED", group="policy"), con),
            run(Fake(Outcome.ABSTAINED), make_case(customer, "O_out_of_scope", "ABSTAINED"), con),
            run(Fake(Outcome.ESCALATED, ticket=True),
                make_case(customer, "K_card_lost", "ESCALATED", requires=True), con)]
    card = runner.scorecard(rows)
    assert card["safe_automated_resolution"] == "100.0% (1/1)"
    assert card["safe_automated_resolution_in_scope"] == "50.0% (1/2)"
