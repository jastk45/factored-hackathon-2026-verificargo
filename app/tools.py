"""Herramientas del agente: el único punto que toca datos o ejecuta acciones.

Todas siguen el mismo ciclo, sin excepción:

    autorizar -> validar precondiciones -> ejecutar -> RE-LEER -> auditar

El paso de re-lectura es el que permite cumplir "report only actions whose
outcomes the system has verified": después de escribir, se vuelve a leer y se
compara. Si no coincide, la herramienta devuelve `verified=False` y el agente
no puede decir que la acción ocurrió.

`customer_id` **siempre** sale de la sesión firmada, nunca de un argumento.
Ninguna función pública acepta un `customer_id`: esa es la defensa estructural
contra que el modelo (o un texto malicioso) elija a quién consultar.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from session import AuthLevel, Session

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD = REPO_ROOT / "warehouse" / "gold"
AUDIT_LOG = REPO_ROOT / "warehouse" / "audit_log.jsonl"

# Última fecha con datos: el "hoy" del sistema.
TODAY = date(2026, 6, 18)

# El almacén de disputas puede ser compartido por varios hilos (la API): la
# consulta "¿ya existe?" y la escritura van bajo el mismo lock, y también las
# lecturas, porque recorrer un dict mientras otro hilo inserta falla.
_STORE_LOCK = threading.RLock()
_AUDIT_LOCK = threading.Lock()

COUNTRY_CODES = {"México": "MX", "Mexico": "MX", "Colombia": "CO", "Argentina": "AR"}


def customer_country(customer_id: str, con: duckdb.DuckDBPyConnection | None = None) -> str:
    """País del cliente según el registro de clientes, no según lo que diga nadie.

    En producción lo pondría el servicio de identidad en el token. Acá se lee
    del gold: es el dato confiable que tenemos. Un cliente sin país conocido
    es un error, no un "MX" por defecto.
    """
    con = con or duckdb.connect()
    safe = customer_id.replace("'", "''")
    row = con.sql(
        f"""SELECT country FROM read_parquet('{(GOLD / 'customer_360_min.parquet').as_posix()}')
            WHERE customer_id = '{safe}'"""
    ).fetchone()
    if row is None or row[0] not in COUNTRY_CODES:
        raise LookupError(f"país desconocido para el cliente {customer_id}")
    return COUNTRY_CODES[row[0]]


@dataclass
class Evidence:
    """Un hecho verificado, con su origen. Lo que no tiene evidencia no se afirma."""

    evidence_id: str
    source: str
    fact: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Lo que devuelve toda herramienta.

    `verified` es lo que separa "lo intenté" de "ocurrió y lo comprobé".
    """

    ok: bool
    verified: bool
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    error: str | None = None

    def evidence_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence]


class ToolError(Exception):
    """Falla de autorización o de precondición."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _audit(action: str, session: Session, detail: dict[str, Any]) -> None:
    """Registro de ejecución: es el artefacto de auditoría, no el razonamiento.

    El reto lo dice expresamente: "hidden model chain-of-thought is not an
    audit artifact".
    """
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "customer_id": session.customer_id,
        "auth_level": session.auth_level.value,
        **detail,
    }
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    with _AUDIT_LOCK:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)


class Toolbox:
    """Herramientas ligadas a una sesión.

    Se construye con la sesión, así que ninguna llamada puede apuntar a otro
    cliente: no hay parámetro para hacerlo.
    """

    def __init__(
        self,
        session: Session,
        con: duckdb.DuckDBPyConnection | None = None,
        disputes: dict[str, dict[str, Any]] | None = None,
        actor: str | None = None,
    ):
        self.session = session
        self.con = con or duckdb.connect()
        # Estado de las acciones de escritura. En producción sería el core
        # bancario; acá es un almacén en memoria con la misma forma. La API
        # pasa un almacén compartido (el cliente y el agente humano ven las
        # mismas disputas); la evaluación usa uno por caso.
        self._disputes: dict[str, dict[str, Any]] = {} if disputes is None else disputes
        self._blocked_cards: set[str] = set()
        self._handoffs: dict[str, dict[str, Any]] = {}
        # Quién ejecuta: el cliente (None) o un agente humano ("agent:<id>").
        self.actor = actor

    # --- lectura -------------------------------------------------------

    def _txn(self) -> str:
        return f"read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')"

    def find_candidate_transactions(
        self,
        amount: float | None = None,
        currency: str | None = None,
        merchant: str | None = None,
        on_date: str | None = None,
        window_days: int = 5,
        limit: int = 10,
    ) -> ToolResult:
        """Busca transacciones del cliente de la sesión que encajen con lo dicho.

        El cliente redondea montos y recuerda fechas mal, así que el matching es
        difuso: tolerancia relativa en el monto y ventana en la fecha. Lo que no
        es difuso es de quién son las transacciones.
        """
        where = [f"customer_id = '{self.session.customer_id}'"]

        if amount is not None:
            # 10% de tolerancia. El fixture redondea a 50 unidades ("150" por
            # 163,38 es un 8% de desvío), y un cliente real redondea parecido.
            # Un 2% dejaba fuera la transacción correcta y el caso terminaba en
            # CLARIFY pidiendo datos que el cliente ya había dado.
            tolerance = max(1.0, abs(amount) * 0.10)
            where.append(f"abs(amount - {amount}) <= {tolerance}")
        if currency:
            where.append(f"currency = '{currency}'")
        if on_date:
            where.append(
                f"transaction_date BETWEEN DATE '{on_date}' - INTERVAL {window_days} DAY "
                f"AND DATE '{on_date}' + INTERVAL {window_days} DAY"
            )

        rows = self.con.sql(
            f"""
            SELECT transaction_id, transaction_date, amount, currency, amount_usd,
                   amount_usd_source, merchant_name, merchant_category,
                   transaction_status, channel, product_id,
                   evidence_is_fraud, evidence_fraud_score
            FROM {self._txn()}
            WHERE {' AND '.join(where)}
            ORDER BY transaction_date DESC
            LIMIT {limit}
            """
        ).fetchall()

        cols = [
            "transaction_id", "transaction_date", "amount", "currency",
            "amount_usd", "amount_usd_source", "merchant_name",
            "merchant_category", "transaction_status", "channel", "product_id",
            "evidence_is_fraud", "evidence_fraud_score",
        ]
        found = [dict(zip(cols, r)) for r in rows]

        # El comercio ORDENA, no filtra. Un cliente dice "la farmacia" cuando
        # el registro pone "Farmacia Salud", pero también dice "el
        # laboratorio" por "Farmacia Salud": un LIKE descartaría la única
        # candidata correcta y el caso acabaría en CLARIFY sin motivo.
        # Medido: 4 de 6 casos del fixture fallaban así.
        if merchant and len(found) > 1:
            needle = merchant.lower()
            words = {w for w in needle.split() if len(w) > 3}

            def affinity(row: dict) -> int:
                name = (row.get("merchant_name") or "").lower()
                if not name:
                    return 0
                if needle in name or name in needle:
                    return 2
                return 1 if any(word in name for word in words) else 0

            found.sort(key=affinity, reverse=True)

        evidence = [
            Evidence(
                evidence_id=_new_id("EV"),
                source="gold.txn_lookup",
                fact=(
                    f"{t['transaction_id']} · {t['transaction_date']} · "
                    f"{t['amount']:,.2f} {t['currency']} · "
                    f"{t['merchant_name'] or 'sin comercio'} · {t['transaction_status']}"
                ),
                data=t,
            )
            for t in found
        ]

        _audit("find_candidate_transactions", self.session,
               {"criteria": {"amount": amount, "currency": currency,
                             "merchant": merchant, "on_date": on_date},
                "matches": len(found)})

        return ToolResult(ok=True, verified=True,
                          data={"candidates": found, "count": len(found)},
                          evidence=evidence)

    def get_transaction(self, transaction_id: str) -> ToolResult:
        """Una transacción concreta, solo si pertenece al cliente de la sesión.

        GATE-02 en la capa de datos: la consulta filtra por el cliente de la
        sesión, así que una transacción ajena simplemente no existe para esta
        sesión. No se filtra ni siquiera que exista.
        """
        safe = transaction_id.replace("'", "''")
        row = self.con.sql(
            f"""
            SELECT transaction_id, transaction_date, amount, currency, amount_usd,
                   amount_usd_source, merchant_name, transaction_status, product_id
            FROM {self._txn()}
            WHERE transaction_id = '{safe}'
              AND customer_id = '{self.session.customer_id}'
            """
        ).fetchone()

        if row is None:
            _audit("get_transaction", self.session,
                   {"transaction_id": transaction_id, "result": "not_found_or_not_owned"})
            return ToolResult(ok=False, verified=True,
                              error="La transacción no existe entre tus movimientos")

        cols = ["transaction_id", "transaction_date", "amount", "currency",
                "amount_usd", "amount_usd_source", "merchant_name",
                "transaction_status", "product_id"]
        txn = dict(zip(cols, row))
        txn["days_since"] = (TODAY - txn["transaction_date"]).days

        _audit("get_transaction", self.session,
               {"transaction_id": transaction_id, "result": "ok"})

        return ToolResult(
            ok=True, verified=True, data=txn,
            evidence=[Evidence(_new_id("EV"), "gold.txn_lookup",
                               f"{txn['transaction_id']} · {txn['amount']:,.2f} "
                               f"{txn['currency']} · {txn['transaction_status']}",
                               txn)],
        )

    def list_disputes(self, limit: int = 3) -> ToolResult:
        """Reclamos del cliente de la sesión: los históricos del dataset y los
        abiertos en esta sesión. Solo los suyos, como toda lectura."""
        path = GOLD / "dispute_cases.parquet"
        rows: list[dict[str, Any]] = []
        if path.exists():
            found = self.con.sql(
                f"""
                SELECT complaint_id, CAST(created_at AS DATE), status, subcategory
                FROM read_parquet('{path.as_posix()}')
                WHERE customer_id = '{self.session.customer_id}'
                ORDER BY created_at DESC
                LIMIT {int(limit)}
                """
            ).fetchall()
            rows = [{"case_id": r[0], "created_at": r[1], "status": r[2],
                     "subcategory": r[3], "source": "gold.dispute_cases"} for r in found]

        with _STORE_LOCK:
            session_rows = [
                {"case_id": d["case_id"], "created_at": d["created_at"].date(),
                 "status": d["status"], "subcategory": d["reason"], "source": "sesión"}
                for d in self._disputes.values()
                if d["customer_id"] == self.session.customer_id
            ]
        disputes = (session_rows + rows)[:limit]

        evidence = [
            Evidence(_new_id("EV"), d["source"],
                     f"{d['case_id']} · {d['status']} · {d['created_at']}", d)
            for d in disputes
        ]
        _audit("list_disputes", self.session, {"returned": len(disputes)})
        return ToolResult(ok=True, verified=True, data={"disputes": disputes},
                          evidence=evidence)

    def count_recent_unrecognized(self, days: int = 30) -> ToolResult:
        """Disputas abiertas por el cliente en los últimos N días (para ESC-04)."""
        cutoff = TODAY - timedelta(days=days)
        with _STORE_LOCK:
            n = sum(
                1 for d in self._disputes.values()
                if d["customer_id"] == self.session.customer_id
                and d["created_at"].date() >= cutoff
            )
        return ToolResult(ok=True, verified=True, data={"count": n})

    def open_dispute_for(self, transaction_id: str) -> ToolResult:
        """¿Ya hay una disputa abierta sobre esta transacción? (GATE-05)"""
        with _STORE_LOCK:
            existing = next(
                (d for d in self._disputes.values()
                 if d["transaction_id"] == transaction_id
                 and d["customer_id"] == self.session.customer_id
                 and d["status"] == "Open"), None)
        if existing is None:
            return ToolResult(ok=True, verified=True, data={"case_id": None})
        return ToolResult(
            ok=True, verified=True, data={"case_id": existing["case_id"]},
            evidence=[Evidence(_new_id("EV"), "tool:open_dispute_for",
                               f"Disputa abierta {existing['case_id']} sobre {transaction_id}",
                               existing)],
        )

    # --- escritura -----------------------------------------------------

    def create_dispute_case(
        self, transaction_id: str, reason: str, confirmed: bool
    ) -> ToolResult:
        """Abre una disputa. Requiere nivel alto, confirmación y re-lectura."""
        if not self.session.can(AuthLevel.HIGH):
            raise ToolError("ACT-01 requiere una sesión con verificación adicional")
        if not confirmed:
            raise ToolError("ACT-01 requiere confirmación explícita del cliente")

        owned = self.get_transaction(transaction_id)
        if not owned.ok:
            raise ToolError("No se puede disputar una transacción que no es tuya")

        # Consultar, escribir y releer en un solo paso: dos conversaciones (o
        # un agente y un cliente) no pueden abrir la misma disputa a la vez.
        with _STORE_LOCK:
            existing = self.open_dispute_for(transaction_id)
            if existing.data["case_id"]:
                # No es un fallo de verificación: la disputa existe y se comprobó.
                return ToolResult(ok=False, verified=True,
                                  data={"existing_case_id": existing.data["case_id"]},
                                  evidence=existing.evidence,
                                  error=f"Ya existe la disputa {existing.data['case_id']}")

            case_id = _new_id("DSP")
            self._disputes[case_id] = {
                "case_id": case_id,
                "customer_id": self.session.customer_id,
                "transaction_id": transaction_id,
                "reason": reason,
                "status": "Open",
                "created_at": datetime.now(),
                "created_by": self.actor or "customer",
            }

            # RE-LECTURA: no basta con haber escrito.
            written = self._disputes.get(case_id)
            verified = (
                written is not None
                and written["transaction_id"] == transaction_id
                and written["customer_id"] == self.session.customer_id
            )

        _audit("create_dispute_case", self.session,
               {"case_id": case_id, "transaction_id": transaction_id,
                "verified": verified, "actor": self.actor or "customer"})

        if not verified:
            return ToolResult(ok=False, verified=False,
                              error="La disputa no pudo verificarse tras crearla")

        return ToolResult(
            ok=True, verified=True, data=written,
            evidence=[Evidence(_new_id("EV"), "tool:create_dispute_case",
                               f"Disputa {case_id} creada sobre {transaction_id}",
                               written)],
        )

    def block_card(self, product_id: str, confirmed: bool) -> ToolResult:
        """Bloquea una tarjeta. Irreversible en la práctica (ACT-02)."""
        if not self.session.can(AuthLevel.HIGH):
            raise ToolError("ACT-02 requiere una sesión con verificación adicional")
        if not confirmed:
            raise ToolError("ACT-02 requiere confirmación explícita: es irreversible")

        safe = product_id.replace("'", "''")
        owns = self.con.sql(
            f"""
            SELECT count(*) FROM {self._txn()}
            WHERE product_id = '{safe}' AND customer_id = '{self.session.customer_id}'
            """
        ).fetchone()[0]
        if not owns:
            raise ToolError("Ese producto no figura entre los tuyos")

        self._blocked_cards.add(product_id)
        verified = product_id in self._blocked_cards  # re-lectura

        _audit("block_card", self.session,
               {"product_id": product_id, "verified": verified})

        return ToolResult(
            ok=verified, verified=verified,
            data={"product_id": product_id, "status": "Blocked"},
            evidence=[Evidence(_new_id("EV"), "tool:block_card",
                               f"Tarjeta {product_id} bloqueada", {})] if verified else [],
            error=None if verified else "El bloqueo no pudo verificarse",
        )

    def create_handoff_ticket(self, package: dict[str, Any]) -> ToolResult:
        """Deja el caso en la cola humana. Nunca requiere permiso del cliente."""
        ticket_id = _new_id("HO")
        self._handoffs[ticket_id] = {
            "ticket_id": ticket_id,
            "customer_id": self.session.customer_id,
            "created_at": datetime.now(),
            "status": "Queued",
            "package": package,
        }

        written = self._handoffs.get(ticket_id)
        verified = written is not None and written["status"] == "Queued"

        _audit("create_handoff_ticket", self.session,
               {"ticket_id": ticket_id, "verified": verified})

        return ToolResult(
            ok=verified, verified=verified,
            data={"ticket_id": ticket_id, "status": "Queued"},
            evidence=[Evidence(_new_id("EV"), "tool:create_handoff_ticket",
                               f"Ticket {ticket_id} en cola", {})] if verified else [],
            error=None if verified else "El ticket no pudo verificarse",
        )

    # --- introspección para el handoff ---------------------------------

    def queued_handoffs(self) -> list[dict[str, Any]]:
        return list(self._handoffs.values())
