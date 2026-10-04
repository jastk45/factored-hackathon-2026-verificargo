"""Máquina de estados del agente.

El flujo es explícito y el control lo lleva el código:

    AUTH -> DETECT_LANG -> GUARD -> UNDERSTAND -> LOCATE_TXN
         -> CHECK_POLICY -> (CLARIFY | CONFIRM -> ACT -> VERIFY) -> RESPOND
         -> ESCALATE en cualquier punto que la política lo exija

El LLM interviene en **un solo nodo**: UNDERSTAND, donde extrae campos del
mensaje. No elige herramientas, no decide elegibilidad y no dispara acciones.
Esa separación es lo que hace que un modelo débil —o un texto malicioso— no
pueda causar daño: E-02b midió a qwen3 clasificando mal el 37% de los casos en
español, y aun así el sistema no se equivoca, porque esa decisión no es suya.

Cada turno deja un `Turn` con la traza completa: estados recorridos, reglas
evaluadas, herramientas llamadas y evidencia. Eso es el artefacto de auditoría,
no el razonamiento del modelo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from guards import (
    Origin, ProvenanceError, Tagged, detect_injection, require_trusted,
)
from policy_engine import CaseFacts, Decision, PolicyEngine
from session import AuthLevel, Session, SessionError, verify_token
from tools import Evidence, Toolbox, ToolError


class State(str, Enum):
    AUTH = "AUTH"
    DETECT_LANG = "DETECT_LANG"
    GUARD = "GUARD"
    UNDERSTAND = "UNDERSTAND"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"
    LOCATE_TXN = "LOCATE_TXN"
    CHECK_POLICY = "CHECK_POLICY"
    CONFIRM = "CONFIRM"
    ACT = "ACT"
    VERIFY = "VERIFY"
    RESPOND = "RESPOND"
    ESCALATE = "ESCALATE"
    DENY = "DENY"


class Outcome(str, Enum):
    """Cómo termina un turno. Se corresponde con las métricas del scorecard."""

    RESOLVED = "RESOLVED"      # se resolvió sin humano
    CLARIFY = "CLARIFY"        # se pidió información
    ESCALATED = "ESCALATED"    # pasó a un humano
    DENIED = "DENIED"          # no procede, explicado
    ABSTAINED = "ABSTAINED"    # fuera de alcance
    BLOCKED = "BLOCKED"        # ataque detectado o sesión inválida


class Extractor(Protocol):
    """Lo que el orquestador necesita del LLM. Nada más."""

    def extract(self, message: str) -> dict[str, Any]: ...


@dataclass
class Turn:
    """El registro completo de un turno. Es lo que se audita."""

    outcome: Outcome
    message: str
    states: list[State] = field(default_factory=list)
    policy_trace: list[str] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    actions_not_taken: list[dict[str, Any]] = field(default_factory=list)
    extracted: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    language: str = "es"
    latency_ms: int = 0
    error: str | None = None

    def visited(self, state: State) -> bool:
        return state in self.states


# Pistas léxicas para detectar el idioma sin llamar al modelo (E-02: el LLM
# devolvía "pt" para todo con el prompt v1).
PT_MARKERS = ("cobrança", "não", "reconheço", "olá", "podem", "foi ", "uma ", "você")
ES_MARKERS = ("cargo", "no reconozco", "hola", "pueden", "fue ", "una ", "usted")


class Orchestrator:
    def __init__(
        self,
        extractor: Extractor,
        engine: PolicyEngine | None = None,
        max_clarifications: int = 2,
    ) -> None:
        self.extractor = extractor
        self.engine = engine or PolicyEngine()
        self.max_clarifications = max_clarifications

    # --- nodos ---------------------------------------------------------

    def _detect_language(self, message: str) -> str:
        """Determinista y barato. El LLM no decide esto."""
        low = message.lower()
        pt = sum(m in low for m in PT_MARKERS)
        es = sum(m in low for m in ES_MARKERS)
        return "pt" if pt > es else "es"

    def _guard(self, message: str) -> str | None:
        """Capa 2: señal de inyección. La defensa real es la capa 3 (DATA-01)."""
        verdict = detect_injection(message)
        return verdict.reason if verdict.suspicious else None

    # --- turno ---------------------------------------------------------

    def handle(
        self,
        token: str,
        message: str,
        toolbox_factory,
        clarification_turns: int = 0,
        confirmed: bool = False,
    ) -> Turn:
        started = time.perf_counter()
        turn = Turn(outcome=Outcome.BLOCKED, message=message)

        def finish(outcome: Outcome, error: str | None = None) -> Turn:
            turn.outcome = outcome
            turn.error = error
            turn.latency_ms = int((time.perf_counter() - started) * 1000)
            return turn

        # AUTH --------------------------------------------------------
        turn.states.append(State.AUTH)
        try:
            session: Session = verify_token(token)
        except SessionError as exc:
            return finish(Outcome.BLOCKED, str(exc))

        box: Toolbox = toolbox_factory(session)

        # DETECT_LANG -------------------------------------------------
        turn.states.append(State.DETECT_LANG)
        turn.language = self._detect_language(message)

        # GUARD -------------------------------------------------------
        turn.states.append(State.GUARD)
        if (marker := self._guard(message)) is not None:
            # No se corta la conversación: se registra y se sigue tratando el
            # texto como datos. La acción queda bloqueada por DATA-01 igual.
            turn.escalation_reasons.append(f"GUARD: patrón de inyección '{marker}'")
            turn.states.append(State.ESCALATE)
            handoff = box.create_handoff_ticket(
                {"reason": "prompt_injection_detected", "marker": marker}
            )
            turn.evidence.extend(handoff.evidence)
            turn.actions_taken.append(
                {"action": "create_handoff_ticket", "verified": handoff.verified,
                 "evidence_ids": handoff.evidence_ids()}
            )
            return finish(Outcome.ESCALATED)

        # UNDERSTAND --------------------------------------------------
        turn.states.append(State.UNDERSTAND)
        try:
            extracted = self.extractor.extract(message)
        except Exception as exc:  # noqa: BLE001 - un fallo del LLM escala, no rompe
            turn.escalation_reasons.append(f"UNDERSTAND: el extractor falló ({exc})")
            turn.states.append(State.ESCALATE)
            return finish(Outcome.ESCALATED, str(exc))

        turn.extracted = extracted
        intent = extracted.get("intent")

        # Fuera de alcance: abstenerse es la respuesta correcta.
        if intent == "out_of_scope":
            turn.states.append(State.ABSTAIN)
            return finish(Outcome.ABSTAINED)

        # Tarjeta perdida o robada: ESC-03 escala sin pasar por lo demás.
        if intent == "card_lost_stolen":
            turn.states.append(State.ESCALATE)
            turn.escalation_reasons.append("ESC-03: tarjeta perdida o comprometida")
            handoff = box.create_handoff_ticket(
                {"reason": "card_lost_stolen", "extracted": extracted}
            )
            turn.evidence.extend(handoff.evidence)
            turn.actions_taken.append(
                {"action": "create_handoff_ticket", "verified": handoff.verified,
                 "evidence_ids": handoff.evidence_ids()}
            )
            return finish(Outcome.ESCALATED)

        # LOCATE_TXN --------------------------------------------------
        turn.states.append(State.LOCATE_TXN)
        found = box.find_candidate_transactions(
            amount=extracted.get("amount"),
            currency=extracted.get("currency"),
            merchant=extracted.get("merchant"),
            on_date=extracted.get("date"),
        )
        turn.candidates = found.data["candidates"]
        turn.evidence.extend(found.evidence)

        txn = turn.candidates[0] if len(turn.candidates) == 1 else None

        # CHECK_POLICY ------------------------------------------------
        turn.states.append(State.CHECK_POLICY)
        recent = box.count_recent_unrecognized()

        facts = CaseFacts(
            session_customer_id=session.customer_id,
            country=session.country,
            days_since_transaction=(
                (self.engine.country_params(session.country)["claim_window_days"]
                 - 0) if txn is None else None
            ),
            transaction_customer_id=session.customer_id if txn else None,
            transaction_status=txn["transaction_status"] if txn else None,
            amount_usd=float(txn["amount_usd"]) if txn and txn["amount_usd"] else None,
            amount_usd_source=txn["amount_usd_source"] if txn else None,
            candidate_count=len(turn.candidates),
            intent=intent,
            unrecognized_charges_last_30d=recent.data["count"],
            clarification_turns=clarification_turns,
        )
        if txn is not None:
            from tools import TODAY
            facts = CaseFacts(
                **{**facts.__dict__,
                   "days_since_transaction": (TODAY - txn["transaction_date"]).days}
            )

        outcome = self.engine.evaluate(facts)
        turn.policy_trace = outcome.as_policy_trace()
        turn.escalation_reasons.extend(outcome.escalation_reasons)

        if outcome.decision is Decision.DENY:
            turn.states.append(State.DENY)
            return finish(Outcome.DENIED, outcome.message_key)

        if outcome.decision is Decision.CLARIFY:
            if clarification_turns >= self.max_clarifications:
                turn.states.append(State.ESCALATE)
                turn.escalation_reasons.append("ESC-06: se agotaron las aclaraciones")
                handoff = box.create_handoff_ticket(
                    {"reason": "clarifications_exhausted", "extracted": extracted}
                )
                turn.evidence.extend(handoff.evidence)
                turn.actions_taken.append(
                    {"action": "create_handoff_ticket", "verified": handoff.verified,
                     "evidence_ids": handoff.evidence_ids()}
                )
                return finish(Outcome.ESCALATED)

            turn.states.append(State.CLARIFY)
            return finish(Outcome.CLARIFY)

        if outcome.decision is Decision.ESCALATE:
            turn.states.append(State.ESCALATE)
            handoff = box.create_handoff_ticket(
                {"reason": "policy_escalation", "rules": outcome.escalation_reasons,
                 "transaction_id": txn["transaction_id"] if txn else None}
            )
            turn.evidence.extend(handoff.evidence)
            turn.actions_taken.append(
                {"action": "create_handoff_ticket", "verified": handoff.verified,
                 "evidence_ids": handoff.evidence_ids()}
            )
            return finish(Outcome.ESCALATED)

        # CONFIRM -----------------------------------------------------
        turn.states.append(State.CONFIRM)
        if not confirmed:
            turn.actions_not_taken.append(
                {"action": "create_dispute_case",
                 "reason": "ACT-01 requiere confirmación explícita del cliente"}
            )
            return finish(Outcome.CLARIFY)

        # ACT ---------------------------------------------------------
        turn.states.append(State.ACT)
        if not session.can(AuthLevel.HIGH):
            turn.actions_not_taken.append(
                {"action": "create_dispute_case",
                 "reason": "ACT-01 requiere verificación adicional de identidad"}
            )
            return finish(Outcome.CLARIFY)

        # Capa 3 (DATA-01): los argumentos de la acción salen de la búsqueda
        # verificada, nunca de lo que el cliente escribió. Aunque el extractor
        # haya sido engañado —E-02b lo midió— el id que se usa acá viene de la
        # base de datos.
        try:
            require_trusted(
                transaction_id=Tagged(txn["transaction_id"], Origin.VERIFIED_TOOL),
                customer_id=Tagged(session.customer_id, Origin.SESSION),
            )
        except ProvenanceError as exc:
            turn.states.append(State.ESCALATE)
            turn.escalation_reasons.append(str(exc))
            turn.actions_not_taken.append(
                {"action": "create_dispute_case", "reason": str(exc)}
            )
            return finish(Outcome.ESCALATED, str(exc))

        try:
            created = box.create_dispute_case(
                txn["transaction_id"], "cargo no reconocido", confirmed=True
            )
        except ToolError as exc:
            turn.states.append(State.ESCALATE)
            turn.escalation_reasons.append(f"ACT: la herramienta rechazó la acción ({exc})")
            return finish(Outcome.ESCALATED, str(exc))

        # VERIFY ------------------------------------------------------
        turn.states.append(State.VERIFY)
        if not created.verified:
            # No se puede informar una acción que no se pudo comprobar.
            turn.states.append(State.ESCALATE)
            turn.escalation_reasons.append("VERIFY: la acción no pudo verificarse")
            turn.actions_not_taken.append(
                {"action": "create_dispute_case", "reason": created.error}
            )
            handoff = box.create_handoff_ticket(
                {"reason": "unverified_action", "detail": created.error}
            )
            turn.evidence.extend(handoff.evidence)
            return finish(Outcome.ESCALATED)

        turn.evidence.extend(created.evidence)
        turn.actions_taken.append(
            {"action": "create_dispute_case", "verified": True,
             "case_id": created.data["case_id"],
             "evidence_ids": created.evidence_ids()}
        )

        # RESPOND -----------------------------------------------------
        turn.states.append(State.RESPOND)
        return finish(Outcome.RESOLVED)
