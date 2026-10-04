"""Clasificador de intención: tres brazos, abstención conformal y reporte.

Ejecuta exactamente lo pre-registrado en docs/experiments.md (E-05):

    A  TF-IDF de caracteres 2-5 + regresión logística          (baseline)
    B  multilingual-e5-small congelado + LR, solo inglés        (zero-shot)
    C  multilingual-e5-small congelado + LR, inglés + es/pt     (candidato)

Test: 128 mensajes escritos a mano (eval/cases/intent_test_v1.jsonl).
Conformal: la mitad del test calibra (estratificada), la otra mitad mide.

Escribe:
    models/intent/head.joblib       cabeza del brazo desplegado
    models/intent/conformal.json    cuantiles por idioma
    eval/reports/intent_classifier.json

    uv run python ml/train_intent.py
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_EN = REPO_ROOT / "data" / "corpus" / "banking77_en.jsonl"
CORPUS_TR = REPO_ROOT / "data" / "corpus" / "banking77_es_pt.jsonl"
TEST = REPO_ROOT / "eval" / "cases" / "intent_test_v1.jsonl"
MODEL_DIR = REPO_ROOT / "models" / "intent"
REPORT = REPO_ROOT / "eval" / "reports" / "intent_classifier.json"

ENCODER = "intfloat/multilingual-e5-small"
ALPHA = 0.10           # pre-registrado (E-05)
# Punto de operación desplegado, elegido DESPUÉS de ver el barrido de α sobre
# la mitad de evaluación del test: con 0,10 el sistema pregunta en 54 de 64
# casos; con 0,30 decide casi siempre pero yerra el flujo en 15%, y mandar
# "me robaron la tarjeta" al flujo de disputa omite un escalamiento.
DEPLOY_ALPHA = 0.20
FIXED_THRESHOLD = 0.70
MAX_CLARIFY = 3
SEED = 20261003

INTENTS = [
    "unrecognized_charge", "duplicate_charge", "wrong_amount",
    "merchandise_not_received", "card_lost_stolen", "dispute_status",
    "policy_question", "out_of_scope",
]


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def ece(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Error de calibración esperado: confianza promedio vs acierto real."""
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf > lo) & (conf <= hi)
        if mask.any():
            total += mask.mean() * abs((pred[mask] == y[mask]).mean() - conf[mask].mean())
    return float(total)


def scores(y: np.ndarray, pred: np.ndarray, langs: np.ndarray) -> dict:
    out = {"macro_f1": f1_score(y, pred, average="macro", labels=range(len(INTENTS)),
                                zero_division=0),
           "accuracy": float((y == pred).mean()), "n": int(len(y))}
    for lang in ("es", "pt"):
        m = langs == lang
        out[f"macro_f1_{lang}"] = f1_score(y[m], pred[m], average="macro",
                                           labels=range(len(INTENTS)), zero_division=0)
        out[f"n_{lang}"] = int(m.sum())
    oos = INTENTS.index("out_of_scope")
    m = y == oos
    out["oos_recall"] = float((pred[m] == oos).mean()) if m.any() else None
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


# --- conformal ---------------------------------------------------------

def fit_qhat(probs: np.ndarray, y: np.ndarray, alpha: float) -> float:
    """Split conformal marginal: score = 1 - p(clase verdadera)."""
    s = 1.0 - probs[np.arange(len(y)), y]
    n = len(s)
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(s, level, method="higher"))


def prediction_set(p: np.ndarray, qhat: float) -> list[int]:
    return [c for c in range(len(p)) if 1.0 - p[c] <= qhat]


GROUPS = {
    "unrecognized_charge": "dispute", "duplicate_charge": "dispute",
    "wrong_amount": "dispute", "merchandise_not_received": "dispute",
    "card_lost_stolen": "card", "dispute_status": "status",
    "policy_question": "policy", "out_of_scope": "out_of_scope",
}


def route(pset: list[int]) -> str:
    """La misma regla que app/intent.py: se rutea por grupo de flujo."""
    if not pset:
        return "ESCALATE"
    groups = {GROUPS[INTENTS[c]] for c in pset}
    if len(groups) == 1:
        return "ABSTAIN" if groups == {"out_of_scope"} else "ACT"
    return "CLARIFY"


def group_of(c: int) -> str:
    return GROUPS[INTENTS[c]]


def main() -> None:
    started = time.perf_counter()
    en = [r for r in load(CORPUS_EN) if r["split"] == "train"]
    tr = load(CORPUS_TR)
    test = load(TEST)

    idx = {name: i for i, name in enumerate(INTENTS)}
    print(f"entrenamiento: {len(en):,} en + {len(tr):,} traducidas · test: {len(test)}")

    # --- datos ---------------------------------------------------------
    X_en = [r["text"] for r in en]
    y_en = np.array([idx[r["intent"]] for r in en])
    X_all = X_en + [r["text"] for r in tr]
    y_all = np.concatenate([y_en, [idx[r["intent"]] for r in tr]])

    X_te = [r["text"] for r in test]
    y_te = np.array([idx[r["intent"]] for r in test])
    lang_te = np.array([r["language"] for r in test])

    # --- A: TF-IDF -----------------------------------------------------
    tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2,
                            sublinear_tf=True)
    A = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced")
    A.fit(tfidf.fit_transform(X_all), y_all)
    prob_A = A.predict_proba(tfidf.transform(X_te))

    # --- encoder -------------------------------------------------------
    from sentence_transformers import SentenceTransformer
    enc = SentenceTransformer(ENCODER)

    def embed(texts: list[str]) -> np.ndarray:
        return enc.encode([f"query: {t}" for t in texts], batch_size=64,
                          normalize_embeddings=True, show_progress_bar=False)

    t0 = time.perf_counter()
    E_all = embed(X_all)
    E_te = embed(X_te)
    per_msg_ms = (time.perf_counter() - t0) / (len(X_all) + len(X_te)) * 1000

    # --- B: solo inglés ------------------------------------------------
    B = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced")
    B.fit(E_all[: len(X_en)], y_en)
    prob_B = B.predict_proba(E_te)

    # --- C: inglés + traducciones --------------------------------------
    C = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced")
    C.fit(E_all, y_all)
    prob_C = C.predict_proba(E_te)

    arms = {"A_tfidf": prob_A, "B_e5_en_only": prob_B, "C_e5_translate_train": prob_C}
    results = {}
    print(f"\n{'brazo':<22} {'macroF1':>8} {'es':>7} {'pt':>7} {'acc':>6} {'OOS rec':>8} {'ECE':>6}")
    print("-" * 70)
    for name, prob in arms.items():
        s = scores(y_te, prob.argmax(axis=1), lang_te)
        s["ece"] = round(ece(prob, y_te), 4)
        results[name] = s
        print(f"{name:<22} {s['macro_f1']:>8.3f} {s['macro_f1_es']:>7.3f} "
              f"{s['macro_f1_pt']:>7.3f} {s['accuracy']:>6.3f} "
              f"{s['oos_recall']:>8.3f} {s['ece']:>6.3f}")

    # --- criterio pre-registrado ---------------------------------------
    a, c = results["A_tfidf"], results["C_e5_translate_train"]
    gain = c["macro_f1"] - a["macro_f1"]
    worst_lang_gap = min(c["macro_f1_es"] - a["macro_f1_es"],
                         c["macro_f1_pt"] - a["macro_f1_pt"])
    accept = gain >= 0.03 and worst_lang_gap >= -0.10
    deployed = "C_e5_translate_train" if accept else "A_tfidf"
    print(f"\ncriterio E-05: ganancia C-A = {gain:+.3f} (umbral +0.030) · "
          f"peor brecha por idioma = {worst_lang_gap:+.3f} (umbral -0.100)")
    print(f"-> se despliega: {deployed}")

    prob_dep = prob_C if accept else prob_A

    # --- conformal por idioma ------------------------------------------
    # Mitad del test calibra, mitad mide. Estratificado por idioma e intención.
    rng = random.Random(SEED)
    cells: dict[tuple[str, int], list[int]] = defaultdict(list)
    for i, (lang, y) in enumerate(zip(lang_te, y_te)):
        cells[(lang, int(y))].append(i)
    cal, ev = [], []
    for members in cells.values():
        rng.shuffle(members)
        half = len(members) // 2
        cal += members[:half]
        ev += members[half:]
    cal, ev = np.array(sorted(cal)), np.array(sorted(ev))

    # v2 (3 oct): la conformal se calibra sobre la probabilidad de cada GRUPO
    # de flujo, que es la variable que el sistema decide. Calibrar sobre las 8
    # clases (v1) inflaba el cuantil con confusiones que no cambian ninguna
    # decisión ("no reconocido" vs "duplicado") y hacía preguntar casi siempre.
    G = sorted(set(GROUPS.values()))
    member = np.array([[GROUPS[i] == g for g in G] for i in INTENTS], dtype=float)
    prob_grp = prob_dep @ member                       # (n, grupos)
    y_grp = np.array([G.index(GROUPS[INTENTS[y]]) for y in y_te])

    qhat_class = {lang: fit_qhat(prob_dep[cal][lang_te[cal] == lang],
                                 y_te[cal][lang_te[cal] == lang], ALPHA)
                  for lang in ("es", "pt")}
    qhat = {lang: fit_qhat(prob_grp[cal][lang_te[cal] == lang],
                           y_grp[cal][lang_te[cal] == lang], ALPHA)
            for lang in ("es", "pt")}
    qhat_global = fit_qhat(prob_grp[cal], y_grp[cal], ALPHA)

    def evaluate(strategy: str) -> dict:
        per = defaultdict(lambda: defaultdict(int))
        for i in ev:
            lang = lang_te[i]
            if strategy == "class_mondrian_v1":
                p, y = prob_dep[i], int(y_te[i])
                pset_cls = prediction_set(p, qhat_class[lang])
                gset = sorted({G.index(group_of(c)) for c in pset_cls})
                covered = int(y in pset_cls)
            else:
                pg, yg = prob_grp[i], int(y_grp[i])
                if strategy == "fixed":
                    top = int(pg.argmax())
                    gset = [top] if pg[top] >= FIXED_THRESHOLD else []
                elif strategy == "group_global":
                    gset = prediction_set(pg, qhat_global)
                else:
                    gset = prediction_set(pg, qhat[lang])
                covered = int(yg in gset)
            yg = int(y_grp[i])
            if not gset:
                r = "ESCALATE"
            elif len(gset) == 1:
                r = "ABSTAIN" if G[gset[0]] == "out_of_scope" else "ACT"
            else:
                r = "CLARIFY"
            for key in (lang, "all"):
                d = per[key]
                d["n"] += 1
                d["covered"] += covered
                d[r] += 1
                if r == "ACT":
                    d["act_errors"] += int(gset[0] != yg)
                d["set_size_sum"] += len(gset)
        out = {}
        for key, d in per.items():
            n = d["n"]
            out[key] = {
                "n": n,
                "coverage": round(d["covered"] / n, 3),
                "act": d["ACT"], "clarify": d["CLARIFY"],
                "abstain": d["ABSTAIN"], "escalate": d["ESCALATE"],
                "act_error_rate": round(d["act_errors"] / d["ACT"], 3) if d["ACT"] else None,
                "avg_set_size": round(d["set_size_sum"] / n, 2),
            }
        return out

    conformal = {s: evaluate(s) for s in ("fixed", "class_mondrian_v1", "group_global", "group_mondrian")}
    print(f"\nconformal (α={ALPHA}, objetivo de cobertura {1 - ALPHA:.0%}) · "
          f"medido sobre {len(ev)} casos no usados para calibrar")
    print(f"{'estrategia':<20} {'idioma':<5} {'cobert.':>8} {'ACT':>4} {'CLAR':>5} "
          f"{'ABST':>5} {'ESC':>4} {'error en ACT':>13}")
    for s, by in conformal.items():
        for lang in ("es", "pt", "all"):
            d = by[lang]
            err = "—" if d["act_error_rate"] is None else f"{d['act_error_rate']:.1%}"
            print(f"{s:<20} {lang:<5} {d['coverage']:>8.1%} {d['act']:>4} "
                  f"{d['clarify']:>5} {d['abstain']:>5} {d['escalate']:>4} {err:>13}")

    # --- barrido de alfa: la curva riesgo-cobertura -------------------
    # α es una decisión de negocio: la tasa de ruteo equivocado que se acepta.
    sweep = []
    for a_ in (0.05, 0.10, 0.20, 0.30, 0.40):
        q_ = {lang: fit_qhat(prob_grp[cal][lang_te[cal] == lang],
                             y_grp[cal][lang_te[cal] == lang], a_) for lang in ("es", "pt")}
        cov = act = err = clar = 0
        for i in ev:
            gset = prediction_set(prob_grp[i], q_[lang_te[i]])
            cov += int(int(y_grp[i]) in gset)
            if len(gset) == 1:
                act += 1
                err += int(gset[0] != int(y_grp[i]))
            elif len(gset) > 1:
                clar += 1
        sweep.append({"alpha": a_, "qhat": {k: round(v, 3) for k, v in q_.items()},
                      "coverage": round(cov / len(ev), 3), "decides": act,
                      "clarifies": clar, "routing_errors": err, "n": int(len(ev))})
    print()
    print(f"barrido de α (conformal por grupo y por idioma, {len(ev)} casos):")
    print(f"{'α':>5} {'qhat es':>8} {'qhat pt':>8} {'cobert.':>8} {'decide':>7} {'pregunta':>9} {'errores':>8}")
    for s in sweep:
        print(f"{s['alpha']:>5} {s['qhat']['es']:>8} {s['qhat']['pt']:>8} {s['coverage']:>8.1%} "
              f"{s['decides']:>7} {s['clarifies']:>9} {s['routing_errors']:>8}")

    # --- matriz de confusión del brazo desplegado ----------------------
    cm = confusion_matrix(y_te, prob_dep.argmax(axis=1), labels=range(len(INTENTS)))

    # --- artefactos ----------------------------------------------------
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    head = {"kind": deployed, "intents": INTENTS, "encoder": ENCODER}
    if accept:
        head["model"] = C
    else:
        head["model"] = A
        head["vectorizer"] = tfidf
    joblib.dump(head, MODEL_DIR / "head.joblib")
    qhat_deploy = {lang: fit_qhat(prob_grp[cal][lang_te[cal] == lang],
                                  y_grp[cal][lang_te[cal] == lang], DEPLOY_ALPHA)
                   for lang in ("es", "pt")}
    (MODEL_DIR / "conformal.json").write_text(json.dumps({
        "alpha": DEPLOY_ALPHA, "alpha_preregistered": ALPHA, "qhat_preregistered": qhat,
        "level": "group", "groups": G, "qhat": qhat_deploy,
        "qhat_global": qhat_global, "qhat_class_v1": qhat_class,
        "max_clarify": MAX_CLARIFY, "calibration_n": int(len(cal)),
    }, indent=2), encoding="utf-8")

    report = {
        "experiment": "E-05",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "encoder": ENCODER,
        "data": {
            "train_en": len(X_en), "train_translated": len(tr),
            "test": len(test),
            "hash_train_en": file_hash(CORPUS_EN),
            "hash_train_translated": file_hash(CORPUS_TR),
            "hash_test": file_hash(TEST),
        },
        "arms": results,
        "acceptance": {"gain_macro_f1": round(gain, 4),
                       "worst_language_gap": round(worst_lang_gap, 4),
                       "accepted": accept, "deployed": deployed},
        "conformal": conformal,
        "alpha_sweep": sweep,
        "confusion_matrix": {"labels": INTENTS, "matrix": cm.tolist()},
        "encode_ms_per_message": round(per_msg_ms, 2),
        "seconds": round(time.perf_counter() - started, 1),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{per_msg_ms:.1f} ms por mensaje (encoder, CPU) · "
          f"{report['seconds']}s total -> {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
