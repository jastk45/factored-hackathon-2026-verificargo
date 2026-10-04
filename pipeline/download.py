"""Descarga reproducible del dataset LATAM Bank desde S3.

Idempotente: omite los archivos ya descargados cuyo tamano coincide con el
objeto remoto, asi que si la descarga se corta basta con relanzarlo.

Deja dos artefactos:
  - data/                        los archivos crudos, con la jerarquia de S3
  - docs/DATA_INVENTORY.md       inventario por tabla (archivos, bytes)
  - data/_manifest.json          key, size y etag de cada objeto (linaje)

Uso:
    uv run python pipeline/download.py            # descarga todo
    uv run python pipeline/download.py --dry-run  # solo lista, no baja nada
    uv run python pipeline/download.py --prefix data/complaints/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DOCS_DIR = REPO_ROOT / "docs"
MANIFEST_PATH = DATA_DIR / "_manifest.json"
INVENTORY_PATH = DOCS_DIR / "DATA_INVENTORY.md"

MAX_WORKERS = 8


def human(n: int) -> str:
    """Bytes a una unidad legible."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def table_of(key: str, prefix: str) -> str:
    """Nombre de tabla a partir de la key de S3.

    Las tablas grandes vienen particionadas por fecha, asi que la tabla es el
    primer segmento despues del prefijo:
        data/transactions/year=2026/month=06/part-0.parquet -> transactions
    """
    rest = key[len(prefix):] if key.startswith(prefix) else key
    parts = [p for p in rest.split("/") if p]
    return parts[0] if len(parts) > 1 else "(root)"


def build_client():
    load_dotenv(REPO_ROOT / ".env")
    missing = [
        var
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
        if not os.getenv(var)
    ]
    if missing:
        sys.exit(
            f"Faltan variables en .env: {', '.join(missing)}\n"
            "Copia .env.example a .env y rellena las credenciales."
        )
    # Reintentos en modo adaptive: boto3 maneja backoff y throttling solo.
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-2"),
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def list_objects(s3, bucket: str, prefix: str) -> list[dict]:
    objects: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            # Las "carpetas" de S3 son keys de tamano 0 terminadas en /
            if obj["Key"].endswith("/"):
                continue
            objects.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "etag": obj["ETag"].strip('"'),
                }
            )
    return objects


def local_path(key: str) -> Path:
    """Ruta local de una key de S3.

    Se quita el prefijo 'data/' del bucket para no terminar con data/data/...
    """
    rel = key[len("data/"):] if key.startswith("data/") else key
    return DATA_DIR / rel


def download_one(s3, bucket: str, obj: dict) -> tuple[str, bool]:
    """Devuelve (key, descargado). descargado=False si ya estaba."""
    dest = local_path(obj["key"])
    if dest.exists() and dest.stat().st_size == obj["size"]:
        return obj["key"], False
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Descarga a un temporal y renombra, para que una corrida interrumpida
    # no deje un archivo truncado que la siguiente daria por bueno.
    tmp = dest.with_suffix(dest.suffix + ".part")
    s3.download_file(bucket, obj["key"], str(tmp))
    tmp.replace(dest)
    return obj["key"], True


def write_inventory(objects: list[dict], prefix: str) -> None:
    by_table: dict[str, list[dict]] = defaultdict(list)
    for obj in objects:
        by_table[table_of(obj["key"], prefix)].append(obj)

    total_files = len(objects)
    total_bytes = sum(o["size"] for o in objects)

    lines = [
        "# Inventario del dataset",
        "",
        "Generado por `pipeline/download.py`. No editar a mano.",
        "",
        f"- **Tablas:** {len(by_table)}",
        f"- **Archivos:** {total_files:,}",
        f"- **Tamano total:** {human(total_bytes)}",
        "",
        "| Tabla | Archivos | Tamano |",
        "|---|---:|---:|",
    ]
    for table in sorted(by_table):
        items = by_table[table]
        lines.append(
            f"| {table} | {len(items):,} | {human(sum(i['size'] for i in items))} |"
        )
    lines.append("")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="listar sin descargar")
    parser.add_argument("--prefix", default=None, help="prefijo S3 (default: .env)")
    args = parser.parse_args()

    s3 = build_client()
    bucket = os.getenv("S3_BUCKET", "")
    prefix = args.prefix or os.getenv("S3_PREFIX", "data/")
    if not bucket:
        sys.exit("Falta S3_BUCKET en .env")

    print(f"Listando s3://{bucket}/{prefix} ...")
    objects = list_objects(s3, bucket, prefix)
    if not objects:
        sys.exit("No se encontro ningun objeto. Revisa bucket, prefijo y credenciales.")

    total_bytes = sum(o["size"] for o in objects)
    print(f"{len(objects):,} archivos · {human(total_bytes)}")

    write_inventory(objects, prefix)
    print(f"Inventario -> {INVENTORY_PATH.relative_to(REPO_ROOT)}")

    if args.dry_run:
        print("\n--dry-run: no se descargo nada.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(download_one, s3, bucket, obj): obj for obj in objects
        }
        for i, future in enumerate(as_completed(futures), start=1):
            obj = futures[future]
            try:
                _, was_downloaded = future.result()
                if was_downloaded:
                    downloaded += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001 - se reporta y se sigue
                failed += 1
                print(f"  ERROR {obj['key']}: {exc}")
            if i % 50 == 0 or i == len(objects):
                print(f"  {i:,}/{len(objects):,}", flush=True)

    MANIFEST_PATH.write_text(
        json.dumps({"bucket": bucket, "prefix": prefix, "objects": objects}, indent=2),
        encoding="utf-8",
    )

    print(
        f"\nDescargados {downloaded:,} · ya presentes {skipped:,} · fallidos {failed:,}"
    )
    print(f"Manifiesto -> {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    if failed:
        print("\nRelanza el script: omite lo ya descargado y reintenta lo que fallo.")
        sys.exit(1)


if __name__ == "__main__":
    main()
