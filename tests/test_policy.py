"""Un test por regla de política.

Si alguien cambia un umbral o el orden de evaluación, estos tests dicen
exactamente qué comportamiento se rompió. Son el contrato ejecutable de
`policy/dispute_policy.yaml`.

    uv run pytest tests/test_policy.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from policy_engine import CaseFacts, Decision, PolicyEngine  # noqa: E402


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine()


def facts(**overrides) -> CaseFacts:
    """Un caso que pasa todas las reglas; cada test rompe solo una."""
    base = {
        "session_customer_id": "CUS-001",
        "country": "MX",
        "days_since_transaction": 20,
        "transaction_customer_id": "CUS-001",
        "transaction_status": "Approved",
        "amount_usd": 120.0,
        "amount_usd_source": "daily_rate",
        "candidate_count": 1,
    }
    return CaseFacts(**{**base, **overrides})


# --- Caso base ---------------------------------------------------------

def test_clean_case_proceeds(engine) -> None:
    out = engine.evaluate(facts())
    assert out.decision is Decision.PROCEED
    assert out.fired() == []


# --- Gates -------------------------------------------------------------

def test_gate01_outside_claim_window_escalates(engine) -> None:
    out = engine.evaluate(facts(days_since_transaction=120))
    assert out.decision is Decision.ESCALATE
    assert "GATE-01" in out.fired()


def test_gate02_other_customers_transaction_is_denied(engine) -> None:
    """La regla que F-07 vuelve imprescindible."""
    out = engine.evaluate(facts(transaction_customer_id="CUS-999"))
    assert out.decision is Decision.DENY
    assert out.message_key == "not_your_transaction"


def test_gate02_is_evaluated_before_anything_else(engine) -> None:
    """Si la transacción es de otro, no se revela nada más sobre ella.

    Un caso ajeno Y fuera de plazo debe DENY sin llegar a mencionar el plazo.
    """
    out = engine.evaluate(
        facts(transaction_customer_id="CUS-999", days_since_transaction=400)
    )
    assert out.decision is Decision.DENY
    assert out.fired() == ["GATE-02"], "no debe filtrarse ninguna otra evaluación"


def test_gate03_declined_transaction_is_denied(engine) -> None:
    out = engine.evaluate(facts(transaction_status="Declined"))
    assert out.decision is Decision.DENY
    assert out.message_key == "not_disputable_status"


def test_gate04_multiple_candidates_ask_for_clarification(engine) -> None:
    out = engine.evaluate(facts(candidate_count=4))
    assert out.decision is Decision.CLARIFY
    assert out.message_params["count"] <= engine.threshold("max_candidates_to_offer")


def test_gate04_zero_candidates_also_clarifies(engine) -> None:
    out = engine.evaluate(facts(candidate_count=0))
    assert out.decision is Decision.CLARIFY


def test_gate05_duplicate_dispute_is_denied(engine) -> None:
    out = engine.evaluate(facts(existing_open_dispute=True))
    assert out.decision is Decision.DENY
    assert out.message_key == "duplicate_dispute"


# --- Escalamientos -----------------------------------------------------

def test_esc01_high_amount_escalates(engine) -> None:
    out = engine.evaluate(facts(amount_usd=5000.0))
    assert out.decision is Decision.ESCALATE
    assert any("ESC-01" in r for r in out.escalation_reasons)


def test_esc02_near_deadline_escalates(engine) -> None:
    out = engine.evaluate(facts(days_since_transaction=85))
    assert out.decision is Decision.ESCALATE
    assert any("ESC-02" in r for r in out.escalation_reasons)


def test_esc03_declared_fraud_escalates(engine) -> None:
    out = engine.evaluate(facts(customer_claims_fraud=True))
    assert out.decision is Decision.ESCALATE
    assert any("ESC-03" in r for r in out.escalation_reasons)


def test_esc03_lost_card_intent_escalates(engine) -> None:
    out = engine.evaluate(facts(intent="card_lost_stolen"))
    assert out.decision is Decision.ESCALATE


def test_esc04_repeated_charges_escalate(engine) -> None:
    out = engine.evaluate(facts(unrecognized_charges_last_30d=3))
    assert out.decision is Decision.ESCALATE
    assert any("ESC-04" in r for r in out.escalation_reasons)


def test_esc05_missing_exchange_rate_escalates(engine) -> None:
    """F-12: 35 transacciones sin tasa. No se inventa: se escala."""
    out = engine.evaluate(facts(amount_usd_source="unavailable"))
    assert out.decision is Decision.ESCALATE
    assert any("ESC-05" in r for r in out.escalation_reasons)


def test_esc06_exhausted_clarifications_escalate(engine) -> None:
    out = engine.evaluate(facts(clarification_turns=3))
    assert out.decision is Decision.ESCALATE


def test_esc07_assisted_channel_escalates(engine) -> None:
    out = engine.evaluate(facts(requires_assisted_channel=True))
    assert out.decision is Decision.ESCALATE


def test_all_escalation_reasons_are_reported(engine) -> None:
    """El handoff necesita todos los motivos, no solo el primero."""
    out = engine.evaluate(
        facts(amount_usd=9000.0, customer_claims_fraud=True,
              unrecognized_charges_last_30d=5)
    )
    assert out.decision is Decision.ESCALATE
    assert len(out.escalation_reasons) >= 3


# --- Permisos de acción ------------------------------------------------

def test_block_card_requires_confirmation(engine) -> None:
    ok, reason = engine.check_action("block_card", "high", confirmed=False)
    assert not ok and "confirmación" in reason


def test_block_card_requires_high_auth(engine) -> None:
    ok, reason = engine.check_action("block_card", "low", confirmed=True)
    assert not ok and "auth_level" in reason


def test_read_only_action_needs_neither(engine) -> None:
    ok, _ = engine.check_action("list_recent_transactions", "low", confirmed=False)
    assert ok


def test_handoff_never_requires_customer_confirmation(engine) -> None:
    """Escalar no se negocia con el cliente."""
    ok, _ = engine.check_action("create_handoff_ticket", "low", confirmed=False)
    assert ok


def test_irreversible_actions_are_marked(engine) -> None:
    assert engine.action("block_card")["reversible"] is False
    assert engine.action("block_card")["verify_after"] is True


# --- DATA-01: procedencia ----------------------------------------------

def test_argument_from_customer_text_is_rejected(engine) -> None:
    """Defensa arquitectónica contra prompt injection."""
    ok, reason = engine.argument_sources_are_valid(
        {"amount": "customer_text", "transaction_id": "verified_tool"}
    )
    assert not ok and "DATA-01" in reason


def test_arguments_from_verified_tools_are_accepted(engine) -> None:
    ok, _ = engine.argument_sources_are_valid(
        {"amount": "verified_tool", "transaction_id": "verified_tool"}
    )
    assert ok


# --- Trazabilidad y procedencia ----------------------------------------

def test_trace_is_auditable(engine) -> None:
    """Cada decisión deja la traza que va al handoff."""
    out = engine.evaluate(facts(amount_usd=5000.0))
    trace = out.as_policy_trace()
    assert "GATE-04:pass" in trace
    assert "ESC-01:fire" in trace


def test_synthetic_countries_are_declared(engine) -> None:
    """CO y AR llevan parámetros inventados: no se presentan como normativa."""
    synthetic = set(engine.synthetic_countries())
    assert synthetic == {"CO", "AR"}
    assert engine.country_params("MX")["provenance"] == "real"


def test_unknown_country_fails_loudly(engine) -> None:
    with pytest.raises(KeyError):
        engine.evaluate(facts(country="BR"))


def test_messages_exist_in_both_languages(engine) -> None:
    for key in ("outside_claim_window", "not_your_transaction",
                "ambiguous_transaction", "duplicate_dispute"):
        assert engine.message(key, "es")
        assert engine.message(key, "pt")
