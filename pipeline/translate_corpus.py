"""Traduce un subconjunto balanceado de BANKING77 a español y portugués.

translate-train (D-08b): el corpus de entrenamiento son consultas reales de
clientes bancarios en inglés, traducidas. El test NO sale de acá: se escribe a
mano en es/pt (D-11), para que el modelo no se evalúe sobre traducciones
hechas por el mismo traductor que generó su entrenamiento.

Solo se traduce el split `train` de BANKING77.

    uv run python pipeline/translate_corpus.py
    uv run python pipeline/translate_corpus.py --per-class 30
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "data" / "corpus" / "banking77_en.jsonl"
OUT = REPO_ROOT / "data" / "corpus" / "banking77_es_pt.jsonl"

LANGS = {
    "es": "español latinoamericano",
    "pt": "português do Brasil",
}

PROMPT = """Traduce este mensaje de un cliente bancario al {target}.
Conserva el tono informal y cualquier error de redacción del original.
No agregues explicaciones. Responde SOLO un JSON: {{"t": "<traducción>"}}

Mensaje: {text}"""


def translate(text: str, lang: str, cfg: dict) -> str | None:
    body = {
        "model": cfg["model"],
        "prompt": PROMPT.format(target=LANGS[lang], text=text),
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "num_predict": 200},
    }
    req = urllib.request.Request(
        f"{cfg['base_url']}/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
                raw = json.load(resp)["response"]
            out = (json.loads(raw).get("t") or "").strip()
            # Una "traducción" idéntica al inglés es un fallo silencioso.
            if out and out.lower() != text.lower():
                return out
        except Exception:  # noqa: BLE001 - se reintenta y, si no, se omite
            time.sleep(1 + attempt)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-class", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261003)
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    cfg = {
        "base_url": os.getenv("LLM_BASE_URL", "http://localhost:11434"),
        "model": os.getenv("LLM_MODEL", "qwen3:1.7b"),
        "timeout": 60,
    }

    rows = [json.loads(line) for line in SRC.read_text(encoding="utf-8").splitlines()]
    train = [r for r in rows if r["split"] == "train"]

    by_intent: dict[str, list[dict]] = defaultdict(list)
    for row in train:
        by_intent[row["intent"]].append(row)

    rng = random.Random(args.seed)
    picked: list[dict] = []
    for intent, items in sorted(by_intent.items()):
        rng.shuffle(items)
        if intent == "out_of_scope":
            # Variedad temática: uno por intención de origen antes de repetir.
            seen: set[str] = set()
            spread = [r for r in items if not (r["source_intent"] in seen
                                               or seen.add(r["source_intent"]))]
            items = spread + [r for r in items if r not in spread]
        picked.extend(items[: args.per_class])

    total = len(picked) * len(LANGS)
    print(f"{len(picked)} frases × {len(LANGS)} idiomas = {total} traducciones "
          f"con {cfg['model']}", flush=True)

    # Reanudable: si se corta, no se repite lo ya traducido.
    done: set[tuple[str, str]] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            done.add((rec["source_text"], rec["language"]))

    started = time.perf_counter()
    written = failed = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, row in enumerate(picked, start=1):
            for lang in LANGS:
                if (row["text"], lang) in done:
                    continue
                out = translate(row["text"], lang, cfg)
                if out is None:
                    failed += 1
                    continue
                fh.write(json.dumps({
                    "text": out,
                    "intent": row["intent"],
                    "source_intent": row["source_intent"],
                    "language": lang,
                    "source_text": row["text"],
                    "origin": "external-public",
                    "source": "PolyAI/banking77 (train)",
                    "translated_by": cfg["model"],
                    "split": "train",
                }, ensure_ascii=False) + "\n")
                fh.flush()
                written += 1
            if i % 20 == 0:
                rate = (time.perf_counter() - started) / max(written, 1)
                left = (len(picked) - i) * len(LANGS) * rate / 60
                print(f"  {i}/{len(picked)} frases · ~{left:.0f} min restantes",
                      flush=True)

    print(f"\nescritas {written} · fallidas {failed} · "
          f"{(time.perf_counter() - started) / 60:.1f} min -> "
          f"{OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
