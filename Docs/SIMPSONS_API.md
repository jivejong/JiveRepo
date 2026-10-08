# The Simpsons API Integration

## What it's used for here

The entire candidate pool. No auth, no key, nothing to register.

## Base URL and auth

```
https://thesimpsonsapi.com/api
```

Fully open, no authentication of any kind.

## Endpoints used

| Endpoint | Purpose |
|---|---|
| `GET /characters?page={n}` | Paginated list — **exactly 20 items per page, not configurable** |
| `GET /characters/{id}` | Single character detail |

1,182 characters total across exactly 60 pages, confirmed against the live API on 2026-09-09.
`count` and `pages` come back in every list response, so the sync reads the page count from page 1
rather than hardcoding 60.

## Character fields

```json
{
  "id": 1,
  "age": 39,
  "birthdate": "1956-05-12",
  "gender": "Male",
  "name": "Homer Simpson",
  "occupation": "Safety Inspector",
  "portrait_path": "/character/1.webp",
  "phrases": ["Doh!", "Why you little...!", "..."],
  "status": "Alive"
}
```

Not every character has every field populated — `age` in particular is frequently absent for minor/background characters. Map missing fields to null, don't default them to something that looks like real data (e.g. don't default a missing age to 0).

### How sparse, measured (all 1,182 records, 2026-09-09)

| Field | Null/empty | Notes |
|---|---|---|
| `age` | 1,115 of 1,182 (94%) | present for only 67 characters; one joke value of `1000000` is real source data, not corruption |
| `phrases` | 911 of 1,182 (77%) | up to 15 per character, longest 706 chars |
| `occupation` | **0** | populated for every single character — which matters, since it's the field requisition matching runs against |
| `name` | 0 | longest 58 chars |
| `birthdate` | 1,163 of 1,182 (98%) | returned by the API but not modelled in `DATA_MODEL.md` |

### `status` — seven values, not three

**This spec originally predicted `Alive` / `Dead` / `Unknown`. That was wrong.** The live API
returns seven distinct values and never returns `Dead`:

| Value | Count |
|---|---|
| `Alive` | 855 |
| `Deceased` | 136 |
| `Unknown` | 126 |
| `Fictional` | 50 |
| `Noncanon` | 9 |
| `Noncanon Deceased` | 4 |
| `Destroyed Icon` | 2 |

Modelling only the predicted three would misfile 201 records (17% of the pool). The implemented
`CharacterStatus` enum covers all seven and falls back to `UNKNOWN` with a logged warning for any
value added upstream later — a new value costs one record's fidelity instead of failing its
import.

## Images

**Confirmed 2026-09-09** (this section previously said to go and check rather than guess — this is
the result of doing that). The CDN is:

```
https://cdn.thesimpsonsapi.com/{width}/character/{id}.webp
```

Valid widths are `200`, `500` and `1280`; other values return 403. So Homer's
`portrait_path` of `/character/1.webp` resolves to
`https://cdn.thesimpsonsapi.com/500/character/1.webp`.

The base is configuration (`simpsons.cdn-base-url`), not a hardcoded constant, and the raw
`portrait_path` is what gets stored — so a CDN change is a config edit, not a re-sync. Portraits
remain a nice-to-have for the UI, not load-bearing for the pipeline mechanics.

## Sync strategy

- One-time (or re-runnable) batch job, not called live per-request — this is reference data that barely changes.
- No published rate limit, but be a reasonable citizen: a small delay between page requests (e.g. 150–250ms) rather than firing all ~60 page requests concurrently.
- Upsert on `externalId` (the API's `id`) so re-running the sync updates existing candidates rather than duplicating them.
- Log a summary (characters added/updated) rather than failing the whole sync on one bad record — same isolate-and-report pattern used across this portfolio's other import jobs.

### As built and measured

A full run takes **roughly 20 seconds** (60 pages, 200ms pause between them) and the endpoint
returns the summary as its response body rather than a bare 200:

```json
{"startedAt":"...","durationMs":19275,"pagesExpected":60,"pagesFetched":60,"pagesFailed":0,
 "recordsSeen":1182,"created":1182,"updated":0,"failed":0,"failures":[]}
```

Isolation happens at two levels, not one:

- **Per record** — each character is upserted in its own transaction, so a record that fails to map
  rolls back only itself. This is why the upsert lives in its own bean: a self-invoked
  `@Transactional` method is bypassed by the Spring proxy and would silently join the caller's
  transaction, letting one bad record poison the batch.
- **Per page** — a page that won't load costs its 20 records, not the other 1,160. The one
  exception is page 1: without it there is no page count and no run to salvage, so that aborts and
  the endpoint returns **502** (the fault is upstream, not ours).

Re-running is safe and verified: a second run reports `created: 0, updated: 1182`, the row count
stays at 1,182, primary keys are unchanged, `importedAt` is preserved while `lastSyncedAt`
advances, and phrase rows do not accumulate.

## Licensing and attribution

The API's own site states it sources data from The Simpsons Wiki under CC BY-SA. Required credit line (already included in the root `README.md`):

> Character data provided by [The Simpsons API](https://thesimpsonsapi.com), sourced from The Simpsons Wiki (CC BY-SA).

The Simpsons itself is Fox/Disney IP — this project is a non-commercial portfolio demo, not a product, and should say so plainly in the README rather than assuming that's obvious.
