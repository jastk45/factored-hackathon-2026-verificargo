"""Concurrencia: la API atiende requests en paralelo (threadpool de FastAPI).

El 4 de octubre, con 32 requests simultáneos, 183 de 200 fallaron y 10
devolvieron un país equivocado: todos los hilos compartían una conexión de
DuckDB y uno leía el resultado de la consulta de otro. Con datos de clientes,
eso no es un problema de estabilidad sino de aislamiento.

    uv run pytest tests/test_concurrency.py -v
"""

from __future__ import annotations

import concurrent.futures as cf
import sys
import threading
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

from session import AuthLevel, issue_token, verify_token  # noqa: E402
from tools import GOLD, Toolbox, customer_country  # noqa: E402

pytestmark = pytest.mark.skipif(not (GOLD / "txn_lookup.parquet").exists(),
                                reason="capa gold no construida")

WORKERS, CALLS = 32, 160


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    import handoff_queue
    monkeypatch.setattr(handoff_queue, "QUEUE", tmp_path / "queue.jsonl")
    monkeypatch.setattr(handoff_queue, "DEAD_LETTER", tmp_path / "dead.jsonl")
    import api as module
    monkeypatch.setattr(module, "DISPUTES", {})
    return module


def test_parallel_requests_never_fail_or_mix_customers(api) -> None:
    con = duckdb.connect()
    scenarios = api.scenarios()
    expected_country = {s["customer"]: customer_country(s["customer"], con) for s in scenarios}

    def one(i: int):
        sc = scenarios[i % len(scenarios)]
        conv = api.new_conversation(api.NewConversation(scenario_id=sc["id"]))
        txns = api.recent_transactions(conv["conversation_id"])
        return sc["customer"], conv["session"]["country"], [t["transaction_id"] for t in txns]

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, range(CALLS)))

    owner = dict(con.sql(f"""SELECT transaction_id, customer_id
        FROM read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')""").fetchall())
    for customer, country, txn_ids in results:
        assert country == expected_country[customer]
        assert txn_ids, "sin movimientos"
        # Ningún hilo recibe movimientos de otro cliente.
        assert {owner[t] for t in txn_ids} == {customer}


def test_one_dispute_per_transaction_under_concurrent_writes() -> None:
    con = duckdb.connect()
    customer, txn = con.sql(f"""SELECT customer_id, transaction_id
        FROM read_parquet('{(GOLD / 'txn_lookup.parquet').as_posix()}')
        WHERE transaction_status = 'Approved' LIMIT 1""").fetchone()
    store: dict = {}
    session = verify_token(issue_token(customer, "MX", "es", AuthLevel.HIGH))
    start = threading.Barrier(16)

    def write(_):
        box = Toolbox(session, con.cursor(), disputes=store)
        start.wait()
        res = box.create_dispute_case(txn, "unrecognized_charge", confirmed=True)
        box.list_disputes()                      # leer mientras otros escriben
        return res.data.get("case_id"), res.data.get("existing_case_id")

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(write, range(16)))
    created = [c for c, _ in results if c]
    assert len(created) == 1 and len(store) == 1
    assert all(existing == created[0] for c, existing in results if not c)


def test_concurrent_queue_writes_lose_nothing(monkeypatch, tmp_path) -> None:
    import handoff_queue
    monkeypatch.setattr(handoff_queue, "QUEUE", tmp_path / "queue.jsonl")
    rows = [{"status": "pending", "queued_at": "x", "package": {
        "handoff_id": f"HO-{i:08d}", "priority": "normal", "sla": {}}} for i in range(40)]
    handoff_queue.QUEUE.write_text(
        "\n".join(__import__("json").dumps(r) for r in rows) + "\n", encoding="utf-8")

    def decide(i):
        return handoff_queue.update(f"HO-{i:08d}", "rejected" if i % 2 else "info_requested",
                                    "agente", "nota")["status"]

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(decide, range(40)))
    final = {r["package"]["handoff_id"]: r["status"] for r in handoff_queue._load()}
    assert len(final) == 40
    assert all(final[f"HO-{i:08d}"] == ("rejected" if i % 2 else "info_requested")
               for i in range(40)), "una actualización pisó a otra"
