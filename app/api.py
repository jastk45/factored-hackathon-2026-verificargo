"""API HTTP de VerifiCargo.

    uv run uvicorn api:app --app-dir app --port 8000

Sirve también el frontend compilado (frontend/dist) en `/`, así la demo es un
solo proceso y una sola URL.

Endpoints:
    GET  /api/health
    GET  /api/scenarios
    POST /api/conversations                     {scenario_id}
    POST /api/conversations/{id}/messages       {message}
    POST /api/conversations/{id}/step-up        {otp}
    GET  /api/handoffs?status=pending|resolved
    POST /api/handoffs/{handoff_id}/resolve     {decision, note}
    GET  /api/eval

Estado de conversación en memoria del proceso: suficiente para la demo; en
producción iría a un almacén con TTL igual a la vida del token.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import duckdb  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import handoff_queue  # noqa: E402
from demo import scenarios  # noqa: E402
from llm import SlotExtractor  # noqa: E402
from orchestrator import GROUP_LABEL, Orchestrator, Outcome, Turn  # noqa: E402
from policy_engine import PolicyEngine  # noqa: E402
from session import AuthLevel, SessionError, issue_token, step_up, verify_token  # noqa: E402
from tools import Toolbox  # noqa: E402

app = FastAPI(title="VerifiCargo", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])

ENGINE = PolicyEngine()
CON = duckdb.connect()
_ORCH: Orchestrator | None = None
CONVERSATIONS: dict[str, dict[str, Any]] = {}


def orchestrator() -> Orchestrator:
    global _ORCH
    if _ORCH is None:
        from intent import IntentClassifier
        _ORCH = Orchestrator(SlotExtractor(), ENGINE, classifier=IntentClassifier())
    return _ORCH


# --- modelos de request ------------------------------------------------

class NewConversation(BaseModel):
    scenario_id: str


class Message(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class StepUp(BaseModel):
    otp: str = Field(min_length=1, max_length=12)


class Resolution(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|info_requested)$")
    note: str = Field(default="", max_length=500)


# --- serialización -----------------------------------------------------

def session_info(token: str) -> dict[str, Any]:
    try:
        s = verify_token(token)
    except SessionError as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "customer_id": s.customer_id, "country": s.country,
            "language": s.language, "auth_level": s.auth_level.value,
            "high_active": s.can(AuthLevel.HIGH),
            "seconds_remaining": s.seconds_remaining}


def turn_json(turn: Turn, handoff: dict | None) -> dict[str, Any]:
    return {
        "outcome": turn.outcome.value,
        "reply": turn.reply,
        "language": turn.language,
        "latency_ms": turn.latency_ms,
        "states": [s.value for s in turn.states],
        "intent": turn.intent_decision,
        "extraction_source": turn.extraction_source,
        "extracted": {k: v for k, v in (turn.extracted or {}).items() if v is not None},
        "policy_trace": turn.policy_trace,
        "escalation_reasons": turn.escalation_reasons,
        "actions_taken": turn.actions_taken,
        "actions_not_taken": turn.actions_not_taken,
        "evidence": [{"id": e.evidence_id, "source": e.source, "fact": e.fact}
                     for e in turn.evidence[:8]],
        "candidates": len(turn.candidates),
        "awaiting": (turn.context_out or {}).get("awaiting"),
        "options": [{"key": g, "label": GROUP_LABEL[g][turn.language]}
                    for g in (turn.context_out or {}).get("options", [])],
        "grounding_violations": turn.grounding_violations,
        "handoff": handoff,
        "error": turn.error,
    }


def conversation(cid: str) -> dict[str, Any]:
    conv = CONVERSATIONS.get(cid)
    if conv is None:
        raise HTTPException(404, "conversación inexistente o expirada")
    return conv


# --- endpoints ---------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "llm_provider": os.getenv("LLM_PROVIDER", "ollama"),
            "policy_version": ENGINE.version}


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return scenarios()


@app.post("/api/conversations")
def new_conversation(body: NewConversation) -> dict[str, Any]:
    sc = next((s for s in scenarios() if s["id"] == body.scenario_id), None)
    if sc is None:
        raise HTTPException(404, "escenario inexistente")
    cid = uuid.uuid4().hex
    token = issue_token(sc["customer"], "MX", sc["lang"], AuthLevel.LOW)
    CONVERSATIONS[cid] = {"token": token, "context": None, "box": None, "scenario": sc}
    return {"conversation_id": cid, "scenario": sc, "session": session_info(token)}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: str, body: Message) -> dict[str, Any]:
    conv = conversation(cid)

    def factory(session):
        if conv["box"] is None:
            conv["box"] = Toolbox(session, CON)
        conv["box"].session = session
        return conv["box"]

    turn = orchestrator().handle(conv["token"], body.message, factory,
                                 context=conv["context"])
    conv["context"] = turn.context_out or None

    package = None
    if turn.outcome is Outcome.ESCALATED:
        try:
            package = handoff_queue.enqueue(turn, verify_token(conv["token"]), ENGINE)
        except Exception as exc:  # noqa: BLE001 - la respuesta al cliente no depende de esto
            package = {"error": f"no se pudo encolar: {exc}"}
    return {"turn": turn_json(turn, package), "session": session_info(conv["token"])}


@app.post("/api/conversations/{cid}/step-up")
def do_step_up(cid: str, body: StepUp) -> dict[str, Any]:
    conv = conversation(cid)
    try:
        conv["token"] = step_up(conv["token"], body.otp)
    except SessionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"session": session_info(conv["token"])}


@app.get("/api/handoffs")
def list_handoffs(status: str = "pending") -> list[dict]:
    return handoff_queue.pending() if status == "pending" else handoff_queue.resolved()


@app.post("/api/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, body: Resolution) -> dict[str, Any]:
    handoff_queue.resolve(handoff_id, body.decision, body.note)
    return {"ok": True}


@app.get("/api/eval")
def eval_reports() -> dict[str, Any]:
    reports = ROOT / "eval" / "reports"
    out: dict[str, Any] = {}
    for key, name in [("baseline", "system_baseline.json"),
                      ("proposed", "system_proposed_v2.json"),
                      ("proposed_v1", "system_proposed_v1_alpha010.json"),
                      ("classifier", "intent_classifier.json")]:
        path = reports / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            out[key] = data.get("scorecard", data) if key != "classifier" else {
                "arms": data["arms"], "acceptance": data["acceptance"],
                "alpha_sweep": data.get("alpha_sweep"), "conformal": data["conformal"]}
    return out


# --- frontend compilado ------------------------------------------------

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        file = DIST / path
        return FileResponse(file if path and file.is_file() else DIST / "index.html")
