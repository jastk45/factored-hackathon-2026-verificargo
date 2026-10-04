"""Fixture de disputas vinculadas a transacciones reales.

F-06 demostró que ninguna de las 8.125 disputas del dataset se corresponde con
una transacción del cliente: los montos se generaron por separado. Sin ese
enlace no se puede ejercitar ni evaluar el paso LOCATE_TXN.

Este script construye el enlace que falta: parte de transacciones **reales** de
`gold/txn_lookup` y genera sobre ellas disputas coherentes — mismo cliente,
mismo monto, fecha dentro del plazo regulatorio. Lo sintético es la disputa; la
transacción disputada existe de verdad.

Cada caso incluye cómo el cliente *expresaría* el reclamo, con el grado de
imprecisión que tiene una persona real: a veces redondea el monto, a veces dice
"el martes" en vez de una fecha, a veces confunde el comercio.

**Dos perfiles con parámetros distintos (D-11, anticircularidad):**

    dev   semilla 20260928, tolerancias estrechas, comercio literal
    eval  semilla 771  · tolerancias amplias, comercio parafraseado

El de evaluación no comparte semilla, ni tolerancias, ni forma de nombrar
comercios con el de desarrollo, para que el matching difuso no se afine contra
la misma distribución que lo evalúa.

    uv run python pipeline/build_fixture.py
    uv run python pipeline/build_fixture.py --profile eval --n 60
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD = REPO_ROOT / "warehouse" / "gold"
OUT_DIR = REPO_ROOT / "fixtures" / "linked_disputes"

# Última fecha con datos. Las disputas se fechan respecto de este "hoy" para
# que los plazos regulatorios sean evaluables.
TODAY = date(2026, 6, 18)

# Plazo de CONDUSEF (México) para reclamar un cargo no reconocido.
CLAIM_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Profile:
    """Parámetros de generación. dev y eval NO comparten ninguno."""

    name: str
    seed: int
    # Con qué probabilidad el cliente redondea el monto, y a cuántas unidades.
    round_amount_prob: float
    round_to: int
    # Con qué probabilidad da una fecha vaga en vez de exacta.
    vague_date_prob: float
    # Cuánto se puede desviar la fecha que recuerda, en días.
    date_drift_days: int
    # Cómo nombra el comercio.
    merchant_style: str  # 'literal' | 'paraphrase' | 'omit'
    # Proporción de casos por resultado esperado.
    outcome_mix: dict[str, float] = field(default_factory=dict)


PROFILES = {
    "dev": Profile(
        name="dev",
        seed=20260928,
        round_amount_prob=0.30,
        round_to=10,
        vague_date_prob=0.20,
        date_drift_days=1,
        merchant_style="literal",
        outcome_mix={"RESOLVE": 0.5, "CLARIFY": 0.3, "ESCALATE": 0.2},
    ),
    "eval": Profile(
        name="eval",
        seed=771,
        round_amount_prob=0.55,
        round_to=50,
        vague_date_prob=0.45,
        date_drift_days=3,
        merchant_style="paraphrase",
        outcome_mix={"RESOLVE": 0.4, "CLARIFY": 0.3, "ESCALATE": 0.3},
    ),
}

# Cómo un cliente se refiere a un comercio cuando no recuerda el nombre exacto.
PARAPHRASE = {
    "es": {
        "Food": ["un restaurante", "el súper", "una cafetería"],
        "Transport": ["un taxi", "la app de viajes", "el transporte"],
        "Entertainment": ["el cine", "una suscripción", "un streaming"],
        "Health": ["la farmacia", "el laboratorio", "una consulta"],
        "Services": ["un servicio", "una suscripción", "el proveedor"],
        "Other": ["una tienda", "un comercio", "no recuerdo cuál"],
    },
    "pt": {
        "Food": ["um restaurante", "o mercado", "uma cafeteria"],
        "Transport": ["um táxi", "o aplicativo de viagens", "o transporte"],
        "Entertainment": ["o cinema", "uma assinatura", "um streaming"],
        "Health": ["a farmácia", "o laboratório", "uma consulta"],
        "Services": ["um serviço", "uma assinatura", "o fornecedor"],
        "Other": ["uma loja", "um comércio", "não lembro qual"],
    },
}

VAGUE_DATES = {
    "es": [
        "hace unos días", "la semana pasada", "el martes pasado",
        "a principios de mes", "hace como dos semanas", "el fin de semana",
    ],
    "pt": [
        "há alguns dias", "na semana passada", "na terça passada",
        "no começo do mês", "há umas duas semanas", "no fim de semana",
    ],
}


def money(value: float, currency: str) -> str:
    """Formatea un monto como lo escribiría una persona."""
    if currency in ("COP", "ARS"):
        return f"{value:,.0f}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def customer_amount(rng: random.Random, amount: float, prof: Profile) -> tuple[float, bool]:
    """Monto tal como lo diría el cliente, y si fue redondeado."""
    if rng.random() < prof.round_amount_prob:
        rounded = round(amount / prof.round_to) * prof.round_to
        return float(max(rounded, prof.round_to)), True
    return float(amount), False


def customer_date(
    rng: random.Random, txn_day: date, prof: Profile, lang: str
) -> tuple[str, bool]:
    """Fecha tal como la diría el cliente, y si es vaga."""
    if rng.random() < prof.vague_date_prob:
        return rng.choice(VAGUE_DATES[lang]), True
    drift = rng.randint(-prof.date_drift_days, prof.date_drift_days)
    return (txn_day + timedelta(days=drift)).isoformat(), False


def customer_merchant(
    rng: random.Random,
    name: str | None,
    category: str | None,
    prof: Profile,
    lang: str,
) -> str | None:
    if prof.merchant_style == "omit" or not name:
        return None
    if prof.merchant_style == "literal":
        return name
    table = PARAPHRASE[lang]
    return rng.choice(table.get(category or "Other", table["Other"]))


def message_es(amount_str: str, currency: str, when: str, merchant: str | None) -> str:
    where = f" en {merchant}" if merchant else ""
    return (
        f"Hola, tengo un cargo de {amount_str} {currency}{where} que no reconozco. "
        f"Fue {when}. ¿Me pueden ayudar?"
    )


def message_pt(amount_str: str, currency: str, when: str, merchant: str | None) -> str:
    # "em" se contrae con el artículo: em + o = no, em + a = na, em + um = num.
    if not merchant:
        where = ""
    elif merchant.startswith("o "):
        where = f" n{merchant}"
    elif merchant.startswith("a "):
        where = f" n{merchant}"
    elif merchant.startswith(("um ", "uma ")):
        where = f" n{merchant}"
    else:
        where = f" em {merchant}"
    return (
        f"Olá, tenho uma cobrança de {amount_str} {currency}{where} que não reconheço. "
        f"Foi {when}. Podem me ajudar?"
    )


# Umbral de escalamiento, en USD y normalizado (F-12: los productos mexicanos
# están en USD, así que comparar en moneda local daría resultados absurdos).
ESCALATE_USD = 1000.0

# A partir de aquí queda tan poco plazo que el caso necesita revisión humana.
NEAR_DEADLINE_DAYS = 80


def derive_outcome(
    amount_usd: float, days_since: int, merchant: str | None, vague_date: bool
) -> tuple[str, str]:
    """El resultado esperado sale de los datos, no de un sorteo.

    Devuelve (resultado, motivo). El motivo permite auditar la etiqueta: si
    alguien discute un caso, la regla que lo clasificó está escrita.
    """
    if days_since > CLAIM_WINDOW_DAYS:
        return "ESCALATE", "fuera del plazo de 90 días"
    if days_since >= NEAR_DEADLINE_DAYS:
        return "ESCALATE", f"a {CLAIM_WINDOW_DAYS - days_since} días de vencer el plazo"
    if amount_usd >= ESCALATE_USD:
        return "ESCALATE", f"monto {amount_usd:,.0f} USD sobre el umbral"
    if merchant is None and vague_date:
        return "CLARIFY", "sin comercio ni fecha precisa: no identifica la transacción"
    if merchant is None:
        return "CLARIFY", "sin comercio: puede haber varias transacciones del monto"
    return "RESOLVE", "monto, fecha y comercio identifican la transacción"


def build(profile: Profile, n: int) -> list[dict]:
    con = duckdb.connect()
    txn = f"read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')"
    cust = f"read_parquet('{(GOLD / 'customer_360_min.parquet').as_posix()}')"

    cutoff = (TODAY - timedelta(days=CLAIM_WINDOW_DAYS)).isoformat()

    # Solo compras aprobadas dentro del plazo: es lo que se puede disputar.
    # El orden es determinista y depende de la semilla del perfil, así que dev
    # y eval toman transacciones distintas y cada corrida es reproducible.
    # (No se usa USING SAMPLE: aplica el muestreo antes del filtro.)
    rows = con.sql(
        f"""
        SELECT t.transaction_id, t.customer_id, t.product_id,
               t.transaction_date, t.amount, t.currency, t.amount_usd,
               t.merchant_name, t.merchant_category, t.channel,
               t.transaction_city, c.country, c.segment, c.first_name
        FROM {txn} t
        JOIN {cust} c USING (customer_id)
        WHERE t.transaction_type = 'Purchase'
          AND t.transaction_status = 'Approved'
          AND t.transaction_date >= DATE '{cutoff}'
          AND t.amount_usd IS NOT NULL
          AND t.merchant_name IS NOT NULL
        ORDER BY hash(t.transaction_id || '{profile.seed}')
        LIMIT {n * 3}
        """
    ).fetchall()

    cols = [
        "transaction_id", "customer_id", "product_id", "transaction_date",
        "amount", "currency", "amount_usd", "merchant_name",
        "merchant_category", "channel", "transaction_city", "country",
        "segment", "first_name",
    ]
    records = [dict(zip(cols, r)) for r in rows]

    rng = random.Random(profile.seed)
    rng.shuffle(records)

    cases: list[dict] = []
    for i, txn_row in enumerate(records):
        if len(cases) >= n:
            break

        lang = "pt" if i % 4 == 0 else "es"

        said_amount, was_rounded = customer_amount(rng, txn_row["amount"], profile)
        said_when, vague = customer_date(
            rng, txn_row["transaction_date"], profile, lang
        )
        said_merchant = customer_merchant(
            rng, txn_row["merchant_name"], txn_row["merchant_category"],
            profile, lang,
        )

        # Una parte de los casos omite el comercio a propósito, para que el
        # sistema tenga que pedir aclaración.
        if rng.random() < profile.outcome_mix.get("CLARIFY", 0.3):
            said_merchant = None

        days_since = (TODAY - txn_row["transaction_date"]).days
        outcome, outcome_reason = derive_outcome(
            float(txn_row["amount_usd"]), days_since, said_merchant, vague
        )

        amount_str = money(said_amount, txn_row["currency"])
        text = (message_pt if lang == "pt" else message_es)(
            amount_str, txn_row["currency"], said_when, said_merchant
        )

        case = {
            "case_id": f"FIX-{profile.name.upper()}-{len(cases) + 1:04d}",
            "profile": profile.name,
            "origin": "team-generated",
            "language": lang,
            "customer_id": txn_row["customer_id"],
            "country": txn_row["country"],
            "segment": txn_row["segment"],
            "customer_message": text,
            # Lo que el cliente dijo, con su imprecisión.
            "stated": {
                "amount": said_amount,
                "currency": txn_row["currency"],
                "when": said_when,
                "merchant": said_merchant,
                "amount_was_rounded": was_rounded,
                "date_is_vague": vague,
            },
            # La verdad verificable: una transacción que existe de verdad.
            "ground_truth": {
                "transaction_id": txn_row["transaction_id"],
                "product_id": txn_row["product_id"],
                "transaction_date": txn_row["transaction_date"].isoformat(),
                "amount": float(txn_row["amount"]),
                "amount_usd": float(txn_row["amount_usd"]),
                "currency": txn_row["currency"],
                "merchant_name": txn_row["merchant_name"],
                "merchant_category": txn_row["merchant_category"],
                "channel": txn_row["channel"],
                "days_since_transaction": days_since,
            },
            "expected_outcome": outcome,
            "expected_outcome_reason": outcome_reason,
        }
        cases.append(case)

    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=list(PROFILES), default=None)
    parser.add_argument("--n", type=int, default=80)
    args = parser.parse_args()

    if not (GOLD / "txn_lookup.parquet").exists():
        raise SystemExit("Falta gold/txn_lookup.parquet. Correr pipeline/gold.py")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [args.profile] if args.profile else list(PROFILES)

    for name in targets:
        profile = PROFILES[name]
        cases = build(profile, args.n)
        out = OUT_DIR / f"{name}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for case in cases:
                fh.write(json.dumps(case, ensure_ascii=False) + "\n")

        by_outcome: dict[str, int] = {}
        by_lang: dict[str, int] = {}
        for c in cases:
            by_outcome[c["expected_outcome"]] = by_outcome.get(c["expected_outcome"], 0) + 1
            by_lang[c["language"]] = by_lang.get(c["language"], 0) + 1

        print(f"{name:5s} · {len(cases):>3} casos · semilla {profile.seed}")
        print(f"        resultado: " + " · ".join(f"{k}={v}" for k, v in sorted(by_outcome.items())))
        print(f"        idioma   : " + " · ".join(f"{k}={v}" for k, v in sorted(by_lang.items())))
        print(f"        -> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
