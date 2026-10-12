"""Structural guarantees of the dbt project that no data test can give, checked offline from the model SQL (no dbt command runs, no warehouse):
the baseline is probe-only because nothing it reads can carry a report. Doc 03: "gold.sector_baseline contains no report-sourced data -- the
most important test in the project". The dbt data test assert_baseline_probe_only and assert_reports_stay_in_their_own_tables check the data;
this checks the wiring that makes the data right, so a refactor that adds a report input fails here by name, before any build.

Comments (block, line and Jinja) are stripped first: the model headers talk about the models they must not read."""
import re
import unittest
from pathlib import Path

DBT = Path(__file__).resolve().parents[2] / "warehouse" / "dbt"
MODELS = DBT / "models"
REPORT_ONLY = {"silver_force_report", "silver_report_rejects"}
REPORT_CARRYING = REPORT_ONLY | {"gold_sector_reading", "gold_disturbance"}


def strip_comments(text):
    s = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"--[^\n]*", "", s)


def code(path):
    return strip_comments(Path(path).read_text(encoding="utf-8"))


def model_sql():
    return {p.stem: code(p) for p in MODELS.rglob("*.sql")}


def refs(sql):
    return set(re.findall(r"\bref\(\s*'([a-z0-9_]+)'\s*\)", sql))


def sources(sql):
    return set(re.findall(r"\bsource\(\s*'([a-z0-9_]+)'\s*,\s*'([a-z0-9_]+)'\s*\)", sql))


def ancestors(name, sqls):
    seen, todo = set(), list(refs(sqls[name]))
    while todo:
        parent = todo.pop()
        if parent in seen:
            continue
        seen.add(parent)
        if parent in sqls:                    # a seed (dim_sector) has no SQL model
            todo.extend(refs(sqls[parent]))
    return seen


def flat(sql):
    return re.sub(r"\s+", " ", sql)


class BaselineIsProbeOnlyTests(unittest.TestCase):
    def setUp(self):
        self.sqls = model_sql()

    def test_the_models_exist(self):
        for name in ("gold_sector_baseline", "silver_probe_reading", "silver_rejects", "stg_bronze_events", "silver_force_report", "silver_report_rejects",
                     "gold_sector_reading", "gold_disturbance"):
            self.assertIn(name, self.sqls)

    def test_the_baseline_reads_silver_probe_reading_and_nothing_else(self):
        self.assertEqual(refs(self.sqls["gold_sector_baseline"]), {"silver_probe_reading"})
        self.assertEqual(sources(self.sqls["gold_sector_baseline"]), set())

    def test_nothing_upstream_of_the_baseline_carries_a_report(self):
        upstream = ancestors("gold_sector_baseline", self.sqls)
        self.assertEqual(upstream, {"silver_probe_reading", "silver_rejects", "stg_bronze_events", "dim_sector"})
        self.assertFalse(upstream & REPORT_CARRYING, upstream & REPORT_CARRYING)

    def test_the_probe_silver_models_take_probe_rows_only(self):
        for name in ("silver_probe_reading", "silver_rejects"):
            self.assertIn("source_type = 'probe'", flat(self.sqls[name]), name)
            self.assertNotIn("'report'", self.sqls[name], name)

    def test_the_report_silver_models_take_report_rows_only(self):
        for name in REPORT_ONLY:
            self.assertIn("source_type = 'report'", flat(self.sqls[name]), name)
            self.assertNotIn("'probe'", self.sqls[name], name)

    def test_no_model_feeds_a_report_model_back_into_a_probe_one(self):
        """A report model may be read only by the gold models that score and fire on reports; no silver or baseline model reads one."""
        for name, sql in self.sqls.items():
            if name in ("gold_sector_reading", "gold_disturbance", "silver_force_report", "silver_report_rejects"):
                continue
            self.assertFalse(refs(sql) & REPORT_CARRYING, f"{name} reads {refs(sql) & REPORT_CARRYING}")

    def test_gold_disturbance_builds_its_probe_runs_from_probe_rows_only(self):
        sql = flat(self.sqls["gold_disturbance"])
        flagged = sql[sql.index("flagged as ("):sql.index("ranked as (")]
        self.assertIn("coalesce(source_type, 'probe') = 'probe'", flagged)
        self.assertNotIn("'report'", flagged)

    def test_gold_sector_reading_marks_the_source_of_every_row(self):
        sql = flat(self.sqls["gold_sector_reading"])
        self.assertIn("cast('probe' as string) as source_type", sql)
        self.assertIn("cast('report' as string) as source_type", sql)

    def test_a_report_never_reaches_the_probe_baseline_through_the_reading_table(self):
        """gold_sector_reading holds reports now; the baseline must not read it (it would count them)."""
        self.assertNotIn("gold_sector_reading", ancestors("gold_sector_baseline", self.sqls))
        self.assertNotIn("gold_sector_reading", refs(self.sqls["gold_sector_baseline"]))

    def test_the_lineage_helper_follows_chains_and_stops_at_seeds(self):
        fake = {"a": "{{ ref('b') }} {{ ref('seed') }}", "b": "{{ ref('c') }}", "c": "select 1"}
        self.assertEqual(ancestors("a", fake), {"b", "c", "seed"})
        self.assertEqual(refs(strip_comments("-- ref('x')\n/* ref('y') */ {# ref('z') #} {{ ref('w') }}")), {"w"})


if __name__ == "__main__":
    unittest.main()
