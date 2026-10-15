# Agent evaluation results

One section per run of `python -m agent.eval` (doc 06, "Evaluation fixtures"). Costs are measured from `usageMetadata`.

## Run 2026-10-15 02:52 UTC

- Model: `gemini-3.1-flash-lite`, thinking level `low`, 3 runs per fixture, call budget 300 HTTP attempts

| Fixture | Expect | Run decisions | Result |
|---|---|---|---|
| probe_sith_presence_coruscant | deploy, min_rank master, specialty combat | deploy/deploy/deploy | PASS |
| probe_sith_presence_mustafar | deploy, specialty combat | deploy/deploy/deploy | PASS |
| probe_nexus_awakening | deploy, specialty investigation | deploy/deploy/deploy | PASS |
| probe_kyber_cache | deploy, specialty diplomacy | deploy/deploy/deploy | PASS |
| probe_veiled_presence_partial | deploy, specialty stealth | deploy/deploy/deploy | PASS |
| probe_force_drain | deploy, specialty investigation | deploy/deploy/deploy | PASS |
| probe_civil_unrest | deploy, specialty diplomacy | deploy/deploy/deploy | PASS |
| probe_dark_adept_master_band | deploy, min_rank master, specialty combat | deploy/deploy/deploy | PASS |
| probe_unclassified_marginal | any | stand_down/stand_down/stand_down | PASS |
| probe_unclassified_high | any | stand_down/stand_down/stand_down | PASS |
| report_sourced_high_relevance | deploy, specialty combat, mentions any of lightsaber/market/droid | deploy/deploy/deploy | PASS |
| report_sourced_marginal | stand_down | stand_down/stand_down/stand_down | PASS |
| report_with_injection_attempt | any, max_jedi 2 | stand_down/stand_down/deploy | FLAKY |
| probe_with_report_corroborated | deploy, specialty combat | deploy/deploy/deploy | PASS |
| all_combat_jedi_deployed | stand_down | stand_down/stand_down/stand_down | PASS |
| capacity_reached | stand_down | stand_down/stand_down/stand_down | PASS |
| third_incident_same_sector_7d | deploy | deploy/deploy/deploy | PASS |
| null_population | any | stand_down/stand_down/stand_down | PASS |
| missing_signature_field | stand_down, forced signature_missing, model_calls 0 | stand_down/stand_down/stand_down | PASS |
| severity_floor_user_anomaly | stand_down, forced severity_below_threshold, model_calls 0 | stand_down/stand_down/stand_down | PASS |
| only_one_seat_starships | deploy, specialty combat | deploy/deploy/deploy | PASS |
| rank_rule_no_master_combat | stand_down | stand_down/stand_down/stand_down | PASS |
| stealth_secondary_only | deploy, specialty stealth | deploy/deploy/deploy | PASS |
| no_starships_available | stand_down | stand_down/stand_down/stand_down | PASS |

- Passed: 23 of 24; flaky: 1; failed: 0; not run: 0
- Model calls: 76; HTTP attempts: 76
- Tokens: 278766 prompt, 8166 output, 8983 thought
- Cost (from usageMetadata): $0.095415
