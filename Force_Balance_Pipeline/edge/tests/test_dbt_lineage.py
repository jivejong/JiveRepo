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


class RecursionLimitTests(unittest.TestCase):
    """gold_disturbance walks each sector with two recursive CTEs. The engine stops the whole build when a chain is longer than a CTE's limit (default
    100), and each level costs about 0.95 s (measured: depth 150 in 195 s, depth 400 in 433 s) against Job 1's 2400 s task timeout. So both walks take
    their limit from ONE var, and a data test errors at 80% of it. A refactor that drops the clause from a walk, hard-codes a number, or moves the
    limit outside the measured range fails here by name, before any build."""

    def setUp(self):
        self.gold = flat(model_sql()["gold_disturbance"])
        self.alarm_raw = (DBT / "tests" / "assert_walk_depth_below_limit.sql").read_text(encoding="utf-8")
        self.alarm = flat(strip_comments(self.alarm_raw))
        self.limit = int(re.search(r"^\s*walk_recursion_limit:\s*(\d+)\s*(?:#.*)?$", (DBT / "dbt_project.yml").read_text(encoding="utf-8"), re.M).group(1))

    def test_both_recursive_walks_take_their_limit_from_the_var(self):
        for name in ("walked", "report_walk"):
            self.assertIn(f"{name} MAX RECURSION LEVEL {{{{ var('walk_recursion_limit') }}}} as (", self.gold, name)
            body = self.gold[self.gold.index(f"{name} MAX RECURSION LEVEL"):]
            self.assertIn(f"from {name} ", body, f"{name} is no longer recursive: update this test")

    def test_there_are_exactly_two_limits(self):
        """A third recursive CTE added without a limit would run at the engine's default of 100."""
        self.assertEqual(self.gold.count("MAX RECURSION LEVEL"), 2)
        self.assertEqual(self.gold.count("with recursive"), 1)

    def test_the_limit_is_above_the_depth_tests_and_inside_the_task_timeout(self):
        """Above 150, the depth the unit tests prove completes (the engine's default of 100 would fail them); at most 1000: two full walks at
        0.95 s a level are 1900 s, which with the rest of Job 1's build (about 450 s) is already past the 2400 s task timeout."""
        self.assertGreater(self.limit, 150)
        self.assertLessEqual(self.limit, 1000)

    def test_the_depth_alarm_reads_the_same_var_at_80_percent_and_is_an_error(self):
        self.assertIn("{% set alarm_depth = (var('walk_recursion_limit') * 8) // 10 %}", self.alarm)
        self.assertIn("{{ config(severity='error') }}", self.alarm)
        self.assertEqual(self.alarm.count("{{ alarm_depth }}"), 4)         # the label column and the having clause of each walk

    def test_the_depth_alarm_checks_both_walks_from_the_same_inputs(self):
        for label in ("'probe walk' as walk", "'report walk' as walk"):
            self.assertIn(label, self.alarm)
        for model in ("gold_sector_reading", "silver_force_report", "gold_disturbance"):
            self.assertIn(f"ref('{model}')", self.alarm, model)

    def test_the_depth_unit_tests_are_there_and_deeper_than_the_default_limit(self):
        text = (MODELS / "gold" / "_gold__unit_tests.yml").read_text(encoding="utf-8")
        for walk in ("report", "probe"):
            found = re.findall(rf"name: unit_test_disturbance_{walk}_walk_completes_at_depth_(\d+)", text)
            self.assertEqual(len(found), 1, walk)
            self.assertGreater(int(found[0]), 100, walk)


if __name__ == "__main__":
    unittest.main()
