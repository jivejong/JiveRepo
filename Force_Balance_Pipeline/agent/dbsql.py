"""Databricks SQL Statement Execution API client (doc 06 "Runtime" and "Failure handling").

One statement per call, with named parameters. Every request goes through files_api.http_request (the repo's one outbound HTTP
path); the opener, sleep and monotonic clock are injected so tests run without a network or real time.

Limits (doc 06): a statement that is still running after STATEMENT_TIMEOUT_SECONDS is cancelled; a read gets 2 attempts and a
write 3, retrying only errors that can succeed on a second try (a transient HTTP error or a Delta concurrent-modification
error). Error messages never carry the token or the request body.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from agent.config import ROOT

sys.path.insert(0, str(ROOT / "ingest" / "bridge"))      # files_api: the one outbound HTTP path

import files_api  # noqa: E402

PARAM_TYPES = ("STRING", "DOUBLE", "BOOLEAN", "BIGINT", "INT", "TIMESTAMP")
POLL_SECONDS = 5
STATEMENT_TIMEOUT_SECONDS = 15 * 60
READ_BACKOFF_SECONDS = (5,)             # 2 attempts
WRITE_BACKOFF_SECONDS = (5, 15)         # 3 attempts: 5 s after the first failure, 15 s after the second


class StatementError(Exception):
    retryable = False


class StatementFailed(StatementError):
    def __init__(self, error_code, message, retryable):
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


class TransientHTTPError(StatementError):
    retryable = True


class StatementTimeout(StatementError):
    pass


class ResultTooLarge(StatementError):
    pass


def iso_z(moment):
    """An aware datetime as ISO-8601 UTC with a Z suffix and millisecond precision."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_timestamp(text):
    """A warehouse timestamp string as an aware UTC datetime (no zone means UTC)."""
    m = _TIMESTAMP.match(text.strip())
    if not m:
        raise ValueError(f"unrecognised timestamp {text!r}")
    day, clock, fraction, zone = m.groups()
    micro = (fraction or "0")[:6].ljust(6, "0")
    moment = datetime.fromisoformat(f"{day}T{clock}.{micro}")
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return (moment - sign * offset).replace(tzinfo=timezone.utc)
    return moment.replace(tzinfo=timezone.utc)


def convert(value, type_name):
    """One cell of a JSON_ARRAY result as a Python value, by the column's type_name."""
    if value is None:
        return None
    kind = (type_name or "").upper()
    if kind == "BOOLEAN":
        return value if isinstance(value, bool) else str(value).strip().lower() == "true"
    if kind in ("INT", "BIGINT", "SHORT", "LONG", "TINYINT", "BYTE"):
        return int(value)
    if kind in ("DOUBLE", "FLOAT", "DECIMAL"):
        return float(value)
    if kind == "TIMESTAMP":
        return parse_timestamp(value)
    if kind in ("ARRAY", "MAP", "STRUCT", "VARIANT"):
        return json.loads(value) if isinstance(value, str) else value
    return value


def _parameter(name, value, type_name):
    if type_name not in PARAM_TYPES:
        raise ValueError(f"parameter {name!r}: type must be one of {PARAM_TYPES}")
    entry = {"name": name, "type": type_name}
    if value is None:
        return entry                                    # no "value" key: a typed NULL
    if isinstance(value, bool):
        entry["value"] = "true" if value else "false"
    elif isinstance(value, datetime):
        entry["value"] = iso_z(value)
    else:
        entry["value"] = str(value)
    return entry


class StatementClient:
    def __init__(self, host, token, warehouse_id, *, opener, sleep, monotonic):
        self._host = files_api.normalise_host(host)
        self._token = token
        self._warehouse_id = warehouse_id
        self._opener = opener
        self._sleep = sleep
        self._monotonic = monotonic

    def _request(self, method, path, payload=None):
        url = f"{self._host}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        try:
            status, body = files_api.http_request(method, url, headers=headers, data=data, opener=self._opener)
        except files_api.TransportError as e:
            raise TransientHTTPError(f"transport error: {e}") from None
        if status == 429 or status >= 500:
            raise TransientHTTPError(f"HTTP {status}")
        if not 200 <= status < 300:
            raise StatementFailed(f"HTTP_{status}", f"HTTP {status}", False)
        try:
            parsed = json.loads(body)
        except ValueError:
            raise StatementFailed("BAD_RESPONSE", "the response is not JSON", False) from None
        if not isinstance(parsed, dict):
            raise StatementFailed("BAD_RESPONSE", "the response is not a JSON object", False)
        return parsed

    def execute(self, sql, params=None):
        """Run one statement; the rows as a list of dicts. Raises a StatementError subclass on any failure."""
        body = {
            "warehouse_id": self._warehouse_id,
            "statement": sql,
            "parameters": [_parameter(name, value, type_name) for name, (value, type_name) in (params or {}).items()],
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        started = self._monotonic()
        response = self._request("POST", "/api/2.0/sql/statements", body)
        statement_id = response.get("statement_id")
        while (response.get("status") or {}).get("state") in ("PENDING", "RUNNING"):
            if self._monotonic() - started >= STATEMENT_TIMEOUT_SECONDS:
                try:
                    self._request("POST", f"/api/2.0/sql/statements/{statement_id}/cancel", {})
                except StatementError:
                    pass
                raise StatementTimeout(f"statement still running after {STATEMENT_TIMEOUT_SECONDS} s; cancelled")
            self._sleep(POLL_SECONDS)
            response = self._request("GET", f"/api/2.0/sql/statements/{statement_id}")
        status = response.get("status") or {}
        state = status.get("state")
        if state == "SUCCEEDED":
            return self._rows(response)
        error = status.get("error") or {}
        code = str(error.get("error_code") or state or "UNKNOWN")
        message = str(error.get("message") or "")
        retryable = "CONCURRENT" in (code + message).upper()
        raise StatementFailed(code, message, retryable)

    @staticmethod
    def _rows(response):
        result = response.get("result") or {}
        if result.get("next_chunk_internal_link"):
            raise ResultTooLarge("the result has more than one chunk")
        columns = ((response.get("manifest") or {}).get("schema") or {}).get("columns") or []
        names = [c["name"] for c in columns]
        types = [c.get("type_name") for c in columns]
        return [{n: convert(v, t) for n, t, v in zip(names, types, row)} for row in (result.get("data_array") or [])]

    def _with_retries(self, sql, params, backoff):
        for delay in backoff:
            try:
                return self.execute(sql, params)
            except StatementError as e:
                if not e.retryable:
                    raise
                self._sleep(delay)
        return self.execute(sql, params)

    def read(self, sql, params=None):
        return self._with_retries(sql, params, READ_BACKOFF_SECONDS)

    def write(self, sql, params=None):
        return self._with_retries(sql, params, WRITE_BACKOFF_SECONDS)
