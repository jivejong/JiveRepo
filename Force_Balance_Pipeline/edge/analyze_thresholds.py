"""Rerun the threshold analysis behind doc 03's anomaly and emergency thresholds.

  python edge/analyze_thresholds.py --earliest-live 2026-09-26T14:27:00.000Z [--out FILE]

It regenerates the full local backfill in memory (the same generator, texture and seed as `backfill.py`, about a
minute), then prints Markdown tables: noise-only exceedances and detection for emergency thresholds 4.5 to 8.0 and
anomaly thresholds 3.0 to 8.0 in 0.25 steps, and the injection targets recomputed at each. It recommends nothing.
Nothing is written unless --out is given, and nothing touches the workspace.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backfill import TEXTURE_PATH, parse_utc  # noqa: E402
from forcesim import backfill as bf  # noqa: E402
from forcesim import thresholds  # noqa: E402
from forcesim.sectors import DEFAULT_SEED as SECTOR_PATH  # noqa: E402
from forcesim.sectors import load_sectors  # noqa: E402
from forcesim.window import make_window  # noqa: E402


def run_analysis(sectors, window, texture, seed):
    run = bf.Backfill(sectors, window, texture, seed)
    for _ in run.files():
        pass
    return thresholds.analyze(run)


def main(argv=None, sectors=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--earliest-live", required=True, help="the earliest live probe event_time in bronze (UTC, ends in Z)")
    p.add_argument("--seed", type=int, default=bf.DEFAULT_RUN_SEED)
    p.add_argument("--texture", default=str(TEXTURE_PATH))
    p.add_argument("--out", help="also write the tables to this file")
    args = p.parse_args(argv)
    sectors = sectors if sectors is not None else load_sectors(SECTOR_PATH)
    window = make_window(parse_utc(args.earliest_live))
    text = thresholds.format_markdown(run_analysis(sectors, window, bf.load_texture(args.texture, sectors), args.seed))
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline=chr(10)) as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
