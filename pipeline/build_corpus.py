"""Corpus capa 1: BANKING77 filtrado y mapeado al catálogo de intenciones.

F-01/F-02/F-05 dejaron claro que el dataset no tiene texto de cliente
utilizable. BANKING77 sí: son consultas reales de clientes bancarios, y 27 de
sus 77 intenciones pertenecen al dominio de disputas (D-08b).

Este script descarga el dataset, se queda con el subconjunto relevante, lo mapea
a las 8 clases de `docs/intent_catalog.md` y guarda el resultado con su
procedencia declarada.

La traducción a es/pt NO se hace acá: queda para el día siguiente, cuando haya
API de LLM disponible. Este paso deja el material en inglés, listo y trazable.

    uv run python pipeline/build_corpus.py
    uv run python pipeline/build_corpus.py --report   # solo el resumen
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "corpus"

BASE = (
    "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/"
    "master/banking_data"
)
SPLITS = {"train": f"{BASE}/train.csv", "test": f"{BASE}/test.csv"}

# Mapeo de BANKING77 al catálogo de 8 clases. Las decisiones discutibles están
# argumentadas en docs/intent_catalog.md.
MAPPING: dict[str, str] = {
    # --- no reconoce el cargo ---
    "card_payment_not_recognised": "unrecognized_charge",
    "cash_withdrawal_not_recognised": "unrecognized_charge",
    "direct_debit_payment_not_recognised": "unrecognized_charge",
    "extra_charge_on_statement": "unrecognized_charge",
    # --- cobro duplicado ---
    "transaction_charged_twice": "duplicate_charge",
    # --- el monto no es el esperado ---
    "card_payment_wrong_exchange_rate": "wrong_amount",
    "wrong_exchange_rate_for_cash_withdrawal": "wrong_amount",
    "wrong_amount_of_cash_received": "wrong_amount",
    "cash_withdrawal_charge": "wrong_amount",
    "card_payment_fee_charged": "wrong_amount",
    "transfer_fee_charged": "wrong_amount",
    "exchange_charge": "wrong_amount",
    "top_up_by_card_charge": "wrong_amount",
    "top_up_by_bank_transfer_charge": "wrong_amount",
    # --- pagó y no recibió / quiere devolución ---
    "Refund_not_showing_up": "merchandise_not_received",
    "request_refund": "merchandise_not_received",
    # --- tarjeta perdida o comprometida (siempre escala, ESC-03) ---
    "lost_or_stolen_card": "card_lost_stolen",
    "compromised_card": "card_lost_stolen",
    "lost_or_stolen_phone": "card_lost_stolen",
    # --- pregunta por algo en curso ---
    "pending_card_payment": "dispute_status",
    "pending_cash_withdrawal": "dispute_status",
    "pending_transfer": "dispute_status",
    "pending_top_up": "dispute_status",
    # --- quiere entender por qué falló algo ---
    "declined_card_payment": "policy_question",
    "declined_cash_withdrawal": "policy_question",
    "declined_transfer": "policy_question",
    "cancel_transfer": "policy_question",
}

# Cuántos ejemplos out_of_scope tomar de las intenciones no mapeadas. Se limita
# para que la clase no domine: es una clase más, no un cajón de sastre.
OOS_PER_INTENT = 6


def fetch(url: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(url, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def build(split: str, url: str) -> list[dict]:
    rows = fetch(url)
    in_domain: list[dict] = []
    oos_pool: dict[str, list[str]] = {}

    for row in rows:
        text = (row.get("text") or "").strip()
        source_intent = (row.get("category") or "").strip()
        if not text or not source_intent:
            continue

        target = MAPPING.get(source_intent)
        if target:
            in_domain.append(
                {
                    "text": text,
                    "intent": target,
                    "source_intent": source_intent,
                    "language": "en",
                    "origin": "external-public",
                    "source": "PolyAI/banking77",
                    "split": split,
                }
            )
        else:
            oos_pool.setdefault(source_intent, []).append(text)

    # out_of_scope: unos pocos ejemplos de cada intención no mapeada, para que
    # la clase cubra variedad temática sin desbalancear el corpus.
    out_of_scope = [
        {
            "text": text,
            "intent": "out_of_scope",
            "source_intent": source_intent,
            "language": "en",
            "origin": "external-public",
            "source": "PolyAI/banking77",
            "split": split,
        }
        for source_intent, texts in sorted(oos_pool.items())
        for text in texts[:OOS_PER_INTENT]
    ]

    return in_domain + out_of_scope


def report(records: list[dict]) -> None:
    by_intent = Counter(r["intent"] for r in records)
    by_split = Counter(r["split"] for r in records)
    total = len(records)

    print(f"\n{total:,} ejemplos · " + " · ".join(
        f"{k}={v:,}" for k, v in sorted(by_split.items())
    ))
    print(f"\n{'intención':<26} {'n':>6}   {'%':>6}")
    print("-" * 44)
    for intent, n in by_intent.most_common():
        print(f"{intent:<26} {n:>6,}   {100 * n / total:>5.1f}%")

    smallest = min(by_intent.values())
    largest = max(by_intent.values())
    print(f"\ndesbalance: {largest / smallest:.1f}x entre la mayor y la menor")
    if smallest < 20:
        print(
            f"AVISO: la clase más pequeña tiene {smallest} ejemplos. "
            "SetFit rinde desde ~8-20 por clase, pero conviene reforzarla con "
            "ejemplos propios."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="mostrar el resumen sin escribir archivos")
    args = parser.parse_args()

    records: list[dict] = []
    for split, url in SPLITS.items():
        rows = build(split, url)
        print(f"  {split:5s} {len(rows):>6,} ejemplos relevantes")
        records.extend(rows)

    # El BANKING77 original tiene algún texto repetido entre train y test.
    # Sin quitarlo, cualquier métrica sobre el test estaría inflada: el modelo
    # ya habría visto la frase. Se conserva la copia de train.
    seen_in_train = {r["text"] for r in records if r["split"] == "train"}
    before = len(records)
    records = [
        r for r in records
        if r["split"] == "train" or r["text"] not in seen_in_train
    ]
    leaked = before - len(records)
    if leaked:
        print(f"  leakage  {leaked} texto(s) de test ya estaban en train: eliminados")

    report(records)

    if args.report:
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "banking77_en.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n-> {out.relative_to(REPO_ROOT)}")
    print(
        "\nSiguiente paso: traducir a es/pt (requiere API de LLM) y sumar los "
        "ejemplos escritos por el equipo."
    )


if __name__ == "__main__":
    main()
