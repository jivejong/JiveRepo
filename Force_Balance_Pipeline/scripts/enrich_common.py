"""Shared helpers for the build-time enrichment scripts (doc 08).

Enrichment runs once, offline, and its reviewed output is committed as seed CSVs. Nothing here is
called at pipeline runtime. Standard library only.
"""
import csv
import hashlib
import json
import os
import re
import statistics
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = REPO_ROOT / "data" / "swapi_snapshot"
SEEDS_DIR = REPO_ROOT / "warehouse" / "dbt" / "seeds"

ENRICH_MODEL = "gemini-3.1-flash-lite"
THINKING_LEVELS = ("minimal", "low", "medium", "high")

# ASSUMPTION, not settled by the docs: multi-word ids are lowercase with underscores
# ("bestine_iv", "obi_wan_kenobi"). Doc 02 only shows the single-word "tatooine".
SLUG_SEP = "_"

# SWAPI planet 28 is named "unknown". It gets an explicit id and is never sent to the model (doc 08).
UNKNOWN_PLANET_ID = 28
UNKNOWN_SECTOR_ID = "uncharted"

REGIONS = ("Core Worlds", "Colonies", "Inner Rim", "Expansion Region", "Mid Rim", "Outer Rim",
           "Wild Space", "Unknown Regions")
SPECIALTIES = ("combat", "diplomacy", "investigation", "stealth")
RANKS = ("padawan", "knight", "master", "council_member", "grand_master")

# Review anchors (doc 08 rule 5): planets named in the doc 07 checkpoint or the doc 08 review
# checklist. They must never be named in enrichment prompt text. Ilum and Korriban are not in
# SWAPI but were named in earlier prompt drafts, so they stay on the list.
ANCHOR_NAMES = ("Mustafar", "Coruscant", "Utapau", "Ilum", "Dathomir", "Geonosis", "Naboo",
                "Alderaan", "Korriban")


def load_dotenv():
    """Load REPO_ROOT/.env into os.environ without overriding real environment variables."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if val and key.strip() not in os.environ:
            os.environ[key.strip()] = val


def get_api_key():
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set (put an AI Studio key in .env; see .env.example)")
    return key


def read_snapshot(name, snapshot_dir=SNAPSHOT_DIR):
    return json.loads((Path(snapshot_dir) / f"{name}.json").read_text(encoding="utf-8"))


def slugify(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", SLUG_SEP, ascii_text.lower()).strip(SLUG_SEP)


def url_id(url):
    m = re.search(r"/(\d+)/?$", url or "")
    if not m:
        raise ValueError(f"no numeric id in URL {url!r}")
    return int(m.group(1))


def sector_ids(planets):
    """Map SWAPI planet id -> sector_id. Every id is a slug of the SWAPI name, except planets/28.

    The unknown planet's id is explicit and never derived from its name (doc 08).
    """
    ids = {}
    for p in planets:
        pid = url_id(p["url"])
        ids[pid] = UNKNOWN_SECTOR_ID if pid == UNKNOWN_PLANET_ID else slugify(p["name"])
    if len(set(ids.values())) != len(ids):
        dupes = sorted({v for v in ids.values() if list(ids.values()).count(v) > 1})
        raise SystemExit(f"sector_id collisions after slugging: {dupes}")
    return ids


def assert_no_anchors(**texts):
    """Refuse to run if prompt text names a review anchor (doc 08 rule 5)."""
    for label, text in texts.items():
        for name in ANCHOR_NAMES:
            if re.search(rf"\b{name}\b", text, re.IGNORECASE):
                raise SystemExit(f"anchor {name!r} is named in the {label}; doc 08 rule 5 forbids it")


def prompt_hash(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def add_common_args(parser, seed_name):
    parser.add_argument("--thinking-level", choices=THINKING_LEVELS,
                        help="Gemini thinking level; recorded in provenance. Required except with "
                             "--print-prompts (OPEN until the first review pass, doc 08)")
    parser.add_argument("--model", default=ENRICH_MODEL, help=f"default: {ENRICH_MODEL}")
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR,
                        help="SWAPI snapshot to read (default: data/swapi_snapshot)")
    parser.add_argument("--out-dir", type=Path, default=SEEDS_DIR,
                        help=f"where {seed_name}.csv and its provenance sidecar are written")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing seed. Regeneration is a migration event (doc 08)")
    parser.add_argument("--print-prompts", action="store_true",
                        help="print the prompts, schema and request settings, then exit. No API call")
    parser.add_argument("--trial", action="store_true",
                        help="call the model for the first batch only, print the parsed rows, write nothing")


def require_thinking_level(args):
    if not args.print_prompts and not args.thinking_level:
        raise SystemExit("--thinking-level is required (minimal, low, medium or high)")


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: ("true" if v is True else "false" if v is False else v)
                        for k, v in row.items()})


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def median(values):
    return statistics.median(values)


def sidecar_path(out_dir, seed_name):
    return Path(out_dir) / f"{seed_name}.provenance.json"


def regeneration_cycles(out_dir, seed_name):
    """0 for a first generation, otherwise the previous count + 1 (a --force overwrite)."""
    side = sidecar_path(out_dir, seed_name)
    if side.exists():
        return int(json.loads(side.read_text(encoding="utf-8")).get("regeneration_cycles", 0)) + 1
    return 1 if (Path(out_dir) / f"{seed_name}.csv").exists() else 0


def provenance_record(seed, model, thinking_level, prompt_version, prompt_hash_, cycles, rows,
                      usage, warnings, extra=None):
    rec = {
        "seed": f"{seed}.csv",
        "model_id": model,
        "temperature": "1.0 (model default; not sent in the request)",
        "thinking_level": thinking_level,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash_,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regeneration_cycles": cycles,
        "rows": rows,
        "usage_tokens": usage,
        "warnings": warnings,
    }
    rec.update(extra or {})
    return rec


def write_sidecar(out_dir, seed, record):
    path = sidecar_path(out_dir, seed)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def provenance_table_row(rec):
    """A row for seeds/ENRICHMENT_PROVENANCE.md (doc 08). 'Reviewed by' is filled by hand."""
    return (f"| {rec['seed']} | {rec['model_id']} | 1.0 (default) | {rec['thinking_level']} | "
            f"{rec['prompt_version']} / {rec['prompt_hash']} | {rec['generated_utc'][:10]} | "
            f"{rec['regeneration_cycles']} | <you> | {rec['rows']} |")


def sum_usage(usages):
    total = {}
    for u in usages:
        for k, v in u.items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
    return total


def print_request_settings(model, thinking_level, prompt_version, prompt_hash_):
    print("--- request settings ---")
    print(f"endpoint:        generateContent (REST, via http_request; explicit User-Agent)")
    print(f"model:           {model}")
    print(f"temperature:     not sent (model default 1.0)")
    print(f"thinking level:  {thinking_level or '<--thinking-level, required for a real run>'}")
    print(f"prompt version:  {prompt_version}")
    print(f"prompt hash:     {prompt_hash_}")
