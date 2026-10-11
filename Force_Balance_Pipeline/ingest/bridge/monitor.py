"""Dead-man's-switch pings from the bridge (doc 05, "Monitoring").

Two of the three freshness signals are sent from here, to an external dead-man's-switch (Healthchecks.io), so that alerting
shares no failure domain with the desktop's own services or with Databricks quota:

  S1  bridge liveness   a ping every 5 minutes while the MQTT client is connected, whether or not any data arrives
  S3  data landed       a ping after an upload that landed at least one line, at most one per 5 minutes

(S2, Job 1 liveness, is sent by Databricks, not by this process.) A check goes late when its ping is overdue and down when the
grace time has also passed; silence is the alarm, so nothing here needs to report a failure.

The rules this module exists to keep:
  * Monitoring must never take the bridge down or slow it. Pings go through a small bounded queue to one daemon thread; a full
    queue DROPS the ping; every exception is caught; nothing here raises into, or waits for, the MQTT, upload or acknowledgement
    paths.
  * A ping URL is credential-like (anyone holding it can mark a check up). It is never printed, logged or put in an error
    message: failures are described by exception type or HTTP status only.
  * The sender is files_api.http_request called with no headers, so the only header is the project User-Agent (the repo's one
    outbound HTTP path, doc 05). It has no access to, and never imports, the Databricks token provider.
  * Missing or invalid URLs disable that signal, with one warning that names the setting and never its value.
"""
import queue
import threading
import time
from urllib.parse import urlparse

from files_api import TransportError, http_request

PING_TIMEOUT_S = 10                  # connect + read; a ping that takes longer is abandoned
QUEUE_SIZE = 16                      # pings waiting for the sender thread; beyond this they are dropped
S1_INTERVAL_S = 300                  # S1: one ping every 5 minutes while connected
S3_MIN_INTERVAL_S = 300              # S3: at most one ping every 5 minutes
FAILURE_LOG_INTERVAL_S = 900         # a failing ping is logged at most once every 15 minutes

SIGNAL_KEYS = {"S1": "BRIDGE_PING_S1_URL", "S3": "BRIDGE_PING_S3_URL"}


class PingFailed(Exception):
    """The server answered, but not with a 2xx."""


def describe(exc):
    """What went wrong, without the URL: an HTTP status or an exception type, never str(exc)."""
    if isinstance(exc, PingFailed):
        return str(exc)                          # "HTTP <status>"
    if isinstance(exc, TransportError):
        return f"network error ({exc})"          # files_api puts only the underlying exception's TYPE NAME in the message
    return type(exc).__name__


def send_ping(url, timeout=PING_TIMEOUT_S, http=http_request):
    """GET the URL through files_api.http_request, passing no headers: it adds the project User-Agent and nothing else. Raises
    PingFailed on a non-2xx answer and files_api.TransportError on a network failure."""
    status, _ = http("GET", url, timeout=timeout)
    if not 200 <= status < 300:
        raise PingFailed(f"HTTP {status}")


def load_ping_urls(env):
    """({signal: url}, [warnings]) from a mapping holding BRIDGE_PING_S1_URL / BRIDGE_PING_S3_URL. A missing, empty or invalid
    value disables that signal; the warning names the setting, never the value."""
    urls, warnings, unset = {}, [], []
    for signal, key in SIGNAL_KEYS.items():
        value = (env.get(key) or "").strip()
        if not value:
            unset.append((signal, key))
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            warnings.append(f"{key} is not an https URL: {signal} monitoring is disabled")
            continue
        urls[signal] = value
    if len(unset) == len(SIGNAL_KEYS):
        warnings.insert(0, " and ".join(key for _, key in unset) + " are not set: monitoring is disabled")
    else:
        warnings = [f"{key} is not set: {signal} monitoring is disabled" for signal, key in unset] + warnings
    return urls, warnings


class Monitor:
    def __init__(self, urls, *, sender=send_ping, clock=time.time, log=print, queue_size=QUEUE_SIZE,
                 s1_interval=S1_INTERVAL_S, s3_min_interval=S3_MIN_INTERVAL_S,
                 failure_log_interval=FAILURE_LOG_INTERVAL_S, start=True):
        self.urls = dict(urls)
        self.sender, self.clock, self.log = sender, clock, log
        self.s1_interval, self.s3_min_interval = s1_interval, s3_min_interval
        self.failure_log_interval = failure_log_interval
        self.stats = {"sent": 0, "failed": 0, "dropped": 0}
        self._queue = queue.Queue(maxsize=queue_size)
        self._last = {"S1": None, "S3": None}          # when each signal was last queued
        self._last_failure_log = None
        self._suppressed = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name="monitor", daemon=True)
        if start and self.urls:
            self._thread.start()

    @property
    def enabled(self):
        return bool(self.urls)

    # ---- called from the bridge: all of these return at once and never raise ----------------------------
    def tick(self, connected):
        """Called about four times a second from run()'s main loop. S1: due every s1_interval while connected."""
        try:
            if "S1" in self.urls and connected:
                self._maybe_queue("S1", self.s1_interval)
        except Exception:
            pass

    def landed(self, lines):
        """Called after an upload that landed `lines` lines. S3: at most one ping per s3_min_interval."""
        try:
            if "S3" in self.urls and lines >= 1:
                self._maybe_queue("S3", self.s3_min_interval)
        except Exception:
            pass

    def close(self):
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def drain(self, timeout=5.0):
        """Wait until every queued ping has been sent or has failed (tests). True when idle."""
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), done.set()), daemon=True)
        waiter.start()
        return done.wait(timeout)

    # ---- internals -----------------------------------------------------------------------------------
    def _maybe_queue(self, signal, interval):
        now = self.clock()
        with self._lock:
            last = self._last[signal]
            if last is not None and now - last < interval:
                return
        try:
            self._queue.put_nowait(signal)             # never put(): a full queue drops the ping, it does not wait
        except queue.Full:
            with self._lock:
                self.stats["dropped"] += 1
            return
        with self._lock:
            self._last[signal] = now

    def _worker(self):
        while True:
            signal = self._queue.get()
            try:
                if signal is None:
                    return
                try:
                    self.sender(self.urls[signal], timeout=PING_TIMEOUT_S)
                    with self._lock:
                        self.stats["sent"] += 1
                except Exception as exc:               # every failure, of every kind, ends here
                    self._failed(signal, exc)
            finally:
                self._queue.task_done()

    def _failed(self, signal, exc):
        now = self.clock()
        with self._lock:
            self.stats["failed"] += 1
            if self._last_failure_log is not None and now - self._last_failure_log < self.failure_log_interval:
                self._suppressed += 1
                return
            suppressed, self._suppressed, self._last_failure_log = self._suppressed, 0, now
        try:
            self.log(f"bridge: monitoring ping {signal} failed ({describe(exc)}); {suppressed} earlier failure(s) not "
                     f"logged; further failures are logged at most every {self.failure_log_interval // 60} minutes")
        except Exception:
            pass


def build_monitor(env, *, log=print, **options):
    """A Monitor for the ping URLs in `env`, or a disabled one (no thread) with one warning per missing or invalid setting."""
    urls, warnings = load_ping_urls(env)
    for warning in warnings:
        try:
            log(f"bridge: {warning}")
        except Exception:
            pass
    return Monitor(urls, log=log, **options)
