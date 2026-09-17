"""Regenerates docs/images/detection-coverage.svg from mart_detection_coverage.

The README's headline chart is our own data, not a screenshot - hand-written
SVG string formatting rather than a plotting dependency (matplotlib etc.),
since four tiers x two series is well within what string formatting can do
cleanly, and a hand-written SVG renders natively on GitHub with no build
step for a reader who clones the repo.

Run after `make triage-eval-sample`-equivalent data exists (any run that
populates fct_technique_evaluations for both sources):

    uv run python docs/images/generate_coverage_chart.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).parent.parent.parent
WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"
OUT_PATH = Path(__file__).parent / "detection-coverage.svg"

TIER_ORDER = ["high", "partial", "low_camouflaged", "low_no_evidence"]
TIER_LABELS = {
    "high": "high",
    "partial": "partial",
    "low_camouflaged": "low\n(camouflaged)",
    "low_no_evidence": "low\n(no evidence)",
}
SOURCE_COLORS = {"baseline": "#4a5568", "llm": "#dd6b20"}
SOURCE_LABELS = {"baseline": "rule-based baseline", "llm": "LLM"}

WIDTH, HEIGHT = 720, 380
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 60, 30, 60, 70
PLOT_W = WIDTH - MARGIN_L - MARGIN_R
PLOT_H = HEIGHT - MARGIN_T - MARGIN_B


def fetch_rows(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, tuple[float, int, int]]]:
    """tier -> source -> (recall_pct, reachable, attempts)"""
    rows = con.execute(
        """
        select coverage_tier, source, recall, reachable_techniques, attempts
        from mart_detection_coverage
        where source is not null
        """
    ).fetchall()
    out: dict[str, dict[str, tuple[float, int, int]]] = {t: {} for t in TIER_ORDER}
    for tier, source, recall, reachable, attempts in rows:
        pct = round((recall or 0.0) * 100, 1)
        out[tier][source] = (pct, reachable, attempts)
    return out


def render_svg(data: dict[str, dict[str, tuple[float, int, int]]]) -> str:
    sources = ["baseline", "llm"]
    group_w = PLOT_W / len(TIER_ORDER)
    bar_w = group_w * 0.28
    gap = group_w * 0.06

    parts: list[str] = []
    font_stack = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{font_stack}">'
    )
    parts.append(f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>')
    parts.append(
        f'<text x="{WIDTH / 2}" y="24" text-anchor="middle" font-size="16" '
        f'font-weight="600" fill="#1a202c">Detection coverage by observability tier</text>'
    )
    parts.append(
        f'<text x="{WIDTH / 2}" y="42" text-anchor="middle" font-size="12" '
        f'fill="#718096">Technique recall, 36 sessions across all twelve villains</text>'
    )

    # Gridlines + y-axis labels (0/25/50/75/100%)
    for pct in (0, 25, 50, 75, 100):
        y = MARGIN_T + PLOT_H - (pct / 100) * PLOT_H
        parts.append(
            f'<line x1="{MARGIN_L}" y1="{y:.1f}" x2="{WIDTH - MARGIN_R}" y2="{y:.1f}" '
            f'stroke="#e2e8f0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{MARGIN_L - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="#a0aec0">{pct}%</text>'
        )

    # Bars
    for i, tier in enumerate(TIER_ORDER):
        group_x = MARGIN_L + i * group_w
        for j, source in enumerate(sources):
            pct, reachable, attempts = data[tier].get(source, (0.0, 0, 0))
            bar_h = (pct / 100) * PLOT_H
            x = group_x + gap + j * (bar_w + gap)
            y = MARGIN_T + PLOT_H - bar_h
            color = SOURCE_COLORS[source]
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
                f'fill="{color}" rx="2"/>'
            )
            label_y = y - 6 if bar_h > 2 else y - 6
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" text-anchor="middle" '
                f'font-size="11" font-weight="600" fill="{color}">{pct:g}%</text>'
            )
        # Tier label (two lines if it contains \n)
        label = TIER_LABELS[tier]
        lines = label.split("\n")
        base_y = MARGIN_T + PLOT_H + 20
        for li, line in enumerate(lines):
            parts.append(
                f'<text x="{group_x + group_w / 2:.1f}" y="{base_y + li * 14:.1f}" '
                f'text-anchor="middle" font-size="12" fill="#2d3748">{line}</text>'
            )
        # reachable/attempts footnote under the tier label
        _, reachable, attempts = data[tier].get("baseline", (0.0, 0, 0))
        parts.append(
            f'<text x="{group_x + group_w / 2:.1f}" y="{base_y + len(lines) * 14 + 12:.1f}" '
            f'text-anchor="middle" font-size="10" fill="#a0aec0">'
            f"{reachable} techniques, {attempts} attempts</text>"
        )

    # Axis line
    parts.append(
        f'<line x1="{MARGIN_L}" y1="{MARGIN_T + PLOT_H}" x2="{WIDTH - MARGIN_R}" '
        f'y2="{MARGIN_T + PLOT_H}" stroke="#cbd5e0" stroke-width="1.5"/>'
    )

    # Legend
    legend_x = WIDTH - MARGIN_R - 220
    legend_y = MARGIN_T - 14
    for k, source in enumerate(sources):
        lx = legend_x + k * 110
        swatch = SOURCE_COLORS[source]
        parts.append(
            f'<rect x="{lx}" y="{legend_y}" width="12" height="12" fill="{swatch}" rx="2"/>'
        )
        parts.append(
            f'<text x="{lx + 17}" y="{legend_y + 10}" font-size="11" fill="#2d3748">'
            f"{SOURCE_LABELS[source]}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    if not WAREHOUSE.exists():
        print(
            f"no warehouse at {WAREHOUSE} - run `make transform` and `make triage` first",
            file=sys.stderr,
        )
        raise SystemExit(1)
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        data = fetch_rows(con)
    finally:
        con.close()
    missing = [t for t in TIER_ORDER if not data[t]]
    if missing:
        print(f"no source data for tiers {missing} - run `make triage` first", file=sys.stderr)
        raise SystemExit(1)
    svg = render_svg(data)
    OUT_PATH.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
