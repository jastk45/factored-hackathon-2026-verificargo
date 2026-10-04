"""Cola de casos para el agente humano.

Cuando el orquestador escala, se arma el paquete validado (handoff.py) y se
encola acá. La consola CRM lee la cola y registra la decisión del agente.

Es un archivo JSONL: suficiente para un prototipo de un solo proceso. En
producción sería la cola del CRM real detrás de un adaptador con esta misma
interfaz (`enqueue`, `pending`, `resolve`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from handoff import build_package, contains_card_number

QUEUE = Path(__file__).resolve().parent.parent / "warehouse" / "handoff_queue.jsonl"

PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}

INTENT_SUMMARY = {
    "es": {
        "card_lost_stolen": "Reporta tarjeta perdida, robada o comprometida",
        "unrecognized_charge": "No reconoce un cargo",
        "duplicate_charge": "Reporta un cobro duplicado",
        "wrong_amount": "Reporta un monto distinto al pactado",
        "merchandise_not_received": "Pagó y no recibió el producto o servicio",
        None: "Caso que requiere revisión",
    },
    "pt": {
        "card_lost_stolen": "Relata cartão perdido, roubado ou comprometido",
        "unrecognized_charge": "Não reconhece uma cobrança",
        "duplicate_charge": "Relata cobrança duplicada",
        "wrong_amount": "Relata valor diferente do combinado",
        "merchandise_not_received": "Pagou e não recebeu",
        None: "Caso que requer revisão",
    },
}


def redact(text: str) -> str:
    """Quita números que parezcan tarjetas antes de que viajen al CRM."""
    def repl(match: re.Match) -> str:
        return "[NUMERO OCULTO]" if contains_card_number(match.group()) else match.group()
    return re.sub(r"(?:\d[ -]?){13,19}", repl, text)


def open_questions(turn) -> list[str]:
    reasons = " ".join(turn.escalation_reasons)
    questions = []
    if "ESC-03" in reasons:
        questions += ["¿El cliente ya bloqueó la tarjeta en la app?",
                      "¿Reconoce otros cargos de los últimos 30 días?"]
    if "ESC-01" in reasons:
        questions.append("¿El cliente compartió datos de la tarjeta o un código OTP?")
    if "ESC-02" in reasons or "GATE-01" in reasons:
        questions.append("Confirmar fecha exacta del cargo: el plazo regulatorio está cerca.")
    if "GUARD" in reasons:
        questions.append("El mensaje contenía instrucciones dirigidas al sistema: "
                         "verificar la identidad y la intención real del cliente.")
    if "ESC-05" in reasons:
        questions.append("Falta la tasa de cambio del día: normalizar el monto a mano.")
    if not questions:
        questions.append("Confirmar con el cliente el detalle del reclamo.")
    return questions


def enqueue(turn, session, engine) -> dict[str, Any] | None:
    """Arma el paquete del turno escalado y lo encola. Devuelve el paquete."""
    ticket = next((a.get("ticket_id") for a in turn.actions_taken
                   if a["action"] == "create_handoff_ticket" and a.get("ticket_id")), None)
    if ticket is None:
        return None

    intent = (turn.extracted or {}).get("intent")
    summary = INTENT_SUMMARY[turn.language].get(intent, INTENT_SUMMARY[turn.language][None])
    excerpt = redact(turn.message)[:160]
    txn = turn.candidates[0] if len(turn.candidates) == 1 else None

    package = build_package(
        handoff_id=ticket, turn=turn, session=session, engine=engine,
        request_summary=f"{summary}. Mensaje: \"{excerpt}\"",
        transaction=txn, open_questions=open_questions(turn),
    )
    record = {"status": "pending", "queued_at": datetime.now().isoformat(timespec="seconds"),
              "package": json.loads(package.model_dump_json())}
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record["package"]


def _load() -> list[dict[str, Any]]:
    if not QUEUE.exists():
        return []
    return [json.loads(l) for l in QUEUE.read_text(encoding="utf-8").splitlines() if l.strip()]


def pending() -> list[dict[str, Any]]:
    items = [r for r in _load() if r["status"] == "pending"]
    return sorted(items, key=lambda r: (PRIORITY_ORDER.get(r["package"]["priority"], 9),
                                        r["package"]["sla"].get("days_remaining") or 999))


def resolved() -> list[dict[str, Any]]:
    return [r for r in _load() if r["status"] != "pending"]


def resolve(handoff_id: str, decision: str, agent_note: str) -> None:
    rows = _load()
    for r in rows:
        if r["package"]["handoff_id"] == handoff_id:
            r["status"] = decision
            r["resolved_at"] = datetime.now().isoformat(timespec="seconds")
            r["agent_note"] = agent_note
    QUEUE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                     encoding="utf-8")
