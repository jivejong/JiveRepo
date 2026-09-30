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
| V1 | Binding (`netstat`) | `127.0.0.1:1883` and `<DESKTOP_IP>:1883` LISTENING, no `0.0.0.0` | **Pass.** `127.0.0.1:1883` and `<DESKTOP_IP>:1883`, both LISTENING, owned by `com.docker.backend` (there is no `mosquitto.exe` on Windows — Docker's own process holds the port). No `0.0.0.0`. |
| V2 | `docker port force-mosquitto` | The same two addresses | `127.0.0.1 -> 1883`, `<DESKTOP_IP> -> 1883`. |
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
| N2 | The Pi's own journal for this boot | **Lost** — `journald` was on volatile storage (`/run/log/journal`, wiped every reboot). Fixed for the future: doc 05, "Persistent journal". |
| N3 | Last file landed before the loss, from the desktop bridge's log | `2026-09-28T13:15:03Z`, 60 lines. |
| N4 | First file landed after the loss | `2026-09-28T13:30:03Z`, 60 lines. |
| N5 | Gap between them | 900 s — exactly one normal quarter-hour cadence step. **0 scans missed**: the 13:15Z scan was taken and published before the loss, and the Pi booted, reconnected and took the 13:30Z scan normally, inside the same 15-minute window. |
| N6 | Recovery | `force-probe` starts at boot unattended (`systemctl enable`, confirmed already in Step 4/5); reconnected without intervention; quarter-hour scans resumed. Buffer count 0 after recovery (reported), nothing stranded. |

**N2's fix, corrected 2026-09-30.** The first fix (`sudo mkdir -p /var/log/journal` + `systemd-tmpfiles`, relying on `Storage=auto`'s
usual "persistent if the directory exists" rule) does not work on this Pi OS image: it ships its own drop-in forcing
`Storage=volatile` outright, which overrides that rule regardless of the directory. Doc 05 now uses an overriding drop-in
(`/etc/systemd/journald.conf.d/90-force-persistent.conf`, `Storage=persistent` and `SystemMaxUse=100M`) instead; the lint test
(`test_infra_phase3.py`) checks for the drop-in, not the old `mkdir`.

**Verified on the Pi, 2026-09-30** (run by the developer, not from an offline check): `ls /var/log/journal` shows one directory,
`<PI_MACHINE_ID>` (the machine ID) — persistent storage took effect. `journalctl --disk-usage`: "Archived and
active journals take up 16M in the file system," comfortably under the 100M cap. Not yet tested across an actual reboot — that
still needs its own boot cycle to confirm.

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

## Phase 3 checkpoint (2026-09-29): MET

The project's core claim (doc 07): force `DISCONNECTED`, restore, and `bronze.events` holds the buffered scans with `event_time`
spanning the outage, arrival clustered at the replay, no gaps, no duplicates; separately, `STEALTH` rows arrive with two null
channels. Recorded from the Pi's UTC journal (`journalctl -u force-probe --utc`) and the workspace SQL editor's output only.

### The outage, from the journal

```
Sep 29 13:32:53 <PI_HOSTNAME> systemd[1]: Started force-probe.service - Force Balance Pipeline probe (Raspberry Pi 3).
Sep 29 13:32:54 <PI_HOSTNAME> python[3701]: probe: mode None -> CONNECTED (startup, offline=False, backlog=0)
Sep 29 13:32:54 <PI_HOSTNAME> python[3701]: probe: probe-01 version b16f0b29a57e5231fd01149fe213a166156584b4; state /var/lib/force-probe; faults x0; mode schedule off; buffer 0 rows
Sep 29 13:33:24 <PI_HOSTNAME> python[3701]: probe: mode CONNECTED -> DISCONNECTED (no_initial_connect, offline=True, backlog=0)
Sep 29 13:35:30 <PI_HOSTNAME> python[3701]: probe: MQTT connected (session present: False)
Sep 29 13:35:30 <PI_HOSTNAME> python[3701]: probe: mode DISCONNECTED -> CONNECTED (link_restored, offline=False, backlog=0)
Sep 29 13:45:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PPJPV0CQJ8KW287ZD6WK9J (CONNECTED): 60 buffered, 60 published
Sep 29 14:00:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PQE5R0J00T9VN2B809MM2N (CONNECTED): 60 buffered, 60 published
Sep 29 14:15:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PR9MN00VZW2C9KV09RQT3V (CONNECTED): 60 buffered, 60 published
Sep 29 14:19:05 <PI_HOSTNAME> python[3701]: probe: MQTT disconnected: client_disconnected:Unspecified error
Sep 29 14:19:05 <PI_HOSTNAME> python[3701]: probe: mode CONNECTED -> DISCONNECTED (link_lost:client_disconnected:Unspecified error, offline=True, backlog=0)
Sep 29 14:30:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PS53J0T8PXZF29VTZ4FSST (DISCONNECTED): 60 buffered, 0 published
Sep 29 14:45:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PT0JF0NZNAKSSFYYYCQ61A (DISCONNECTED): 60 buffered, 0 published
Sep 29 15:00:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PTW1C09MP9SYB385NWB67H (DISCONNECTED): 60 buffered, 0 published
Sep 29 15:01:57 <PI_HOSTNAME> python[3701]: probe: MQTT connected (session present: False)
Sep 29 15:01:57 <PI_HOSTNAME> python[3701]: probe: mode DISCONNECTED -> BURST (link_restored, offline=False, backlog=180)
Sep 29 15:01:57 <PI_HOSTNAME> python[3701]: probe: drain batch sent=180 acked=0 remaining_buffered=180
Sep 29 15:01:57 <PI_HOSTNAME> python[3701]: probe: drain batch sent=180 acked=180 remaining_buffered=0
Sep 29 15:01:57 <PI_HOSTNAME> python[3701]: probe: mode BURST -> CONNECTED (drain_complete, offline=False, backlog=0)
Sep 29 15:15:03 <PI_HOSTNAME> python[3701]: probe: scan 01M3PVQG90WY0MDH43RP2KVXYA (CONNECTED): 60 buffered, 60 published
```
The restore is in the journal after all (`DISCONNECTED -> BURST` then `BURST -> CONNECTED`, all inside the same second, 15:01:57),
and the Pi's own `mode_transitions.jsonl` (`sudo tail -n 2`) gives it to millisecond precision:
```
{"ts_utc":"2026-09-29T14:19:05.866Z","from":"CONNECTED","to":"DISCONNECTED","offline":true,"reason":"link_lost:client_disconnected:Unspecified error","backlog":0}
{"ts_utc":"2026-09-29T15:01:57.431Z","from":"DISCONNECTED","to":"BURST","offline":false,"reason":"link_restored","backlog":180}
```
`<outage_start_utc>` = **2026-09-29T14:19:05.866Z** and `<outage_end_utc>` = **2026-09-29T15:01:57.431Z** (the mode leaving `offline`),
both now directly evidenced by the mode log itself, not approximated from the second-precision journal or the replay's landing time.
The outage was **42 minutes 51.565 seconds**, short of the planned 45. The replay's first landing (15:01:59.000Z, LIST below) follows
the reconnect by 1.569 s — the drain, not a separate event, consistent with both being logged in the same journal second (15:01:57).

Also visible: the boot-with-no-broker fix, live on hardware for the first time. At `13:33:24Z`, 30 s after `startup` (`ack_timeout`),
with no connection yet, the probe correctly recorded `DISCONNECTED` with reason `no_initial_connect` rather than staying silently
`CONNECTED`; at `13:35:30Z` the real connect arrived and it went straight back to `CONNECTED` (`link_restored`; nothing was
buffered in those two minutes, so there was nothing to drain).

### LIST (`/Volumes/force/raw/telemetry/dt=2026-09-29/hh=15/`)

| file | size (bytes) | modification_time (UTC) |
|---|---|---|
| `probe-01-01M3PTZM3CYJR0WNJ75K3STV6Z.ndjson` | 24,157 | 2026-09-29T15:01:59.000Z |
| `probe-01-01M3PTZM4A7B2NXV8S9RQ3YYTG.ndjson` | 24,159 | 2026-09-29T15:02:00.000Z |
| `probe-01-01M3PTZM5G5MRYN2GXHYGRYZ3F.ndjson` | 24,159 | 2026-09-29T15:02:01.000Z |
| `probe-01-01M3PVQKSGYF9FRF38CPMGXEYS.ndjson` | 23,979 | 2026-09-29T15:15:05.000Z |
| `probe-01-01M3PWK2Y38FQ00710BC3AMB5Q.ndjson` | 23,976 | 2026-09-29T15:30:05.000Z |

The three replay files land within 2 seconds of each other, well ahead of the two normal ones 15 minutes apart. **The replay files
are about 180 bytes (3 bytes/line) larger than a normal file**: `"mode":"DISCONNECTED"` (12 characters) versus `"mode":"CONNECTED"`
(9) in every line, because a row keeps the mode it had when it was taken (doc 02), not the mode it was sent under. Confirmed exactly:
`24157 - 23976 = 181` and `24159 - 23979 = 180`, both `/60 ≈ 3` bytes/line, `len("DISCONNECTED") - len("CONNECTED") = 3`.

**Independent confirmation, from the desktop bridge's own log** (not the workspace): the same three files, same byte counts,
appear as three consecutive `landed` lines with no other line between them —
```
bridge: landed dt=2026-09-29/hh=15/probe-01-01M3PTZM3CYJR0WNJ75K3STV6Z.ndjson (60 lines, 24157 bytes)
bridge: landed dt=2026-09-29/hh=15/probe-01-01M3PTZM4A7B2NXV8S9RQ3YYTG.ndjson (60 lines, 24159 bytes)
bridge: landed dt=2026-09-29/hh=15/probe-01-01M3PTZM5G5MRYN2GXHYGRYZ3F.ndjson (60 lines, 24159 bytes)
```
bracketed by its own periodic stats line reading `files=148` beforehand and `files=151 ... last_flush=2026-09-29T15:02:00Z`
right after — a source independent of both the workspace SQL and the Pi's journal, agreeing with the LIST table above file for
file, byte for byte.

### `phase3_checkpoint.sql`

| Query | Result | Note |
|---|---|---|
| p3-3b | `files=3 n=180 first_landed=2026-09-29T15:01:59.000+00:00 last_landed=2026-09-29T15:02:01.000+00:00 landing_spread_s=2 min_lag_s=119 max_lag_s=1919 rows_replayed=60` | **Pass.** `landing_spread_s=2` (the three files land within 2 s); `max_lag_s=1919 > 1800` (the oldest, 14:30Z, scan); `min_lag_s=119` (the newest, 15:00Z, scan) is small; `rows_replayed=60`, only the oldest scan. Proves "clustered at replay" from the landed files themselves, independent of the notebook. |
| p3-0 | `live_readings_before=9493 newest_event_time=2026-09-29T15:30:02.950+00:00` | **Run after the cut, not as a pre-cut baseline** — `newest_event_time` is the just-landed 15:30Z scan, so this is a running total at the time the query was run, not a "before" snapshot to diff against. |
| p3-1 | `n=180 scans=3 sectors=60 first_event=2026-09-29T14:30:00.000+00:00 last_event=2026-09-29T15:00:02.950+00:00` | **Pass.** 180 rows, 3 scans, 60 sectors, spanning the outage (14:30, 14:45, 15:00Z). |
| p3-2 | `DISCONNECTED 180 3` | **Pass.** All 180 rows `DISCONNECTED` — no row taken close enough to detection to still read `CONNECTED` this time (the cut landed well before the next boundary, unlike the earlier drill). |
| p3-3 | `first_ingest=last_ingest=2026-09-29T15:36:50.031+00:00 min_lag_s=2208 max_lag_s=4010 rows_replayed=180` | **Illustrates the doc 03 OPEN item, not a failure.** The notebook ran once, at `15:36:50Z`, well after the replay landed (`15:02Z`) and after the two live scans that followed (`15:15Z`, `15:30Z`); `_ingest_ts` stamps all of them alike, so every row looks equally "late" and `rows_replayed=180` instead of the true 60. p3-3b, above, is the proof that does not depend on when the notebook ran. |
| p3-4 | `slots_seen=6 slots_expected=6` | **Pass.** No gaps. |
| p3-5 | `duplicate_event_ids=0 scans_not_60=0` | **Pass.** No duplicate `event_id`s, and every scan has exactly 60 rows. |
| p3-6 | `2026-09-29 15 3 180` | **Pass.** One `dt`/`hh` group, the ingest hour (15), not the event hours (14 and 15 on the event side too, as it happens, but by ingest time, not event time) — `files=3`, `n=180`. |
| p3-7 | `n=60 scans=1 two_null_channels=60 dark_present=60 temperature_absent=60 first_scan=2026-09-28T19:00:00.000+00:00 last_scan=2026-09-28T19:00:02.950+00:00` | **Pass.** The content-level confirmation of the `STEALTH` scan already recorded by its landing size, above: both channels null, `dark_side_activity` present, `sensor_temp_c` absent, on every one of the 60 rows. |
| p3-9 | `housekeeping_events=0 most_dropped=null` | **Pass.** The buffer never reached its cap. |

**Checkpoint verdict: MET.** p3-1, p3-2, p3-4, p3-6, p3-7 and p3-9 pass outright; p3-3b independently proves the "clustered at
replay" claim that p3-3 alone could not, for the reason recorded as doc 03's OPEN item; p3-0 and p3-5 are recorded exactly as given,
with their caveats, rather than marked pass. See doc 07 for the pointer from the checkpoint's own text.

---

## Unplanned outage, 2026-09-29

A real, local connectivity loss, later the same evening — not a scheduled event (the mode schedule was not deployed until after
tonight's commits) and not forced by an operator. Recorded from `mode_transitions.jsonl` and the desktop bridge's own log.

| # | Check | Actual |
|---|---|---|
| U1 | Lost (detected) | `mode_transitions.jsonl`: **2026-09-29T18:14:16.192Z**, `CONNECTED -> DISCONNECTED`, reason `link_lost` (`client_disconnected`, "Keep alive timeout"). This is *detection* time, not the moment the break happened — see below. |
| U2 | Restored | **2026-09-29T19:29:47.517Z**, `DISCONNECTED -> BURST`, `offline=false`, backlog **300** (5 scans buffered: 18:15, 18:30, 18:45, 19:00, 19:15Z). One drain batch, 300 sent / 300 acknowledged; **2026-09-29T19:29:48.087Z**, `BURST -> CONNECTED`. |
| U3 | Duration | **1h 15m 31.325s** offline by the mode log's detection times (19:29:47.517 − 18:14:16.192); the real break was somewhat longer — see below. |
| U4 | The five replayed files, from the bridge's own log | Landed as one burst once the Pi reconnected — see below. |
| U5 | Bridge-side retries/failures | `retries=0 failed_batches=0` throughout, both before and after — no evidence of an upload-side (Databricks) problem. |

**Broker log (UTC), independent of both the Pi and the bridge's own logs:**
```
18:13:46 probe-01 disconnected: exceeded timeout
19:29:28 force-bridge disconnected: connection closed by client
19:29:29 force-bridge reconnected (session taken over)
19:29:47 probe-01 reconnected
```
**Correction to the earlier finding: the bridge did not drop because of this outage.** The broker log shows no `force-bridge`
disconnect anywhere near 18:13-18:14; `docker inspect` shows the broker container's own start time unchanged since 2026-09-27T14:59:00Z
with `restarts=0`; the Windows System log shows no adapter, DHCP, power or network-profile event between 14:05 and 15:35 EDT. Broker
and desktop both stayed up throughout, and the bridge never lost the broker during the Pi's outage. The break was specifically on the
Pi-to-desktop path — likely the router or switch between them, since neither endpoint's own logs show a cause. My earlier read of the
bridge's own `connected (session present: True)` line as evidence the bridge also dropped *during this outage* was wrong; the broker
log now dates that same line to **19:29:29Z** (see below), 19 s before the Pi returned, not concurrent with the Pi's own loss.

**Detection lag:** the broker declared `probe-01`'s keepalive exceeded at **18:13:46Z**, 30 s before the probe's own
`mode_transitions.jsonl` entry (18:14:16.192Z) — consistent with the probe's own `ack_timeout`/keepalive detection running slightly
behind the broker's. MQTT brokers typically declare a keepalive timeout at 1.5x the keepalive interval (60 s here) of silence, which
would put the last real traffic from the Pi at roughly **18:12Z** — the true break was likely a couple of minutes earlier than either
logged detection, and the outage a couple of minutes longer than U3's 1h15m31s.

**Separate finding, cause undetermined:** why did the bridge close its own connection at 19:29:28Z, 19 s *before* the Pi returned?
The bridge's own log has nothing at all around that point beyond the reconnect itself — no error, no exception, no other line; it
never prints anything on its own disconnect (only on reconnect), so there is no textual evidence in this log of a cause. **Undetermined
from the log alone.** It is not explained by, and does not appear to be caused by, the Pi's return (which came 19 s later).

```
bridge: connected (session present: True)
bridge: subscribed to force/telemetry/# (QoS 1, client id force-bridge)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAA1JR67T70V4KJPDYG6XS.ndjson (60 lines, 24156 bytes)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAA1KJVND3RXK2Y1M2E6E0.ndjson (60 lines, 24156 bytes)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAA1MJFEWAFKDG5ASNV81C.ndjson (60 lines, 24159 bytes)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAA1NKT2Q9ER7DM1KQ22Y3.ndjson (60 lines, 24159 bytes)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAA1PSHM4KCAKBREH46XNF.ndjson (60 lines, 24158 bytes)
bridge: landed dt=2026-09-29/hh=19/probe-01-01M3QAAH3J9Z7F2JK1H1SB0TKK.ndjson (60 lines, 23975 bytes)
```
The `connected`/`subscribed` pair above is the bridge's own 19:29:29Z blip (just above), not a reaction to the Pi. The first five
landed lines are the replay (`DISCONNECTED`-sized, 24156-24159 bytes each, matching the earlier checkpoint's replay files); the
sixth, smaller (23,975 bytes, `CONNECTED`-sized) and with a visibly later ULID, is the next live 19:30Z scan landing in the same
flush, not part of the replay. **Individual per-file landing times aren't available from this log** — the bridge only timestamps
its periodic stats line (`last_flush=2026-09-29T19:30:03Z`, the first one to move past `18:00:05Z`), which brackets the whole burst
but doesn't distinguish the five replayed files from each other or from the sixth.

**Note for Phase 4:** the 19:00Z scan's lag (landing minus `event_time`) is approximately 1,788 s — **under** the `is_replayed`
cutoff Phase 4's silver logic uses at 1,800 s (the pattern already used informally in p3-3b's `min_lag_s`/`max_lag_s`), so it is
not flagged as replayed. The three older scans (18:15Z ≈ 4,190 s, 18:30Z ≈ 3,290 s, 18:45Z ≈ 2,390 s) are all over the cutoff;
19:15Z (≈ 888 s) is under it, same as 19:00Z. This outage counts as **180** replayed rows (3 scans), not the full 300 (5 scans)
actually buffered and replayed — a real edge case in whatever "was this row replayed" test Phase 4 writes, not a checkpoint
concern.

**Correction, <commit date>:** this note originally read "240 (4 scans)". Recomputed from the five scans' lags at the replay's
landing (~19:29:49-19:29:53Z, from U2's `BURST` entry and drain-complete times above): three scans clear 1,800 s, not four —
180 rows, not 240. Individual per-file landing times for this replay were never logged (see above, "Individual per-file
landing times aren't available from this log"), so this is derived from the reconnect time and the batch/publish timing in
doc 04, not read directly off a log.

**Minor, found alongside this — proposal only, deferred to Phase 4:** `"probe: MQTT disconnected: ... Keep alive timeout"` printed
twice at 18:14:15Z while `mode_transitions.jsonl` recorded only the one transition above. Likely cause, from `publisher.py`:
`_on_disconnect` (`edge/probe/publisher.py:78`) prints unconditionally on every call from paho, with no check for whether the
client was already marked disconnected; paho-mqtt is known to invoke `on_disconnect` from more than one internal path around a
keepalive timeout (the periodic keepalive check and a subsequent socket-level error can each trigger it). `mode_transitions.jsonl`
never doubled up because `ModeController`/`Runtime._link()` only records a *change* of state — a second "still disconnected"
signal is a no-op there, but `_on_disconnect`'s `print()` has no equivalent guard.

Proposed fix: track whether `_connected` was already `False` before this call and skip the print (and the `disconnect_reason`
overwrite) if so — but the guard must **reset on every successful connect** (`_on_connect`), so a genuine second outage after a
real reconnect still gets its own line; this isn't a one-way latch like the mode controller's `_ever_connected`. Two tests would
be needed to prove it, not one: (1) two `on_disconnect` callbacks in a row, no connect between them, prints exactly one line;
(2) a full connect/disconnect cycle twice (connect, disconnect, connect, disconnect) prints exactly two lines, not deduplicated
across the reconnect. Needs a test against the real broker (`stress_publisher_broker.py`) forcing two disconnect callbacks in a
row, or at minimum an offline test against a fake client. Not built or applied.
