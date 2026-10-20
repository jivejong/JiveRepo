"""Tests for serving/statements.py (doc 05 "Serving (Phase 7)"): chunks, the row cap, the type check and the read-only guard,
against a fake transport. No network."""
import unittest

from agent.dbsql import ResultTooLarge
from fake_http import FakeOpener
from serving import statements

COLUMNS = [{"name": "n", "type_name": "INT"}, {"name": "s", "type_name": "STRING"}]
CHUNK = "/api/2.0/sql/statements/ST1/result/chunks/"


def rows(start, count):
    return [[str(i), f"v{i}"] for i in range(start, start + count)]


def make(first_rows, later=(), columns=COLUMNS):
    """A fake warehouse: the POST returns first_rows plus a link to the first of `later` chunks; each GET returns the next one."""
    def handler(method, url, body):
        if method == "POST":
            result = {"data_array": first_rows}
            if later:
                result["next_chunk_internal_link"] = CHUNK + "1"
            return 200, {"statement_id": "ST1", "status": {"state": "SUCCEEDED"},
                         "manifest": {"schema": {"columns": columns}}, "result": result}
        index = int(url.rsplit("/", 1)[1])
        chunk = {"data_array": later[index - 1]}
        if index < len(later):
            chunk["next_chunk_internal_link"] = CHUNK + str(index + 1)
        return 200, chunk
    opener = FakeOpener(handler)
    client = statements.ServingStatementClient("example.invalid", "t", "w", opener=opener, sleep=lambda s: None, monotonic=lambda: 0.0)
    return client, opener


class Chunks(unittest.TestCase):
    def test_three_chunks_are_joined_in_order_and_converted(self):
        client, opener = make(rows(0, 2), [rows(2, 2), rows(4, 1)])
        got = client.read("SELECT n, s FROM t")
        self.assertEqual([r["n"] for r in got], [0, 1, 2, 3, 4])
        self.assertEqual(got[4], {"n": 4, "s": "v4"})
        self.assertEqual(client.statements, 1)
        self.assertEqual([m for m, _ in opener.calls], ["POST", "GET", "GET"])

    def test_a_single_chunk_result_needs_no_get(self):
        client, opener = make(rows(0, 3))
        self.assertEqual(len(client.read("select 1")), 3)
        self.assertEqual(len(opener.calls), 1)


class MaxRows(unittest.TestCase):
    def test_50001_rows_over_two_chunks_raise(self):
        client, _ = make(rows(0, 30000), [rows(30000, 20001)])
        with self.assertRaises(ResultTooLarge):
            client.read("select 1")

    def test_exactly_50000_rows_pass(self):
        client, _ = make(rows(0, 30000), [rows(30000, 20000)])
        try:
            got = client.read("select 1")
        except ResultTooLarge:
            self.fail("exactly 50000 rows must pass")
        self.assertEqual(len(got), 50000)

    def test_a_per_call_cap_overrides_the_default(self):
        client, _ = make(rows(0, 5))
        with self.assertRaises(ResultTooLarge):
            client.read("select 1", max_rows=4)
        self.assertEqual(len(client.read("select 1")), 5)


class ExpectedTypes(unittest.TestCase):
    def test_a_match_passes(self):
        client, _ = make(rows(0, 1))
        self.assertEqual(len(client.read("select 1", expected_types={"n": "INT", "s": "STRING"})), 1)

    def test_a_missing_column_is_named(self):
        client, _ = make(rows(0, 1))
        with self.assertRaises(statements.SchemaMismatch) as caught:
            client.read("select 1", expected_types={"n": "INT", "s": "STRING", "gone": "DOUBLE"})
        self.assertIn("gone", str(caught.exception))

    def test_an_extra_column_is_named(self):
        client, _ = make(rows(0, 1))
        with self.assertRaises(statements.SchemaMismatch) as caught:
            client.read("select 1", expected_types={"n": "INT"})
        self.assertIn("s", str(caught.exception))

    def test_a_retyped_column_names_both_types(self):
        client, _ = make(rows(0, 1))
        with self.assertRaises(statements.SchemaMismatch) as caught:
            client.read("select 1", expected_types={"n": "BIGINT", "s": "STRING"})
        text = str(caught.exception)
        self.assertTrue("n" in text and "BIGINT" in text and "INT" in text)


class ReadOnly(unittest.TestCase):
    def test_selects_and_withs_pass(self):
        for sql in ("  -- c\nselect 1", "/* x */ WITH a AS (select 1) select * from a", "SELECT", "select 1;", "select 'a;b'",
                    "select 1 -- ; drop table x", 'select "x;y" from t', "select 'drop' as x", "select last_update, created_at from t"):
            client, opener = make(rows(0, 1))
            try:
                client.read(sql)
            except statements.NotReadOnly:
                self.fail(f"should pass the guard: {sql!r}")
            self.assertEqual(len(opener.calls), 1, sql)

    def test_anything_else_is_refused_before_any_http(self):
        for sql in ("update t set a = 1", "  merge into t", "delete from t", "insert into t values (1)", "create table t (a int)",
                    "select 1; drop table x", "select 1; select 2", "", "-- only a comment", "drop table x",
                    "WITH a AS (select 1) INSERT INTO t select * from a", "select 1 from t where x in (select 1) union select optimize"):
            client, opener = make(rows(0, 1))
            with self.assertRaises(statements.NotReadOnly, msg=sql):
                client.read(sql)
            self.assertEqual(opener.calls, [], sql)
            self.assertEqual(client.statements, 0, sql)

    def test_execute_accepts_expected_types(self):
        client, _ = make(rows(0, 1))
        self.assertEqual(len(client.execute("select 1", None, expected_types={"n": "INT", "s": "STRING"})), 1)
        with self.assertRaises(statements.SchemaMismatch):
            client.execute("select 1", None, expected_types={"n": "INT"})

    def test_execute_is_guarded_too_and_write_does_not_exist(self):
        client, opener = make(rows(0, 1))
        with self.assertRaises(statements.NotReadOnly):
            client.execute("delete from t")
        with self.assertRaises(statements.NotReadOnly):
            client.write("select 1")
        self.assertEqual(opener.calls, [])


if __name__ == "__main__":
    unittest.main()
