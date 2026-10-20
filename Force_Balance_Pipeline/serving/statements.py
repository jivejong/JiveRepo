"""The serving puller's read-only Statement Execution API client (doc 05 "Serving (Phase 7)": reads, chunks, guard).

A subclass of agent.dbsql.StatementClient: polling, retries, type conversion and the statement counter are the agent's own. This adds
only (1) following next_chunk_internal_link, so a result larger than one chunk is joined in order; (2) a row cap (max_rows, default
50000, ResultTooLarge once exceeded); (3) an optional expected_types check against the manifest (SchemaMismatch); (4) a read-only
guard: the first keyword must be SELECT or WITH, no statement may hold a semicolon except a trailing one, and none of INSERT, UPDATE,
DELETE, MERGE, CREATE, DROP, ALTER, TRUNCATE, COPY, GRANT, REVOKE, REPLACE, OPTIMIZE or VACUUM may appear as a word outside quotes and
comments (NotReadOnly, raised before any HTTP call). It has no write method. It is a keyword check, not a SQL parser, and defense in
depth only: a read-only principal on the warehouse is the real control.
"""
import re

from agent.dbsql import ResultTooLarge, StatementClient, StatementError, convert

DEFAULT_MAX_ROWS = 50000
MAX_CHUNKS = 1000                                   # a safety stop against a link that never ends


class NotReadOnly(StatementError):
    """The statement is not a single SELECT or WITH statement; nothing was sent."""


class SchemaMismatch(StatementError):
    """The result's columns differ from expected_types."""


def _scrub(sql):
    """The SQL with comments replaced by a space and quoted text emptied, so keywords and semicolons are seen only in code."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


DENIED = frozenset(("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "ALTER", "TRUNCATE", "COPY", "GRANT", "REVOKE",
                    "REPLACE", "OPTIMIZE", "VACUUM"))


def check_read_only(sql):
    """Raise NotReadOnly unless sql is one SELECT or WITH statement (one trailing semicolon is allowed)."""
    code = _scrub(sql).strip()
    if code.endswith(";"):
        code = code[:-1].rstrip()
    if ";" in code:
        raise NotReadOnly("the statement holds more than one statement")
    m = re.match(r"[A-Za-z]+", code)
    if not m or m.group(0).upper() not in ("SELECT", "WITH"):
        raise NotReadOnly("the first keyword is not SELECT or WITH")
    found = DENIED.intersection(re.findall(r"[A-Za-z_]+", code.upper()))
    if found:
        raise NotReadOnly(f"the statement uses {sorted(found)[0]}")


class ServingStatementClient(StatementClient):
    def __init__(self, *args, max_rows=DEFAULT_MAX_ROWS, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_rows = max_rows
        self._expected = None
        self._cap = max_rows

    def execute(self, sql, params=None, *, expected_types=None, max_rows=None):
        check_read_only(sql)
        if expected_types is None and max_rows is None:
            return super().execute(sql, params)
        saved = (self._expected, self._cap)
        self._expected = expected_types if expected_types is not None else self._expected
        self._cap = max_rows if max_rows is not None else self._cap
        try:
            return super().execute(sql, params)
        finally:
            self._expected, self._cap = saved

    def read(self, sql, params=None, *, expected_types=None, max_rows=None):
        check_read_only(sql)
        self._expected, self._cap = expected_types, self.max_rows if max_rows is None else max_rows
        try:
            return super().read(sql, params)
        finally:
            self._expected, self._cap = None, self.max_rows

    def write(self, sql, params=None):
        raise NotReadOnly("the serving client has no write method")

    def _rows(self, response):
        """Shadows the agent's static method: the same conversion, plus the chunks, the row cap and the type check."""
        result = response.get("result") or {}
        columns = ((response.get("manifest") or {}).get("schema") or {}).get("columns") or []
        names = [c["name"] for c in columns]
        types = [c.get("type_name") for c in columns]
        if self._expected is not None:
            self._compare(dict(zip(names, types)))
        data = list(result.get("data_array") or [])
        self._enforce_cap(len(data))
        link, chunks = result.get("next_chunk_internal_link"), 0
        while link:
            chunks += 1
            if chunks > MAX_CHUNKS:
                raise ResultTooLarge(f"the result has more than {MAX_CHUNKS} chunks")
            chunk = self._request("GET", link)
            data.extend(chunk.get("data_array") or [])
            self._enforce_cap(len(data))
            link = chunk.get("next_chunk_internal_link")
        return [{n: convert(v, t) for n, t, v in zip(names, types, row)} for row in data]

    def _enforce_cap(self, count):
        if count > self._cap:
            raise ResultTooLarge(f"the result has more than {self._cap} rows")

    def _compare(self, actual):
        for name, want in self._expected.items():
            if name not in actual:
                raise SchemaMismatch(f"column {name}: expected {want}, missing from the result")
            if (actual[name] or "").upper() != (want or "").upper():
                raise SchemaMismatch(f"column {name}: expected {want}, got {actual[name]}")
        for name, got in actual.items():
            if name not in self._expected:
                raise SchemaMismatch(f"column {name}: expected nothing, got {got}")
