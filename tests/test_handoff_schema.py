"""El esquema del handoff hace cumplir sus reglas, no las recuerda.

Lo que se verifica acá es que sea *imposible* construir un paquete que engañe
al agente humano: una acción sin evidencia, un hecho sin fuente, un número de
tarjeta colado en texto libre o un traspaso sin motivo.

    uv run pytest tests/test_handoff_schema.py -v
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

from handoff import (  # noqa: E402
    SLA, ActionNotTaken, ActionTaken, CustomerClaim, HandoffPackage, Priority,
    VerifiedFact, contains_card_number, customer_reference,
)


def minimal(**overrides) -> dict:
    base = {
        "handoff_id": "HO-ABC123",
        "created_at": datetime.now(),
        "language": "es",
        "country": "MX",
        "customer_ref": customer_reference("CUS-001"),
        "request_summary": "Cliente no reconoce un cargo de su tarjeta",
        "trigger_rules": ["ESC-01: monto sobre el umbral"],
        "sla": SLA(provenance="real"),
    }
    return {**base, **overrides}


# --- Hechos contra afirmaciones ----------------------------------------

def test_a_verified_fact_requires_evidence() -> None:
    """Sin evidencia no es un hecho verificado."""
    with pytest.raises(ValidationError):
        VerifiedFact(fact="La transacción existe", source="gold.txn_lookup",
                     evidence_id="")


def test_evidence_id_must_have_the_expected_shape() -> None:
    with pytest.raises(ValidationError):
        VerifiedFact(fact="algo", source="gold.txn_lookup", evidence_id="123")


def test_customer_claims_cannot_carry_evidence() -> None:
    """Lo que el cliente afirma no puede disfrazarse de hecho comprobado."""
    claim = CustomerClaim(claim="Dice que la tarjeta está en su poder")
    assert not hasattr(claim, "evidence_id")


def test_facts_and_claims_live_in_separate_lists() -> None:
    package = HandoffPackage(**minimal(
        verified_facts=[VerifiedFact(fact="TRX-1 por 120 USD aprobada",
                                     source="gold.txn_lookup",
                                     evidence_id="EV-AAA111")],
        customer_claims_unverified=[CustomerClaim(claim="Dice que no salió de viaje")],
    ))
    assert len(package.verified_facts) == 1
    assert len(package.customer_claims_unverified) == 1


# --- Acciones y evidencia ----------------------------------------------

def test_verified_action_without_evidence_is_rejected() -> None:
    """La regla central: no se reporta como hecho lo que no se puede probar."""
    with pytest.raises(ValidationError, match="evidencia"):
        ActionTaken(action="create_dispute_case", verified=True,
                    evidence_ids=[], at=datetime.now())


def test_unverified_action_may_have_no_evidence() -> None:
    """Un intento fallido sí puede ir sin evidencia: eso es honesto."""
    action = ActionTaken(action="block_card", verified=False,
                         evidence_ids=[], at=datetime.now())
    assert not action.verified


def test_action_cannot_reference_evidence_outside_the_package() -> None:
    """Un id que el humano no puede abrir es peor que no ponerlo."""
    with pytest.raises(ValidationError, match="no viaja en el paquete"):
        HandoffPackage(**minimal(
            verified_facts=[VerifiedFact(fact="algo comprobado",
                                         source="tool:x",
                                         evidence_id="EV-AAA111")],
            actions_taken=[ActionTaken(action="create_dispute_case",
                                       verified=True,
                                       evidence_ids=["EV-NOEXISTE1"],
                                       at=datetime.now())],
        ))


def test_action_referencing_present_evidence_is_accepted() -> None:
    package = HandoffPackage(**minimal(
        verified_facts=[VerifiedFact(fact="Disputa DSP-1 creada",
                                     source="tool:create_dispute_case",
                                     evidence_id="EV-BBB222")],
        actions_taken=[ActionTaken(action="create_dispute_case", verified=True,
                                   evidence_ids=["EV-BBB222"],
                                   at=datetime.now())],
    ))
    assert package.actions_taken[0].verified


def test_actions_not_taken_must_explain_why() -> None:
    with pytest.raises(ValidationError):
        ActionNotTaken(action="block_card", reason="")


# --- PII y datos sensibles ---------------------------------------------

def test_luhn_detector_recognises_a_card_number() -> None:
    assert contains_card_number("mi tarjeta es 4111 1111 1111 1111")
    assert contains_card_number("4111111111111111")
    # Un número largo que no pasa Luhn no es una tarjeta.
    assert not contains_card_number("1234567890123456")
    # Un importe no debe confundirse con una tarjeta.
    assert not contains_card_number("el cargo fue de 1.121.353 COP")


def test_card_number_in_summary_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tarjeta"):
        HandoffPackage(**minimal(
            request_summary="El cliente reporta la tarjeta 4111 1111 1111 1111"
        ))


def test_card_number_in_narrative_is_rejected() -> None:
    """Es el único campo que puede escribir el LLM: también se valida."""
    with pytest.raises(ValidationError, match="tarjeta"):
        HandoffPackage(**minimal(
            narrative_summary="Mencionó el plástico 4111111111111111 al llamar"
        ))


def test_card_number_in_open_questions_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tarjeta"):
        HandoffPackage(**minimal(
            open_questions=["¿Confirma que su tarjeta 4111111111111111 sigue con él?"]
        ))


def test_customer_id_is_not_exposed_in_the_clear() -> None:
    reference = customer_reference("CUS-HVDU7UF8SBAY")
    assert "CUS-HVDU7UF8SBAY" not in reference
    assert reference.startswith("CUS-REF-")
    # Estable: el mismo cliente da siempre la misma referencia.
    assert reference == customer_reference("CUS-HVDU7UF8SBAY")


def test_there_is_no_field_for_the_transcript() -> None:
    """El reto pide contexto útil, no volcar la conversación cruda."""
    fields = set(HandoffPackage.model_fields)
    for forbidden in ("transcript", "conversation", "messages", "raw_text",
                      "chat_history"):
        assert forbidden not in fields


# --- Trazabilidad -------------------------------------------------------

def test_handoff_without_a_trigger_rule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HandoffPackage(**minimal(trigger_rules=[]))


def test_blank_trigger_rule_is_rejected() -> None:
    with pytest.raises(ValidationError, match="auditable"):
        HandoffPackage(**minimal(trigger_rules=["   "]))


def test_sla_declares_whether_the_deadline_is_real() -> None:
    """MX cita CONDUSEF; CO y AR son parámetros del equipo (D-07)."""
    real = HandoffPackage(**minimal(
        sla=SLA(regulatory_deadline=date(2026, 9, 1), days_remaining=12,
                provenance="real")))
    synthetic = HandoffPackage(**minimal(
        country="CO", sla=SLA(provenance="synthetic")))

    assert real.sla.provenance == "real"
    assert synthetic.sla.provenance == "synthetic"


def test_package_serialises_to_json() -> None:
    """El CRM lo consume como JSON: tiene que poder serializarse."""
    package = HandoffPackage(**minimal(
        verified_facts=[VerifiedFact(fact="TRX-1 aprobada",
                                     source="gold.txn_lookup",
                                     evidence_id="EV-CCC333")],
        priority=Priority.HIGH,
    ))
    payload = package.model_dump_json()
    assert "EV-CCC333" in payload
    assert "CUS-001" not in payload, "el id real del cliente no debe viajar"
