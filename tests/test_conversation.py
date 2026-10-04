"""Conversación de varios turnos, respuestas ancladas y extracción confiable.

    uv run pytest tests/test_conversation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

from llm import SlotExtractor, parse_amount, regex_extract  # noqa: E402
from orchestrator import Orchestrator, Outcome, State, detect_language  # noqa: E402
from session import AuthLevel, issue_token  # noqa: E402
from tools import TODAY, Toolbox  # noqa: E402

GOLD = REPO_ROOT / "warehouse" / "gold"
needs_gold = pytest.mark.skipif(not (GOLD / "txn_lookup.parquet").exists(),
                                reason="capa gold no construida")


class Scripted:
    """Extractor que devuelve una respuesta distinta por turno."""

    def __init__(self, *payloads: dict):
        self.payloads = list(payloads)

    def extract(self, message: str) -> dict:
        return self.payloads.pop(0) if self.payloads else {}


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


@pytest.fixture(scope="module")
def txn(con) -> dict:
    """Transacción reciente, de monto bajo y sin vecinas parecidas: aislable."""
    from datetime import date
    recent = date.fromordinal(TODAY.toordinal() - 40)
    t = f"read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')"
    row = con.sql(f"""
        SELECT * FROM (
          SELECT a.customer_id, a.transaction_id, a.amount, a.currency,
                 a.merchant_name, a.transaction_date,
                 (SELECT count(*) FROM {t} o WHERE o.customer_id = a.customer_id
                   AND abs(o.amount - a.amount) <= a.amount * 0.10) AS n_similar
          FROM {t} a
          WHERE a.transaction_status = 'Approved' AND a.amount_usd < 300
            AND a.merchant_name IS NOT NULL AND a.transaction_date > DATE '{recent}'
          LIMIT 300) WHERE n_similar = 1 LIMIT 1""").fetchone()
    keys = ["customer_id", "transaction_id", "amount", "currency", "merchant_name",
            "transaction_date"]
    return dict(zip(keys, row))


def run(orch, con, customer, message, **kw):
    token = issue_token(customer, "MX", "es", kw.pop("level", AuthLevel.HIGH))
    return orch.handle(token, message, lambda s: Toolbox(s, con), **kw)


# --- contexto entre turnos ---------------------------------------------

@needs_gold
def test_details_given_across_two_turns_are_combined(con, txn) -> None:
    """El cliente da el monto primero y la fecha después: el sistema los une."""
    orch = Orchestrator(Scripted(
        {"intent": "unrecognized_charge", "amount": None, "currency": None,
         "merchant": None, "date": None},
        {"amount": float(txn["amount"]), "currency": txn["currency"],
         "merchant": None, "date": None},
        {"date": str(txn["transaction_date"])},
    ))
    t1 = run(orch, con, txn["customer_id"], "no reconozco un cargo")
    assert t1.outcome is Outcome.CLARIFY
    assert t1.context_out["awaiting"] == "details"

    t2 = run(orch, con, txn["customer_id"], f"fue de {txn['amount']}", context=t1.context_out)
    t3 = run(orch, con, txn["customer_id"], f"el {txn['transaction_date']}",
             context=t2.context_out)

    # Lo dicho en el turno 2 sigue presente en el 3.
    assert t3.extracted["amount"] == float(txn["amount"])
    assert t3.extracted["date"] == str(txn["transaction_date"])
    assert len(t3.candidates) == 1


@needs_gold
def test_affirmative_reply_confirms_without_calling_the_model(con, txn) -> None:
    calls = []

    class Counting(Scripted):
        def extract(self, message):
            calls.append(message)
            return super().extract(message)

    orch = Orchestrator(Counting({
        "intent": "unrecognized_charge", "amount": float(txn["amount"]),
        "currency": txn["currency"], "merchant": txn["merchant_name"],
        "date": str(txn["transaction_date"])}))

    t1 = run(orch, con, txn["customer_id"], "no reconozco este cargo")
    assert t1.context_out.get("awaiting") == "confirmation"

    t2 = run(orch, con, txn["customer_id"], "sí, confirmo", context=t1.context_out)
    assert t2.outcome is Outcome.RESOLVED
    assert len(calls) == 1, "el 'sí' no debe pasar por el modelo"
    assert t2.actions_taken[-1]["action"] == "create_dispute_case"


@needs_gold
def test_continuation_keeps_the_intent(con, txn) -> None:
    """Un 'fue el martes' no se re-clasifica como otra intención."""
    orch = Orchestrator(Scripted(
        {"intent": "unrecognized_charge", "amount": None, "currency": None,
         "merchant": None, "date": None},
        {"intent": "out_of_scope", "date": str(txn["transaction_date"])},
    ))
    t1 = run(orch, con, txn["customer_id"], "no reconozco un cargo")
    t2 = run(orch, con, txn["customer_id"], "fue el martes", context=t1.context_out)
    assert t2.outcome is not Outcome.ABSTAINED
    assert t2.extracted["intent"] == "unrecognized_charge"


@needs_gold
def test_clarifications_are_bounded_across_turns(con, txn) -> None:
    orch = Orchestrator(Scripted(*[{"intent": "unrecognized_charge"}] * 5),
                        max_clarifications=2)
    ctx = None
    outcomes = []
    for _ in range(4):
        turn = run(orch, con, txn["customer_id"], "algo raro", context=ctx)
        outcomes.append(turn.outcome)
        ctx = turn.context_out
        if turn.outcome is not Outcome.CLARIFY:
            break
    assert outcomes[-1] is Outcome.ESCALATED
    assert outcomes.count(Outcome.CLARIFY) == 2


# --- ramas informativas -------------------------------------------------

@needs_gold
def test_policy_question_answers_from_the_policy(con, txn) -> None:
    orch = Orchestrator(Scripted({"intent": "policy_question"}))
    turn = run(orch, con, txn["customer_id"], "¿cuánto tiempo tengo para reclamar?")
    assert turn.outcome is Outcome.RESOLVED
    assert "90" in turn.reply and "45" in turn.reply
    assert not turn.actions_taken


@needs_gold
def test_status_lists_only_own_disputes(con) -> None:
    owner = con.sql(f"""SELECT customer_id FROM
        read_parquet('{(GOLD / 'dispute_cases.parquet').as_posix()}') LIMIT 1""").fetchone()[0]
    orch = Orchestrator(Scripted({"intent": "dispute_status"}))
    turn = run(orch, con, owner, "¿cómo va mi reclamo?")
    assert turn.outcome is Outcome.RESOLVED
    for ev in turn.evidence:
        assert ev.data.get("case_id")
    assert turn.reply


# --- respuesta anclada (DATA-03) ----------------------------------------

@needs_gold
def test_every_reply_figure_is_grounded(con, txn) -> None:
    orch = Orchestrator(Scripted({
        "intent": "unrecognized_charge", "amount": float(txn["amount"]),
        "currency": txn["currency"], "merchant": txn["merchant_name"],
        "date": str(txn["transaction_date"])}))
    turn = run(orch, con, txn["customer_id"], "no reconozco este cargo", confirmed=True)
    assert turn.outcome is Outcome.RESOLVED
    assert turn.grounding_violations == []
    assert str(txn["transaction_date"]) in turn.reply


@needs_gold
def test_reply_is_in_the_customers_language(con, txn) -> None:
    orch = Orchestrator(Scripted({"intent": "out_of_scope"}))
    turn = run(orch, con, txn["customer_id"], "quero abrir uma conta de investimento")
    assert turn.language == "pt"
    assert "contestações" in turn.reply or "Isso" in turn.reply


# --- idioma -------------------------------------------------------------

@pytest.mark.parametrize("text,lang", [
    ("nao reconheco essa compra de 350", "pt"),
    ("tengo un cargo q no reconosco", "es"),
    ("Che, me figura un consumo en la tarjeta", "es"),
    ("Tem um gasto na minha fatura que não é meu", "pt"),
    ("me cobraron dos veces", "es"),
])
def test_language_detection_without_accents_or_with_slang(text, lang) -> None:
    assert detect_language(text) == lang


# --- extracción: reintentos acotados y fallback ------------------------

def test_regex_fallback_extracts_without_a_model() -> None:
    out = regex_extract("Hola, tengo un cargo de 1.121.353 COP del 2026-05-22")
    assert out["amount"] == 1121353.0
    assert out["currency"] == "COP"
    assert out["date"] == "2026-05-22"


def test_regex_never_takes_the_year_as_an_amount() -> None:
    out = regex_extract("fue el 2026-05-22")
    assert out["amount"] is None


@pytest.mark.parametrize("raw,value", [
    ("150,00", 150.0), ("1.121.353", 1121353.0), ("1.500,50", 1500.5),
    ("87.694", 87694.0), (450, 450.0), ("null", None),
])
def test_amount_parsing_latam_format(raw, value) -> None:
    assert parse_amount(raw) == value


def test_unreachable_model_falls_back_to_regex_after_bounded_retries(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")  # puerto cerrado
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "1")
    import llm
    monkeypatch.setattr(llm, "BACKOFF_SECONDS", 0.01)

    ex = SlotExtractor(provider="ollama")
    result = ex.extract_with_trace("no reconozco un cargo de 350,00 USD")

    assert result.source == "regex"
    assert result.attempts == llm.MAX_ATTEMPTS
    assert len(result.errors) == llm.MAX_ATTEMPTS
    assert result.fields["amount"] == 350.0


def test_offline_mode_never_calls_a_model() -> None:
    result = SlotExtractor(provider="none").extract_with_trace("cargo de 99,90 USD")
    assert result.source == "regex" and result.attempts == 0
    assert result.fields["amount"] == 99.9
