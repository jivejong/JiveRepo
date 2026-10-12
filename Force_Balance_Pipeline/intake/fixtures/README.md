# Intake fixtures

The report-inference model gates emergency creation, so it gets its own fixture suite (doc 04, "This model needs its own
fixture suite"). One JSON file per case. The offline tests in `intake/tests/` check the files and the assertion engine
against a stub model; `intake/run_fixtures.py` runs them against the real model (manual, never part of a default suite).

## A fixture

```json
{
  "name": "vader_in_the_market",
  "source": "doc 04, fixture table, row 2",
  "sector_id": "coruscant",
  "description": "Saw Darth Vader walking through the market",
  "must_cross_gold": true,
  "expect": {"outcome": "scored", "relevance": {"gt": 0.85}, "min_sigma": {"dark_side_activity": 4.0}}
}
```

`name` is the file name without `.json`. Give the text as `description`, or as `description_repeat` (`unit` repeated and
cut to exactly `chars` characters; used for the 5000-character ramble). The runner submits the same body the browser would,
through the same code path as the endpoint (`intake_core.assess_report`), so the outcome is what a real report would get.

## Outcomes

| Outcome | Meaning |
|---|---|
| `scored` | The model answered; the report would be stored. |
| `rejected` | The request failed validation before any model call (422, 413, 400). `code` names the reason. |
| `blocked` | The model refused the text (`blocked_by_model`, 422). Recorded on its own, never as an error or a pass. |
| `error` | The inference failed (503 territory: transport, rate limit, malformed answer). |

A fixture expects `scored` or `rejected`. A `blocked` or `error` result is always a failing iteration, and the record says which.

## Assertions (`expect`)

The runner checks only the model's numbers and the stored text. It does **not** compute the dbt imbalance score; whether a
report crosses 5.75 in `gold` is proven at the Phase 5 checkpoint, against the real baselines.

| Key | Meaning |
|---|---|
| `outcome` | `scored` or `rejected` |
| `code` | for `rejected`: the exact reject code |
| `inference_calls` | the number of model calls the submission made (0 for a request rejected before inference) |
| `relevance` | `{"gt": x}` and/or `{"lt": y}`, strict, on `relevance_score` |
| `min_sigma` | per channel, the signed distance from the planet's baseline in sigmas must be at least this: `(value - baseline) / sigma` |
| `max_abs_sigma` | every channel must be within this many sigmas of baseline, either way |

For every `scored` iteration the runner also requires that all four values lie inside their doc 02 ranges and that the
stored description equals the submitted one byte for byte.

## Planets

Every fixture with `"must_cross_gold": true` uses a coruscant-class planet: a high dark-side baseline with room to move
(coruscant: baseline 55, sigma 3.5). Never use **dathomir** or **mustafar** for a case that must cross 5.75: their dark-side
baselines are 90 and 95, so even the clamp of 100 cannot reach the threshold on the dark channel alone (found in the Round 0
comparison of the rolling baselines). The offline tests enforce both rules.

## Running

```
edge\.venv\Scripts\python.exe intake\run_fixtures.py --live                          # all fixtures, 3 iterations each
edge\.venv\Scripts\python.exe intake\run_fixtures.py --live --only vader_in_the_market --iterations 1
edge\.venv\Scripts\python.exe intake\run_fixtures.py --list
```

It needs `GEMINI_API_KEY` and `INFERENCE_MODEL` from the environment or `.env.intake`, and never prints the key. Each run
appends a record to `intake/eval_results.md` (`--results` to choose another file). Exit code 0: every iteration passed;
1: something failed, was blocked or errored; 2: usage or configuration error (nothing was called).
