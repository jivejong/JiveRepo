# Exercises

One-off demonstrations, performed for real against the live stack and recorded here with the actual
output rather than a description of expected output. Referenced from `docs/03-attack-simulation.md`
and `docs/06-implementation-plan.md`.

---

## Consumer restart: offset-commit ordering (Phase 4)

**What this proves.** The consumer writes every Parquet file for a batch, *then* commits the Kafka
offsets for that batch (`services/consumer/consumer.py`) — never the other way round. A crash between
write and commit must replay the batch on restart, producing visible duplicates, and must never lose
the batch. This is at-least-once delivery working as designed (docs/01); it is proven here by actually
killing the consumer process mid-batch, not by reading the code.

### Making the window hittable

The real write-then-commit window is microseconds wide — too narrow to land `docker kill` in on
purpose. `CONSUMER_DEBUG_PRE_COMMIT_DELAY_S` (default `0`, never set by `docker-compose.yml`) holds a
flush open — written to disk, offsets not yet committed — for N seconds so the kill can be timed
deterministically instead of by luck. Set only for this exercise, via `docker compose run -e ...`.

### Procedure and real output

1. `make dev-reset && make dev-up` — clean stack, empty topic, normal consumer running.
2. Swap in a debug instance with a wide hold window and a flush interval long enough that a full
   attack run finishes before the timer fires:
   ```
   docker kill batcave-consumer && docker rm -f batcave-consumer
   docker compose run -d --name batcave-consumer \
     -e CONSUMER_DEBUG_PRE_COMMIT_DELAY_S=20 -e CONSUMER_FLUSH_INTERVAL_S=50 consumer
   ```
3. Run a Killer Croc attack (highest volume/retry_ratio, docs/03) at `time_scale=0.3`:
   ```
   uv run python -m services.simulator --villain 386-killer-croc --time-scale 0.3
   ```
   `386-killer-croc    run=602851d5 session=65b4d64c attempts= 44 requests= 45 stalled@2`
4. Watch the logs for the hold to open, then kill immediately:
   ```
   2026-09-16 13:54:44,557 WARNING consumer DEBUG: holding 20.0s after flush, before commit -
                                    written but uncommitted; a kill now must replay this batch
   ```
   Killed 8 seconds into the window: `docker kill batcave-consumer` at `13:54:52`.

5. **State at the moment of the kill, confirmed from real output — not asserted from the code:**

   Landed on disk (written, during the hold):
   ```
   attack_run  1 row
   attempt    44 rows
   request    47 rows
   ------------------
   TOTAL      92 rows
   ```
   Consumer-group offsets (`rpk group describe attack-events-writer`):
   ```
   TOPIC          PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
   attack.events  0          -               2               2
   attack.events  1          -               90              90
   attack.events  2          -               0               -
   ```
   **The batch was fully written and not one offset was committed** — `CURRENT-OFFSET` is unset and
   `LAG` equals the full backlog (92). This is the invariant, demonstrated rather than inferred.

6. Restart with the normal consumer (no debug delay): `docker compose up -d consumer`. The group briefly
   shows `PreparingRebalance` (the killed member's session hadn't expired yet), then stabilizes and
   replays:
   ```
   2026-09-16 13:55:35,236 INFO consumer flushed 1 messages across 1 partitions; offsets committed
   2026-09-16 13:56:05,473 INFO consumer flushed 91 messages across 3 partitions; offsets committed
   ```
   Offsets then show `LAG 0` on every partition.

7. **Landed rows after the replay:**
   ```
   attack_run  2 rows  (1 unique run, landed twice)
   attempt    88 rows  (44 unique attempts, each landed twice)
   request    94 rows  (47 unique requests, each landed twice)
   -------------------
   TOTAL     184 rows  = 92 written before the kill + 92 replayed after restart
   ```
   One concrete pair, read back with DuckDB — the same `event_id` at the same `kafka_offset`, landed
   a minute apart:
   ```
   event_id                              kafka_offset  landed_at
   05ae3131-d3e6-4a1e-a72e-5d079ce6612a  32            2026-09-16 13:54:35.156518+00
   05ae3131-d3e6-4a1e-a72e-5d079ce6612a  32            2026-09-16 13:55:35.237250+00
   ```
   Every row in the pre-kill batch was replayed exactly once — no loss, no extra, no partial file
   (the atomic rename in `writer.py` means a kill mid-write never leaves a corrupt `.parquet`).

**If the ordering were inverted** (commit before write), step 5's offsets would show `LAG 0` at the
moment of the kill and the 92 pre-kill rows would never have reached disk at all — silent data loss.
That is the failure this exercise exists to rule out.

### The two duplicate populations, distinguished in the same corpus

A Two-Face run (`678-two-face`, 12 requests) was added to the same corpus after the restart, so both
kinds of request-level duplicate Phase 5 must tell apart are present together (docs/03: Two-Face's
deliberate re-issued requests must **survive** dedupe; the replay/delivery duplicates above must be
**removed**). Measured with `make landing-check`, which reads the landed Parquet through DuckDB the
way Phase 5's dbt sources will:

```
same event_id, >1 row (delivery dup + replay):        48  -> dedupe REMOVES
Two-Face, same (session, path), distinct event_ids:     3  -> dedupe KEEPS
```

**A bug found and fixed while gathering these numbers.** The first version of this query counted
"same `(session, path)`, distinct `event_id`" without restricting to Two-Face's sessions, and reported
`5` instead of `3`. Killer Croc has the highest `retry_ratio` of any villain (docs/03) and produces
the identical *shape* — repeated legitimate retries to the same path — which was inflating a count
labeled Two-Face with an unrelated signal. Fixed by joining `request.run_id` to `attack_run.villain_slug`
(the only place villain identity lives) and filtering to `678-two-face`, with the `attack_run` side
`DISTINCT`-ed first — `attack_run` was itself duplicated by this same replay, and joining the raw
relation would have fanned out every Two-Face request row by the number of `attack_run` copies.
Regression tests for both (`tests/test_consumer_landing_check.py`) land synthetic fixtures that
reproduce each failure mode directly, without needing a live corpus to catch a regression.

### `make landing-check` after this exercise legitimately fails

Run right after the steps above, `make landing-check` reports a topic-vs-disk **divergence** on
`request`/`attempt`/`attack_run` and exits non-zero. This is expected, not a regression: the replay
duplicates rows in **landed Parquet**, but the Kafka **topic** itself holds each message exactly once
— replaying a message re-reads it, it doesn't re-produce it. A normal, uninterrupted corpus reconciles
cleanly (verified separately against the full Phase 3 pathology corpus, 144 sessions: `request` 2,592
on both sides, `attempt` 2,102 on both, `attack_run` 144 on both, `PASS`). The divergence here is the
positive evidence that the replay happened, not a fault in the check.

### Undeserializable: the consumer survives it, verified structurally

Separately (the Phase 3 pathology corpus, which the `undeserializable` pathology fires at 0.2%):
`make landing-check` cross-references every quarantined offset against later-landed rows on the same
Kafka partition —
```
partition=0 offset=1363: 421 later rows on that partition -> consumer CONTINUED
partition=1 offset=1188:  53 later rows on that partition -> consumer CONTINUED
```
proving the loop kept consuming past a message it couldn't parse, not just that a quarantine file
exists.
