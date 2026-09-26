# Enrichment provenance

<!-- Generated from the seeds/*.provenance.json sidecars by scripts/enrich_planets.py and scripts/enrich_jedi.py at promote time, or by scripts/rebuild_provenance.py. Do not edit by hand. -->

| Seed | Model ID | Temperature | Thinking level | Prompt version / hash | Generated | Regeneration cycles | Reviewed by | Rows |
|---|---|---|---|---|---|---|---|---|
| dim_sector.csv | gemini-3.1-flash-lite | 1.0 (default) | low | planets-v1 / 0ab79f1844ca | 2026-09-25 | 0 | jivejong | 60 |
| dim_jedi.csv | gemini-3.1-flash-lite | 1.0 (default) | low | jedi-v2 / 93521a8f269a | 2026-09-25 | 0 | jivejong | 17 |

Source: LLM-generated from model knowledge, human-reviewed. Not scraped from any wiki.
Reruns do not reproduce the committed values; the reviewed CSVs are the source of truth.
Regeneration invalidates the 90-day backfill and all derived baselines — see
docs/08-ai-enrichment.md.

## Review gate and override status

| Seed | Review gate | Override | Corrections | Promoted from | Promoted (UTC) |
|---|---|---|---|---|---|
| dim_sector.csv | PASS (failed before corrections) | no | 2 | `planets_20260925T024301Z_low_call1of1.json` | 2026-09-25T03:27:47Z |
| dim_jedi.csv | PASS | no | 0 | `jedi_20260925T124917Z_low_call1of1.json` | 2026-09-25T12:54:32Z |

## Recorded corrections

Human corrections to reviewed values, applied at promote time from `data/enrichment_corrections.csv` (doc 08).

### dim_sector.csv

Corrections file SHA-256: `0c11f3a804532355c2c6c37e1ce53a41e32588b728f72ab0a6c5f5d87fb7f4a7`

- `geonosis.dark_baseline`: 40 -> 55; dark_sigma 2.5 -> 3.4375. Reason: War-scarred Separatist foundry world, first battle of the Clone Wars, later sterilized by the Empire (a site of atrocity); model placed it at 40, tied with several ordinary worlds.
- `utapau.kyber_baseline`: 25 -> 80; kyber_sigma 3 -> 9.6. Reason: Clone Wars Utapau arc: a giant kyber crystal, a major crystal deposit per the prompt's 'very high'; model placed it at 25.

### dim_jedi.csv

None recorded.
