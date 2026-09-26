"""The Auto Loader notebook, checked statically (there is no Spark here): it is valid Python in the Databricks
notebook format, identical to the code block in doc 05, its columns are doc 03's bronze.events columns in order, its
paths are the documented ones, and the first-run, first-query and reset text in doc 05 matches the files."""
import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _support as S  # noqa: E402
import bridge  # noqa: E402

ROOT = S.ROOT
NOTEBOOK = ROOT / "ingest" / "autoloader_bronze.py"
CHECKPOINT_SQL = ROOT / "ingest" / "phase2_checkpoint.sql"
RULE = chr(10) + "---" + chr(10)  # a markdown horizontal rule on its own line


def doc(name):
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def doc05_auto_loader_block():
    section = doc("05-platform-setup.md").split("## Auto Loader ingestion", 1)[1]
    return re.search(r"```python\n(.*?)```", section, re.S).group(1)


def code_lines(text):
    """Comments and blank lines dropped, so cell separators and explanations do not matter."""
    return [l.rstrip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def doc03_bronze_columns():
    """The bronze.events columns in doc 03's order, one name per column."""
    section = re.split(re.escape(RULE), doc("03-data-model.md").split("### `bronze.events`", 1)[1], maxsplit=1)[0]
    names = []
    for row in re.findall(r"^\| ((?:`[^`|]+`(?:, )?)+) \|", section, re.M):
        names += re.findall(r"`([^`]+)`", row)
    return names


def normalise_sql(text):
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", text)).strip()


class NotebookFormatTests(unittest.TestCase):
    def test_it_is_a_databricks_notebook_source_file(self):
        text = NOTEBOOK.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# Databricks notebook source")
        self.assertGreaterEqual(text.count("# COMMAND ----------"), 3)

    def test_it_is_valid_python_and_uses_nothing_that_needs_a_credential(self):
        text = NOTEBOOK.read_text(encoding="utf-8")
        tree = ast.parse(text)
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                          and getattr(n, "module", "") not in ("pyspark.sql",) and not (isinstance(n, ast.ImportFrom) is False)])
        for word in ("dbutils.secrets", "token", "password", "DATABRICKS_", "client_secret", "Bearer"):
            self.assertNotIn(word, text)

    def test_the_code_cells_are_identical_to_the_doc_05_block(self):
        self.assertEqual(code_lines(NOTEBOOK.read_text(encoding="utf-8")), code_lines(doc05_auto_loader_block()))


class NotebookContentTests(unittest.TestCase):
    def setUp(self):
        self.text = NOTEBOOK.read_text(encoding="utf-8")

    def test_the_paths_are_the_documented_ones(self):
        self.assertIn(f'LANDING = "{bridge.DEFAULT_VOLUME_PATH}"', self.text)
        self.assertIn('CHECKPOINT = "/Volumes/force/raw/checkpoints/bronze_events"', self.text)
        self.assertIn('.toTable("force.bronze.events")', self.text)
        self.assertIn('f"{CHECKPOINT}/schema"', self.text)
        self.assertIn('f"{CHECKPOINT}/write"', self.text)
        doc05 = doc("05-platform-setup.md")
        self.assertIn("CREATE VOLUME IF NOT EXISTS force.raw.checkpoints;", doc05)
        self.assertIn("CREATE VOLUME IF NOT EXISTS force.raw.telemetry;", doc05)

    def test_the_select_is_doc_03s_column_order(self):
        select = re.search(r"\.select\((.*?)\)\n\)", self.text, re.S).group(1)
        columns = re.findall(r'"([^"]+)"', select)
        expected = doc03_bronze_columns()
        self.assertEqual(columns[:len(expected) - 1], expected[:-1], "every column but the last, in doc 03's order")
        self.assertEqual(columns, expected)
        self.assertEqual(len(columns), 16)

    def test_the_streaming_options_the_docs_require(self):
        for needle in ('.option("cloudFiles.format", "json")', '.option("cloudFiles.inferColumnTypes", "false")',
                       '.option("cloudFiles.schemaEvolutionMode", "rescue")', '.option("multiLine", "false")',
                       '.partitionBy("dt")', ".trigger(availableNow=True)", ".awaitTermination()",
                       '.option("mergeSchema", "true")'):
            self.assertIn(needle, self.text, needle)

    def test_the_two_new_envelope_columns_are_typed_by_schema_hints(self):
        self.assertIn('"is_synthetic BOOLEAN, synthetic_ingest_ts TIMESTAMP"', self.text)

    def test_types_are_cast_to_doc_03s_types(self):
        for needle in ('F.to_timestamp("event_time")', 'F.col("schema_version").cast("int")', 'F.to_date("dt")',
                       'F.col("hh").cast("int")', 'F.expr("try_parse_json(payload)")', 'F.col("_metadata.file_path")',
                       "F.current_timestamp()"):
            self.assertIn(needle, self.text, needle)

    def test_payload_is_parsed_from_the_raw_string_and_never_through_to_json(self):
        """With inferColumnTypes false, payload arrives as a STRING: to_json fails on it at analysis
        (DATATYPE_MISMATCH.INVALID_JSON_SCHEMA, recorded in doc 05). try_parse_json also keeps a malformed
        payload from failing the stream."""
        code = "\n".join(code_lines(self.text))
        self.assertNotIn("to_json", code)
        self.assertNotIn("F.parse_json", code)
        payload_lines = [l for l in code.splitlines() if '"payload"' in l and "withColumn" in l]
        self.assertEqual(payload_lines, ['      .withColumn("payload", F.expr("try_parse_json(payload)"))'])
        self.assertIn("try_parse_json", doc05_auto_loader_block())
        self.assertNotIn("to_json", doc05_auto_loader_block().replace("try_parse_json", ""))

    def test_doc_03_types_for_the_columns_it_casts(self):
        d3 = doc("03-data-model.md")
        for row in ("| `schema_version` | INT |", "| `event_time` | TIMESTAMP |", "| `payload` | VARIANT |",
                    "| `dt` | DATE (partition) |", "| `hh` | INT |"):
            self.assertIn(row, d3)


class FirstRunDocTests(unittest.TestCase):
    def setUp(self):
        self.doc05 = doc("05-platform-setup.md")

    def test_the_git_folder_steps_name_the_repo_and_the_notebook(self):
        self.assertIn("### First run, by hand", self.doc05)
        self.assertIn("`https://github.com/jivejong/JiveRepo`", self.doc05)
        self.assertIn("`Force_Balance_Pipeline/ingest/autoloader_bronze.py`", self.doc05)
        self.assertIn("`Force_Balance_Pipeline/ingest`", self.doc05)          # the sparse-checkout cone
        self.assertTrue(NOTEBOOK.exists())

    def test_the_first_query_in_doc_05_is_the_first_query_in_the_sql_file(self):
        block = re.search(r"### The first query.*?```sql\n(.*?)```", self.doc05, re.S).group(1)
        first_doc = normalise_sql(block).split(";")[0].strip()
        sql = CHECKPOINT_SQL.read_text(encoding="utf-8")
        first_file = normalise_sql(sql).split(";")[0].strip()  # comments (which contain semicolons) go first
        self.assertEqual(first_doc, first_file)
        self.assertIn("typeof(payload)", first_doc)
        self.assertIn("schema_of_variant(payload)", first_doc)
        self.assertIn("schema_of_variant_agg(payload)", normalise_sql(block))

    def test_the_null_payload_count_is_in_doc_05_and_the_sql_file_and_the_pass_criteria_use_it(self):
        section = self.doc05.split("### The first query", 1)[1].split("### Reset", 1)[0]
        block = re.search(r"```sql\n(.*?)```", section, re.S).group(1)
        query = "SELECT count(*) AS null_payloads FROM force.bronze.events WHERE payload IS NULL"
        self.assertIn(query, normalise_sql(block))
        self.assertIn(query, normalise_sql(CHECKPOINT_SQL.read_text(encoding="utf-8")))
        pass_and_fail = section.split("- **Pass:**", 1)[1]
        self.assertIn("`null_payloads` is 0", " ".join(pass_and_fail.split()))
        self.assertIn("NULL payload", " ".join(pass_and_fail.split()))

    def test_doc_05_records_the_first_run_result_and_no_longer_speculates(self):
        section = " ".join(self.doc05.split("### The first query", 1)[1].split("### Reset", 1)[0].split())
        for text in ("Recorded result (2026-09-26, first run)", "DATATYPE_MISMATCH.INVALID_JSON_SCHEMA",
                     "a STRING of raw JSON", "try_parse_json(payload)", "a malformed payload becomes NULL"):
            self.assertIn(text, section, text)
        self.assertNotIn("may instead give strings", section.lower())
        self.assertNotIn("parse_json(to_json(payload))`, works only", section)
        self.assertNotIn("`parse_json` not found", self.doc05)

    def test_the_first_query_is_read_only_and_uses_the_documented_table(self):
        sql = CHECKPOINT_SQL.read_text(encoding="utf-8")
        self.assertNotRegex(normalise_sql(sql).upper(), r"\b(DROP|DELETE|TRUNCATE|INSERT|UPDATE|ALTER)\b")
        self.assertIn("FROM force.bronze.events", sql)

    def test_the_reset_names_the_exact_table_and_checkpoint_and_needs_both(self):
        reset = self.doc05.split("### Reset, only if the first query fails", 1)[1].split(RULE, 1)[0]
        self.assertIn("DROP TABLE IF EXISTS force.bronze.events;", reset)
        self.assertIn('dbutils.fs.rm("/Volumes/force/raw/checkpoints/bronze_events", True)', reset)
        self.assertIn("databricks fs rm -r dbfs:/Volumes/force/raw/checkpoints/bronze_events", reset)
        self.assertIn("**and** delete the checkpoint", reset)
        self.assertIn("Do not delete anything in `/Volumes/force/raw/telemetry`", reset.replace("**", ""))

    def test_the_reset_never_touches_the_landing_files_or_uses_the_bridge_identity(self):
        reset = self.doc05.split("### Reset, only if the first query fails", 1)[1].split(RULE, 1)[0]
        commands = re.findall(r"```[a-z]*\n(.*?)```", reset, re.S)
        for command in commands:
            self.assertNotIn("/Volumes/force/raw/telemetry", command)
            self.assertNotIn("force-bridge", command)

    def test_the_checkpoint_sql_file_has_no_credentials(self):
        text = CHECKPOINT_SQL.read_text(encoding="utf-8")
        for word in ("token", "secret", "password"):
            self.assertNotIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
