"""Junta las corridas repetidas de un eval set: tasa combinada y variación.

El modelo no es determinista entre corridas (temperatura 0, pero no garantiza
la misma salida), así que una sola corrida no dice cuánto puede moverse una
cifra. Para cada métrica se informa:

  - la tasa combinada: suma de numeradores / suma de denominadores
  - el rango por corrida: el mínimo y el máximo del numerador

y los cortes por idioma, país y segmento (combinados), y la latencia por turno
y por conversación completa (media de las corridas).

    uv run python eval/aggregate.py --cases v3 --tag v6
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / "reports"
FRACTION = re.compile(r"\((\d+)/(\d+)\)")

METRICS = [
    "unsafe_outcomes", "false_statements", "missed_escalations", "escalation_recall",
    "safe_automated_resolution", "safe_automated_resolution_in_scope", "automation_attempted",
    "wrong_resolutions", "unnecessary_escalations", "outcome_acceptable", "faults_activated",
]
LATENCY = ["latency_turn_p50_ms", "latency_turn_p95_ms", "latency_case_p50_ms",
           "latency_case_p95_ms", "avg_turns"]
CUTS = ["by_language", "by_country", "by_segment"]
CUT_METRICS = ["unsafe_outcomes", "safe_automated_resolution", "escalation_recall",
               "outcome_acceptable"]


def fraction(value) -> tuple[int, int] | None:
    match = FRACTION.search(str(value))
    return (int(match.group(1)), int(match.group(2))) if match else None


def combine(values: list) -> dict:
    parts = [f for f in (fraction(v) for v in values) if f]
    if not parts:
        return {"rate": None, "num": None, "den": None, "range": None}
    num, den = sum(p[0] for p in parts), sum(p[1] for p in parts)
    nums = [p[0] for p in parts]
    return {"rate": num / den if den else None, "num": num, "den": den,
            "per_run_den": parts[0][1], "range": [min(nums), max(nums)], "runs": len(parts)}


def aggregate(cards: list[dict]) -> dict:
    out = {"runs": len(cards), "n_cases": cards[0]["n_cases"],
           "metrics": {m: combine([c.get(m) for c in cards]) for m in METRICS},
           "latency": {m: statistics.mean(c[m] for c in cards) for m in LATENCY}}
    for cut in CUTS:
        if not all(cut in c for c in cards):
            continue
        groups = sorted(set().union(*(c[cut].keys() for c in cards)))
        out[cut] = {g: {"n": cards[0][cut].get(g, {}).get("n"),
                        **{m: combine([c[cut].get(g, {}).get(m) for c in cards])
                           for m in CUT_METRICS}}
                    for g in groups}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="v3")
    parser.add_argument("--tag", default="v6")
    args = parser.parse_args()

    summary = {}
    for system in ("baseline", "proposed"):
        files = sorted(REPORTS.glob(f"system_{system}_{args.cases}_{args.tag}-r*.json"))
        cards = [json.loads(f.read_text(encoding="utf-8"))["scorecard"] for f in files]
        if cards:
            summary[system] = {"files": [f.name for f in files], **aggregate(cards)}
    out = REPORTS / f"summary_{args.cases}_{args.tag}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for system, data in summary.items():
        print(f"\n{system} · {data['runs']} corridas · {data['n_cases']} casos")
        for name, m in data["metrics"].items():
            if m["rate"] is not None:
                print(f"  {name:<36} {100 * m['rate']:5.1f}%  ({m['num']}/{m['den']}; "
                      f"por corrida {m['range'][0]}–{m['range'][1]} de {m['per_run_den']})")
    print(f"\n-> {out.relative_to(REPORTS.parent.parent)}")


if __name__ == "__main__":
    main()
