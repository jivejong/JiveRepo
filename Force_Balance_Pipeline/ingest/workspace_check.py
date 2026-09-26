#!/usr/bin/env python3
"""First workspace calls for the collector bridge's credential (doc 05, Phase 2 step 5).

Uses the bridge's own service principal (force-bridge, from .env.bridge or the environment), requests the token
with the configured OAuth scope (default 'files'), and checks that the token can do what the bridge needs and
nothing more:

  1. the token exchange: prints the requested scope and what the response says was GRANTED (the response's
     "scope" field, if the server sends one), the token type, the lifetime and the response's field names. The
     access token itself is never printed.
  2. list the telemetry volume through the Files API                      -> allowed
  3. upload a tiny test file under _connectivity_check/ (overwrite=false) -> allowed (204)
  4. upload the same path again (overwrite=false)                         -> records the status for "already exists"
  5. get the file's metadata, delete the file, delete the directory       -> allowed; leaves nothing behind
  6. list the checkpoints volume                                          -> must be DENIED (narrow grant)
  7. call a non-files API (SQL warehouses)                                -> expected DENIED with a files-only scope

The test file sits under a directory that starts with an underscore, is deleted at the end (cleanup runs even if a
check fails), and no Auto Loader stream exists yet. Output shows API paths and statuses only: never a token, a
secret, the workspace host or the client id (error text is redacted).

Usage:  edge/.venv/Scripts/python.exe ingest/workspace_check.py [--env-file F] [--oauth-scope files]
"""
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "bridge"))

import bridge  # noqa: E402
import files_api  # noqa: E402
from files_api import AuthError, TransportError  # noqa: E402

CHECK_DIR = "_connectivity_check"
CHECKPOINTS_VOLUME = "/Volumes/force/raw/checkpoints"
NON_FILES_API = "/api/2.0/sql/warehouses"


class Reporter:
    """Prints results and never lets a secret, the host or the client id through."""

    def __init__(self, out, secrets):
        self.out, self.secrets, self.failures = out, [s for s in secrets if s], []

    def clean(self, text):
        text = str(text)
        for s in self.secrets:
            text = text.replace(s, "<redacted>")
        return text

    def line(self, text=""):
        print(self.clean(text), file=self.out, flush=True)

    def result(self, verdict, text):
        self.line(f"  [{verdict}] {text}")
        if verdict == "FAIL":
            self.failures.append(text)


def error_summary(body):
    try:
        parsed = json.loads(body)
        bits = [str(parsed[k])[:160] for k in ("error_code", "error", "message", "error_description") if parsed.get(k)]
        return " (" + "; ".join(bits) + ")" if bits else ""
    except (ValueError, AttributeError, TypeError):
        return ""


def run_checks(tokens, host, volume_path, http, out=sys.stdout, now=time.time):
    """Run the checks; returns the list of failures (empty means everything behaved as expected)."""
    report = Reporter(out, [host, urllib.parse.urlparse(host).netloc, tokens._client_id, tokens._client_secret])
    volume = "/" + volume_path.strip("/")

    def call(method, path, expect, *, data=None, note=""):
        """One Files API call with the bridge's token. Returns (status, body); network errors are reported."""
        try:
            status, body = http(method, host + path, data=data, headers={
                "Authorization": f"Bearer {tokens.token()}",
                **({"Content-Type": "application/octet-stream"} if data is not None else {})})
        except TransportError as e:
            report.result("FAIL", f"{method} {path.split('?')[0]} -> network error ({e})")
            return None, b""
        label = f"{method} {path.split('?')[0]} -> {status}{error_summary(body) if status >= 400 else ''}"
        if expect is None:
            report.result("INFO", label + (f"  {note}" if note else ""))
        else:
            report.result("PASS" if status in expect else "FAIL", label + (f"  {note}" if note else ""))
        return status, body

    report.line(f"1. token exchange (requested scope: {tokens.scope!r})")
    try:
        tokens.token()
    except AuthError as e:
        report.result("FAIL", str(e))
        report.line("stopping: without a token there is nothing else to check")
        return report.failures
    report.result("PASS", "HTTP 200, an access token was issued (not printed)")
    granted = tokens.granted_scope
    report.result("INFO", f"granted scope in the response: {granted!r}" if granted is not None
                  else "granted scope: the response has no 'scope' field")
    report.result("INFO", f"token_type={tokens.token_type!r} expires_in={tokens.expires_in:g}s "
                          f"response fields: {', '.join(tokens.response_fields)}")
    if granted is not None and set(str(granted).split()) != set(tokens.scope.split()):
        report.result("INFO", f"granted differs from requested: requested {tokens.scope!r}, granted {granted!r}")

    report.line("2. list the telemetry volume")
    status, body = call("GET", f"/api/2.0/fs/directories{volume}", (200, 204))
    if status == 200:
        try:
            report.result("INFO", f"{len(json.loads(body or b'{}').get('contents', []))} entries at the top level")
        except (ValueError, AttributeError):
            pass

    test_dir = f"{volume}/{CHECK_DIR}"
    test_file = f"{test_dir}/force-bridge-check-{bridge.new_ulid(int(now() * 1000))}.txt"
    file_url = f"/api/2.0/fs/files{urllib.parse.quote(test_file, safe='/=')}"
    uploaded = False
    try:
        report.line("3. upload a test file (overwrite=false)")
        status, _ = call("PUT", f"{file_url}?overwrite=false", (204, 200, 201), data=b'{"connectivity_check": true}\n')
        uploaded = status in (200, 201, 204)
        if uploaded:
            report.line("4. upload the same path again (overwrite=false): what does 'already exists' return?")
            call("PUT", f"{file_url}?overwrite=false", None, data=b'{"connectivity_check": "second"}\n',
                 note="<- the status the bridge must treat as 'already exists' (doc 05 OPEN)")
            status, _ = call("HEAD", file_url, (200,))
    finally:
        report.line("5. clean up")
        if uploaded:
            call("DELETE", file_url, (204, 200, 404))
            call("DELETE", f"/api/2.0/fs/directories{urllib.parse.quote(test_dir, safe='/=')}", (204, 200, 404))
    if uploaded:
        status, body = call("GET", f"/api/2.0/fs/directories{volume}", (200, 204))
        if status == 200:
            names = [c.get("name") for c in json.loads(body or b"{}").get("contents", [])]
            report.result("PASS" if CHECK_DIR not in names else "FAIL", f"{CHECK_DIR} is not left behind in the volume")

    report.line("6. the checkpoints volume must be denied (the grant is narrow)")
    status, _ = call("GET", f"/api/2.0/fs/directories{CHECKPOINTS_VOLUME}", (401, 403, 404))
    if status == 200:
        report.result("FAIL", "the checkpoints volume is readable: the grant is broader than doc 05 allows")

    report.line("7. a non-files API (SQL warehouses) should be denied by a files-only scope")
    status, _ = call("GET", NON_FILES_API, None)
    if status == 200:
        report.result("INFO", "the token can call a non-files API: this secret is not scoped to files only")
    elif status in (401, 403):
        report.result("PASS", "denied, as expected for a files-only scope")
    return report.failures


def main(argv=None):
    args = bridge.parse_args(argv)
    if args.local_dir:
        raise SystemExit("workspace_check needs workspace mode: drop --local-dir")
    uploader = bridge.build_uploader(args)
    print(f"requested scope {uploader.tokens.scope!r}; landing volume {uploader.volume_path}", flush=True)
    failures = run_checks(uploader.tokens, uploader.host, uploader.volume_path, files_api.http_request)
    print("\n" + ("ALL CHECKS BEHAVED AS EXPECTED" if not failures else f"{len(failures)} CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
