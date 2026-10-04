"""Compara proveedores de LLM sobre los mismos casos (D-14).

La pregunta que responde: ¿el modelo local aguanta, o hay que presentar con
API? Y sobre todo, ¿la brecha está en portugués?

Corre `extraction_bench` para cada proveedor disponible y arma una tabla con
calidad por idioma, latencia y costo estimado.

    uv run python eval/compare_providers.py
    uv run python eval/compare_providers.py --providers ollama anthropic
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS = REPO_ROOT / "eval" / "reports"

# Precio por millón de tokens, para estimar el costo por caso. Se toma de la
# página oficial del proveedor el día de la corrida; se declara como supuesto,
# no como medición.
PRICING = {
    "anthropic": {"input": 1.0, "output": 5.0, "label": "Claude Haiku 4.5"},
    "ollama": {"input": 0.0, "output": 0.0, "label": "local, sin costo por token"},
}

# Tokens típicos de una llamada de extracción, medidos sobre el fixture.
TOKENS_IN = 420
TOKENS_OUT = 90


def cost_per_case(provider: str) -> float:
    price = PRICING.get(provider)
    if not price:
        return 0.0
    return (
        TOKENS_IN / 1_000_000 * price["input"]
        + TOKENS_OUT / 1_000_000 * price["output"]
    )


def run(provider: str, profile: str, limit: int | None) -> dict | None:
    cmd = [
        sys.executable, str(REPO_ROOT / "eval" / "extraction_bench.py"),
        "--provider", provider, "--profile", profile,
    ]
    if limit:
        cmd += ["--limit", str(limit)]

    print(f"\n{'=' * 60}\n{provider}\n{'=' * 60}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"  {provider} falló; se omite de la comparación")
        return None

    path = REPORTS / f"extraction_{provider}_{profile}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", default=None)
    parser.add_argument("--profile", default="eval", choices=["dev", "eval"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    providers = args.providers
    if providers is None:
        providers = ["ollama"]
        if os.getenv("ANTHROPIC_API_KEY"):
            providers.append("anthropic")
        else:
            print(
                "Sin ANTHROPIC_API_KEY: se mide solo el proveedor local.\n"
                "Para comparar, añade la clave al .env y vuelve a correr."
            )

    results = {p: r for p in providers if (r := run(p, args.profile, args.limit))}
    if len(results) < 2:
        print("\nSe necesitan al menos dos proveedores para comparar.")
        return

    fields = ("json", "intent", "language", "amount", "currency", "merchant")
    names = list(results)

    print(f"\n\n{'=' * 72}\nCOMPARACIÓN\n{'=' * 72}\n")
    header = f"{'campo':<12}" + "".join(f"{n:>18}" for n in names)
    print(header)
    print("-" * len(header))

    for field in fields:
        row = f"{field:<12}"
        for name in names:
            stats = results[name]["by_field"][field]
            row += f"{stats['overall']:>10.1%} (pt {stats.get('pt', 0):>4.0%})"
        print(row)

    print()
    for label, key in [
        ("extracción completa", "complete_extraction"),
        ("latencia p50 (s)", "latency_p50"),
    ]:
        row = f"{label:<12}"
        for name in names:
            value = results[name][key]
            row += f"{value:>18.1%}" if key.startswith("complete") else f"{value:>18.1f}"
        print(row)

    row = f"{'costo/caso':<12}"
    for name in names:
        row += f"{'$' + format(cost_per_case(name), '.5f'):>18}"
    print(row)

    print(
        "\nSupuestos de costo: "
        f"{TOKENS_IN} tokens de entrada y {TOKENS_OUT} de salida por llamada, "
        "con el precio de lista del día de la corrida. Es una estimación, "
        "no una medición de gasto."
    )

    # La decisión se toma sobre el portugués, que es donde se espera la brecha.
    print("\nLectura:")
    for field in ("language", "amount", "intent"):
        rates = {n: results[n]["by_field"][field].get("pt", 0) for n in names}
        best = max(rates, key=rates.get)
        spread = max(rates.values()) - min(rates.values())
        verdict = "empate" if spread < 0.05 else f"gana {best} por {spread:.0%}"
        print(f"  {field:<10} en pt: {verdict}")


if __name__ == "__main__":
    main()
