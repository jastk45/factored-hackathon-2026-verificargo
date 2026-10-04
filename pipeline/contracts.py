"""Contratos de datos para la capa silver.

Un contrato declara qué debe cumplir una fila para que el agente pueda confiar
en ella. Las filas que no lo cumplen no se corrigen en silencio: van a
cuarentena con el motivo, y el conteo se publica como métrica de calidad.

Cada regla nace de un hallazgo del EDA (ver docs/EDA_FINDINGS.md), no de una
suposición sobre cómo "deberían" ser los datos.

Solo se versionan las 4 tablas que el agente consulta (D-12).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONTRACT_VERSION = "1.0.0"

# Valores observados en el dataset al 28 sep 2026. Un valor nuevo no rompe el
# pipeline: manda la fila a cuarentena y obliga a una nueva versión del
# contrato, que es una decisión humana y no un parche automático.
COUNTRIES = ("México", "Colombia", "Argentina")
CURRENCIES = ("MXN", "COP", "ARS", "USD")
SEGMENTS = ("Premium", "Plus", "Basic", "Student")
CUSTOMER_STATUS = ("Active", "Inactive", "Suspended", "Closed")
PRODUCT_STATUS = ("Active", "Blocked", "Closed", "Suspended")
TXN_STATUS = ("Approved", "Declined", "Pending", "Reversed")
COMPLAINT_STATUS = (
    "Open", "In Process", "Escalated", "Resolved", "Closed", "Rejected",
)
PRIORITIES = ("Low", "Medium", "High", "Critical")


@dataclass(frozen=True)
class Rule:
    """Una condición SQL que una fila válida debe cumplir.

    `expr` se evalúa en DuckDB. Una fila que da FALSE va a cuarentena; una que
    da NULL se considera válida, porque un valor ausente en un campo opcional no
    es una violación (ver `nullable_ok`).
    """

    id: str
    expr: str
    why: str
    severity: str = "error"  # error -> cuarentena · warn -> solo se cuenta


@dataclass(frozen=True)
class Contract:
    table: str
    primary_key: str
    rules: list[Rule] = field(default_factory=list)

    def quarantine_predicate(self) -> str:
        """SQL que es TRUE para una fila que viola alguna regla de error."""
        errors = [r for r in self.rules if r.severity == "error"]
        if not errors:
            return "FALSE"
        return " OR ".join(f"NOT coalesce({r.expr}, TRUE)" for r in errors)

    def reason_expression(self) -> str:
        """SQL que devuelve el ID de la primera regla violada."""
        errors = [r for r in self.rules if r.severity == "error"]
        if not errors:
            return "NULL"
        cases = " ".join(
            f"WHEN NOT coalesce({r.expr}, TRUE) THEN '{r.id}'" for r in errors
        )
        return f"CASE {cases} ELSE NULL END"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


CUSTOMERS = Contract(
    table="customers",
    primary_key="customer_id",
    rules=[
        Rule("CUS-01", "customer_id IS NOT NULL", "PK obligatoria"),
        Rule("CUS-02", _in_list("country", COUNTRIES),
             "País fuera del catálogo observado"),
        Rule("CUS-03", _in_list("segment", SEGMENTS),
             "Segmento fuera del catálogo observado"),
        Rule("CUS-04", _in_list("customer_status", CUSTOMER_STATUS),
             "Estado fuera del catálogo observado"),
        Rule("CUS-05", "credit_score BETWEEN 300 AND 850",
             "El diccionario define el score en 300-850"),
        Rule("CUS-06", "date_of_birth < current_date",
             "Fecha de nacimiento futura"),
        # 30% de detected_accent es nulo: es un hecho del dataset, no un error.
        Rule("CUS-07", "detected_accent IS NOT NULL",
             "Acento ausente (30% del dataset) - solo se cuenta",
             severity="warn"),
    ],
)

PRODUCTS = Contract(
    table="products",
    primary_key="product_id",
    rules=[
        Rule("PRD-01", "product_id IS NOT NULL", "PK obligatoria"),
        Rule("PRD-02", "customer_id IS NOT NULL", "Todo producto tiene dueño"),
        Rule("PRD-03", _in_list("currency", CURRENCIES),
             "Moneda fuera del catálogo"),
        Rule("PRD-04", _in_list("product_status", PRODUCT_STATUS),
             "Estado fuera del catálogo observado"),
        Rule("PRD-05", "credit_limit IS NULL OR credit_limit >= 0",
             "Un límite de crédito negativo no tiene sentido"),
        Rule("PRD-06", "expiration_date IS NULL OR expiration_date > opening_date",
             "La expiración no puede preceder a la apertura"),
    ],
)

TRANSACTIONS = Contract(
    table="transactions",
    primary_key="transaction_id",
    rules=[
        Rule("TXN-01", "transaction_id IS NOT NULL", "PK obligatoria"),
        Rule("TXN-02", "customer_id IS NOT NULL",
             "Sin dueño no se puede autorizar el acceso"),
        Rule("TXN-03", "amount > 0",
             "El signo va en transaction_type, no en el monto"),
        Rule("TXN-04", _in_list("currency", CURRENCIES),
             "Moneda fuera del catálogo"),
        Rule("TXN-05", _in_list("transaction_status", TXN_STATUS),
             "Estado fuera del catálogo observado"),
        Rule("TXN-06", "transaction_date IS NOT NULL", "Fecha obligatoria"),
        # F-04: process_date puede ser anterior a transaction_date. Es la
        # logical date del orquestador, no un error de los datos.
        Rule("TXN-07", "process_date >= (transaction_date - INTERVAL 2 DAY)",
             "Desfase proceso/transacción mayor al esperado", severity="warn"),
        # 57% de amount_usd es nulo: silver lo recalcula con las tasas diarias.
        Rule("TXN-08", "amount_usd IS NOT NULL",
             "Monto en USD ausente (57%) - se recalcula en silver",
             severity="warn"),
    ],
)

COMPLAINTS = Contract(
    table="complaints",
    primary_key="complaint_id",
    rules=[
        Rule("CMP-01", "complaint_id IS NOT NULL", "PK obligatoria"),
        Rule("CMP-02", "customer_id IS NOT NULL", "Sin dueño no hay caso"),
        Rule("CMP-03", "creation_date IS NOT NULL",
             "La fecha de creación fija el plazo regulatorio"),
        Rule("CMP-04", _in_list("status", COMPLAINT_STATUS),
             "Estado fuera del catálogo observado"),
        Rule("CMP-05", _in_list("priority", PRIORITIES),
             "Prioridad fuera del catálogo observado"),
        Rule("CMP-06", "claimed_amount IS NULL OR claimed_amount > 0",
             "Un monto reclamado no positivo no tiene sentido"),
        Rule("CMP-07", "claimed_amount IS NULL OR currency IS NOT NULL",
             "Un monto sin moneda no es interpretable"),
        Rule("CMP-08", "resolution_date IS NULL OR resolution_date >= creation_date",
             "No se puede resolver antes de crear"),
    ],
)

CONTRACTS: dict[str, Contract] = {
    c.table: c for c in (CUSTOMERS, PRODUCTS, TRANSACTIONS, COMPLAINTS)
}
