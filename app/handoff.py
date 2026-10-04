"""Paquete de traspaso a un agente humano.

El reto pide dar al humano "the request, verified facts, actions taken,
supporting evidence, and unresolved questions" — y no volcarle la conversación
cruda encima.

Tres reglas que el esquema hace cumplir, no que recuerda:

1. **Los hechos verificados viven separados de lo que el cliente afirma.**
   Un `VerifiedFact` exige `evidence_id`; un `CustomerClaim` no puede tenerlo.
   El humano ve de un vistazo qué está comprobado y qué es un dicho.

2. **Nunca viaja el transcript.** No hay campo para ponerlo.

3. **Nada de lo que viaja es PII innecesaria.** El cliente va como referencia
   hasheada, y un validador rechaza números que parezcan tarjetas.

El JSON lo construye el código a partir del estado, no el modelo. Lo único que
el LLM puede aportar es `narrative_summary`, un resumen de dos o tres frases
—y aun ese pasa por el validador anti-PAN.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

# Una secuencia de 13-19 dígitos que pasa Luhn es, con alta probabilidad, un
# número de tarjeta. No puede salir del sistema ni siquiera dentro de un texto
# libre.
_DIGIT_RUN = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total, alternate = 0, False
    for char in reversed(digits):
        value = int(char)
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def contains_card_number(text: str) -> bool:
    for match in _DIGIT_RUN.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


def no_card_numbers(value: str) -> str:
    if contains_card_number(value):
        raise ValueError("el texto contiene algo que parece un número de tarjeta")
    return value


def customer_reference(customer_id: str) -> str:
    """Referencia estable y no reversible del cliente.

    El agente humano no necesita el id real para atender el caso: lo busca por
    el ticket. Si en algún momento hiciera falta, se resuelve en el CRM contra
    la sesión, no viajando en el paquete.
    """
    return "CUS-REF-" + hashlib.sha256(customer_id.encode()).hexdigest()[:12].upper()


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class VerifiedFact(BaseModel):
    """Un hecho comprobado contra una fuente. Sin evidencia no entra."""

    fact: str = Field(min_length=3)
    source: str = Field(min_length=3, description="tabla o herramienta de origen")
    evidence_id: str = Field(pattern=r"^EV-[A-Z0-9]{6,}$")

    _clean = field_validator("fact")(no_card_numbers)


class CustomerClaim(BaseModel):
    """Algo que el cliente afirma y que NO se pudo verificar.

    Va en su propia lista para que nadie lo confunda con un hecho.
    """

    claim: str = Field(min_length=3)

    _clean = field_validator("claim")(no_card_numbers)


class ActionTaken(BaseModel):
    action: str
    verified: bool
    evidence_ids: list[str] = Field(default_factory=list)
    at: datetime

    @model_validator(mode="after")
    def verified_actions_need_evidence(self) -> ActionTaken:
        """No se reporta como hecha una acción sin con qué probarlo."""
        if self.verified and not self.evidence_ids:
            raise ValueError(
                f"la acción '{self.action}' se declara verificada pero no "
                "aporta evidencia"
            )
        return self


class ActionNotTaken(BaseModel):
    action: str
    reason: str = Field(min_length=3, description="por qué no se hizo")


class SLA(BaseModel):
    regulatory_deadline: date | None = None
    days_remaining: int | None = None
    provenance: str = Field(
        description="'real' si el plazo sale de normativa citada; "
        "'synthetic' si lo definió el equipo"
    )


class HandoffPackage(BaseModel):
    """Lo que recibe el agente humano. Nada más y nada menos."""

    handoff_id: str = Field(pattern=r"^HO-[A-Z0-9]{6,}$")
    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    language: str = Field(pattern=r"^(es|pt)$")
    country: str

    customer_ref: str = Field(pattern=r"^CUS-REF-[A-F0-9]{12}$")
    request_summary: str = Field(min_length=5, max_length=500)

    trigger_rules: list[str] = Field(
        min_length=1, description="IDs de las reglas que provocaron el traspaso"
    )
    policy_trace: list[str] = Field(default_factory=list)

    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    customer_claims_unverified: list[CustomerClaim] = Field(default_factory=list)

    actions_taken: list[ActionTaken] = Field(default_factory=list)
    actions_not_taken: list[ActionNotTaken] = Field(default_factory=list)

    open_questions: list[str] = Field(default_factory=list)
    sla: SLA
    priority: Priority = Priority.NORMAL

    narrative_summary: str | None = Field(
        default=None, max_length=600,
        description="Lo único que puede redactar el LLM. Los hechos vienen de "
        "verified_facts, no de acá.",
    )

    _clean_summary = field_validator("request_summary")(no_card_numbers)

    @field_validator("narrative_summary")
    @classmethod
    def narrative_is_clean(cls, value: str | None) -> str | None:
        return None if value is None else no_card_numbers(value)

    @field_validator("open_questions")
    @classmethod
    def questions_are_clean(cls, value: list[str]) -> list[str]:
        for question in value:
            no_card_numbers(question)
        return value

    @model_validator(mode="after")
    def evidence_ids_must_resolve(self) -> HandoffPackage:
        """Toda acción verificada apunta a evidencia que está en el paquete.

        Si no, el humano vería un identificador que no puede abrir.
        """
        known = {fact.evidence_id for fact in self.verified_facts}
        for action in self.actions_taken:
            missing = [e for e in action.evidence_ids if e not in known]
            if missing:
                raise ValueError(
                    f"la acción '{action.action}' referencia evidencia que no "
                    f"viaja en el paquete: {', '.join(missing)}"
                )
        return self

    @model_validator(mode="after")
    def escalation_must_have_a_reason(self) -> HandoffPackage:
        if not any(rule.strip() for rule in self.trigger_rules):
            raise ValueError("un traspaso sin regla que lo justifique no es auditable")
        return self


def build_package(
    *,
    handoff_id: str,
    turn: Any,
    session: Any,
    engine: Any,
    request_summary: str,
    transaction: dict[str, Any] | None = None,
    open_questions: list[str] | None = None,
    narrative_summary: str | None = None,
) -> HandoffPackage:
    """Arma el paquete desde el estado del turno.

    El LLM no participa salvo en `narrative_summary`, y ese campo es opcional:
    si falla, el paquete sigue siendo válido y completo.
    """
    facts: list[VerifiedFact] = [
        VerifiedFact(
            fact=evidence.fact,
            source=evidence.source,
            evidence_id=evidence.evidence_id,
        )
        for evidence in turn.evidence
    ]

    claims: list[CustomerClaim] = []
    extracted = turn.extracted or {}
    if extracted.get("amount") is not None:
        claims.append(
            CustomerClaim(
                claim=f"Dice que el cargo fue de {extracted['amount']} "
                f"{extracted.get('currency') or ''}".strip()
            )
        )
    if extracted.get("merchant"):
        claims.append(CustomerClaim(claim=f"Menciona el comercio '{extracted['merchant']}'"))
    if extracted.get("date"):
        claims.append(CustomerClaim(claim=f"Sitúa el cargo el {extracted['date']}"))

    actions = [
        ActionTaken(
            action=a["action"],
            verified=a.get("verified", False),
            evidence_ids=a.get("evidence_ids", []),
            at=datetime.now(),
        )
        for a in turn.actions_taken
    ]
    not_taken = [
        ActionNotTaken(action=a["action"], reason=a["reason"])
        for a in turn.actions_not_taken
    ]

    params = engine.country_params(session.country)
    deadline = None
    remaining = None
    if transaction is not None and transaction.get("transaction_date"):
        from tools import TODAY
        days_since = (TODAY - transaction["transaction_date"]).days
        remaining = params["claim_window_days"] - days_since
        deadline = date.fromordinal(
            transaction["transaction_date"].toordinal() + params["claim_window_days"]
        )

    priority = Priority.NORMAL
    if remaining is not None and remaining <= 10:
        priority = Priority.HIGH
    if any("ESC-03" in reason for reason in turn.escalation_reasons):
        priority = Priority.CRITICAL

    return HandoffPackage(
        handoff_id=handoff_id,
        created_at=datetime.now(),
        language=turn.language,
        country=session.country,
        customer_ref=customer_reference(session.customer_id),
        request_summary=request_summary,
        trigger_rules=turn.escalation_reasons or ["ESC-00: escalamiento manual"],
        policy_trace=turn.policy_trace,
        verified_facts=facts,
        customer_claims_unverified=claims,
        actions_taken=actions,
        actions_not_taken=not_taken,
        open_questions=open_questions or [],
        sla=SLA(
            regulatory_deadline=deadline,
            days_remaining=remaining,
            provenance=params.get("provenance", "synthetic"),
        ),
        priority=priority,
        narrative_summary=narrative_summary,
    )
