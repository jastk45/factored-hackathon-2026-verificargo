"""Máquina de estados del agente.

El flujo es explícito y el control lo lleva el código:

    AUTH -> DETECT_LANG -> GUARD -> UNDERSTAND -> (ruta por intención)
      disputa : LOCATE_TXN -> CHECK_POLICY -> (CLARIFY | CONFIRM -> ACT -> VERIFY)
      estado  : consulta de reclamos del propio cliente
      política: respuesta desde el YAML, con su procedencia
      tarjeta : ESCALATE inmediato (ESC-03)
      fuera   : ABSTAIN
    -> RESPOND, o ESCALATE en cualquier punto que la política lo exija

Quién decide qué:
  - idioma          detección léxica determinista
  - intención       clasificador entrenado + abstención conformal (intent.py)
  - campos          LLM con reintentos y fallback a regex (llm.py)
  - todo lo demás   código: política, herramientas, verificación

**Contexto conversacional.** Cada turno devuelve `context_out`; el siguiente lo
recibe como `context`. Si el sistema quedó esperando un dato o una
confirmación, el mensaje nuevo se interpreta como continuación: se combinan los
campos ya dados con los nuevos y no se re-clasifica la intención.

Cada turno deja un `Turn` con la traza completa. Eso es el artefacto de
auditoría, no el razonamiento del modelo.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from guards import (
    Origin, ProvenanceError, Tagged, check_grounded, detect_injection,
    require_trusted,
)
from policy_engine import CaseFacts, Decision, PolicyEngine
from session import AuthLevel, Session, SessionError, verify_token
from tools import TODAY, Evidence, Toolbox, ToolError


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

    RESOLVED = "RESOLVED"
    CLARIFY = "CLARIFY"
    ESCALATED = "ESCALATED"
    DENIED = "DENIED"
    ABSTAINED = "ABSTAINED"
    BLOCKED = "BLOCKED"


class Extractor(Protocol):
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
    intent_decision: dict[str, Any] | None = None
    extraction_source: str | None = None
    language: str = "es"
    reply: str = ""
    grounding_violations: list[str] = field(default_factory=list)
    context_out: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    def visited(self, state: State) -> bool:
        return state in self.states


# --- idioma -------------------------------------------------------------

PT_WORDS = {
    "não", "nao", "cobrança", "cobranca", "reconheço", "reconheco", "olá", "ola",
    "podem", "foi", "uma", "você", "voce", "vocês", "cartão", "cartao", "fatura",
    "meu", "minha", "quero", "tenho", "essa", "esse", "isso", "já", "ainda",
    "dinheiro", "obrigado", "obrigada", "senha", "loja", "fiz", "comigo",
    "contestação", "contestar", "estorno", "cobraram", "nessa", "pela", "pelo",
}
ES_WORDS = {
    "cargo", "reconozco", "reconosco", "hola", "pueden", "fue", "una", "usted",
    "tarjeta", "mi", "cobraron", "quiero", "tengo", "el", "la", "los", "las",
    "del", "pero", "dinero", "plata", "che", "ayer", "hice", "esa", "ese",
    "cuenta", "compré", "pagué", "reclamo", "disputa", "devuelvan", "qué",
}

# Tarjeta perdida, robada o comprometida: una regla dura, no el clasificador.
# Las reglas solo pueden AÑADIR escalamientos; el modelo no puede evitarlos.
CARD_RISK = re.compile(
    r"\b(rob(aron|ó|o)\b|roub(aram|ou)\b|asalt(aron|o)|assalt(aram|ado|ada)|"
    r"perd[ií](d[oa])?\b.{0,25}(tarjeta|cart[aã]o)|(tarjeta|cart[aã]o).{0,25}perd|"
    r"clon(aron|ada|ado)|clonaram|hurt(aron|o)|furt(aram|o)|extravi)",
    re.IGNORECASE,
)

AFFIRMATIVE = re.compile(
    r"^\s*(s[ií]|sim|confirmo|dale|ok|okay|de acuerdo|claro|correcto|"
    r"pode ser|pode|isso|exato|adelante|hazlo|fa[cç]a)\b", re.IGNORECASE,
)


def detect_language(message: str, default: str = "es") -> str:
    words = re.findall(r"[\wáéíóúãõâêôçñü]+", message.lower())
    pt = sum(w in PT_WORDS for w in words)
    es = sum(w in ES_WORDS for w in words)
    if pt == es:
        return default
    return "pt" if pt > es else "es"


# --- textos al cliente --------------------------------------------------
# Los hechos (montos, fechas, ids) salen siempre de la evidencia verificada;
# la plantilla solo los ordena. Antes de enviar se comprueba (DATA-03).

T = {
    "resolved": {
        "es": "Listo: abrí la disputa {case_id} sobre el cargo de {amount} {currency} del {date} en {merchant}. "
              "El banco tiene {bank_days} días para responderte y te avisaremos por este canal.",
        "pt": "Pronto: abri a contestação {case_id} sobre a cobrança de {amount} {currency} de {date} em {merchant}. "
              "O banco tem {bank_days} dias para responder e avisaremos por este canal.",
    },
    "confirm": {
        "es": "Encontré el cargo de {amount} {currency} del {date} en {merchant}. ¿Confirmás que querés disputarlo?",
        "pt": "Encontrei a cobrança de {amount} {currency} de {date} em {merchant}. Confirma que quer contestá-la?",
    },
    "step_up": {
        "es": "Para abrir la disputa necesito verificar tu identidad con el código que te enviamos.",
        "pt": "Para abrir a contestação preciso verificar sua identidade com o código que enviamos.",
    },
    "candidates": {
        "es": "Encontré {count} cargos que coinciden:\n{options}\n¿Cuál es? Podés decirme la fecha exacta o el comercio.",
        "pt": "Encontrei {count} cobranças que coincidem:\n{options}\nQual delas? Pode me dizer a data exata ou o estabelecimento.",
    },
    "no_match": {
        "es": "No encontré ningún cargo con esos datos. ¿Me decís el monto, la fecha y el comercio?",
        "pt": "Não encontrei nenhuma cobrança com esses dados. Pode me dizer o valor, a data e o estabelecimento?",
    },
    "clarify_intent": {
        "es": "Para ayudarte bien: ¿tu consulta es sobre {options}?",
        "pt": "Para ajudar melhor: sua dúvida é sobre {options}?",
    },
    "escalated": {
        "es": "Pasé tu caso a un especialista (ticket {ticket}). No vas a tener que repetir lo que ya me contaste.",
        "pt": "Encaminhei seu caso a um especialista (protocolo {ticket}). Você não vai precisar repetir o que já me contou.",
    },
    "escalated_no_ticket": {
        "es": "Tu caso necesita revisión de un especialista. Te vamos a contactar.",
        "pt": "Seu caso precisa da revisão de um especialista. Entraremos em contato.",
    },
    "abstain": {
        "es": "Eso está fuera de lo que puedo resolver por acá: atiendo disputas de cargos con tarjeta. "
              "Para otros temas, comunicate con la línea general del banco.",
        "pt": "Isso está fora do que posso resolver por aqui: atendo contestações de cobranças no cartão. "
              "Para outros assuntos, fale com a central do banco.",
    },
    "blocked": {
        "es": "Tu sesión no es válida o expiró. Volvé a iniciar sesión para continuar.",
        "pt": "Sua sessão não é válida ou expirou. Entre novamente para continuar.",
    },
    "policy": {
        "es": "Tenés {window} días desde el cargo para reclamarlo, y el banco tiene {bank_days} días para responder{note}. "
              "Para iniciarlo, contame el monto, la fecha y el comercio del cargo.",
        "pt": "Você tem {window} dias a partir da cobrança para contestá-la, e o banco tem {bank_days} dias para responder{note}. "
              "Para começar, me diga o valor, a data e o estabelecimento.",
    },
    "status_none": {
        "es": "No encontré reclamos de cargos a tu nombre.",
        "pt": "Não encontrei contestações de cobranças em seu nome.",
    },
    "status_list": {
        "es": "Estos son tus reclamos más recientes:\n{items}",
        "pt": "Estas são suas contestações mais recentes:\n{items}",
    },
}

GROUP_LABEL = {
    "dispute": {"es": "un cargo que querés disputar", "pt": "uma cobrança que quer contestar"},
    "card": {"es": "una tarjeta perdida o robada", "pt": "um cartão perdido ou roubado"},
    "status": {"es": "el estado de un reclamo", "pt": "o status de uma contestação"},
    "policy": {"es": "plazos y procedimientos", "pt": "prazos e procedimentos"},
    "out_of_scope": {"es": "otro tema", "pt": "outro assunto"},
}

GROUP_DEFAULT = {
    "dispute": "unrecognized_charge", "card": "card_lost_stolen",
    "status": "dispute_status", "policy": "policy_question",
    "out_of_scope": "out_of_scope",
}

INTENT_GROUP = {
    "unrecognized_charge": "dispute", "duplicate_charge": "dispute",
    "wrong_amount": "dispute", "merchandise_not_received": "dispute",
    "card_lost_stolen": "card", "dispute_status": "status",
    "policy_question": "policy", "out_of_scope": "out_of_scope",
}


GROUP_OF = None  # se completa abajo


def money(value: float) -> str:
    return f"{value:,.2f}"


GROUP_OF = INTENT_GROUP


class Orchestrator:
    def __init__(
        self,
        extractor: Extractor,
        engine: PolicyEngine | None = None,
        max_clarifications: int = 2,
        classifier: Any | None = None,
    ) -> None:
        self.extractor = extractor
        self.engine = engine or PolicyEngine()
        self.max_clarifications = max_clarifications
        # Sin clasificador, la intención la trae el extractor (modo de los
        # tests unitarios, que inyectan un extractor falso).
        self.classifier = classifier

    # --- utilidades ----------------------------------------------------

    def _detect_language(self, message: str) -> str:
        return detect_language(message)

    def _guard(self, message: str) -> str | None:
        verdict = detect_injection(message)
        return verdict.reason if verdict.suspicious else None

    @staticmethod
    def _chosen_group(message: str, context: dict[str, Any]) -> str | None:
        """¿El mensaje elige una de las opciones ofrecidas?

        Acepta "choice:dispute" (lo que manda un botón) o el texto de la
        etiqueta en cualquiera de los dos idiomas.
        """
        options = context.get("options") or []
        text = message.strip().lower()
        if text.startswith("choice:"):
            key = text.split(":", 1)[1].strip()
            return key if key in options else None
        for group in options:
            labels = [GROUP_LABEL[group]["es"].lower(), GROUP_LABEL[group]["pt"].lower()]
            if any(text == label or text in label and len(text) > 8 for label in labels):
                return group
        return None

    def _say(self, turn: Turn, key: str, **params: Any) -> None:
        turn.reply = T[key][turn.language].format(**params)

    def _escalate(self, turn: Turn, box: Toolbox, payload: dict[str, Any]) -> None:
        turn.states.append(State.ESCALATE)
        handoff = box.create_handoff_ticket(payload)
        turn.evidence.extend(handoff.evidence)
        turn.actions_taken.append({
            "action": "create_handoff_ticket", "verified": handoff.verified,
            "evidence_ids": handoff.evidence_ids(),
            "ticket_id": handoff.data.get("ticket_id"),
        })
        if handoff.verified:
            self._say(turn, "escalated", ticket=handoff.data["ticket_id"])
        else:
            self._say(turn, "escalated_no_ticket")

    # --- turno ---------------------------------------------------------

    def handle(
        self,
        token: str,
        message: str,
        toolbox_factory,
        clarification_turns: int = 0,
        confirmed: bool = False,
        context: dict[str, Any] | None = None,
    ) -> Turn:
        """Atiende un turno. Ninguna excepción inesperada sale de acá.

        Si algo falla fuera de lo previsto (una herramienta que se cae, un
        timeout de la base), el turno termina en ESCALATE con el error
        registrado: el fallback seguro es pasar a un humano, nunca inventar.
        """
        try:
            return self._handle(token, message, toolbox_factory,
                                clarification_turns, confirmed, context)
        except Exception as exc:  # noqa: BLE001 - es el fallback de último recurso
            turn = Turn(outcome=Outcome.ESCALATED, message=message,
                        language=detect_language(message))
            turn.states.append(State.ESCALATE)
            turn.escalation_reasons.append(
                f"SYSTEM: error inesperado ({type(exc).__name__}: {exc})"[:200])
            turn.error = f"{type(exc).__name__}: {exc}"
            self._say(turn, "escalated_no_ticket")
            return turn

    def _handle(
        self,
        token: str,
        message: str,
        toolbox_factory,
        clarification_turns: int = 0,
        confirmed: bool = False,
        context: dict[str, Any] | None = None,
    ) -> Turn:
        started = time.perf_counter()
        turn = Turn(outcome=Outcome.BLOCKED, message=message)
        context = dict(context or {})
        clarification_turns = max(clarification_turns, context.get("clarification_turns", 0))

        def finish(outcome: Outcome, error: str | None = None) -> Turn:
            turn.outcome = outcome
            turn.error = error
            # DATA-03: ninguna cifra o fecha de la respuesta sin respaldo.
            if turn.reply:
                facts = [e.fact for e in turn.evidence]
                violations = check_grounded(turn.reply, facts + [str(v) for v in self._reply_constants()])
                if violations:
                    turn.grounding_violations = violations
                    self._say(turn, "escalated_no_ticket")
            turn.latency_ms = int((time.perf_counter() - started) * 1000)
            return turn

        # AUTH --------------------------------------------------------
        turn.states.append(State.AUTH)
        try:
            session: Session = verify_token(token)
        except SessionError as exc:
            turn.language = detect_language(message)
            self._say(turn, "blocked")
            return finish(Outcome.BLOCKED, str(exc))

        box: Toolbox = toolbox_factory(session)

        # DETECT_LANG -------------------------------------------------
        turn.states.append(State.DETECT_LANG)
        turn.language = context.get("language") or detect_language(message, session.language)

        # GUARD -------------------------------------------------------
        turn.states.append(State.GUARD)
        if (marker := self._guard(message)) is not None:
            turn.escalation_reasons.append(f"GUARD: {marker}")
            self._escalate(turn, box, {"reason": "prompt_injection_detected", "marker": marker})
            return finish(Outcome.ESCALATED)

        # UNDERSTAND --------------------------------------------------
        turn.states.append(State.UNDERSTAND)
        awaiting = context.get("awaiting")

        # Confirmación pendiente: un "sí" no necesita pasar por el modelo.
        if awaiting == "confirmation" and AFFIRMATIVE.match(message):
            confirmed = True
            extracted = dict(context.get("fields", {}))
        else:
            try:
                fresh = self.extractor.extract(message)
            except Exception as exc:  # noqa: BLE001 - un fallo del LLM escala
                turn.escalation_reasons.append(f"UNDERSTAND: el extractor falló ({exc})")
                self._escalate(turn, box, {"reason": "extractor_failure"})
                return finish(Outcome.ESCALATED, str(exc))
            turn.extraction_source = getattr(getattr(self.extractor, "last", None), "source", None)
            # Contexto: los campos ya dados se conservan; los nuevos los pisan.
            extracted = dict(context.get("fields", {}))
            extracted.update({k: v for k, v in fresh.items()
                              if v is not None and k != "intent"})
            if "intent" in fresh:
                extracted["intent"] = fresh["intent"]

        # Intención: si estábamos en medio de un flujo, el mensaje es una
        # continuación y no se re-clasifica.
        choice = self._chosen_group(message, context) if awaiting == "intent" else None
        if CARD_RISK.search(message):
            intent = "card_lost_stolen"
        elif awaiting in ("details", "confirmation") and context.get("intent"):
            intent = context["intent"]
        elif choice is not None:
            # El cliente eligió una de las opciones que se le ofrecieron: la
            # elección es determinista, no se vuelve a clasificar.
            intent = context.get("group_intent", {}).get(choice) or GROUP_DEFAULT[choice]
            extracted = {**context.get("fields", {}),
                         **{k: v for k, v in extracted.items() if v is not None}}
        elif self.classifier is not None:
            decision = self.classifier.decide(message, turn.language)
            turn.intent_decision = decision.as_dict()
            if decision.route == "CLARIFY" and clarification_turns >= self.max_clarifications:
                turn.escalation_reasons.append("ESC-06: la intención sigue sin aclararse")
                self._escalate(turn, box, {"reason": "intent_unclear",
                                           "prediction_set": list(decision.prediction_set)})
                return finish(Outcome.ESCALATED)
            if decision.route == "CLARIFY":
                turn.states.append(State.CLARIFY)
                options = " / ".join(GROUP_LABEL[g][turn.language] for g in decision.groups)
                self._say(turn, "clarify_intent", options=options)
                turn.context_out = {
                    "awaiting": "intent", "fields": extracted, "language": turn.language,
                    "options": list(decision.groups),
                    "group_intent": {GROUP_OF.get(i): i for i in reversed(decision.prediction_set)},
                    "clarification_turns": clarification_turns + 1}
                return finish(Outcome.CLARIFY)
            if decision.route == "ESCALATE":
                turn.escalation_reasons.append(f"INTENT: {decision.reason}")
                self._escalate(turn, box, {"reason": "intent_uncertain",
                                           "prediction_set": list(decision.prediction_set)})
                return finish(Outcome.ESCALATED)
            intent = decision.intent if decision.route == "ACT" else "out_of_scope"
        else:
            intent = extracted.get("intent")

        turn.extracted = {**extracted, "intent": intent}
        group = INTENT_GROUP.get(intent or "", "dispute")

        if group == "out_of_scope":
            turn.states.append(State.ABSTAIN)
            self._say(turn, "abstain")
            return finish(Outcome.ABSTAINED)

        if group == "card":
            turn.escalation_reasons.append("ESC-03: tarjeta perdida o comprometida")
            self._escalate(turn, box, {"reason": "card_lost_stolen", "fields": extracted})
            return finish(Outcome.ESCALATED)

        if group == "policy":
            params = self.engine.country_params(session.country)
            note = "" if params.get("provenance") == "real" else (
                " (plazo de referencia del prototipo)" if turn.language == "es"
                else " (prazo de referência do protótipo)")
            turn.states.append(State.RESPOND)
            self._say(turn, "policy", window=params["claim_window_days"],
                      bank_days=params["bank_resolution_days"], note=note)
            turn.policy_trace = [f"POLICY:{session.country}:{params.get('provenance')}"]
            return finish(Outcome.RESOLVED)

        if group == "status":
            listed = box.list_disputes(limit=3)
            turn.evidence.extend(listed.evidence)
            turn.states.append(State.RESPOND)
            items = listed.data["disputes"]
            if not items:
                self._say(turn, "status_none")
            else:
                lines = "\n".join(
                    f"- {d['case_id']}: {d['status']} (desde {d['created_at']})" for d in items
                )
                self._say(turn, "status_list", items=lines)
            return finish(Outcome.RESOLVED)

        return self._dispute_flow(turn, session, box, extracted, intent,
                                  clarification_turns, confirmed, finish)

    # --- flujo de disputa ---------------------------------------------

    def _dispute_flow(self, turn, session, box, extracted, intent,
                      clarification_turns, confirmed, finish):
        turn.states.append(State.LOCATE_TXN)
        found = box.find_candidate_transactions(
            amount=extracted.get("amount"), currency=extracted.get("currency"),
            merchant=extracted.get("merchant"), on_date=extracted.get("date"),
        )
        turn.candidates = found.data["candidates"]
        turn.evidence.extend(found.evidence)
        txn = turn.candidates[0] if len(turn.candidates) == 1 else None

        turn.states.append(State.CHECK_POLICY)
        recent = box.count_recent_unrecognized()
        facts = CaseFacts(
            session_customer_id=session.customer_id,
            country=session.country,
            days_since_transaction=(TODAY - txn["transaction_date"]).days if txn else None,
            transaction_customer_id=session.customer_id if txn else None,
            transaction_status=txn["transaction_status"] if txn else None,
            amount_usd=float(txn["amount_usd"]) if txn and txn["amount_usd"] is not None else None,
            amount_usd_source=txn["amount_usd_source"] if txn else None,
            candidate_count=len(turn.candidates),
            intent=intent,
            unrecognized_charges_last_30d=recent.data["count"],
            clarification_turns=clarification_turns,
        )
        outcome = self.engine.evaluate(facts)
        turn.policy_trace = outcome.as_policy_trace()
        turn.escalation_reasons.extend(outcome.escalation_reasons)

        base_ctx = {"intent": intent, "fields": extracted, "language": turn.language}

        if outcome.decision is Decision.DENY:
            turn.states.append(State.DENY)
            turn.reply = self.engine.message(outcome.message_key, turn.language).format(
                **{"days": "", "status": (txn or {}).get("transaction_status", ""),
                   "case_id": "", "count": "", **outcome.message_params})
            return finish(Outcome.DENIED, outcome.message_key)

        if outcome.decision is Decision.CLARIFY:
            if clarification_turns >= self.max_clarifications:
                turn.escalation_reasons.append("ESC-06: se agotaron las aclaraciones")
                self._escalate(turn, box, {"reason": "clarifications_exhausted",
                                           "fields": extracted})
                return finish(Outcome.ESCALATED)
            turn.states.append(State.CLARIFY)
            if turn.candidates:
                shown = turn.candidates[: self.engine.threshold("max_candidates_to_offer")]
                options = "\n".join(
                    f"- {c['transaction_date']} · {money(float(c['amount']))} {c['currency']} · "
                    f"{c['merchant_name'] or '—'}" for c in shown)
                self._say(turn, "candidates", count=len(turn.candidates), options=options)
            else:
                self._say(turn, "no_match")
            turn.context_out = {**base_ctx, "awaiting": "details",
                                "clarification_turns": clarification_turns + 1}
            return finish(Outcome.CLARIFY)

        if outcome.decision is Decision.ESCALATE:
            self._escalate(turn, box, {
                "reason": "policy_escalation", "rules": outcome.escalation_reasons,
                "transaction_id": txn["transaction_id"] if txn else None,
            })
            return finish(Outcome.ESCALATED)

        # PROCEED: hay una transacción y la política permite actuar.
        details = {
            "amount": money(float(txn["amount"])), "currency": txn["currency"],
            "date": txn["transaction_date"], "merchant": txn["merchant_name"] or "—",
        }

        turn.states.append(State.CONFIRM)
        if not confirmed:
            turn.actions_not_taken.append({
                "action": "create_dispute_case",
                "reason": "ACT-01 requiere confirmación explícita del cliente"})
            self._say(turn, "confirm", **details)
            turn.context_out = {**base_ctx, "awaiting": "confirmation",
                                "clarification_turns": clarification_turns}
            return finish(Outcome.CLARIFY)

        turn.states.append(State.ACT)
        if not session.can(AuthLevel.HIGH):
            turn.actions_not_taken.append({
                "action": "create_dispute_case",
                "reason": "ACT-01 requiere verificación adicional de identidad"})
            self._say(turn, "step_up")
            turn.context_out = {**base_ctx, "awaiting": "confirmation",
                                "clarification_turns": clarification_turns}
            return finish(Outcome.CLARIFY)

        # Capa 3 (DATA-01): los argumentos salen de la búsqueda verificada.
        try:
            require_trusted(
                transaction_id=Tagged(txn["transaction_id"], Origin.VERIFIED_TOOL),
                customer_id=Tagged(session.customer_id, Origin.SESSION),
            )
        except ProvenanceError as exc:
            turn.escalation_reasons.append(str(exc))
            turn.actions_not_taken.append({"action": "create_dispute_case", "reason": str(exc)})
            self._escalate(turn, box, {"reason": "provenance_violation"})
            return finish(Outcome.ESCALATED, str(exc))

        try:
            created = box.create_dispute_case(
                txn["transaction_id"], intent or "unrecognized_charge", confirmed=True)
        except ToolError as exc:
            turn.escalation_reasons.append(f"ACT: la herramienta rechazó la acción ({exc})")
            self._escalate(turn, box, {"reason": "tool_error", "detail": str(exc)})
            return finish(Outcome.ESCALATED, str(exc))

        turn.states.append(State.VERIFY)
        if not created.verified:
            # No se informa una acción que no se pudo comprobar.
            turn.escalation_reasons.append("VERIFY: la acción no pudo verificarse")
            turn.actions_not_taken.append({"action": "create_dispute_case",
                                           "reason": created.error or "sin verificar"})
            self._escalate(turn, box, {"reason": "unverified_action", "detail": created.error})
            return finish(Outcome.ESCALATED)

        turn.evidence.extend(created.evidence)
        turn.actions_taken.append({
            "action": "create_dispute_case", "verified": True,
            "case_id": created.data["case_id"], "evidence_ids": created.evidence_ids(),
        })
        turn.states.append(State.RESPOND)
        bank_days = self.engine.country_params(session.country)["bank_resolution_days"]
        self._say(turn, "resolved", case_id=created.data["case_id"], bank_days=bank_days,
                  **details)
        return finish(Outcome.RESOLVED)

    def _reply_constants(self) -> list[int]:
        """Cifras de la política que las plantillas pueden citar sin evidencia."""
        consts: list[int] = []
        for params in self.engine.policy["countries"].values():
            consts += [params["claim_window_days"], params["bank_resolution_days"]]
        return consts
