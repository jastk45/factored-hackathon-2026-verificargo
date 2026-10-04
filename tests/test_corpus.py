"""Verifica el corpus de entrenamiento.

El corpus es el insumo del componente aprendido. Estos tests fijan lo que el
reto exige: procedencia declarada, sin leakage entre splits y cobertura de las
8 clases del catálogo.

    uv run pytest tests/test_corpus.py -v
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "corpus" / "banking77_en.jsonl"

CATALOG = {
    "unrecognized_charge", "duplicate_charge", "wrong_amount",
    "merchandise_not_received", "card_lost_stolen", "dispute_status",
    "policy_question", "out_of_scope",
}

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(),
    reason="corpus no construido; correr pipeline/build_corpus.py",
)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()]


def test_every_intent_is_in_the_catalog(rows) -> None:
    """Nada se cuela fuera de las 8 clases congeladas."""
    found = {r["intent"] for r in rows}
    assert found <= CATALOG, f"intenciones desconocidas: {found - CATALOG}"


def test_all_eight_classes_are_present(rows) -> None:
    found = {r["intent"] for r in rows}
    assert found == CATALOG, f"clases sin ejemplos: {CATALOG - found}"


def test_every_row_declares_its_origin(rows) -> None:
    """El reto pide distinguir lo real de lo generado por el equipo."""
    for r in rows:
        assert r["origin"] == "external-public"
        assert r["source"] == "PolyAI/banking77"


def test_no_text_leaks_between_splits(rows) -> None:
    """Un texto en train y test infla cualquier métrica."""
    train = {r["text"] for r in rows if r["split"] == "train"}
    test = {r["text"] for r in rows if r["split"] == "test"}
    overlap = train & test
    assert not overlap, f"{len(overlap)} textos compartidos entre splits"


def test_no_text_has_two_different_labels(rows) -> None:
    """El mismo texto con dos etiquetas haría imposible aprender la frontera."""
    labels: dict[str, set[str]] = {}
    for r in rows:
        labels.setdefault(r["text"], set()).add(r["intent"])
    conflicts = {t: ls for t, ls in labels.items() if len(ls) > 1}
    assert not conflicts, f"{len(conflicts)} textos con etiqueta ambigua"


def test_each_class_has_enough_examples(rows) -> None:
    """SetFit rinde desde ~8-20 por clase; exigimos holgura."""
    counts = Counter(r["intent"] for r in rows)
    for intent, n in counts.items():
        assert n >= 50, f"{intent} solo tiene {n} ejemplos"


def test_class_imbalance_is_bounded(rows) -> None:
    """Un desbalance extremo haría que la clase mayoritaria domine."""
    counts = Counter(r["intent"] for r in rows)
    ratio = max(counts.values()) / min(counts.values())
    assert ratio < 15, f"desbalance de {ratio:.1f}x"


def test_out_of_scope_comes_from_many_topics(rows) -> None:
    """out_of_scope es una clase real, no un solo tema repetido."""
    topics = {
        r["source_intent"] for r in rows if r["intent"] == "out_of_scope"
    }
    assert len(topics) >= 20, f"solo {len(topics)} temas en out_of_scope"


def test_texts_are_not_empty(rows) -> None:
    for r in rows:
        assert r["text"].strip(), "texto vacío en el corpus"
