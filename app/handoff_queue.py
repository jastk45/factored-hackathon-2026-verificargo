"""Cola de casos para el agente humano.

Cuando el orquestador escala, se arma el paquete validado (handoff.py) y se
encola acá. La consola CRM lee la cola y registra la decisión del agente.

Es un archivo JSONL: suficiente para un prototipo de un solo proceso. En
producción sería la cola del CRM real detrás de un adaptador con esta misma
interfaz (`enqueue`, `get`, `pending`, `awaiting_customer`, `closed`, `update`).

Estados de un caso:
    pending         esperando a un agente
    info_requested  el agente pidió datos al cliente: sigue ABIERTO
    approved        el agente aprobó y la acción se ejecutó y verificó
    rejected        el agente lo cerró sin acción

`enqueue` y `update` releen lo que escribieron: un caso que no aparece en la
cola no se informa como encolado.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from handoff import build_package, contains_card_number

QUEUE = Path(__file__).resolve().parent.parent / "warehouse" / "handoff_queue.jsonl"
# Escalamientos que no se pudieron encolar: alguien de operaciones los revisa.
DEAD_LETTER = QUEUE.with_name("handoff_deadletter.jsonl")

OPEN_STATUSES = ("pending", "info_requested")
CLOSED_STATUSES = ("approved", "rejected")


# La API escribe y lee la cola desde varios hilos. Sin este lock, un hilo
# leía el archivo mientras otro lo reescribía (JSON cortado) y dos decisiones
# simultáneas se pisaban. Es un lock de proceso: con varios procesos, la cola
# sería el CRM o una base con transacciones, no un archivo.
_LOCK = threading.RLock()


class QueueError(Exception):
    """El caso no quedó en la cola (o no se pudo actualizar)."""

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


def enqueue(turn, session, engine, customer_message: str | None = None,
            conversation_id: str | None = None) -> dict[str, Any]:
    """Arma el paquete del turno escalado, lo encola y lo relee.

    `customer_message` es lo último que el cliente ESCRIBIÓ: si el turno que
    escaló fue la elección de un botón, el texto del turno no dice nada útil.
    Lanza QueueError si el turno no tiene ticket verificado o si el caso no
    aparece en la cola al releerla.
    """
    ticket = next((a.get("ticket_id") for a in turn.actions_taken
                   if a["action"] == "create_handoff_ticket" and a.get("verified")
                   and a.get("ticket_id")), None)
    if ticket is None:
        raise QueueError("el turno escaló sin ticket verificado")

    intent = (turn.extracted or {}).get("intent")
    summary = INTENT_SUMMARY[turn.language].get(intent, INTENT_SUMMARY[turn.language][None])
    excerpt = redact(customer_message or turn.message)[:160]
    txn = turn.candidates[0] if len(turn.candidates) == 1 else None

    package = build_package(
        handoff_id=ticket, turn=turn, session=session, engine=engine,
        request_summary=f"{summary}. Mensaje: \"{excerpt}\"",
        transaction=txn, open_questions=open_questions(turn),
    )
    # Lo que el agente necesita para EJECUTAR si aprueba. Queda del lado del
    # servidor: la API no lo devuelve a la consola.
    internal = {
        "customer_id": session.customer_id, "country": session.country,
        "language": turn.language, "intent": (turn.extracted or {}).get("intent"),
        "transaction_id": txn["transaction_id"] if txn else None,
        # Para devolverle al cliente la pregunta del agente (info_requested).
        "conversation_id": conversation_id,
    }
    record = {"status": "pending", "queued_at": datetime.now().isoformat(timespec="seconds"),
              "package": json.loads(package.model_dump_json()), "internal": internal}
    with _LOCK:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        # RE-LECTURA: el caso tiene que estar en la cola, tal cual.
        stored = get(ticket)
    if stored is None or stored["status"] != "pending":
        raise QueueError(f"el caso {ticket} no aparece en la cola al releerla")
    return record["package"]


def dead_letter(turn, reason: str) -> None:
    """Registra un escalamiento que no llegó a la cola, para recuperarlo a mano."""
    line = json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "reason": reason,
        "escalation_reasons": turn.escalation_reasons, "error": turn.error,
    }, ensure_ascii=False) + "\n"
    with _LOCK:
        DEAD_LETTER.parent.mkdir(parents=True, exist_ok=True)
        with DEAD_LETTER.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _load() -> list[dict[str, Any]]:
    with _LOCK:
        if not QUEUE.exists():
            return []
        text = QUEUE.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _rewrite(rows: list[dict[str, Any]]) -> None:
    """Reescribe la cola de forma atómica: nunca queda un archivo a medias."""
    tmp = QUEUE.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    os.replace(tmp, QUEUE)


def get(handoff_id: str) -> dict[str, Any] | None:
    return next((r for r in _load() if r["package"]["handoff_id"] == handoff_id), None)


def _by_priority(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda r: (PRIORITY_ORDER.get(r["package"]["priority"], 9),
                                        r["package"]["sla"].get("days_remaining") or 999))


def pending() -> list[dict[str, Any]]:
    return _by_priority([r for r in _load() if r["status"] == "pending"])


def awaiting_customer() -> list[dict[str, Any]]:
    """Casos donde el agente pidió información: siguen abiertos, no resueltos."""
    return _by_priority([r for r in _load() if r["status"] == "info_requested"])


def closed() -> list[dict[str, Any]]:
    return [r for r in _load() if r["status"] in CLOSED_STATUSES]


def update(handoff_id: str, status: str, agent: str, note: str,
           result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cambia el estado de un caso abierto y lo relee. Devuelve el registro."""
    if status not in OPEN_STATUSES + CLOSED_STATUSES:
        raise QueueError(f"estado desconocido: {status}")
    with _LOCK:   # leer, modificar y reescribir sin que otro hilo se meta en el medio
        rows = _load()
        target = next((r for r in rows if r["package"]["handoff_id"] == handoff_id), None)
        if target is None:
            raise QueueError(f"no existe el caso {handoff_id}")
        if target["status"] not in OPEN_STATUSES:
            raise QueueError(f"el caso {handoff_id} ya está cerrado ({target['status']})")
        now = datetime.now().isoformat(timespec="seconds")
        target["status"] = status
        target["agent"] = agent
        target["agent_note"] = note
        target.setdefault("history", []).append({"at": now, "status": status, "agent": agent,
                                                 "note": note, "result": result})
        if result is not None:
            target["result"] = result
        if status == "info_requested":
            # La nota ES la pregunta: llega al chat del cliente (questions_for).
            target["info_request"] = {"question": note, "asked_at": now}
        if status in CLOSED_STATUSES:
            target["resolved_at"] = now
        _rewrite(rows)
        stored = get(handoff_id)
    if stored is None or stored["status"] != status:
        raise QueueError(f"el cambio de {handoff_id} no quedó registrado")
    return stored


def questions_for(conversation_id: str) -> list[dict[str, Any]]:
    """Preguntas de agentes pendientes de respuesta en esta conversación."""
    return [{"handoff_id": r["package"]["handoff_id"], **r["info_request"]}
            for r in _load()
            if r["status"] == "info_requested" and r.get("info_request")
            and (r.get("internal") or {}).get("conversation_id") == conversation_id]


def customer_reply(handoff_id: str, conversation_id: str, text: str) -> dict[str, Any]:
    """El cliente responde la pregunta del agente: el caso vuelve a pendientes.

    La respuesta es una afirmación del cliente, no un hecho verificado: se
    guarda aparte, redactada (sin números de tarjeta), y el agente decide.
    """
    with _LOCK:
        rows = _load()
        target = next((r for r in rows if r["package"]["handoff_id"] == handoff_id), None)
        if target is None or (target.get("internal") or {}).get("conversation_id") != conversation_id:
            raise QueueError(f"no existe el caso {handoff_id} en esta conversación")
        if target["status"] != "info_requested":
            raise QueueError(f"el caso {handoff_id} no espera una respuesta del cliente")
        now = datetime.now().isoformat(timespec="seconds")
        target.setdefault("customer_replies", []).append(
            {"at": now, "question": target["info_request"]["question"], "text": redact(text)[:500]})
        target["status"] = "pending"
        target.setdefault("history", []).append(
            {"at": now, "status": "pending", "agent": "cliente", "note": "respondió la pregunta",
             "result": None})
        _rewrite(rows)
        stored = get(handoff_id)
    if stored is None or stored["status"] != "pending":
        raise QueueError(f"la respuesta a {handoff_id} no quedó registrada")
    return stored
