"""Q2 - Validez de etiquetas: ¿tienen señal o son ruido?

Para cada etiqueta candidata entrena un gradient boosting sobre features
disponibles *en el momento del contacto* (sin leakage) y reporta el AUC en un
split temporal. AUC ~ 0.5 significa que la etiqueta no es predecible desde lo
observable: no sirve como ground truth.

    uv run python pipeline/label_validity.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPLAINTS = (
    "read_csv_auto('data/complaints/*/*/*/*.csv', "
    "union_by_name=true, ignore_errors=true)"
)
DISPUTES = "subcategory IN ('Cargo no reconocido','Cobro indebido')"

# Solo lo que se conoce cuando entra el caso. Se excluyen a propósito
# resolution_days, status, closing_date y compensation_granted: son posteriores
# a la resolución y su uso sería leakage.
FEATURES = [
    "claimed_amount",
    "case_type",
    "category",
    "reception_channel",
    "currency",
    "is_repeat_complainer",
    "customer_segment",
    "customer_country",
    "credit_score",
]

TARGETS = {
    "sla_breached": "sla_breached",
    "priority_high": "priority IN ('High','Critical')",
    "was_escalated": "status = 'Escalated'",
    "rejected": "status = 'Rejected'",
}


def load() -> "duckdb.DuckDBPyRelation":
    con = duckdb.connect()
    con.sql(
        f"""
        CREATE VIEW base AS
        SELECT c.*,
               cu.segment  AS customer_segment,
               cu.country  AS customer_country,
               cu.credit_score
        FROM {COMPLAINTS} c
        LEFT JOIN read_csv_auto('data/customers.csv') cu USING (customer_id)
        WHERE {DISPUTES}
        """
    )
    return con


def main() -> None:
    con = load()
    cols = ", ".join(FEATURES)
    targets = ", ".join(f"({expr}) AS t_{name}" for name, expr in TARGETS.items())
    df = con.sql(
        f"SELECT {cols}, {targets}, creation_date FROM base ORDER BY creation_date"
    ).df()

    print(f"casos de disputa: {len(df):,}\n")

    # Split temporal: 70% más antiguo para entrenar, 30% reciente para evaluar.
    cut = int(len(df) * 0.7)
    X_all = df[FEATURES].copy()
    for col in X_all.columns:
        # pandas 3 expone el texto como StringDtype, no como 'object'.
        if not pd.api.types.is_numeric_dtype(X_all[col]):
            X_all[col] = X_all[col].astype("category")

    print(f"{'etiqueta':<18} {'prevalencia':>12} {'AUC':>7}   veredicto")
    print("-" * 62)

    for name in TARGETS:
        y = df[f"t_{name}"].astype("float")
        mask = y.notna()
        if mask.sum() < 500 or y[mask].nunique() < 2:
            print(f"{name:<18} {'n/d':>12} {'--':>7}   muestra insuficiente")
            continue

        X_tr, y_tr = X_all[mask][:cut], y[mask][:cut]
        X_te, y_te = X_all[mask][cut:], y[mask][cut:]
        if y_te.nunique() < 2 or y_tr.nunique() < 2:
            print(f"{name:<18} {'n/d':>12} {'--':>7}   una sola clase en el split")
            continue

        model = HistGradientBoostingClassifier(
            max_iter=120, categorical_features="from_dtype", random_state=0
        )
        model.fit(X_tr, y_tr)
        auc = roc_auc_score(y_te, model.predict_proba(X_te)[:, 1])

        if auc < 0.55:
            verdict = "SIN SEÑAL - no usar como ground truth"
        elif auc < 0.65:
            verdict = "señal debil"
        else:
            verdict = "señal utilizable"
        print(f"{name:<18} {y.mean():>11.1%} {auc:>7.3f}   {verdict}")

    print(
        "\nFeatures usadas (todas anteriores a la resolución): "
        + ", ".join(FEATURES)
        + "\nExcluidas por leakage: resolution_days, status final, closing_date, "
        "compensation_granted."
    )


if __name__ == "__main__":
    main()
