# Phase 3 results

Checks for the four modes and hardware (doc 07, Phase 3), recorded from command output only. A cell stays
empty until the output that supports it has been pasted in. Nothing here is inferred. Step 3 (the LAN
broker, desktop-only) is recorded here; Steps 4-5 (the Pi, the 45-minute checkpoint) follow in later
sections once they have run.

## Step 3: the LAN broker on the desktop

### Setup and the two issues found

| # | Item | Found | Fix | Status |
|---|---|---|---|---|
| S1 | Password file crash loop | Mosquitto 2.1.2 refused to start against the password file mounted straight from Windows: `Unable to open pwfile`. The container's `mosquitto` user cannot read a file the bind mount hands it as root-owned. | Copied the password file into a Docker volume (`force-mosquitto-secrets`), `chown mosquitto:mosquitto`, `chmod 0600`, mounted at `/mosquitto/secrets` instead of the Windows bind mount. | **Fixed and documented** (doc 05, "The broker on the desktop"). |
| S2 | ACL group-ownership warning | The same version warns that `/mosquitto/config/acl` (still a Windows bind mount) has the wrong group and that "future versions will refuse to load this file". | Not applied. Proposed: the same volume-copy treatment as S1, or pin the image to a version that still accepts it. | **OPEN** (doc 05, same section). |
| S3 | LAN exposure | The broker's TCP port was reachable from the LAN despite a Pi-only Windows Firewall rule: TLS connection attempts from elsewhere reached the broker at the TCP level (visible in Mosquitto's own log, though every client there shows as `172.17.0.1` — Docker's port relay hides the real source). Docker Desktop Backend had installed two enabled Allow rules on the Private profile; Windows combines Allow rules rather than picking the most specific one, so those rules overrode the Pi-only scoping. | Identified and disabled the two Docker Desktop Backend Allow rules (Private profile) — not Block, which would also have stopped the Pi. The Public-profile Block rules were left unchanged. | **Fixed and documented** (doc 05, "The firewall"). |

### Verification

| # | Check | Expected | Actual |
|---|---|---|---|
| V1 | Binding (`netstat`) | `127.0.0.1:1883` and `<DESKTOP_IP>:1883` LISTENING, no `0.0.0.0` | **Pass.** `127.0.0.1:1883` and `192.168.0.110:1883`, both LISTENING, owned by `com.docker.backend` (there is no `mosquitto.exe` on Windows — Docker's own process holds the port). No `0.0.0.0`. |
| V2 | `docker port force-mosquitto` | The same two addresses | `127.0.0.1 -> 1883`, `192.168.0.110 -> 1883`. |
| V3 | `edge/mqtt_check.py` on the desktop, all three users | Every check PASS | **BROKER CHECKS PASSED**, all 11: anonymous refused; wrong password refused; probe-01 connects, may subscribe `force/control/probe-01`, and publishes `force/telemetry/probe-01` (acknowledged); force-bridge and operator connect; and the four ACL-by-delivery checks — probe-01's telemetry reaches force-bridge; operator's control message reaches probe-01; probe-01 publishing to the control topic does not reach operator; force-bridge publishing to the control topic does not reach probe-01; operator publishing telemetry does not reach force-bridge. |
| V4 | Third-device TCP probe, after the S3 fix | `Test-NetConnection <DESKTOP_IP> -Port 1883` from another Windows machine on the LAN: `TcpTestSucceeded` `False` | **Pass.** `TcpTestSucceeded` = `False`. Firewall enabled on all profiles; the desktop's Ethernet adapter is on the Private profile. |

## Desktop cadence run (closes the Phase 2 deviation)

Phase 2's checkpoint ran 3 scans at a 60 s cadence instead of the documented 45 minutes (doc 07, deviation
note, 2026-09-26); the real 15-minute cadence was deferred to Phase 3. This run closes that deviation. It
used the **desktop simulator standing in for the Pi** — `edge/probe_sim.py --live`, `source_id probe-01`,
`--assume-clock-synced` (Windows has no `timedatectl`; never used on the Pi itself) — against the LAN
broker above, with the bridge in workspace mode (`--exit-after-files 3`) and fault injection off
(`--fault-rate 0`). **These 180 rows are from the desktop simulator, not the Pi.**

| # | Check | Expected | Actual |
|---|---|---|---|
| C1 | 3 scans at the real 15-minute cadence | 3 files, 60 events each, landed about 15 minutes apart | **Pass.** `received=180 files=3 events=180 bytes=71930 dead_lettered=0 retries=0 failed_batches=0`. |

Files landed:

| Landed at (`_ingest_ts`) | Path | Lines | Bytes |
|---|---|---|---|
| 2026-09-27T18:45:04Z | `dt=2026-09-27/hh=18/probe-01-01M3J2YP9NADJ6J99TR72EZR3H.ndjson` | 60 | 23,976 |
| 2026-09-27T19:00:03Z | `dt=2026-09-27/hh=19/probe-01-01M3J3T4WRCDGT4NDEQ0WPP6YC.ndjson` | 60 | 23,977 |
| 2026-09-27T19:15:04Z | `dt=2026-09-27/hh=19/probe-01-01M3J4NM27NYEHFZ0XJY1DCZEC.ndjson` | 60 | 23,977 |

Idle behaviour: between scans the bridge held at `buffer_depth=0`, reprinting an unchanged 30 s stats line
for each roughly 15-minute gap (899 s, then 901 s between flushes), with nothing flushed early by the 90 s
timer or the 4 MB size limit — each file was triggered only by its scan's 60th event landing. `dead_lettered=0`
and `failed_batches=0` throughout; nothing was rejected or retried.

Hour boundary: file 1 landed under `hh=18`; files 2 and 3 landed under `hh=19` — **the crossing is between
files 1 and 2**, on ingest time (doc 02), each file in its own `dt=/hh=` prefix.

---

## Step 5 finding: a lock-order-inversion deadlock in the publisher, live on the Pi

While enabling `force-probe` (Step 5), the service deadlocked partway through the 23:45Z scan. `py-spy` on the Pi showed the main
thread inside paho's `client.publish()` (`client.py:1787`, waiting on paho's own `_out_message_mutex`) while holding the probe's
lock, and paho's network thread inside `on_publish` (`publisher.py:69`, called from `_handle_pubackcomp` while that same paho
mutex was held) waiting on the probe lock — a lock-order inversion introduced when an earlier fix this phase (the PUBACK-loss race,
Step 2) held that lock across the paho call. Fixed in `edge/probe/publisher.py`: `on_publish` now only puts the acknowledged mid
on a `queue.Queue` (no lock, never blocks); `publish()` never calls into paho while holding the probe lock; the early-PUBACK race
is handled by draining that queue and checking a set of "acked before its mid was registered" mids, entirely on the one thread
that calls `publish()`/`acked()`.

Verified against a real `paho-mqtt` client and a real (throwaway, local, anonymous) broker, not the offline fake: 30,000 QoS 1
publishes in unthrottled chunks of 500 (doc 04's drain batch size), every one acknowledged exactly once, no hang. Confirmed this
reproduces the exact deadlock when the old pattern is restored — a tight, unthrottled run of 50,000+ hangs indefinitely; the same
scenario against the offline fake client never hangs, because the fake has no internal lock of its own to invert against, which is
why this needed a real broker to catch at all. `edge/tests/stress_publisher_broker.py` (kept out of the offline `test_*.py` sweep
on purpose; needs Docker).

**Consequence for this run, recorded rather than cleaned up:** the buffer held all 60 readings of the 23:45Z scan safely (nothing
was lost — the write-ahead buffer is exactly what this durability layer is for). 13 of those 60 had already been published and
landed in `bronze.events` (the third file in the table above) before the deadlock froze the service. Once the fix is deployed and
the service restarts, the drain will resend the whole scan, including those already-landed 13 — at-least-once delivery, doc 04 —
so **13 duplicate `event_id`s in `bronze.events` are expected** from this scan once the drain runs. This is outside the checkpoint
window and silver dedups on `event_id` (doc 03); noted here so it isn't mistaken for a checkpoint failure later.

**Independent confirmation, from the broker's own log** (`probe-01` lines): a connection at 23:07:16Z (the first Pi version), then
`probe-01 disconnected: exceeded timeout` at **23:46:34Z** — exactly the landing time of the 13-row partial file above — with no
disconnect recorded again until the reconnect on the fixed commit (`ca5dae0`). Mosquitto dropped the deadlocked probe for missed
keepalives (its network thread was the one frozen inside the lock), independently pinning down when the freeze happened, from a
source outside the probe's own logs.

**A second, related bug found from the same broker log, before the redeploy:** at that later reconnect (02:23:29.580Z) `mode_transitions.jsonl` logged a false `link_lost` — the broker log shows one continuous connection with no disconnect anywhere near it. Root cause: before the probe's first successful connect, `Runtime._link()` reported "not connected" indistinguishably from a real link loss, so `ModeController` recorded a spurious `CONNECTED -> DISCONNECTED -> BURST` instead of going straight to `BURST` on the real first connect. Fixed in `edge/probe/modes.py`: a "waiting for the first connect" state, distinct from a link loss, that a forced `DISCONNECTED` (operator or schedule) still overrides. Also added, per-batch and per-transition, to journald: `edge/probe/runtime.py` now prints a line when a drain batch is sent and another when it is fully acknowledged (sent/acked/remaining), and `edge/probe/modes.py` prints every mode transition alongside its `mode_transitions.jsonl` line. Found and fixed alongside this: the *last* batch of any drain never printed its "acked" line, because `ModeController.update()`'s own backlog-zero check can move the mode out of `BURST` the instant a batch's acks bring the backlog to zero — one tick before `_drain()` would have logged it. Tests for both startup paths (clean, and with a backlog) and the forced-offline override; both suites green (edge 479, ingest 257).

Not yet deployed: the Pi is soaking overnight on `ca5dae0`, the commit before this fix.

---

Phase 3's core checkpoint (the 45-minute forced `DISCONNECTED` on the Pi, doc 07) has not run yet. It
follows in a later section once Step 4 (Pi deployment) and Step 5 (the checkpoint itself) are done.
