"""Test helper: reads docs/03-data-model.md and exposes what the code must agree with (the signature
table, the thresholds, the firing rules, the composite formula). It shares nothing with
forcesim.signatures, so a test that uses it is an independent check."""
import math
import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "03-data-model.md"

_CONDITION = re.compile(r"^(ABS\()?(\w+)\)?\s*([<>])\s*(-?\d+(?:\.\d+)?(?:e\d+)?)$")


def text():
    return DOC.read_text(encoding="utf-8")


def signature_table():
    """(rows, fallback): rows is [(name, conditions, specialty)] in document order, where a condition
    is (variable, op, value) with op in {'>', '<', 'abs<'}; fallback is (name, specialty)."""
    lines = text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| `signature` | Pattern"))
    rows, fallback = [], None
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name, pattern, specialty = cells[0].strip("`"), cells[1], cells[2].strip("`")
        if pattern == "fallback":
            fallback = (name, specialty)
            continue
        conditions = []
        for segment in re.findall(r"`([^`]+)`", pattern):
            m = _CONDITION.fullmatch(segment)
            if not m:
                raise ValueError(f"cannot parse doc 03 condition {segment!r}")
            is_abs, variable, op, value = m.groups()
            if is_abs and op != "<":
                raise ValueError(f"unsupported ABS condition {segment!r}")
            conditions.append((variable, "abs<" if is_abs else op, float(value)))
        rows.append((name, tuple(conditions), specialty))
    return rows, fallback


def evaluate(rows, fallback_name, z_midi, z_kyber, z_dark, population, channels_present):
    """First matching row of a parsed table, else the fallback. NULL (None) never satisfies a condition."""
    env = {"z_midi": z_midi, "z_kyber": z_kyber, "z_dark": z_dark, "population": population,
           "channels_present": channels_present}
    for name, conditions, _ in rows:
        ok = True
        for variable, op, value in conditions:
            x = env[variable]
            if x is None:
                ok = False
            elif op == ">":
                ok = ok and x > value
            elif op == "<":
                ok = ok and x < value
            else:
                ok = ok and abs(x) < value
        if ok:
            return name
    return fallback_name


def thresholds():
    """(anomaly, emergency) from 'Anomaly above A, emergency above E.'"""
    m = re.search(r"Anomaly above (\d+(?:\.\d+)?), emergency above (\d+(?:\.\d+)?)", text())
    return float(m.group(1)), float(m.group(2))


def firing_thresholds():
    """Every `imbalance_score > X` in the firing rules (probe-sourced and report-sourced)."""
    return [float(x) for x in re.findall(r"imbalance_score\s*>\s*(\d+(?:\.\d+)?)", text())]


def sustained_scans():
    return int(re.search(r"Sustained across at least (\d+) consecutive scans", text()).group(1))


def composite():
    """(weights per channel, channels the score is scaled against) from the doc's SQL block."""
    t = text()
    weights = {ch: float(w) for w, ch in
               re.findall(r"(\d+(?:\.\d+)?)\s*\*\s*POWER\(COALESCE\(z_(midi|kyber|dark),\s*0\),\s*2\)", t)}
    full = float(re.search(r"SQRT\((\d+(?:\.\d+)?)\s*/\s*channels_present\)", t).group(1))
    return weights, full


def composite_score(z_midi, z_kyber, z_dark, channels_present=3):
    weights, full = composite()
    z = {"midi": z_midi, "kyber": z_kyber, "dark": z_dark}
    return math.sqrt(sum(weights[c] * (0.0 if z[c] is None else z[c]) ** 2 for c in weights)) \
        * math.sqrt(full / channels_present)
