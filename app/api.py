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
    GET  /api/conversations/{id}/questions      preguntas del agente para el cliente
    POST /api/conversations/{id}/handoffs/{handoff_id}/reply   {message}
    POST /api/agent/login                       {access_code}
    GET  /api/handoffs?status=pending|info_requested|closed     (token de agente)
    POST /api/handoffs/{handoff_id}/resolve     {decision, note} (token de agente)
    GET  /api/eval

Estado de conversación en memoria del proceso: suficiente para la demo; en
producción iría a un almacén con TTL igual a la vida del token.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import duckdb  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import handoff_queue  # noqa: E402
from demo import scenarios  # noqa: E402
from llm import SlotExtractor  # noqa: E402
from orchestrator import GROUP_LABEL, T, Orchestrator, Outcome, Turn  # noqa: E402
from policy_engine import PolicyEngine  # noqa: E402
from session import (  # noqa: E402
    AgentSession, AuthLevel, SessionError, agent_login, issue_token, step_up,
    verify_agent_token, verify_token,
)
from tools import Toolbox, ToolError, customer_country  # noqa: E402

app = FastAPI(title="VerifiCargo", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])

ENGINE = PolicyEngine()
# Una conexión de DuckDB NO se comparte entre hilos, y FastAPI atiende los
# endpoints síncronos en un threadpool. Cada request abre su propio cursor
# (`db()`). Con la conexión compartida, 32 requests simultáneos fallaban en su
# mayoría y algunos devolvían el resultado de la consulta de otro hilo.
CON = duckdb.connect()
_ORCH: Orchestrator | None = None
_ORCH_LOCK = threading.Lock()
# Aprobar = ejecutar + cerrar el caso: una sola decisión a la vez.
_RESOLVE_LOCK = threading.Lock()
CONVERSATIONS: dict[str, dict[str, Any]] = {}
# Almacén de disputas compartido por todas las conversaciones y por la consola
# del agente: una disputa aprobada por un humano la ve el cliente, y GATE-05
# detecta duplicados entre conversaciones. En producción, el core bancario.
DISPUTES: dict[str, dict[str, Any]] = {}


def db() -> duckdb.DuckDBPyConnection:
    """Cursor propio para este request (se cierra con `with`)."""
    return CON.cursor()


def orchestrator() -> Orchestrator:
    global _ORCH
    if _ORCH is None:
        with _ORCH_LOCK:
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


class AgentLogin(BaseModel):
    access_code: str = Field(min_length=1, max_length=100)


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


def turn_json(turn: Turn, handoff: dict[str, Any] | None) -> dict[str, Any]:
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


def live_session(conv: dict[str, Any]):
    """La sesión de la conversación, si sigue vigente. Si no, 401."""
    try:
        return verify_token(conv["token"])
    except SessionError as exc:
        raise HTTPException(401, str(exc)) from exc


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
    # El país sale del registro del cliente (dato confiable), no de un valor
    # fijo: los plazos y la procedencia regulatoria dependen de él.
    try:
        with db() as cur:
            country = customer_country(sc["customer"], cur)
    except LookupError as exc:
        raise HTTPException(422, str(exc)) from exc
    token = issue_token(sc["customer"], country, sc["lang"], AuthLevel.LOW)
    # El lock serializa los requests de UNA conversación (p. ej. un doble
    # clic): el contexto del turno anterior tiene que estar escrito antes de
    # leer el siguiente. Conversaciones distintas siguen en paralelo.
    CONVERSATIONS[cid] = {"id": cid, "token": token, "context": None, "box": None,
                          "scenario": sc, "lock": threading.Lock()}
    return {"conversation_id": cid, "scenario": sc, "session": session_info(token)}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: str, body: Message) -> dict[str, Any]:
    conv = conversation(cid)
    with conv["lock"], db() as cur:
        def factory(session):
            if conv["box"] is None:
                conv["box"] = Toolbox(session, cur, disputes=DISPUTES)
            conv["box"].session = session
            conv["box"].con = cur
            return conv["box"]

        # Lo último que el cliente escribió (no los botones ni las
        # confirmaciones), para el resumen del handoff.
        text = body.message.strip()
        if not text.startswith("choice:") and len(text) > 15:
            conv["last_customer_text"] = text

        turn = orchestrator().handle(conv["token"], body.message, factory,
                                     context=conv["context"])
        conv["context"] = turn.context_out or None

        handoff = None
        if turn.outcome is Outcome.ESCALATED:
            handoff = deliver_handoff(turn, conv)
        return {"turn": turn_json(turn, handoff), "session": session_info(conv["token"])}


def deliver_handoff(turn: Turn, conv: dict[str, Any]) -> dict[str, Any]:
    """Todo escalamiento termina en la cola humana, verificado al releer.

    Si no llega (sin ticket, cola caída, paquete inválido), al cliente no se
    le promete un contacto: la respuesta lo dice y el caso queda en la
    dead-letter para que operaciones lo recupere.
    """
    try:
        package = handoff_queue.enqueue(turn, verify_token(conv["token"]), ENGINE,
                                        customer_message=conv.get("last_customer_text"),
                                        conversation_id=conv["id"])
        return {"handoff_id": package["handoff_id"], "queued": True}
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"[:200]
        try:
            handoff_queue.dead_letter(turn, reason)
        except OSError:
            pass
        turn.reply = T["escalated_no_ticket"][turn.language]
        turn.actions_not_taken.append({"action": "enqueue_handoff", "reason": reason})
        return {"handoff_id": None, "queued": False, "error": reason}


@app.get("/api/conversations/{cid}/transactions")
def recent_transactions(cid: str, limit: int = 8) -> list[dict[str, Any]]:
    """Movimientos del cliente de la sesión, para la página del home banking.

    Usa la misma herramienta que el asistente: el filtro por cliente sale de
    la sesión firmada, así que no hay forma de pedir los de otro.
    """
    conv = conversation(cid)
    try:
        session = verify_token(conv["token"])
    except SessionError as exc:
        raise HTTPException(401, str(exc)) from exc
    with conv["lock"], db() as cur:
        box = conv["box"] or Toolbox(session, cur, disputes=DISPUTES)
        box.con = cur
        conv["box"] = box
        rows = box.find_candidate_transactions(limit=min(limit, 20)).data["candidates"]
    return [{
        "transaction_id": r["transaction_id"],
        "date": str(r["transaction_date"]),
        "merchant": r["merchant_name"] or "—",
        "category": r["merchant_category"],
        "amount": float(r["amount"]),
        "currency": r["currency"],
        "status": r["transaction_status"],
        "channel": r["channel"],
    } for r in rows]


@app.get("/api/conversations/{cid}/questions")
def agent_questions(cid: str) -> list[dict[str, Any]]:
    """Lo que un agente le preguntó a este cliente y espera respuesta."""
    live_session(conversation(cid))
    return handoff_queue.questions_for(cid)


@app.post("/api/conversations/{cid}/handoffs/{handoff_id}/reply")
def reply_to_agent(cid: str, handoff_id: str, body: Message) -> dict[str, Any]:
    """El cliente responde al agente. Solo sobre un caso de SU conversación y
    con la sesión vigente: un token vencido no reabre nada."""
    conv = conversation(cid)
    live_session(conv)
    with conv["lock"]:
        try:
            stored = handoff_queue.customer_reply(handoff_id, cid, body.message)
        except handoff_queue.QueueError as exc:
            raise HTTPException(404 if "no existe" in str(exc) else 409, str(exc)) from exc
    return {"ok": True, "handoff_id": handoff_id, "status": stored["status"]}


@app.post("/api/conversations/{cid}/step-up")
def do_step_up(cid: str, body: StepUp) -> dict[str, Any]:
    conv = conversation(cid)
    with conv["lock"]:
        try:
            conv["token"] = step_up(conv["token"], body.otp)
        except SessionError as exc:
            raise HTTPException(401, str(exc)) from exc
        return {"session": session_info(conv["token"])}


# --- consola del agente humano ------------------------------------------

def require_agent(authorization: str = Header(default="")) -> AgentSession:
    """La cola tiene datos de clientes: solo la abre un agente autenticado."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "se requiere un token de agente")
    try:
        return verify_agent_token(token)
    except SessionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/api/agent/login")
def login_agent(body: AgentLogin) -> dict[str, Any]:
    try:
        return {"token": agent_login(body.access_code)}
    except SessionError as exc:
        raise HTTPException(401, str(exc)) from exc


def public_item(record: dict[str, Any]) -> dict[str, Any]:
    """Lo que ve la consola: sin el bloque interno (ids crudos del cliente)."""
    internal = record.get("internal") or {}
    return {k: v for k, v in record.items() if k != "internal"} | {
        "can_approve": bool(internal.get("transaction_id"))}


@app.get("/api/handoffs")
def list_handoffs(status: str = "pending",
                  agent: AgentSession = Depends(require_agent)) -> list[dict]:
    source = {"pending": handoff_queue.pending,
              "info_requested": handoff_queue.awaiting_customer,
              "closed": handoff_queue.closed}.get(status)
    if source is None:
        raise HTTPException(422, "status: pending | info_requested | closed")
    return [public_item(r) for r in source()]


@app.post("/api/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, body: Resolution,
                    agent: AgentSession = Depends(require_agent)) -> dict[str, Any]:
    """Registra la decisión del agente. Aprobar EJECUTA la disputa y la verifica.

    Leer el estado, ejecutar y cerrar van juntos: dos agentes que deciden el
    mismo caso a la vez no pueden aprobarlo los dos.
    """
    with _RESOLVE_LOCK:
        if body.decision == "info_requested" and not body.note.strip():
            raise HTTPException(422, "Escribí la pregunta para el cliente: es lo que le llega.")
        record = handoff_queue.get(handoff_id)
        if record is None:
            raise HTTPException(404, "caso inexistente")
        if record["status"] not in handoff_queue.OPEN_STATUSES:
            raise HTTPException(409, f"el caso ya está cerrado ({record['status']})")

        result = None
        if body.decision == "approved":
            result = approve_dispute(record, agent)
        try:
            stored = handoff_queue.update(handoff_id, body.decision, agent.agent_id,
                                          body.note, result)
        except handoff_queue.QueueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "item": public_item(stored)}


def approve_dispute(record: dict[str, Any], agent: AgentSession) -> dict[str, Any]:
    """El agente aprueba: se abre la disputa con la misma herramienta y la
    misma re-lectura que usa el asistente, registrando quién la ejecutó."""
    internal = record.get("internal") or {}
    txn_id = internal.get("transaction_id")
    if not txn_id:
        raise HTTPException(409, "No hay una transacción verificada para disputar: "
                                 "pedí información al cliente o rechazá el caso.")
    session = verify_token(issue_token(internal["customer_id"], internal["country"],
                                       internal.get("language") or "es", AuthLevel.HIGH))
    with db() as cur:
        box = Toolbox(session, cur, disputes=DISPUTES, actor=f"agent:{agent.agent_id}")
        try:
            created = box.create_dispute_case(
                txn_id, internal.get("intent") or "unrecognized_charge", confirmed=True)
        except ToolError as exc:
            raise HTTPException(409, str(exc)) from exc
    if created.verified and created.data.get("existing_case_id"):
        return {"action": "none", "existing_case_id": created.data["existing_case_id"],
                "verified": True, "evidence_ids": created.evidence_ids()}
    if not (created.ok and created.verified):
        # El caso sigue abierto: no se marca aprobado lo que no ocurrió.
        raise HTTPException(502, "La disputa no pudo verificarse; el caso sigue pendiente.")
    return {"action": "create_dispute_case", "case_id": created.data["case_id"],
            "verified": True, "evidence_ids": created.evidence_ids()}


@app.get("/api/eval")
def eval_reports() -> dict[str, Any]:
    """Scorecards del evaluador v2: eval-v2 (casos nuevos, congelados antes de
    correr) y eval-v1 (el set usado durante el desarrollo)."""
    reports = ROOT / "eval" / "reports"
    out: dict[str, Any] = {}
    for cases in ("v2", "v1"):
        for system in ("baseline", "proposed"):
            path = next((p for p in (reports / f"system_{system}_{cases}_v6r.json",
                                     reports / f"system_{system}_{cases}_v6.json",
                                     reports / f"system_{system}_{cases}_v5.json") if p.exists()),
                        reports / f"system_{system}_{cases}_v6.json")
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                out.setdefault(cases, {})[system] = data["scorecard"]
    # eval-v3: casos creados después de congelar; 3 corridas por sistema.
    summary = reports / "summary_v3_v6.json"
    if summary.exists():
        out["v3"] = json.loads(summary.read_text(encoding="utf-8"))
    path = reports / "intent_classifier.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        out["classifier"] = {"arms": data["arms"], "acceptance": data["acceptance"],
                             "alpha_sweep": data.get("alpha_sweep"),
                             "conformal": data["conformal"]}
    return out


# --- frontend compilado ------------------------------------------------

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    DIST_ROOT = DIST.resolve()

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        # Solo archivos DENTRO de dist. Sin esto, "/%2e%2e/%2e%2e/README.md"
        # servía cualquier archivo del repo (revisión externa).
        file = (DIST_ROOT / path).resolve()
        if path and file.is_relative_to(DIST_ROOT) and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST_ROOT / "index.html")
