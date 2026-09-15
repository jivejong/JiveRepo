Placeholder for the committed sample partition (docs/05-local-environment.md).

A real Parquet file lands here in Phase 5, once the honeypot, simulator, and consumer exist and
have produced actual traffic. This placeholder exists only so the `.gitignore` negation pattern
(`!data/raw/**/sample-*`) has something real to match from Phase 0 onward, rather than going
untested until Phase 5.

The path itself is provisional (see conflict C in the Phase 0 plan) — the real Hive partition
layout is empirical output from Phase 4/5, not a decision made here.
