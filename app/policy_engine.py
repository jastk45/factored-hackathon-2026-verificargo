"""Motor de política: decide, sin modelo de lenguaje de por medio.

Recibe los hechos de un caso y devuelve una decisión con la traza de las reglas
que se evaluaron. El LLM no participa: propone una intención y unos campos, y
esto resuelve qué se puede hacer.

Las reglas se evalúan como funciones puras de Python, no interpretando
expresiones del YAML. El YAML aporta los *parámetros* (plazos, umbrales,
mensajes) y los IDs; el código aporta la lógica. Interpretar expresiones
arbitrarias de un archivo de datos sería un vector de ejecución innecesario en
un sistema que decide sobre dinero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy" / "dispute_policy.yaml"


class Decision(str, Enum):
    """Qué puede pasar con un caso."""

    PROCEED = "PROCEED"      # elegible: sigue al paso de confirmación
    CLARIFY = "CLARIFY"      # falta información para identificar el cargo
    ESCALATE = "ESCALATE"    # lo atiende un humano
    DENY = "DENY"            # no procede, y se explica por qué


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    outcome: str | None = None       # qué provoca si no pasa
    detail: str | None = None        # dato concreto, para el handoff


@dataclass(frozen=True)
class PolicyOutcome:
    decision: Decision
    trace: list[RuleResult] = field(default_factory=list)
    message_key: str | None = None
    message_params: dict[str, Any] = field(default_factory=dict)
    escalation_reasons: list[str] = field(default_factory=list)

    def fired(self) -> list[str]:
        """IDs de las reglas que no pasaron, en orden de evaluación."""
        return [r.rule_id for r in self.trace if not r.passed]

    def as_policy_trace(self) -> list[str]:
        """Traza compacta para el paquete de handoff: 'GATE-01:pass'."""
        return [f"{r.rule_id}:{'pass' if r.passed else 'fire'}" for r in self.trace]


@dataclass(frozen=True)
class CaseFacts:
    """Hechos verificados de un caso.

    Todo lo que llega acá salió de una herramienta o de la sesión, nunca del
    texto del cliente (DATA-01). `customer_claims_fraud` es la excepción
    deliberada: es una afirmación del cliente, se trata como tal y solo puede
    *añadir* un escalamiento, nunca quitarlo.
    """

    session_customer_id: str
    country: str
    days_since_transaction: int | None = None
    transaction_customer_id: str | None = None
    transaction_status: str | None = None
    amount_usd: float | None = None
    amount_usd_source: str | None = None
    candidate_count: int = 0
    existing_open_dispute: bool = False
    intent: str | None = None
    customer_claims_fraud: bool = False
    unrecognized_charges_last_30d: int = 0
    requires_assisted_channel: bool = False
    clarification_turns: int = 0


class PolicyEngine:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or POLICY_PATH
        self.policy: dict[str, Any] = yaml.safe_load(
            self.path.read_text(encoding="utf-8")
        )

    # --- acceso a parámetros -------------------------------------------

    @property
    def version(self) -> str:
        return self.policy["version"]

    def country_params(self, country: str) -> dict[str, Any]:
        countries = self.policy["countries"]
        if country not in countries:
            raise KeyError(
                f"País '{country}' no está en la política. "
                f"Disponibles: {', '.join(countries)}"
            )
        return countries[country]

    def threshold(self, name: str) -> Any:
        return self.policy["thresholds"][name]

    def action(self, name: str) -> dict[str, Any]:
        for act in self.policy["actions"]:
            if act["name"] == name:
                return act
        raise KeyError(f"Acción '{name}' no está declarada en la política")

    def message(self, key: str, lang: str) -> str:
        return self.policy["messages"][key][lang]

    def synthetic_countries(self) -> list[str]:
        """Países cuyos parámetros inventó el equipo. Se declaran en la UI."""
        return [
            code for code, params in self.policy["countries"].items()
            if params.get("provenance") == "synthetic"
        ]

    # --- evaluación ----------------------------------------------------

    def evaluate(self, facts: CaseFacts) -> PolicyOutcome:
        """Evalúa gates y escalamientos. Devuelve la decisión y la traza."""
        trace: list[RuleResult] = []
        country = self.country_params(facts.country)

        # GATE-02 primero: el aislamiento por cliente se comprueba antes que
        # nada. Si la transacción no es suya, no se revela nada más sobre ella.
        if facts.transaction_customer_id is not None:
            owns = facts.transaction_customer_id == facts.session_customer_id
            trace.append(RuleResult("GATE-02", owns, "DENY"))
            if not owns:
                return PolicyOutcome(
                    Decision.DENY, trace, "not_your_transaction"
                )

        # GATE-04: ¿hay exactamente una transacción candidata?
        unique = facts.candidate_count == 1
        trace.append(
            RuleResult("GATE-04", unique, "CLARIFY", f"candidatas={facts.candidate_count}")
        )
        if not unique:
            return PolicyOutcome(
                Decision.CLARIFY,
                trace,
                "ambiguous_transaction",
                {"count": min(facts.candidate_count, self.threshold("max_candidates_to_offer"))},
            )

        # GATE-03: estado disputable.
        if facts.transaction_status is not None:
            disputable = facts.transaction_status in ("Approved", "Pending")
            trace.append(
                RuleResult("GATE-03", disputable, "DENY", facts.transaction_status)
            )
            if not disputable:
                return PolicyOutcome(
                    Decision.DENY, trace, "not_disputable_status",
                    {"status": facts.transaction_status},
                )

        # GATE-05: no duplicar un caso abierto.
        no_dup = not facts.existing_open_dispute
        trace.append(RuleResult("GATE-05", no_dup, "DENY"))
        if not no_dup:
            return PolicyOutcome(Decision.DENY, trace, "duplicate_dispute")

        # GATE-01: dentro del plazo de reclamo.
        window = country["claim_window_days"]
        days = facts.days_since_transaction
        within = days is not None and days <= window
        trace.append(RuleResult("GATE-01", within, "ESCALATE", f"{days}d de {window}d"))
        if not within:
            return PolicyOutcome(
                Decision.ESCALATE, trace, "outside_claim_window",
                {"days": days}, ["GATE-01: fuera del plazo de reclamo"],
            )

        # Escalamientos: se evalúan TODOS, porque el handoff necesita saber
        # cuántos motivos hay, no solo el primero.
        reasons = self._escalations(facts, country, trace)
        if reasons:
            return PolicyOutcome(Decision.ESCALATE, trace, None, {}, reasons)

        return PolicyOutcome(Decision.PROCEED, trace)

    def _escalations(
        self, facts: CaseFacts, country: dict[str, Any], trace: list[RuleResult]
    ) -> list[str]:
        reasons: list[str] = []

        limit = self.threshold("escalate_amount_usd")
        over = facts.amount_usd is not None and facts.amount_usd >= limit
        trace.append(RuleResult("ESC-01", not over, "ESCALATE"))
        if over:
            # Sin símbolos fuera de ASCII: estas cadenas viajan a logs y
            # consolas cuya codificación no controlamos (Windows usa cp1252).
            reasons.append(
                f"ESC-01: monto {facts.amount_usd:,.2f} USD supera el umbral "
                f"de {limit:,.0f} USD"
            )

        margin = self.threshold("near_deadline_days")
        remaining = country["claim_window_days"] - (facts.days_since_transaction or 0)
        near = remaining <= margin
        trace.append(RuleResult("ESC-02", not near, "ESCALATE", f"quedan {remaining}d"))
        if near:
            reasons.append(f"ESC-02: quedan {remaining} días del plazo")

        fraud = facts.intent == "card_lost_stolen" or facts.customer_claims_fraud
        trace.append(RuleResult("ESC-03", not fraud, "ESCALATE"))
        if fraud:
            reasons.append("ESC-03: fraude declarado o tarjeta comprometida")

        pattern = facts.unrecognized_charges_last_30d >= 3
        trace.append(
            RuleResult("ESC-04", not pattern, "ESCALATE",
                       f"{facts.unrecognized_charges_last_30d} en 30d")
        )
        if pattern:
            reasons.append(
                f"ESC-04: {facts.unrecognized_charges_last_30d} cargos no "
                "reconocidos en 30 días"
            )

        no_rate = facts.amount_usd_source == "unavailable"
        trace.append(RuleResult("ESC-05", not no_rate, "ESCALATE"))
        if no_rate:
            reasons.append("ESC-05: sin tasa de cambio para normalizar el monto")

        exhausted = facts.clarification_turns > self.threshold("max_clarification_turns")
        trace.append(RuleResult("ESC-06", not exhausted, "ESCALATE"))
        if exhausted:
            reasons.append("ESC-06: se agotaron las aclaraciones")

        assisted = facts.requires_assisted_channel
        trace.append(RuleResult("ESC-07", not assisted, "ESCALATE"))
        if assisted:
            reasons.append("ESC-07: el cliente requiere canal asistido")

        return reasons

    # --- permisos de acción --------------------------------------------

    def check_action(
        self, name: str, auth_level: str, confirmed: bool
    ) -> tuple[bool, str | None]:
        """¿Se puede ejecutar esta acción ahora? Devuelve (permitido, motivo)."""
        act = self.action(name)

        levels = {"low": 0, "high": 1}
        needed = act["requires_auth_level"]
        if levels.get(auth_level, -1) < levels[needed]:
            return False, f"{act['id']}: requiere auth_level '{needed}'"

        if act["requires_confirmation"] and not confirmed:
            return False, f"{act['id']}: requiere confirmación explícita"

        return True, None

    def argument_sources_are_valid(self, sources: dict[str, str]) -> tuple[bool, str | None]:
        """DATA-01: ningún argumento sensible viene del texto del cliente.

        `sources` mapea nombre de argumento a su procedencia.
        """
        for arg, origin in sources.items():
            if origin == "customer_text":
                return False, (
                    f"DATA-01: el argumento '{arg}' proviene del texto del "
                    "cliente y no fue verificado contra una herramienta"
                )
        return True, None
