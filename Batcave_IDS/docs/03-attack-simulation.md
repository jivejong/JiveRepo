# 03 — Attack simulation

The simulator has three jobs: translate powerstats into traffic shape, apply a per-villain behavioral
signature, and inject realistic data defects. The third job is what makes this a data engineering
project rather than a toy.

---

## Two-layer behavior model

**Layer 1 — stats set the shape.** All six powerstats drive continuous parameters. This layer is
derived, not hardcoded, so any villain in the dataset works.

**Layer 2 — signatures set the fingerprint.** One deterministic quirk per villain, drawn from
character lore. This layer exists because twelve villains cannot be separated on six stats alone:
Riddler and Two-Face sit at Euclidean distance 18.5, and Scarecrow and Penguin at 24.1. Signatures
are what make those pairs distinguishable.

The signatures are documented in the triage prompt, so the LLM has the same threat-intelligence
profiles a real analyst would. That keeps the classification task fair. The ground-truth villain is
still withheld.

---

## Layer 1 — powerstat mapping

| Powerstat | Controls | Effect |
|---|---|---|
| `intelligence` | Targeting precision and evasion | High: goes straight for high-tier paths, rotates user agents, strips headers, adds timing jitter. Low: sequential enumeration with no evasion. |
| `speed` | Request rate | Requests per minute scales roughly linearly. |
| `strength` | Payload size | Body bytes and repetition count on brute-force requests. |
| `durability` | Persistence | Session duration and how long it continues after errors. |
| `power` | Payload complexity and growth | Exotic encodings, oversized headers, unusual methods, and whether payload size trends upward over the session. |
| `combat` | Escalation aggressiveness | How fast it climbs path tiers and how willing it is to hit tier 4. |

Implement as a `BehaviorProfile` dataclass derived from the six stats, consumed by strategy classes.
Do not hardcode per-villain values in Layer 1 — derive them, so the mapping itself is the artifact.

---

## Layer 2 — villain signatures

Twelve villains. Stats shown for reference; the signature is the additional deterministic behavior.

| Villain | INT/STR/SPD/DUR/PWR/CMB | Signature |
|---|---|---|
| **Joker** | 100/10/12/60/43/70 | Abandons a path immediately before it would succeed. Every ~8th request carries a deliberate nonsense payload. Occasional absurd HTTP methods. |
| **Riddler** | 100/10/12/14/10/14 | Every request carries a `?riddle=` style query parameter containing an anagram or word puzzle. Stops entirely on the first hard error (durability 14). |
| **Bane** | 88/38/23/56/51/95 | Escalates to tier 4 almost immediately (combat 95), then hammers that single endpoint with large repeated bodies. No evasion whatsoever. |
| **Catwoman** | 69/11/33/28/27/85 | Minimal footprint: under 20 requests total, exactly one tier-4 touch, zero errors, clean exit. The low-volume high-severity case. |
| **Poison Ivy** | 81/14/21/40/100/40 | Very slow drip over a long session. Body size grows monotonically (power 100 drives `body_bytes_trend`). Gradual tier climb, almost no errors. |
| **Scarecrow** | 81/10/12/14/48/50 | Deliberately probes paths that produce errors, generating a high 4xx ratio, then gives up quickly (durability 14). |
| **Penguin** | 75/10/12/28/30/45 | Source IP rotates within a single session — he sends henchmen. Drives `distinct_source_ips`, which nothing else does. |
| **Harley Quinn** | 88/12/33/65/55/80 | Erratic bursts separated by long pauses. Repeats paths Joker touched, with variation. High durability keeps her going through errors. |
| **Mister Freeze** | 75/32/12/70/37/28 | Holds connections open: long `response_time_ms`, very few distinct paths, longest session duration of any villain, minimal escalation. |
| **Killer Croc** | 19/53/35/90/53/60 | Intelligence 19: pure sequential enumeration, no evasion, no jitter. Durability 90 means it never stops. Highest raw request count. |
| **Ra's al Ghul** | 100/28/32/42/27/100 | Combat 100 with intelligence 100: straight to tier 4 with near-zero wasted requests, rotating UAs, then exits. Lowest `wasted_request_ratio`. |
| **Two-Face** | 88/10/12/14/9/28 | Every request issued exactly twice. Path choices split 50/50 between two candidates by coin flip. Stops on the first error. Drives `exact_duplicate_path_pairs`. |

**The disambiguating features** added in `docs/02-data-model.md` map directly to signatures:
`riddle_param_count` (Riddler), `exact_duplicate_path_pairs` (Two-Face),
`distinct_source_ips` (Penguin), `body_bytes_trend` (Poison Ivy), `error_ratio` plus low duration
(Scarecrow), `wasted_request_ratio` (Ra's al Ghul).

Note that Two-Face's signature interacts with pathology 1. His deliberate duplicate *requests* are
distinct events with distinct `event_id`s and must not be removed by deduplication, which only
collapses identical `event_id`s. Worth an explicit test: a Two-Face session should retain its
duplicate path pairs after staging.

### What `pivot_ratio` and `retry_ratio` actually measure (Phase 3 finding)

`pivot_ratio` and `retry_ratio` are only defined for villains that *fail often enough to face the
choice* — a villain who clears every stage on the first attempt makes no retry-or-pivot decisions at
all. So they discriminate *within the frequently-failing population*, not across the whole roster,
and a villain's rank on them is shaped as much by how often it fails as by its policy. Measured over
10 runs each (`make separability`):

- **`retry_ratio` leader: Killer Croc.** He grinds because gating leaves him nothing to pivot to —
  the high retry rate is a *consequence of his gating*, not just a policy choice.
- **`pivot_ratio` leader: Joker.** Chaotic, no coherent objective, constant reconsideration — he
  pivots more than the methodical villains, who either succeed (no decision) or grind.

An earlier draft claimed Ra's al Ghul had the highest `pivot_ratio`. That contradicts his own combat
100 / intelligence 100 profile: a villain who clears stages immediately doesn't accumulate pivot
decisions. Corrected — his discriminator is his low wasted-request behavior and his reaching tier 4
in very few requests, not pivot volume. See docs/06 and docs/07.

### Low-durability villains are identifiable by signature, not by outcome (Phase 3 finding)

Riddler and Two-Face (durability 14) stop on the first hard error, so a couple of early rolls decide
how far the session gets — their outcome features (`max_path_tier`, `duration_s`) are high-variance
run to run *by design*, not from a mapping defect. Their separability therefore has to rest on their
**signature** features, which are present regardless of how far the run gets. The harness confirms
this directly: over 10 runs each, Riddler vs Two-Face show large per-feature effect sizes on the
signature features (`riddle_param_count` d≈3.3, `exact_duplicate_path_pairs` d≈2.2) but near-zero on
the outcome features (`max_path_tier` d≈0.08, `duration_s` d≈0.37).

This is a real, testable property: **a low-durability villain is recognizable by *how* it attacks,
not by *how far* it gets** — a prediction Phase 6 can check against the LLM's per-session
attribution.

---

## Verification (Phase 2 checkpoint)

Run all twelve villains for 120 seconds each, then confirm from `int_session_features`:

1. No two villains share a nearest neighbour in feature space that crosses archetype boundaries
2. Riddler and Two-Face separate on `riddle_param_count` and `exact_duplicate_path_pairs`
3. Scarecrow and Penguin separate on `error_ratio` and `distinct_source_ips`
4. Joker and Harley Quinn separate on burst structure (`inter_request_stddev_ms`)
5. Catwoman and Ra's al Ghul both reach `max_path_tier` 4 on under 25 requests
6. Killer Croc produces the highest `request_count`, Mister Freeze the longest `duration_s`

If any check fails, adjust the mapping before moving on. This checkpoint will loop — budget for it.

---

## Deliberate data pathologies

Injected at configurable rates via `services/simulator/pathologies.yml`.

| # | Pathology | Injection | Required handling | Visible in |
|---|---|---|---|---|
| 1 | **Duplicate delivery** | Producer re-sends ~2% of events with identical `event_id`; consumer restart replays an uncommitted batch | Dedupe on `event_id`, keep earliest `received_at`. Must not collapse Two-Face's distinct duplicate requests. | Raw vs staged row count delta |
| 2 | **Out-of-order `client_ts`** | Shuffle client timestamps within a 30s window | Never order or watermark on `client_ts` | Model comments |
| 3 | **Unkeyed messages** | ~1% published with a null Kafka key | Land normally; the ordering test shows these sessions span partitions. Document the consequence. | `assert_session_ordering_preserved` |
| 4 | **Late arrivals** | ~1% carry `client_ts` 1–6 hours old | Partition on `received_at`; surface `late_arrival_count` | `int_session_features` |
| 5 | **Malformed JSON bodies** | ~3% truncated or unbalanced | `parse_json_safe()`; set `body_is_valid_json = false`. Valid events with invalid bodies, not quarantine cases. | `invalid_body_ratio` |
| 6 | **Undeserializable messages** | ~0.2% published as raw non-JSON bytes | Consumer writes to `data/quarantine/undeserializable/` with the offset, then continues. Must not halt. | Quarantine file count |
| 7 | **Missing required fields** | ~0.5% with null `source_ip` or null `path` | Null `path` → quarantine. Null `source_ip` → keep, feeds `null_ip_ratio` | `fct_quarantined_events` |
| 8 | **Schema drift** | Mid-run, bump `schema_version` to `v2`, add `tls_fingerprint` | Staging tolerates the new column; drift test flags the unknown version | dbt test output |
| 9 | **Clock skew** | ~1% with negative `response_time_ms` or future `received_at` | Future `received_at` → quarantine. Negative duration → clamp to null and count | `assert_no_negative_durations` |
| 10 | **Burst** | Occasional 10x rate spike | Consumer lag rises then recovers with no loss | Redpanda Console screenshot + reconciliation |

Record which pathologies ran in `attack_runs.pathologies_enabled`.

**This table is the single most valuable thing in the repository.** Most portfolio pipelines work
only on clean data. Reproduce it in the README with actual observed counts.

---

## Consumer restart exercise

Worth performing once and documenting in `docs/exercises.md`:

1. Start a Killer Croc attack with a long duration (highest volume)
2. `docker kill` the consumer mid-batch
3. Restart it
4. Observe the uncommitted batch replay and duplicates appear in `data/raw/`
5. Run `dbt run` and confirm `stg_attack_events` removes them
6. Record both counts

At-least-once semantics with downstream deduplication is among the most discussed topics in
streaming interviews and among the least often demonstrated.

---

## Reconciliation

`mart_reconciliation`, readable in ten seconds:

```
requests sent by simulator         : 12,847
messages landed in data/raw/       : 13,104   (+257 duplicates)
events after dedupe                : 12,847   ✓ matches sent
events quarantined                 :     71   (0.55%)
undeserializable, quarantined      :     26
events with invalid JSON body      :    391   (3.04%)
late arrivals detected             :    126   (0.98%)
sessions spanning >1 partition     :      4   (unkeyed messages, expected)
```

Committing this with real numbers does more for credibility than any amount of architecture prose.
