"""CLI entry for `make attack` / `make attack-all`.

Runs one villain (or all twelve) against the honeypot with the deliberate
pathologies enabled (services/simulator/pathologies.yml), publishing request
events (via the honeypot), attempt events, and one attack_run per run.

`--time-scale` compresses the idle gaps between requests (1.0 = faithful; the
default is small so a run finishes quickly). The value is recorded on each
attack_run as timing_compression_factor, so the corpus stays self-describing.
"""

from __future__ import annotations

import argparse
import random

from services.simulator.catalog import load_villains
from services.simulator.pathologies import PathologyConfig, PathologyInjector
from services.simulator.session import run_scripted_session


def _run_one(slug: str, seed: int, time_scale: float, no_pathologies: bool) -> None:
    import time

    rng = random.Random(seed)
    injector = None if no_pathologies else PathologyInjector(PathologyConfig.load(), rng)
    result = run_scripted_session(
        slug,
        rng=rng,
        sleep_fn=lambda s: time.sleep(s * time_scale),
        injector=injector,
        timing_compression_factor=time_scale,
    )
    outcome = "cleared" if result.max_stage_reached >= 4 else f"stalled@{result.max_stage_reached}"
    print(
        f"{slug:18} run={result.run_id[:8]} session={result.session_id[:8]} "
        f"attempts={len(result.attempts):3} requests={result.requests_sent:3} {outcome}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--villain", help="villain slug; omit with --all")
    ap.add_argument("--all", action="store_true", help="run all twelve sequentially")
    ap.add_argument("--runs", type=int, default=1, help="runs per villain")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--time-scale", type=float, default=0.05)
    ap.add_argument("--no-pathologies", action="store_true", help="clean stream")
    args = ap.parse_args()

    slugs = list(load_villains()) if args.all else [args.villain]
    if not args.all and not args.villain:
        ap.error("give --villain <slug> or --all")

    for vi, slug in enumerate(slugs):
        if slug not in load_villains():
            ap.error(f"unknown villain slug: {slug}")
        for r in range(args.runs):
            _run_one(slug, args.seed + vi * 1000 + r, args.time_scale, args.no_pathologies)


if __name__ == "__main__":
    main()
