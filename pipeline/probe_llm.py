"""Identifica que tipo de API de LLM hay detras de LLM_BASE_URL.

Prueba los formatos habituales y reporta cual responde, que modelos ofrece y
cuanto tarda. No imprime la API key.

Uso:
    uv run python pipeline/probe_llm.py
    uv run python pipeline/probe_llm.py --url https://host/v1 --key-env MI_VAR
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 20


def call(url: str, headers: dict[str, str], body: dict | None = None) -> tuple[int, str]:
    """Devuelve (status, cuerpo). status 0 si ni siquiera se pudo conectar."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de red es informativo
        return 0, str(exc)


def show(title: str, status: int, body: str, limit: int = 300) -> None:
    mark = "OK " if 200 <= status < 300 else "-- "
    print(f"  {mark}{title}: HTTP {status or 'sin conexion'}")
    if body:
        snippet = body[:limit].replace("\n", " ")
        print(f"      {snippet}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="base URL (default: LLM_BASE_URL del .env)")
    parser.add_argument("--key-env", default="LLM_API_KEY", help="variable de entorno con la key")
    parser.add_argument("--model", default=None, help="modelo a probar")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    base = (args.url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
    key = os.getenv(args.key_env, "")
    model = args.model or os.getenv("LLM_MODEL") or ""

    if not base:
        raise SystemExit(
            "Falta la URL. Rellena LLM_BASE_URL en .env o pasa --url.\n"
            "Ejemplos: https://host/v1 · http://localhost:11434/v1"
        )

    print(f"Endpoint : {base}")
    print(f"API key  : {'presente (' + str(len(key)) + ' chars)' if key else 'AUSENTE'}")
    print(f"Modelo   : {model or '(sin definir)'}\n")

    # 1) Formato OpenAI: GET /models
    print("[1] Compatible con OpenAI")
    status, body = call(f"{base}/models", {"Authorization": f"Bearer {key}"})
    show("GET /models", status, body)
    if 200 <= status < 300:
        try:
            ids = [m.get("id") for m in json.loads(body).get("data", [])]
            if ids:
                print(f"      modelos: {', '.join(str(i) for i in ids[:10])}")
                if not model:
                    model = str(ids[0])
                    print(f"      usando '{model}' para la prueba de chat")
        except (json.JSONDecodeError, AttributeError):
            pass

    # 2) Chat en formato OpenAI
    if model:
        print("\n[2] POST /chat/completions")
        t0 = time.perf_counter()
        status, body = call(
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": model,
                "messages": [{"role": "user", "content": "Responde solo: ok"}],
                "max_tokens": 10,
            },
        )
        show(f"chat ({time.perf_counter() - t0:.1f}s)", status, body)

    # 3) Formato Anthropic
    print("\n[3] Formato Anthropic")
    status, body = call(
        f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages",
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        {
            "model": model or "claude-haiku-4-5-20251001",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Responde solo: ok"}],
        },
    )
    show("POST /messages", status, body)

    print(
        "\nInterpretacion:\n"
        "  [1] o [2] OK  -> compatible con OpenAI   (LLM_PROVIDER=openai_compatible)\n"
        "  [3] OK        -> Anthropic               (LLM_PROVIDER=anthropic)\n"
        "  401 / 403     -> la key no es valida para ese endpoint\n"
        "  404           -> la ruta base es otra (probar con o sin /v1)\n"
        "  sin conexion  -> host inalcanzable: VPN, firewall o URL incorrecta"
    )


if __name__ == "__main__":
    main()
