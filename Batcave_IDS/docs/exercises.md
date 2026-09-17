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

---

## Zero-credential orchestration: the whole graph, no Groq key (Phase 7)

**What this proves.** docs/04's documented zero-credential path — triage runs the rule-based baseline
alone when `GROQ_API_KEY` is absent — has to hold not just for `make triage` in isolation, but for the
*entire* Dagster asset graph, including the Python step sitting between two halves of the dbt build.
Proven here by actually removing `.env` and running the graph, not by reading the asset code and
assuming the fallback fires.

### Procedure and real output

1. `make transform` first — the graph needs an existing warehouse to attach to.
2. Move `.env` aside entirely (not just unset one variable for one call):
   ```
   mv .env .env.bak
   ```
3. Execute the full asset job by name (`batcave_pipeline`, `AssetSelection.all()` —
   `dagster asset materialize --select '*'` was tried first and rejected: a literal `*` in that
   position gets expanded by the Windows CRT's own argv globbing before Dagster ever sees it, even
   inside single quotes, and returns "unexpected extra arguments" naming the repo's own top-level
   files. Targeting the job by name sidesteps the glob character entirely):
   ```
   DAGSTER_HOME=$PWD/orchestration/dagster_home \
     uv run dagster job execute -m orchestration.definitions -j batcave_pipeline
   ```
4. **Real output, in order:**
   ```
   dbt_upstream: Finished running 3 seeds, 27 table models, 43 data tests in 7.38s.
   Completed successfully. Done. PASS=73 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=73
   ...
   triage__raw_triage_predictions - STEP_START
   no GROQ_API_KEY - running baseline only (docs/04's zero-credential path)
   [1/20] 3978ca6c baseline=558-riddler
   [2/20] bec8b9e7 baseline=576-scarecrow
   ... (20 sessions total, the production threshold selection - 7 of 12 villains, docs/02's
       calibration note)
   wrote orders for 20 sessions
   triage__raw_triage_predictions - STEP_SUCCESS in 959ms
   ...
   dbt_evaluation: Finished running 4 table models in 0.57s. Completed successfully.
   ...
   RUN_SUCCESS - Finished execution of run for "batcave_pipeline".
   ```
5. Restore `.env`:
   ```
   mv .env.bak .env
   ```

**The line that matters is `no GROQ_API_KEY - running baseline only`** appearing mid-run, between a
successful upstream dbt build and a successful downstream one — proof the fallback fires inside the
orchestrated graph, not just when the triage CLI is called directly, and that the two dbt asset groups
either side of the Python step compose into one working run.

### A verification step that writes to shared state needs to be checked against what it disturbs

This exercise's own run added 4 sessions to `raw_triage_predictions` outside Phase 6's already-reported
36-session evaluation sample (the production threshold selector chose 20 sessions, of which 4 weren't
part of the earlier stratified sample). Running it silently shifted `mart_detection_coverage`'s
high-tier baseline recall from the reported 89.7% to 87.3% — caught by regenerating
`docs/images/detection-coverage.svg` and noticing it disagreed with the already-committed docs/04
figures, not by any error or failed test; both runs completed successfully. Fixed by deleting the 4
extra sessions and rebuilding the four evaluation marts, confirmed to reproduce the original numbers
exactly. Full account: `docs/09-engineering-log.md`.
