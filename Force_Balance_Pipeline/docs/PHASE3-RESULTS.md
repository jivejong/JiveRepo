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

The false `link_lost` had already appeared twice on the Pi, each time within a fraction of a second of a start, in the Pi's own
`mode_transitions.jsonl`: at 23:07:16.685Z, 215 ms after the first version's `startup`, and at 02:23:29.580Z, 214 ms after the
`ca5dae0` start (backlog 60, which then went `DISCONNECTED -> BURST -> CONNECTED` instead of straight to `BURST`).

---

## Overnight soak on the Pi (Step 5): `d7f0b96`, the startup fix

Deployed 2026-09-27 23:30:09 EDT (03:30:09Z). The previous run (`ca5dae0`) shut down cleanly on the restart:
`probe: stopped; {'scans': 5, 'skipped': 0, 'published': 360, 'acked': 360, 'overflows': 0}`. Recorded from the Pi's journal, its
`mode_transitions.jsonl` and `buffer.db`, and the desktop bridge's log.

| # | Check | Expected | Actual |
|---|---|---|---|
| K1 | Start on `d7f0b96` | Startup line with the commit, faults 0, schedule off, empty buffer, connected | `probe: probe-01 version d7f0b96acbfb4e02b7cc5437c1f273b6a30f0a61; state /var/lib/force-probe; faults x0; mode schedule off; buffer 0 rows`, then `probe: MQTT connected (session present: False)`. **Pass.** |
| K2 | Mode transitions on the new start | Only `startup`; no false `link_lost` | The file's last line is `{"ts_utc":"2026-09-27T23:30:09.470Z","from":null,"to":"CONNECTED","offline":false,"reason":"startup","backlog":0}` and nothing follows it. **Pass: the startup fix works on hardware.** (The two false `link_lost` entries above are in the same file, from the two earlier starts.) |
| K3 | Consecutive scans, every quarter hour | 23:45 EDT to 07:15 EDT, no gaps | **31 scans**, 23:45:03 to 07:15:03 EDT (03:45:03Z to 11:15:03Z), every quarter hour, no gap; each `(CONNECTED): 60 buffered, 60 published`. Together with the 23:30:03 scan that closed the `ca5dae0` run, 32 scans in a row. **Pass.** |
| K4 | Buffer | 0 | `buffer count: 0`. **Pass.** |
| K5 | Every scan landed exactly once | One 60-line file per scan | See the reconciliation below. **Pass at file level.** |

### Reconciliation against the bridge (2026-09-28, run at about 11:48Z)

The desktop bridge had been running in continuous mode since 23:07Z on the 27th and was **never down**: one MQTT connection for the
whole run (`connected (session present: True)` once, the subscription once), no reconnect, and `dead_lettered=1` (the known
`mqtt_check.py` test string), `retries=0`, `failed_batches=0`. There was nothing queued at the broker to catch up on. Its final
state at the time of the check: `received=2474 files=42 events=2473`, that is 41 files of 60 lines and one of 13.

Each of the 32 `scan_id`s in the soak journal was matched to a landed file by timestamp: a `scan_id` is a ULID stamped when the scan
runs, and each landed file's name is a ULID stamped when the bridge flushed it. All 32 matched a distinct 60-line file, the file
always 3.4 to 4.0 s after the scan, under `dt=2026-09-28/hh=03` to `hh=11` as expected (ingest hour, doc 02). Zero mismatches, zero
unmatched journal scans. The 10 files not in the supplied journal are the earlier Pi runs (23:15, 23:30 and the 13-line partial at
23:46:33 on the 27th; the 60-row drain of the 23:45Z scan at 02:23:30Z; 02:30, 02:45, 03:00, 03:15Z) and the two scans after the
journal was pasted (11:30 and 11:45Z), all 60 lines except the known partial.

Follow-up written after the soak, not yet deployed or run on hardware: the startup fix above suppresses every "not connected" tick
before the first connect, so a probe that boots with **no broker at all** would never have said it was offline. Now, if the first
connect has not arrived within the runtime's own real-loss detection window (`ack_timeout`, 30 s by default; no new number), the
probe records `DISCONNECTED` with reason `no_initial_connect`, buffers and labels scans as in any outage, and drains through
`BURST` when the connect comes; a connect inside the window still records nothing. Also closed in the same change: a scan that
falls *inside* the wait was buffered but never drained, because nothing had marked a drain; the first connect now drains any such
rows (`startup_backlog`). Fake-clock tests for both boot paths and the in-window scan, mutation-checked.

Not checked here: the `scan_id` values *inside* the files. Reading them needs the workspace, so the by-`scan_id` reconciliation
happens in bronze after the notebook run (query p3-5 of `ingest/phase3_checkpoint.sql` counts scans not 60 and duplicate ids).

### A STEALTH scan's landing evidence (from an operator-forced STEALTH period this session, ahead of tonight's checkpoint)

`dt=2026-09-28/hh=19/probe-01-01M3MP6WFJ2KP11JDN4Z817RN1.ndjson`, landed `2026-09-28T19:00:03Z`, 60 lines, **22,467 bytes** — about
1,500 bytes smaller than a `CONNECTED` scan's usual ~23,976. Confirmed offline, not inferred from the size alone: serialising one
`CONNECTED` and one `STEALTH` envelope for the same planet from `forcesim.probe.SimProbe.sweep()` and diffing them gives the same
1,509 bytes over 60 lines (25.15 bytes/line) — `"mode":"STEALTH"` (2 bytes shorter than `"CONNECTED"`), `midichlorian_ppm` and
`kyber_resonance` as JSON `null` in place of a number, and `sensor_temp_c` omitted entirely (doc 04). This is the landing-size
evidence that the file is a `STEALTH` scan; the content check (both channels null, `dark_side_activity` present, no
`sensor_temp_c`) is p3-7, run as part of tonight's checkpoint.

## Accidental power-loss test (unplanned, 2026-09-28)

A cable move briefly cut the Pi's power. Recorded from the Pi (`uptime -s`) and the desktop bridge's log; the Pi's own journal for
this boot was lost, because journald was volatile (fixed below).

| # | Check | Actual |
|---|---|---|
| N1 | Boot time | `uptime -s`: **2026-09-28 09:22:09 EDT (13:22:09Z)**. |
| N2 | The Pi's own journal for this boot | **Lost** — `journald` was on volatile storage (`/run/log/journal`, wiped every reboot). Fixed for the future: doc 05, "Persistent journal", added below. |
| N3 | Last file landed before the loss, from the desktop bridge's log | `2026-09-28T13:15:03Z`, 60 lines. |
| N4 | First file landed after the loss | `2026-09-28T13:30:03Z`, 60 lines. |
| N5 | Gap between them | 900 s — exactly one normal quarter-hour cadence step. **0 scans missed**: the 13:15Z scan was taken and published before the loss, and the Pi booted, reconnected and took the 13:30Z scan normally, inside the same 15-minute window. |
| N6 | Recovery | `force-probe` starts at boot unattended (`systemctl enable`, confirmed already in Step 4/5); reconnected without intervention; quarter-hour scans resumed. Buffer count 0 after recovery (reported), nothing stranded. |

**A real bug found from this incident, not from a test:** the mode log's `startup` entry for this boot is stamped
`2026-09-28T13:19:43.xxxZ` — *before* `uptime -s`'s `13:22:09Z`, which is impossible for a boot that happened at 13:22:09. Cause: the
Pi has no RTC; `fake-hwclock` restores the last saved (stale) time at boot, and the probe's own clock gate (`probe/clock.py`)
guards *readings* against this (`NTPSynchronized`) but `ModeController._record()` (`probe/modes.py`) stamps every `mode_transitions.jsonl`
entry, `startup` included, with the raw, ungated `now` — so an entry written in the few seconds before NTP corrects the clock gets a
false, too-early timestamp. `uptime -s` itself is queried after sync and is trustworthy; the log entry is not.

**Decided, 2026-09-28 — not built yet.** Option (a): defer `ModeController`'s log writes until the clock is confirmed synced once
(reusing `probe/clock.py`'s `timedatectl_synced`), writing one snapshot entry when it first is; the in-memory mode still updates
normally in the meantime so buffering/publishing stays correct. Matches doc 04's existing "skip and log" treatment of readings before
sync. Option (b) — a `clock_synced` boolean on every entry instead of suppressing anything — was not chosen: it loses no information
but relies on the reader checking the flag, which is exactly what this incident needed and didn't have.

**Addition to (a), so a boot with the broker down is never hidden:** the snapshot entry written at sync must also record what
happened during the unsynced window — `suppressed_transitions` (how many mode/offline changes were suppressed) and, if any of them
entered `DISCONNECTED` (`no_initial_connect` included), `disconnected_before_sync` (its reason) and *when*, measured by uptime or the
monotonic clock, never by the (still unsynced, still possibly wrong) wall clock. A test for exactly that case — a boot with no
broker, still unsynced when `no_initial_connect` fires — is part of the build.

Implementation is deferred until after tonight's checkpoint: tonight's Pi deploy carries only the boot-with-no-broker fix already
built and tested (above), nothing from this item. Not needed for the checkpoint either way: the desktop's clock (not a Pi, not
gated) is what stamps `_ingest_ts`, and the Pi's `event_time`s are already protected by the existing reading-level gate.

---

Phase 3's core checkpoint (the 45-minute forced `DISCONNECTED` on the Pi, doc 07) has not run yet. It
follows in a later section once Step 4 (Pi deployment) and Step 5 (the checkpoint itself) are done.
