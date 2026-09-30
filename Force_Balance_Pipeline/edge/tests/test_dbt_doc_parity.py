"""Parity between warehouse/dbt (vars, classify_signature.sql, imbalance_score.sql) and docs/03-data-model.md, via
the same independent doc-03 parser edge/tests/test_doc_parity.py already uses for forcesim.signatures
(edge/tests/_doc03.py). Offline: no dbt command runs here, these are plain text/YAML reads. The document is the
source of truth: if doc 03 is edited (a threshold, a rule, a weight) and dbt_project.yml or a macro is not, these
fail first."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _doc03  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DBT = ROOT / "warehouse" / "dbt"


def dbt_vars():
    """The `vars:` block of dbt_project.yml, as {name: value}, parsed by hand (no PyYAML dependency in this
    project) -- flow enough for this file's simple `key: value` lines."""
    text = (DBT / "dbt_project.yml").read_text(encoding="utf-8")
    block = text.split("vars:", 1)[1].split("\nmodels:", 1)[0]
    out = {}
    for line in block.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        try:
            out[key] = int(value)
        except ValueError:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value.strip('"')
    return out


def macro_body(filename, name):
    """The text between `{% macro name(...) %}` and the matching `{% endmacro %}` in warehouse/dbt/macros/<filename>."""
    text = (DBT / "macros" / filename).read_text(encoding="utf-8")
    m = re.search(r"\{%-?\s*macro\s+" + re.escape(name) + r"\(.*?\)\s*-?%\}(.*?)\{%-?\s*endmacro\s*-?%\}", text, re.S)
    if not m:
        raise ValueError(f"macro {name!r} not found in {filename}")
    return m.group(1)


# ---- classify_signature.sql -------------------------------------------------------------------------------------

_WHEN_RE = re.compile(r"^\s*WHEN\s+(.+?)\s+THEN\s+'([a-z_]+)'\s*$", re.M)
_ELSE_RE = re.compile(r"^\s*ELSE\s+'([a-z_]+)'\s*$", re.M)
_COALESCE_ABS_TERM = re.compile(r"^COALESCE\(ABS\(\{\{\s*(\w+)\s*\}\}\),\s*0\)\s*<\s*(-?[\d.]+)$")
_ABS_TERM = re.compile(r"^ABS\(\{\{\s*(\w+)\s*\}\}\)\s*<\s*(-?[\d.]+)$")
_PLAIN_TERM = re.compile(r"^\{\{\s*(\w+)\s*\}\}\s*([<>])\s*(-?[\d.]+(?:e\d+)?)$")


def parse_classify_signature():
    """([(name, conditions)], fallback_name) from the macro's CASE block, in the same shape
    _doc03.signature_table() returns its rows (minus specialty, which the macro doesn't carry)."""
    body = macro_body("classify_signature.sql", "classify_signature")
    rows = []
    for condition_text, name in _WHEN_RE.findall(body):
        conditions = []
        for term in condition_text.split(" AND "):
            term = term.strip()
            m = _COALESCE_ABS_TERM.match(term)
            if m:
                conditions.append((m.group(1), "coalesce_abs<", float(m.group(2))))
                continue
            m = _ABS_TERM.match(term)
            if m:
                conditions.append((m.group(1), "abs<", float(m.group(2))))
                continue
            m = _PLAIN_TERM.match(term)
            if not m:
                raise ValueError(f"cannot parse classify_signature term {term!r}")
            conditions.append((m.group(1), m.group(2), float(m.group(3))))
        rows.append((name, tuple(conditions)))
    fallback = _ELSE_RE.search(body)
    return rows, fallback.group(1) if fallback else None


# ---- imbalance_score.sql ----------------------------------------------------------------------------------------

_WEIGHT_TERM = re.compile(r"(\d+(?:\.\d+)?)\s*\*\s*POWER\(COALESCE\(\{\{\s*(z_midi|z_kyber|z_dark)\s*\}\},\s*0\),\s*2\)")
_FULL_TERM = re.compile(r"SQRT\((\d+(?:\.\d+)?)\s*/\s*\{\{\s*channels_present\s*\}\}\)")


def parse_imbalance_score():
    """(weights, full) from macros/imbalance_score.sql, in the same shape _doc03.composite() returns."""
    body = macro_body("imbalance_score.sql", "imbalance_score")
    weights = {var[2:]: float(w) for w, var in _WEIGHT_TERM.findall(body)}  # "z_midi" -> "midi"
    full_match = _FULL_TERM.search(body)
    return weights, float(full_match.group(1))


class VarParityTests(unittest.TestCase):
    def setUp(self):
        self.v = dbt_vars()

    def test_thresholds(self):
        anomaly, emergency = _doc03.thresholds()
        self.assertEqual(self.v["anomaly_threshold"], anomaly)
        self.assertEqual(self.v["emergency_threshold"], emergency)

    def test_sustained_scans(self):
        self.assertEqual(self.v["sustained_scans"], _doc03.sustained_scans())

    def test_cooldown_hours(self):
        self.assertEqual(self.v["cooldown_hours"], _doc03.cooldown_hours())

    def test_replay_lag_seconds(self):
        self.assertEqual(self.v["replay_lag_seconds"], _doc03.replay_lag_seconds())

    def test_future_tolerance_and_max_age(self):
        future, max_age = _doc03.future_tolerance_minutes_and_max_age_days()
        self.assertEqual(self.v["future_tolerance_minutes"], future)
        self.assertEqual(self.v["max_age_days"], max_age)

    def test_lag_tolerance_seconds(self):
        self.assertEqual(self.v["lag_tolerance_seconds"], _doc03.lag_tolerance_seconds())


class ClassifySignatureMacroParityTests(unittest.TestCase):
    def test_rules_equal_the_doc_table_in_order(self):
        rows, fallback = _doc03.signature_table()
        macro_rows, macro_fallback = parse_classify_signature()
        self.assertEqual(macro_rows, [(name, conditions) for name, conditions, _ in rows])
        self.assertEqual(macro_fallback, fallback[0])


class ImbalanceScoreMacroParityTests(unittest.TestCase):
    def test_weights_and_scaling_equal_the_doc(self):
        weights, full = _doc03.composite()
        macro_weights, macro_full = parse_imbalance_score()
        self.assertEqual(macro_weights, weights)
        self.assertEqual(macro_full, full)


if __name__ == "__main__":
    unittest.main()
