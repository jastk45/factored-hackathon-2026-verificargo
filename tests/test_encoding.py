"""El texto que sale del sistema tiene que poder escribirse donde vaya.

Un mensaje de política con un simbolo fuera de Latin-1 hizo reventar la consola
de Windows (cp1252) durante el desarrollo. En produccion eso rompe el log de
auditoria, que es justamente el artefacto que el reto exige conservar.

Dos reglas:
  1. Los mensajes del sistema usan acentos (son en espanol) pero no simbolos
     tipograficos como ≥, —, ✓, →. Esos no caben en cp1252.
  2. Todo archivo se abre con encoding explicito.

    uv run pytest tests/test_encoding.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP = REPO_ROOT / "app"
sys.path.insert(0, str(APP))

# Caracteres que NO caben en cp1252 y revientan una consola Windows.
# Los acentos del espanol (á é í ó ú ñ ü) sí caben: no se prohiben.
FORBIDDEN = "≥≤≠→←↔⇒·✓✗—–…▪●⚠"

# Llamadas cuyo texto acaba en un log, una excepcion o la consola.
LOG_BOUND = re.compile(
    r"(reasons\.append|raise \w+Error|error=|ToolError|SessionError|"
    r"ValueError|_audit|print)\("
)


def app_sources() -> list[Path]:
    return sorted(APP.glob("*.py"))


@pytest.mark.parametrize("path", app_sources(), ids=lambda p: p.name)
def test_log_bound_strings_survive_cp1252(path: Path) -> None:
    """Ningun mensaje que vaya a un log lleva simbolos fuera de Latin-1."""
    offenders: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("#") or not LOG_BOUND.search(line):
            continue
        found = sorted({c for c in line if c in FORBIDDEN})
        if found:
            offenders.append(f"{path.name}:{number} {found} -> {line.strip()[:70]}")

    assert not offenders, (
        "Simbolos que no caben en cp1252 en texto destinado a logs:\n  "
        + "\n  ".join(offenders)
        + "\nUsa '>=' en vez de '≥', '->' en vez de '→', etc."
    )


@pytest.mark.parametrize("path", app_sources(), ids=lambda p: p.name)
def test_files_are_opened_with_explicit_encoding(path: Path) -> None:
    """Abrir sin encoding usa el del sistema, que en Windows no es UTF-8."""
    offenders: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ".open(" not in line and "open(" not in line:
            continue
        if line.strip().startswith("#"):
            continue
        # Solo interesa la apertura de archivos de texto, no urlopen ni similares.
        if "urlopen" in line or "encoding=" in line or '"rb"' in line or "'rb'" in line:
            continue
        if re.search(r"\.open\(|(?<![\w.])open\(", line):
            offenders.append(f"{path.name}:{number} -> {line.strip()[:70]}")

    assert not offenders, (
        "Archivos abiertos sin encoding explicito:\n  " + "\n  ".join(offenders)
    )


def test_every_policy_message_survives_cp1252() -> None:
    """Los mensajes al cliente del YAML tambien viajan a logs."""
    from policy_engine import PolicyEngine

    engine = PolicyEngine()
    failures: list[str] = []
    for key, translations in engine.policy["messages"].items():
        for language, text in translations.items():
            try:
                text.encode("cp1252")
            except UnicodeEncodeError as exc:
                failures.append(f"{key}.{language}: {exc.reason} en '{text[:40]}'")

    assert not failures, "\n  ".join(failures)


def test_escalation_reasons_survive_cp1252() -> None:
    """Se generan con f-strings, asi que hay que probarlos ejecutandolos."""
    from policy_engine import CaseFacts, PolicyEngine

    engine = PolicyEngine()
    facts = CaseFacts(
        session_customer_id="CUS-001", country="MX",
        days_since_transaction=85, transaction_customer_id="CUS-001",
        transaction_status="Approved", amount_usd=9999.0,
        amount_usd_source="unavailable", candidate_count=1,
        intent="card_lost_stolen", unrecognized_charges_last_30d=5,
        requires_assisted_channel=True, clarification_turns=9,
    )
    outcome = engine.evaluate(facts)

    assert outcome.escalation_reasons, "el caso deberia disparar varias reglas"
    for reason in outcome.escalation_reasons:
        reason.encode("cp1252")  # lanza si algun simbolo no cabe


def test_audit_log_writes_utf8(tmp_path, monkeypatch) -> None:
    """El log acepta acentos sin depender de la codificacion del sistema."""
    import tools

    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(tools, "AUDIT_LOG", log)

    from session import AuthLevel, issue_token, verify_token

    session = verify_token(issue_token("CUS-001", "MX", "es", AuthLevel.LOW))
    tools._audit("prueba", session, {"detalle": "sesión con acentos y ñ"})

    assert "sesión con acentos y ñ" in log.read_text(encoding="utf-8")
