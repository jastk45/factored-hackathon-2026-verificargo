"""Regresiones de la revisión externa del 4 de octubre.

Cada test reproduce un hallazgo que se confirmó contra el código antes de
arreglarlo:

    1. path traversal en la ruta del frontend
    2. "Sí, pero no abras la disputa todavía" abría la disputa
    3. un escalamiento por excepción no creaba ticket
    4. la cola humana respondía sin credenciales
    5. aprobar no ejecutaba nada; pedir información figuraba como resuelto
    6. el país de la sesión era siempre "MX"
    7. una disputa repetida rompía con KeyError

    uv run pytest tests/test_review_fixes.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

from orchestrator import T, Orchestrator, Outcome, read_confirmation  # noqa: E402
from session import (  # noqa: E402
    AuthLevel, SessionError, agent_login, issue_token, verify_agent_token, verify_token,
)
from tools import TODAY, Toolbox, ToolResult, customer_country  # noqa: E402

GOLD = REPO_ROOT / "warehouse" / "gold"
DIST = REPO_ROOT / "frontend" / "dist"
needs_gold = pytest.mark.skipif(not (GOLD / "txn_lookup.parquet").exists(),
                                reason="capa gold no construida")
needs_model = pytest.mark.skipif(not (REPO_ROOT / "models" / "intent" / "head.joblib").exists(),
                                 reason="clasificador no entrenado")


class Fixed:
    """Extractor que siempre devuelve los mismos campos."""

    def __init__(self, fields: dict):
        self.fields = fields
        self.calls: list[str] = []

    def extract(self, message: str) -> dict:
        self.calls.append(message)
        return dict(self.fields)


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


@pytest.fixture(scope="module")
def txn(con) -> dict:
    """Transacción reciente, de monto bajo y sin vecinas parecidas."""
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


def fields_of(txn: dict) -> dict:
    return {"intent": "unrecognized_charge", "amount": float(txn["amount"]),
            "currency": txn["currency"], "merchant": txn["merchant_name"],
            "date": str(txn["transaction_date"])}


class Conversation:
    """Varios turnos de un cliente con una sola caja de herramientas."""

    def __init__(self, orch, con, customer, disputes=None, box_cls=Toolbox):
        self.orch, self.ctx = orch, None
        self.token = issue_token(customer, "MX", "es", AuthLevel.HIGH)
        self.box = None
        self._make = lambda s: box_cls(s, con, disputes=disputes)

    def factory(self, session):
        if self.box is None:
            self.box = self._make(session)
        return self.box

    def say(self, message):
        turn = self.orch.handle(self.token, message, self.factory, context=self.ctx)
        self.ctx = turn.context_out or None
        return turn


def created(turn) -> list[dict]:
    return [a for a in turn.actions_taken if a["action"] == "create_dispute_case"]


# --- 2. confirmación inequívoca -----------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Sí, confirmo.", "yes"), ("Sim, confirmo.", "yes"), ("choice:confirm", "yes"),
    ("Sí, pero no abras la disputa todavía", "no"), ("Sim, mas ainda não abra", "no"),
    ("ok, esperá", "no"), ("choice:cancel", "no"), ("No", "no"),
    ("Sí, pero era de 500", None), ("sí, aunque quiero saber cuánto tarda", None),
    ("sí sí sí claro que sí, ábrela ya mismo por favor ahora", None),
])
def test_confirmation_must_be_unambiguous(text, expected) -> None:
    assert read_confirmation(text) == expected


@needs_gold
@pytest.mark.parametrize("reply", [
    "Sí, pero no abras la disputa todavía", "No, mejor no", "Sim, mas ainda não abra",
    "choice:cancel",
])
def test_negative_or_qualified_confirmation_never_acts(con, txn, reply) -> None:
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"])
    assert chat.say("no reconozco este cargo").context_out["awaiting"] == "confirmation"

    turn = chat.say(reply)
    assert turn.outcome is Outcome.CANCELLED
    assert not created(turn)
    assert chat.box._disputes == {}
    assert "no abrí" in turn.reply or "não abri" in turn.reply


@needs_gold
def test_a_correction_at_confirmation_asks_again(con, txn) -> None:
    """Con datos nuevos no se cancela ni se actúa: se vuelve a pedir el sí."""
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"])
    chat.say("no reconozco este cargo")
    turn = chat.say(f"Sí, pero era de {txn['amount']}")
    assert turn.outcome is Outcome.CLARIFY
    assert turn.context_out["awaiting"] == "confirmation"
    assert chat.box._disputes == {}

    assert chat.say("choice:confirm").outcome is Outcome.RESOLVED
    assert len(chat.box._disputes) == 1


@needs_gold
def test_switching_to_a_stolen_card_at_confirmation_escalates(con, txn) -> None:
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"])
    chat.say("no reconozco este cargo")
    turn = chat.say("No, espera: en realidad me robaron la tarjeta")
    assert turn.outcome is Outcome.ESCALATED
    assert chat.box._disputes == {}
    assert chat.box.queued_handoffs(), "escalar es crear el ticket"


# --- 3. todo escalamiento crea un ticket verificado ---------------------

class DbDown(Toolbox):
    def find_candidate_transactions(self, *args, **kwargs):
        raise TimeoutError("la base de transacciones no respondió a tiempo")


class DbAndTicketsDown(DbDown):
    def create_handoff_ticket(self, package):
        raise ConnectionError("el CRM no responde")


@needs_gold
def test_unexpected_exception_escalates_with_a_verified_ticket(con, txn) -> None:
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"],
                        box_cls=DbDown)
    turn = chat.say("no reconozco este cargo")
    assert turn.outcome is Outcome.ESCALATED
    tickets = [a for a in turn.actions_taken if a["action"] == "create_handoff_ticket"]
    assert tickets and tickets[0]["verified"]
    assert len(chat.box.queued_handoffs()) == 1
    assert tickets[0]["ticket_id"] in turn.reply


@needs_gold
def test_if_the_ticket_cannot_be_created_the_reply_does_not_promise_contact(con, txn) -> None:
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"],
                        box_cls=DbAndTicketsDown)
    turn = chat.say("no reconozco este cargo")
    assert turn.outcome is Outcome.ESCALATED
    assert turn.reply == T["escalated_no_ticket"]["es"]
    assert "Te vamos a contactar" not in turn.reply
    assert any(a["action"] == "create_handoff_ticket" for a in turn.actions_not_taken)


@needs_gold
def test_an_ungrounded_reply_is_not_sent_and_the_case_escalates(con, txn, monkeypatch) -> None:
    monkeypatch.setitem(T["resolved"], "es", T["resolved"]["es"] + " Te devolvemos 9.876,54 USD.")
    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"])
    chat.say("no reconozco este cargo")
    turn = chat.say("choice:confirm")
    assert turn.grounding_violations
    assert turn.outcome is Outcome.ESCALATED
    assert "9.876,54" not in turn.reply
    assert chat.box.queued_handoffs()


# --- 7. disputa repetida ------------------------------------------------

@needs_gold
def test_a_repeated_dispute_reports_the_existing_case(con, txn) -> None:
    shared: dict = {}
    first = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"], shared)
    first.say("no reconozco este cargo")
    assert first.say("choice:confirm").outcome is Outcome.RESOLVED
    case_id = next(iter(shared))

    second = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"], shared)
    turn = second.say("no reconozco este cargo")
    assert turn.outcome is Outcome.DENIED
    assert turn.error == "duplicate_dispute"
    assert case_id in turn.reply
    assert len(shared) == 1


@needs_gold
def test_a_dispute_opened_between_check_and_write_is_reported_too(con, txn) -> None:
    """Carrera: la consulta previa no la ve; la escritura sí. Sin KeyError."""
    shared: dict = {}
    other = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"], shared)
    other.say("no reconozco este cargo")

    class Racy(Toolbox):
        seen = 0

        def open_dispute_for(self, transaction_id):
            Racy.seen += 1
            if Racy.seen == 1:     # la consulta del orquestador llega antes
                return ToolResult(ok=True, verified=True, data={"case_id": None})
            return super().open_dispute_for(transaction_id)

    chat = Conversation(Orchestrator(Fixed(fields_of(txn))), con, txn["customer_id"], shared,
                        box_cls=Racy)
    chat.say("no reconozco este cargo")
    other.say("choice:confirm")                      # la otra conversación gana
    turn = chat.say("choice:confirm")
    assert turn.outcome is Outcome.DENIED
    assert turn.error == "duplicate_dispute"
    assert len(shared) == 1


# --- 4. tokens de agente ------------------------------------------------

def test_agent_and_customer_tokens_are_not_interchangeable() -> None:
    agent = agent_login(os.getenv("AGENT_ACCESS_CODE", "agente-demo-2026"))
    with pytest.raises(SessionError):
        verify_token(agent)
    with pytest.raises(SessionError):
        verify_agent_token(issue_token("CUS-1", "MX", "es", AuthLevel.HIGH))
    with pytest.raises(SessionError):
        agent_login("adivinando")
    assert verify_agent_token(agent).agent_id


# --- 6. país del cliente ------------------------------------------------

@needs_gold
def test_country_comes_from_the_customer_record(con) -> None:
    rows = con.sql(f"""
        SELECT country, any_value(customer_id)
        FROM read_parquet('{(GOLD / 'customer_360_min.parquet').as_posix()}')
        GROUP BY 1""").fetchall()
    codes = {customer_country(cid, con) for _, cid in rows}
    assert codes == {"MX", "CO", "AR"}
    with pytest.raises(LookupError):
        customer_country("CLI-NO-EXISTE", con)


# --- API: 1, 3, 4, 5 y 6 de punta a punta ------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    import handoff_queue
    monkeypatch.setattr(handoff_queue, "QUEUE", tmp_path / "queue.jsonl")
    monkeypatch.setattr(handoff_queue, "DEAD_LETTER", tmp_path / "dead.jsonl")
    import api
    monkeypatch.setattr(api, "DISPUTES", {})
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def agent_headers(client) -> dict:
    code = os.getenv("AGENT_ACCESS_CODE", "agente-demo-2026")
    token = client.post("/api/agent/login", json={"access_code": code}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def escalate(client, scenario: str) -> dict:
    """Lleva un escenario hasta que escala (eligiendo 'disputa' si pregunta)."""
    conv = client.post("/api/conversations", json={"scenario_id": scenario}).json()
    cid = conv["conversation_id"]
    message = conv["scenario"]["message"]
    for _ in range(3):
        turn = client.post(f"/api/conversations/{cid}/messages", json={"message": message}).json()["turn"]
        if turn["outcome"] != "CLARIFY":
            return turn
        message = "choice:dispute" if turn["awaiting"] == "intent" else "choice:confirm"
    return turn


@pytest.mark.skipif(not DIST.exists(), reason="frontend no compilado")
@pytest.mark.parametrize("url", [
    "/%2e%2e/%2e%2e/README.md", "/..%2f..%2fREADME.md", "/%2e%2e/%2e%2e/.env",
    "/%2e%2e/%2e%2e/app/session.py",
])
def test_the_frontend_route_never_serves_files_outside_dist(client, url) -> None:
    r = client.get(url)
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.text == (DIST / "index.html").read_text(encoding="utf-8")


@needs_gold
@needs_model
def test_the_human_queue_requires_an_agent(client) -> None:
    assert client.get("/api/handoffs").status_code == 401
    customer = issue_token("CUS-1", "MX", "es", AuthLevel.HIGH)
    assert client.get("/api/handoffs", headers={"Authorization": f"Bearer {customer}"}).status_code == 403
    assert client.post("/api/agent/login", json={"access_code": "x"}).status_code == 401
    assert client.post("/api/handoffs/HO-X/resolve", json={"decision": "approved"}).status_code == 401
    assert client.get("/api/handoffs", headers=agent_headers(client)).status_code == 200


@needs_gold
@needs_model
def test_session_country_is_the_customers(client) -> None:
    conv = client.post("/api/conversations", json={"scenario_id": "high-amount"}).json()
    assert conv["session"]["country"] == customer_country(conv["scenario"]["customer"])


@needs_gold
@needs_model
def test_every_escalation_reaches_the_queue_and_approval_executes(client) -> None:
    import api
    turn = escalate(client, "high-amount")
    assert turn["outcome"] == "ESCALATED"
    assert turn["handoff"]["queued"] is True
    hid = turn["handoff"]["handoff_id"]
    assert hid in turn["reply"]

    headers = agent_headers(client)
    pending = client.get("/api/handoffs", headers=headers).json()
    item = next(i for i in pending if i["package"]["handoff_id"] == hid)
    assert item["can_approve"] and "internal" not in item

    r = client.post(f"/api/handoffs/{hid}/resolve", headers=headers,
                    json={"decision": "approved", "note": "monto verificado"}).json()
    assert r["item"]["status"] == "approved"
    case_id = r["item"]["result"]["case_id"]
    assert r["item"]["result"]["verified"] is True
    assert api.DISPUTES[case_id]["created_by"].startswith("agent:")
    assert client.post(f"/api/handoffs/{hid}/resolve", headers=headers,
                       json={"decision": "rejected"}).status_code == 409


@needs_gold
@needs_model
def test_info_requested_stays_open(client) -> None:
    hid = escalate(client, "high-amount")["handoff"]["handoff_id"]
    headers = agent_headers(client)
    client.post(f"/api/handoffs/{hid}/resolve", headers=headers,
                json={"decision": "info_requested", "note": "¿compartió el OTP?"})
    ids = lambda status: [i["package"]["handoff_id"] for i in  # noqa: E731
                          client.get(f"/api/handoffs?status={status}", headers=headers).json()]
    assert hid in ids("info_requested")
    assert hid not in ids("closed") and hid not in ids("pending")
    r = client.post(f"/api/handoffs/{hid}/resolve", headers=headers, json={"decision": "rejected"})
    assert r.status_code == 200 and hid in ids("closed")


@needs_gold
@needs_model
def test_approval_without_a_verified_transaction_is_refused(client) -> None:
    turn = escalate(client, "injection")
    hid = turn["handoff"]["handoff_id"]
    r = client.post(f"/api/handoffs/{hid}/resolve", headers=agent_headers(client),
                    json={"decision": "approved"})
    assert r.status_code == 409


@needs_gold
@needs_model
def test_if_the_queue_fails_the_customer_is_told_the_truth(client, monkeypatch) -> None:
    import handoff_queue

    def broken(*args, **kwargs):
        raise handoff_queue.QueueError("disco lleno")

    monkeypatch.setattr(handoff_queue, "enqueue", broken)
    turn = escalate(client, "high-amount")
    assert turn["outcome"] == "ESCALATED"
    assert turn["handoff"]["queued"] is False
    assert turn["reply"] == T["escalated_no_ticket"]["es"]
    assert handoff_queue.DEAD_LETTER.read_text(encoding="utf-8").count("\n") == 1
