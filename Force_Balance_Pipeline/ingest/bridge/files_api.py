"""The bridge's only outbound HTTP path (docs 04, 05): the OAuth M2M token exchange and the Files API
PUT. Every request sends the project User-Agent (the default Python-urllib UA gets Cloudflare 1010).

Standard library only. Nothing here runs against the workspace unless the bridge is started in
workspace mode with the BRIDGE_DATABRICKS_* credentials; the offline tests drive it with stubs.
Secrets are never logged and never put in an exception message.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Force_Balance_Pipeline/0.1 (+https://github.com/jivejong/JiveRepo/tree/main/Force_Balance_Pipeline)"

SUCCESS = (200, 201, 204)
CONFLICT = 409

# OAuth scope requested for the token (doc 05). The force-bridge secret is a scoped secret; "files" is the
# Databricks API scope that covers the Files API (upload, list, delete, get status). Scopes are fixed when a
# secret is generated, so an unscoped secret would use "all-apis" instead (--oauth-scope).
DEFAULT_SCOPE = "files"


def normalise_scope(scope):
    """A scope parameter is a space-separated list of scope names; collapse whitespace, refuse empty."""
    scope = " ".join(str(scope).split())
    if not scope:
        raise ValueError("the OAuth scope must not be empty")
    return scope


def oauth_error_summary(raw):
    """': <error>: <error_description>' from an OAuth error body, truncated. These fields never contain
    credentials; anything unparsable is omitted rather than echoed."""
    try:
        parsed = json.loads(raw)
        parts = [str(parsed[k])[:200] for k in ("error", "error_description") if parsed.get(k)]
    except (ValueError, AttributeError, TypeError):
        return ""
    return (": " + ": ".join(parts)) if parts else ""


class TransportError(Exception):
    """A network-level failure (timeout, DNS, reset). The request may or may not have reached the server."""


class AuthError(Exception):
    """The OAuth token exchange failed. The message never contains the client secret or the token."""


def normalise_host(host):
    host = host.strip().rstrip("/")
    return host if host.startswith("http") else "https://" + host


def http_request(method, url, *, headers=None, data=None, timeout=60, opener=urllib.request.urlopen):
    """The one place a request is made. Always sends UA; a caller may not set its own User-Agent.
    Returns (status, body). HTTP error statuses are returned, not raised; network failures raise
    TransportError."""
    sent = {"User-Agent": UA}
    for key, value in (headers or {}).items():
        if key.lower() == "user-agent":
            raise ValueError("callers do not set User-Agent; http_request always sends the project UA")
        sent[key] = value
    request = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with opener(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise TransportError(type(e).__name__) from None


class TokenProvider:
    """OAuth machine-to-machine client credentials (doc 05): exchange the service principal's client id
    and secret at <host>/oidc/v1/token, cache the access token, and refresh it before it expires."""

    def __init__(self, host, client_id, client_secret, *, scope=DEFAULT_SCOPE, http=http_request,
                 clock=time.time, refresh_margin=300):
        self.host = normalise_host(host)
        self._client_id = client_id
        self._client_secret = client_secret
        self.scope = normalise_scope(scope)
        self._http = http
        self._clock = clock
        self.refresh_margin = refresh_margin
        self._token = None
        self._expires_at = 0.0
        self._lock = threading.Lock()
        self.exchanges = 0
        # What the last exchange returned, minus the token itself: safe to print and log.
        self.granted_scope = None      # the response's "scope" field, if the server sent one
        self.token_type = None
        self.expires_in = None
        self.response_fields = ()      # the names of the fields in the response

    def token(self):
        with self._lock:
            now = self._clock()
            if self._token is None or now >= self._expires_at - self.refresh_margin:
                self._exchange(now)
            return self._token

    def invalidate(self):
        with self._lock:
            self._token = None

    def _exchange(self, now):
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": self.scope}).encode()
        try:
            status, raw = self._http("POST", f"{self.host}/oidc/v1/token", data=body, headers={
                "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        except TransportError as e:
            raise AuthError(f"token exchange could not reach the workspace ({e})") from None
        if status != 200:
            raise AuthError(f"token exchange failed: HTTP {status}{oauth_error_summary(raw)} "
                            f"(requested scope {self.scope!r})")
        try:
            parsed = json.loads(raw)
            token = parsed["access_token"]
            expires_in = float(parsed.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthError("token exchange returned an unexpected response") from None
        self._token, self._expires_at = token, now + expires_in
        self.expires_in = expires_in
        self.token_type = parsed.get("token_type")
        self.granted_scope = parsed.get("scope")
        self.response_fields = tuple(sorted(parsed))
        self.exchanges += 1


class FilesApiUploader:
    """PUT one file into a UC volume (doc 05). overwrite=false, so the immutable, uniquely named files
    of doc 02 can never be replaced. put() returns the HTTP status; 409 means the path already exists."""

    def __init__(self, host, volume_path, tokens, *, http=http_request, timeout=60):
        self.host = normalise_host(host)
        self.volume_path = "/" + volume_path.strip("/")
        self.tokens = tokens
        self._http = http
        self.timeout = timeout

    def url(self, relpath):
        return (f"{self.host}/api/2.0/fs/files{self.volume_path}/"
                f"{urllib.parse.quote(relpath, safe='/=')}?overwrite=false")

    def put(self, relpath, data):
        for attempt in (1, 2):
            status, body = self._http("PUT", self.url(relpath), data=data, timeout=self.timeout, headers={
                "Authorization": f"Bearer {self.tokens.token()}", "Content-Type": "application/octet-stream"})
            if status == 401 and attempt == 1:  # an expired or revoked token: get a fresh one, once
                self.tokens.invalidate()
                continue
            return CONFLICT if already_exists(status, body) else status
        return status


# The Files API reference says only that "an error will be returned" when the path exists and overwrite is
# false; it does not name the HTTP status. Observed against the live workspace (2026-09-26): 409 with error
# code ALREADY_EXISTS. Databricks error bodies carry an error_code, so an "already exists" error is
# recognised by status 409 OR by that code, and reported to callers as 409.
ALREADY_EXISTS_CODES = ("ALREADY_EXISTS", "RESOURCE_ALREADY_EXISTS")


def already_exists(status, body):
    if status == CONFLICT:
        return True
    if status in (400, 412):
        try:
            return json.loads(body).get("error_code") in ALREADY_EXISTS_CODES
        except (ValueError, AttributeError, TypeError):
            return False
    return False
