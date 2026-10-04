"""Gráfica de justificación del flujo, para las slides.

Cuatro paneles, todos con números verificables desde el dataset:
  1. Las disputas son el mayor bloque de quejas (36,5%).
  2. Una de cada cinco incumple SLA.
  3. La mitad llega por call center, el canal más caro.
  4. Volumen mensual sostenido, sin estacionalidad que permita diferir.

    uv run python pipeline/plot_justification.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "img"
COMPLAINTS = (
    "read_csv_auto('data/complaints/*/*/*/*.csv', "
    "union_by_name=true, ignore_errors=true)"
)
IS_DISPUTE = "subcategory IN ('Cargo no reconocido','Cobro indebido')"

INK = "#1a1a1a"
MUTED = "#b0b7c3"
ACCENT = "#0d7d8c"
WARN = "#c2410c"


def style(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#d4d8de")
    ax.tick_params(colors="#555", labelsize=9)


def main() -> None:
    con = duckdb.connect()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total = con.sql(f"SELECT count(*) FROM {COMPLAINTS}").fetchone()[0]
    disputes = con.sql(
        f"SELECT count(*) FROM {COMPLAINTS} WHERE {IS_DISPUTE}"
    ).fetchone()[0]

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    fig.suptitle(
        "Por qué el intake de disputas de tarjeta",
        fontsize=15,
        fontweight="bold",
        color=INK,
        x=0.02,
        ha="left",
        y=0.97,
    )
    fig.text(
        0.02,
        0.925,
        f"LATAM Bank · {total:,} quejas · jun 2023 – jun 2026 · verificado sobre el dataset",
        fontsize=9.5,
        color="#666",
        ha="left",
    )

    # --- 1. Mezcla de subcategorías -------------------------------------
    ax = axes[0][0]
    rows = con.sql(
        f"""SELECT coalesce(subcategory,'(sin dato)') s, count(*) c
            FROM {COMPLAINTS} GROUP BY 1 ORDER BY c DESC"""
    ).fetchall()
    labels = [r[0] for r in rows][::-1]
    values = [r[1] for r in rows][::-1]
    colors = [ACCENT if lbl in ("Cargo no reconocido", "Cobro indebido") else MUTED
              for lbl in labels]
    bars = ax.barh(labels, values, color=colors)
    for bar, val in zip(bars, values):
        ax.text(val + total * 0.008, bar.get_y() + bar.get_height() / 2,
                f"{100 * val / total:.1f}%", va="center", fontsize=9, color="#444")
    ax.set_xlim(0, max(values) * 1.18)
    ax.set_xticks([])
    ax.set_title(
        f"Las disputas son el mayor bloque: {100 * disputes / total:.1f}%",
        fontsize=11, fontweight="bold", color=INK, loc="left", pad=10,
    )
    style(ax)
    ax.spines["bottom"].set_visible(False)

    # --- 2. SLA ---------------------------------------------------------
    ax = axes[0][1]
    breached = con.sql(
        f"SELECT count(*) FROM {COMPLAINTS} WHERE {IS_DISPUTE} AND sla_breached"
    ).fetchone()[0]
    # Cuadricula de 100 iconos: cada uno son ~245 disputas. Un bloque de
    # waffle comunica "1 de cada 5" mejor que una barra apilada.
    cols, rows_n = 20, 5
    share = round(100 * breached / disputes)
    for i in range(cols * rows_n):
        r, c = divmod(i, cols)
        ax.add_patch(
            plt.Rectangle(
                (c, rows_n - 1 - r), 0.82, 0.82,
                color=WARN if i < share else "#dfe4ea",
            )
        )
    ax.set_xlim(-0.4, cols)
    ax.set_ylim(-0.6, rows_n)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(
        f"1 de cada 5 incumple SLA: {100 * breached / disputes:.1f}%",
        fontsize=11, fontweight="bold", color=INK, loc="left", pad=10,
    )
    ax.text(0, -0.45,
            f"{breached:,} de {disputes:,} disputas · mediana de resolución: 15 días",
            fontsize=9, color="#666")

    # --- 3. Canal -------------------------------------------------------
    ax = axes[1][0]
    rows = con.sql(
        f"""SELECT reception_channel, count(*) c FROM {COMPLAINTS}
            WHERE {IS_DISPUTE} GROUP BY 1 ORDER BY c DESC"""
    ).fetchall()
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    colors = [WARN] + [MUTED] * (len(labels) - 1)
    bars = ax.bar(labels, values, color=colors)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + disputes * 0.012,
                f"{100 * val / disputes:.0f}%", ha="center", fontsize=9, color="#444")
    ax.set_ylim(0, max(values) * 1.2)
    ax.set_yticks([])
    ax.tick_params(axis="x", labelrotation=20)
    ax.set_title(
        "La mitad llega por el canal más caro",
        fontsize=11, fontweight="bold", color=INK, loc="left", pad=10,
    )
    ax.text(0, -0.30,
            "Llamada de queja: 431 s de mediana (vs 205 s transaccional)",
            fontsize=9, color="#666", transform=ax.transAxes)
    style(ax)
    ax.spines["left"].set_visible(False)

    # --- 4. Volumen mensual --------------------------------------------
    ax = axes[1][1]
    rows = con.sql(
        f"""SELECT date_trunc('month', creation_date) m, count(*) c
            FROM {COMPLAINTS} WHERE {IS_DISPUTE} AND creation_date IS NOT NULL
            GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    # El primero y el último mes están truncados por el rango del dataset
    # (arranca el 17 de junio y termina el 17 de junio): se excluyen para no
    # leer como caída de demanda lo que es un mes incompleto.
    rows = rows[1:-1]
    months = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    ax.plot(months, counts, color=ACCENT, linewidth=1.8)
    ax.fill_between(months, counts, color=ACCENT, alpha=0.10)
    ax.set_ylim(0, max(counts) * 1.32)
    avg = sum(counts) / len(counts)
    ax.axhline(avg, color="#999", linestyle="--", linewidth=1)
    ax.annotate(
        f"media {avg:,.0f}/mes",
        xy=(months[len(months) // 2], avg),
        xytext=(0, 10), textcoords="offset points",
        fontsize=9, color="#666", ha="center",
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
    )
    ax.set_title(
        "Demanda sostenida, sin picos que permitan diferir",
        fontsize=11, fontweight="bold", color=INK, loc="left", pad=10,
    )
    ax.text(0, -0.22, "Meses parciales de inicio y fin excluidos",
            fontsize=8.5, color="#888", transform=ax.transAxes)
    style(ax)

    fig.tight_layout(rect=(0, 0.02, 1, 0.90))
    out = OUT_DIR / "flow_justification.png"
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"-> {out.relative_to(REPO_ROOT)}")

    print(
        f"\nCifras del panel:\n"
        f"  quejas totales      {total:,}\n"
        f"  disputas            {disputes:,} ({100 * disputes / total:.2f}%)\n"
        f"  SLA incumplido      {breached:,} ({100 * breached / disputes:.2f}% de las disputas)\n"
        f"  por call center     {values[0]:,} ({100 * values[0] / disputes:.2f}%)"
    )


if __name__ == "__main__":
    main()
