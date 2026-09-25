#!/usr/bin/env python3
"""Check that the review-gate constants in the enrichment scripts match the analyses SQL (doc 08).

The gate is computed twice: offline in Python (enrich_planets.py, enrich_jedi.py) before a seed is
written, and in SQL (warehouse/dbt/analyses/) after `dbt seed`. Both must use the same thresholds
and the same anchors, or the offline verdict and the warehouse verdict can disagree. The two cannot
be run against each other without a warehouse, so this compares the constants against the SQL text.

Exit code 0 when they match, 1 (with the mismatches listed) when they do not. Standard library only.

Usage:
    python scripts/check_gate_parity.py
"""
import re
import sys
from pathlib import Path

import enrich_jedi
import enrich_planets as ep

ANALYSES = Path(__file__).resolve().parents[1] / "warehouse" / "dbt" / "analyses"


def read(name):
    return (ANALYSES / name).read_text(encoding="utf-8")


def check_spread(problems):
    sql = read("phase1_check_1_spread.sql")
    u = ep.RANGE_USE
    needles = {
        f"min_pos <= {u['max_min_position']:.2f}": "the lowest-baseline position limit",
        f"max_pos >= {u['min_max_position']:.2f}": "the highest-baseline position limit",
        f"share_in_middle <= {u['max_middle_share']:.2f}": "the middle-band share limit",
        f"between {u['middle_lo']:.2f} and {u['middle_hi']:.2f}": "the middle band",
    }
    for needle, what in needles.items():
        if needle not in sql:
            problems.append(f"phase1_check_1_spread.sql: {what}: expected `{needle}` (from enrich_planets.RANGE_USE)")
    for gone in ("stddev", "bins_occupied", "p90 - p10"):
        if gone in sql:
            problems.append(f"phase1_check_1_spread.sql still contains `{gone}`, a dropped criterion")
    for ch, (lo, hi) in ep.RANGES.items():
        if not re.search(rf"\('{ch}', {lo}, {hi}\)", sql):
            problems.append(f"phase1_check_1_spread.sql: range for {ch} should be ({lo}, {hi})")


def check_anchors(problems):
    sql = read("phase1_check_2_anchors.sql")
    sql_anchors = tuple(re.findall(r"\('(\w+)', '(\w+)', '(\w+)'\)", sql))
    if sql_anchors != ep.ANCHORS:
        problems.append(f"phase1_check_2_anchors.sql anchors {sql_anchors} != enrich_planets.ANCHORS {ep.ANCHORS}")
    for needle in (f"pct_rank >= {ep.HIGH_PCT_RANK}", f"pct_rank <= {ep.LOW_PCT_RANK}", "bool_and(ok) over ()"):
        if needle not in sql:
            problems.append(f"phase1_check_2_anchors.sql: expected `{needle}`")


def check_jedi(problems):
    sql = read("phase1_check_3_jedi.sql")
    for needle in (f"jedi_count >= {enrich_jedi.MIN_PER_SPECIALTY}", f"count(*) = {enrich_jedi.EXPECTED_ROWS}"):
        if needle not in sql:
            problems.append(f"phase1_check_3_jedi.sql: expected `{needle}` (from enrich_jedi)")
    for s in ep.ec.SPECIALTIES:
        if f"('{s}')" not in sql:
            problems.append(f"phase1_check_3_jedi.sql: specialty {s!r} missing")


def main():
    problems = []
    check_spread(problems)
    check_anchors(problems)
    check_jedi(problems)
    if problems:
        print("gate parity FAILED: the Python gate and the analyses SQL disagree")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("gate parity OK: thresholds, ranges, anchors and Jedi criteria match the analyses SQL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
