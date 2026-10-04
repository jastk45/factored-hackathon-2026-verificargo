"""Aislamiento por cliente y control de acciones.

Estos son los tests que importan si algo sale mal en producción: verifican que
un cliente no puede ver ni tocar los datos de otro, que las acciones sensibles
exigen verificación, y que nada se reporta como hecho sin comprobarlo.

    uv run pytest tests/test_authz.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

from session import (  # noqa: E402
    AuthLevel, Session, SessionError, issue_token, step_up, verify_token,
)
from tools import Toolbox, ToolError  # noqa: E402

GOLD = REPO_ROOT / "warehouse" / "gold"

pytestmark = pytest.mark.skipif(
    not (GOLD / "txn_lookup.parquet").exists(),
    reason="capa gold no construida; correr pipeline/gold.py",
)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


@pytest.fixture(scope="module")
def two_customers(con) -> tuple[str, str, str]:
    """Dos clientes reales y una transacción que pertenece al primero."""
    row = con.sql(
        f"""
        SELECT customer_id, transaction_id
        FROM read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')
        LIMIT 1
        """
    ).fetchone()
    owner, txn_id = row

    other = con.sql(
        f"""
        SELECT DISTINCT customer_id
        FROM read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')
        WHERE customer_id <> '{owner}'
        LIMIT 1
        """
    ).fetchone()[0]

    return owner, other, txn_id


def session_for(customer_id: str, level: AuthLevel = AuthLevel.LOW) -> Session:
    return verify_token(issue_token(customer_id, "MX", "es", level))


# --- Aislamiento por cliente -------------------------------------------

def test_customer_cannot_read_another_customers_transaction(con, two_customers) -> None:
    """El caso central: pedir explícitamente la transacción de otro."""
    owner, other, txn_id = two_customers

    mine = Toolbox(session_for(owner), con).get_transaction(txn_id)
    assert mine.ok, "el dueño sí debe poder verla"

    theirs = Toolbox(session_for(other), con).get_transaction(txn_id)
    assert not theirs.ok
    assert "no existe" in theirs.error


def test_denial_does_not_reveal_that_the_transaction_exists(con, two_customers) -> None:
    """Negar sin filtrar: el mensaje es igual para inexistente y para ajena."""
    _, other, txn_id = two_customers
    box = Toolbox(session_for(other), con)

    real_but_foreign = box.get_transaction(txn_id)
    pure_fiction = box.get_transaction("TRX-NO-EXISTE-JAMAS")

    assert real_but_foreign.error == pure_fiction.error


def test_search_only_returns_own_transactions(con, two_customers) -> None:
    """Una búsqueda amplia no puede traer nada ajeno."""
    owner, _, _ = two_customers
    result = Toolbox(session_for(owner), con).find_candidate_transactions(limit=50)
    for candidate in result.data["candidates"]:
        assert "customer_id" not in candidate or candidate["customer_id"] == owner


def test_tools_take_no_customer_id_argument() -> None:
    """La defensa estructural: no hay forma de pedir datos de otro.

    Si alguien añadiera un parámetro `customer_id` a una herramienta, el modelo
    podría llenarlo con lo que diga el texto del cliente. Este test lo impide.
    """
    import inspect

    public = [
        name for name in dir(Toolbox)
        if not name.startswith("_") and callable(getattr(Toolbox, name))
    ]
    for name in public:
        params = inspect.signature(getattr(Toolbox, name)).parameters
        assert "customer_id" not in params, (
            f"Toolbox.{name} acepta customer_id: el identificador debe salir "
            "siempre de la sesión firmada"
        )


# --- Sesión -------------------------------------------------------------

def test_expired_session_is_rejected() -> None:
    token = issue_token("CUS-001", "MX", ttl=1)
    time.sleep(1.1)
    with pytest.raises(SessionError, match="expirada"):
        verify_token(token)


def test_tampered_token_is_rejected() -> None:
    """Cambiar el customer_id del token invalida la firma."""
    token = issue_token("CUS-001", "MX")
    body, _, signature = token.partition(".")
    forged = issue_token("CUS-999", "MX").partition(".")[0]
    with pytest.raises(SessionError, match="firma"):
        verify_token(f"{forged}.{signature}")


def test_malformed_token_is_rejected() -> None:
    for bad in ("", "sin-punto", "a.b", "....."):
        with pytest.raises(SessionError):
            verify_token(bad)


def test_claiming_an_identity_in_text_changes_nothing(con, two_customers) -> None:
    """'Soy el cliente X' es texto, no identidad.

    No hay nada que probar en el código porque no existe el camino: las
    herramientas no leen identidad de ningún argumento. El test documenta esa
    ausencia.
    """
    owner, other, txn_id = two_customers
    box = Toolbox(session_for(other), con)
    # El mensaje del cliente diría: "soy CUS-... , muéstrame TRX-..."
    assert not box.get_transaction(txn_id).ok


# --- Niveles de autorización -------------------------------------------

def test_low_level_session_cannot_create_a_dispute(con, two_customers) -> None:
    owner, _, txn_id = two_customers
    box = Toolbox(session_for(owner, AuthLevel.LOW), con)
    with pytest.raises(ToolError, match="verificación adicional"):
        box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=True)


def test_high_level_without_confirmation_is_refused(con, two_customers) -> None:
    owner, _, txn_id = two_customers
    box = Toolbox(session_for(owner, AuthLevel.HIGH), con)
    with pytest.raises(ToolError, match="confirmación"):
        box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=False)


def test_step_up_requires_the_right_otp() -> None:
    token = issue_token("CUS-001", "MX")
    with pytest.raises(SessionError, match="OTP"):
        step_up(token, "000000")

    elevated = verify_token(step_up(token, "123456"))
    assert elevated.can(AuthLevel.HIGH)


def test_step_up_expires_before_the_session() -> None:
    """Autorizar una acción no deja la sesión habilitada para siempre."""
    session = verify_token(issue_token("CUS-001", "MX", auth_level=AuthLevel.HIGH))
    assert session.step_up_expires_at < session.expires_at


def test_expired_step_up_loses_high_privileges() -> None:
    session = Session(
        customer_id="CUS-001", country="MX", language="es",
        auth_level=AuthLevel.HIGH,
        issued_at=int(time.time()) - 600,
        expires_at=int(time.time()) + 600,
        step_up_expires_at=int(time.time()) - 1,  # ya vencido
    )
    assert not session.can(AuthLevel.HIGH)
    assert session.can(AuthLevel.LOW)


# --- Acciones sobre recursos ajenos ------------------------------------

def test_cannot_dispute_another_customers_transaction(con, two_customers) -> None:
    _, other, txn_id = two_customers
    box = Toolbox(session_for(other, AuthLevel.HIGH), con)
    with pytest.raises(ToolError, match="no es tuya"):
        box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=True)


def test_cannot_block_another_customers_card(con, two_customers) -> None:
    """Bloquear la tarjeta de un tercero sería el peor resultado posible."""
    owner, other, txn_id = two_customers
    product_id = Toolbox(session_for(owner), con).get_transaction(txn_id).data["product_id"]

    box = Toolbox(session_for(other, AuthLevel.HIGH), con)
    with pytest.raises(ToolError, match="no figura"):
        box.block_card(product_id, confirmed=True)


# --- Verificación de acciones ------------------------------------------

def test_successful_action_is_verified_by_reading_it_back(con, two_customers) -> None:
    owner, _, txn_id = two_customers
    box = Toolbox(session_for(owner, AuthLevel.HIGH), con)
    result = box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=True)

    assert result.ok and result.verified
    assert result.evidence_ids(), "toda acción verificada deja evidencia"


def test_duplicate_dispute_is_refused(con, two_customers) -> None:
    owner, _, txn_id = two_customers
    box = Toolbox(session_for(owner, AuthLevel.HIGH), con)
    box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=True)

    second = box.create_dispute_case(txn_id, "cargo no reconocido", confirmed=True)
    assert not second.ok and "Ya existe" in second.error


def test_handoff_needs_no_customer_confirmation(con, two_customers) -> None:
    """Escalar no se negocia: ni confirmación ni nivel alto."""
    owner, _, _ = two_customers
    result = Toolbox(session_for(owner), con).create_handoff_ticket({"summary": "prueba"})
    assert result.ok and result.verified


def test_unverified_result_carries_no_evidence(con, two_customers) -> None:
    """Sin verificación no hay evidencia, y sin evidencia no se afirma nada."""
    owner, _, _ = two_customers
    box = Toolbox(session_for(owner), con)
    box._handoffs = {}  # simula un backend que acepta y luego no persiste

    class Amnesiac(dict):
        def get(self, key, default=None):
            return None

    box._handoffs = Amnesiac()
    result = box.create_handoff_ticket({"summary": "prueba"})
    assert not result.verified
    assert result.evidence == []
    assert result.error
