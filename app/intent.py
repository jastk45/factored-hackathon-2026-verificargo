"""Clasificación de intención con abstención conformal.

Reemplaza al LLM en la decisión de intención: E-02b midió que qwen3 clasificaba
mal el 37% de los casos en español en cuanto el prompt pedía seis campos a la
vez. Acá decide un clasificador entrenado (E-05) y la incertidumbre se convierte
en una acción con garantía estadística de cobertura (D-10).

**Rutear por grupo, no por clase.** Las cuatro intenciones de cargo siguen el
mismo flujo de disputa; que el conjunto conformal contenga dos de ellas no es
una ambigüedad que el cliente tenga que resolver. Solo se pregunta cuando el
conjunto mezcla grupos que llevan a flujos distintos. La cobertura no cambia:
el conjunto es el mismo, solo cambia qué se hace con él.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "intent"

GROUPS: dict[str, str] = {
    "unrecognized_charge": "dispute",
    "duplicate_charge": "dispute",
    "wrong_amount": "dispute",
    "merchandise_not_received": "dispute",
    "card_lost_stolen": "card",
    "dispute_status": "status",
    "policy_question": "policy",
    "out_of_scope": "out_of_scope",
}


@dataclass(frozen=True)
class IntentDecision:
    intent: str                  # la más probable
    confidence: float
    prediction_set: tuple[str, ...]
    groups: tuple[str, ...]
    route: str                   # ACT | CLARIFY | ABSTAIN | ESCALATE
    reason: str

    def as_dict(self) -> dict:
        return {
            "intent": self.intent, "confidence": round(self.confidence, 3),
            "prediction_set": list(self.prediction_set),
            "groups": list(self.groups), "route": self.route, "reason": self.reason,
        }


class IntentClassifier:
    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        import joblib

        head = joblib.load(model_dir / "head.joblib")
        self.kind: str = head["kind"]
        self.intents: list[str] = head["intents"]
        self.model = head["model"]
        self.vectorizer = head.get("vectorizer")
        self.encoder_name: str = head["encoder"]
        conf = json.loads((model_dir / "conformal.json").read_text(encoding="utf-8"))
        self.qhat: dict[str, float] = conf["qhat"]
        self.qhat_global: float = conf["qhat_global"]
        self.max_clarify: int = conf.get("max_clarify", 3)
        self._encoder = None

    def _encode(self, text: str) -> np.ndarray:
        if self.vectorizer is not None:
            return self.vectorizer.transform([text])
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(self.encoder_name)
        return self._encoder.encode([f"query: {text}"], normalize_embeddings=True)

    def probabilities(self, text: str) -> np.ndarray:
        return self.model.predict_proba(self._encode(text))[0]

    def decide(self, text: str, language: str) -> IntentDecision:
        p = self.probabilities(text)
        qhat = self.qhat.get(language, self.qhat_global)
        pset = [self.intents[c] for c in range(len(p)) if 1.0 - p[c] <= qhat]
        top = int(p.argmax())
        top_intent = self.intents[top]

        groups = tuple(sorted({GROUPS[i] for i in pset}))

        if not pset:
            route, reason = "ESCALATE", "conjunto vacío: ninguna intención es plausible"
        elif len(groups) == 1:
            group = groups[0]
            if group == "out_of_scope":
                route, reason = "ABSTAIN", "fuera del alcance del servicio"
            else:
                route, reason = "ACT", f"un solo flujo posible: {group}"
        else:
            # Varios flujos posibles: se pregunta, no se escala. Preguntar es
            # seguro y barato; transferir a un humano por no entender el tema
            # es caro. Decidido tras el ensayo con datos parciales: con la
            # regla anterior (escalar si había más de 3 grupos) se escalaba
            # casi todo en el primer turno. El conjunto -y por lo tanto la
            # garantía de cobertura- no cambia; solo qué se hace con él.
            route, reason = "CLARIFY", f"varios flujos posibles: {', '.join(groups)}"

        # Dentro del grupo elegido, la intención concreta es la más probable de
        # las que están en el conjunto.
        if route == "ACT":
            in_set = [self.intents.index(i) for i in pset]
            top_intent = self.intents[max(in_set, key=lambda c: p[c])]

        return IntentDecision(
            intent=top_intent, confidence=float(p.max()),
            prediction_set=tuple(pset), groups=groups, route=route, reason=reason,
        )


@lru_cache(maxsize=1)
def default_classifier() -> IntentClassifier:
    return IntentClassifier()
