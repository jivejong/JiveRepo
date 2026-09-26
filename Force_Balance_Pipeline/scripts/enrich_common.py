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


# Recorded human corrections (doc 08). A committed file OUTSIDE seeds/, because dbt loads every csv
# in seeds/ as a seed. Applied at promote time, before the review gate.
CORRECTIONS_FILE = REPO_ROOT / "data" / "enrichment_corrections.csv"
CORRECTIONS_HEADER = ["seed", "key", "column", "corrected_value", "reason"]
CORRECTABLE_SEEDS = ("dim_sector", "dim_jedi")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_corrections_file(path):
    """The rows of the corrections file, checked for shape only: header, known seed, every field
    filled (a correction without a reason is refused), no duplicate (seed, key, column). Lines
    starting with # and blank lines are ignored. A missing file means no corrections."""
    path = Path(path)
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    reader = csv.DictReader(lines)
    if reader.fieldnames != CORRECTIONS_HEADER:
        raise SystemExit(f"{path.name}: header must be exactly {','.join(CORRECTIONS_HEADER)}")
    rows, seen = [], set()
    for i, raw in enumerate(reader, 1):
        row = {k: (v or "").strip() for k, v in raw.items() if k is not None}
        if row["seed"] not in CORRECTABLE_SEEDS:
            raise SystemExit(f"{path.name} row {i}: seed must be one of {CORRECTABLE_SEEDS}, got {row['seed']!r}")
        blank = [k for k in CORRECTIONS_HEADER if not row.get(k)]
        if blank:
            raise SystemExit(f"{path.name} row {i}: empty {blank}; every field is required, including a reason")
        ident = (row["seed"], row["key"], row["column"])
        if ident in seen:
            raise SystemExit(f"{path.name} row {i}: duplicate correction for {ident}")
        seen.add(ident)
        rows.append(row)
    return rows


# Raw model responses are unreviewed and large, so they are saved outside the repo. They let a trial
# be re-analysed (enrich_planets.py --from-response) without calling the API again.
DEFAULT_RESPONSES_DIR = Path.home() / ".force_balance_pipeline" / "responses"


def resolve_responses_dir(path):
    path = Path(path).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path
    raise SystemExit(f"--responses-dir {path} is inside the repo; raw responses are saved outside it")


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def call_paths(responses_dir, script, thinking_level, call, calls, stamp):
    """(raw response path, metadata path) for one model call."""
    base = f"{script}_{stamp}_{thinking_level}_call{call}of{calls}"
    return Path(responses_dir) / f"{base}.json", Path(responses_dir) / f"{base}.meta.json"


def write_call_meta(meta_path, meta):
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def check_exact_ids(rows, expected_ids, id_key, label):
    """Enforce the exact entry count and exact id set of a model response.

    The response schema cannot pin the array length (Google rejects minItems/maxItems here, see
    gemini_client.build_request), so this is where the count is enforced. Raises ValueError, naming
    what is wrong, on any mismatch: wrong count, duplicates, missing ids, unexpected ids, or an
    entry that is not an object.
    """
    if not all(isinstance(r, dict) for r in rows):
        raise ValueError(f"every {label} entry must be an object")
    got = [str(r.get(id_key)) for r in rows]
    problems = []
    if len(rows) != len(expected_ids):
        problems.append(f"returned {len(rows)} {label} entries, expected {len(expected_ids)}")
    dupes = sorted({g for g in got if got.count(g) > 1})
    if dupes:
        problems.append(f"duplicate {id_key}: {dupes}")
    missing = sorted(set(expected_ids) - set(got))
    if missing:
        problems.append(f"missing {id_key}: {missing}")
    extra = sorted(set(got) - set(expected_ids))
    if extra:
        problems.append(f"unexpected {id_key}: {extra}")
    if problems:
        raise ValueError("; ".join(problems))


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
    parser.add_argument("--max-output-tokens", type=int, default=None,
                        help="generationConfig.maxOutputTokens, which counts thinking tokens too; "
                             "default is set per script (planets 65536, Jedi 16384) and recorded in "
                             "the provenance sidecar")
    parser.add_argument("--responses-dir", type=Path, default=DEFAULT_RESPONSES_DIR,
                        help="where each call's raw response (and a .meta.json) is saved; must be "
                             f"outside the repo (default: {DEFAULT_RESPONSES_DIR})")
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


def stamp_to_iso(stamp):
    """20260925T024007Z (as used in saved-response file names) -> 2026-09-25T02:40:07Z."""
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")


def meta_path_for(raw_path):
    p = Path(raw_path)
    return p.with_name(p.stem + ".meta.json")


def provenance_record(seed, model, thinking_level, prompt_version, prompt_hash_, cycles, rows,
                      usage, warnings, extra=None, generated_utc=None):
    rec = {
        "seed": f"{seed}.csv",
        "model_id": model,
        "temperature": "1.0 (model default; not sent in the request)",
        "thinking_level": thinking_level,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash_,
        "generated_utc": generated_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regeneration_cycles": cycles,
        "rows": rows,
        "usage_tokens": usage,
        "warnings": warnings,
    }
    rec.update(extra or {})
    return rec


def write_sidecar(out_dir, seed, record):
    path = sidecar_path(out_dir, seed)
    with path.open("w", encoding="utf-8", newline="\n") as f:  # LF on every OS (.gitattributes: *.json eol=lf)
        f.write(json.dumps(record, indent=2) + "\n")
    return path


def provenance_table_row(rec):
    """A row of the seeds/ENRICHMENT_PROVENANCE.md table (doc 08). 'Reviewed by' comes from the
    sidecar's `reviewed_by` (set with rebuild_provenance.py --reviewed-by), else the <you> placeholder."""
    return (f"| {rec['seed']} | {rec['model_id']} | 1.0 (default) | {rec['thinking_level']} | "
            f"{rec['prompt_version']} / {rec['prompt_hash']} | {rec['generated_utc'][:10]} | "
            f"{rec['regeneration_cycles']} | {_md(rec.get('reviewed_by') or '<you>')} | {rec['rows']} |")


# seeds/ENRICHMENT_PROVENANCE.md is generated from the sidecars, never edited by hand (doc 08).
PROVENANCE_MD = "ENRICHMENT_PROVENANCE.md"
PROVENANCE_SEED_ORDER = ("dim_sector", "dim_jedi")
SIDECAR_SUFFIX = ".provenance.json"
JINJA_TOKENS = ("{%", "{#", "{{")


def _md(text):
    """One line of text that is safe inside a markdown table cell or list item."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _num(value):
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def _iso_or_raw(stamp):
    try:
        return stamp_to_iso(stamp)
    except (TypeError, ValueError):
        return str(stamp) if stamp else "not recorded"


def read_sidecars(out_dir):
    """seed name -> sidecar record, for every *.provenance.json in out_dir (doc order first)."""
    found = {}
    for path in sorted(Path(out_dir).glob(f"*{SIDECAR_SUFFIX}")):
        found[path.name[:-len(SIDECAR_SUFFIX)]] = json.loads(path.read_text(encoding="utf-8"))
    ordered = [s for s in PROVENANCE_SEED_ORDER if s in found] + [s for s in found if s not in PROVENANCE_SEED_ORDER]
    return {s: found[s] for s in ordered}


def _gate_status(rec):
    gate = rec.get("review_gate")
    if gate is None:
        return "not recorded"
    status = "PASS" if gate.get("pass") else "FAIL"
    pre = rec.get("review_gate_pre_correction")
    if gate.get("pass") and pre is not None and not pre.get("pass"):
        status += " (failed before corrections)"
    return status


def _override_status(rec):
    if "accepted_failing_gate" not in rec:
        return "not recorded"
    return "OVERRIDDEN (--accept-failing-gate)" if rec["accepted_failing_gate"] else "no"


def _correction_line(c):
    line = (f"- `{c.get('sector_id', c.get('key', '?'))}.{c['column']}`: {_num(c['original_baseline'])} -> "
            f"{_num(c['corrected_baseline'])}; {c['sigma_column']} {_num(c['sigma_before'])} -> "
            f"{_num(c['sigma_final'])}")
    if c.get("band_adjusted"):
        line += f" (clamped to the sigma band from {_num(c['sigma_rescaled'])})"
    return f"{line}. Reason: {_md(c['reason'])}"


def render_provenance_md(records):
    """The text of ENRICHMENT_PROVENANCE.md from {seed name: sidecar record}. A pure function of the
    sidecars, so rebuilding it from unchanged sidecars gives identical bytes."""
    lines = [
        "# Enrichment provenance", "",
        f"<!-- Generated from the seeds/*{SIDECAR_SUFFIX} sidecars by scripts/enrich_planets.py and "
        "scripts/enrich_jedi.py at promote time, or by scripts/rebuild_provenance.py. Do not edit by hand. -->",
        "",
        "| Seed | Model ID | Temperature | Thinking level | Prompt version / hash | Generated | "
        "Regeneration cycles | Reviewed by | Rows |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [provenance_table_row(rec) for rec in records.values()]
    lines += [
        "",
        "Source: LLM-generated from model knowledge, human-reviewed. Not scraped from any wiki.",
        "Reruns do not reproduce the committed values; the reviewed CSVs are the source of truth.",
        "Regeneration invalidates the 90-day backfill and all derived baselines — see",
        "docs/08-ai-enrichment.md.",
        "",
        "## Review gate and override status", "",
        "| Seed | Review gate | Override | Corrections | Promoted from | Promoted (UTC) |",
        "|---|---|---|---|---|---|",
    ]
    for rec in records.values():
        promoted_from = ", ".join(f"`{n}`" for n in rec.get("promoted_from", [])) or "not recorded"
        lines.append(f"| {rec['seed']} | {_gate_status(rec)} | {_override_status(rec)} | "
                     f"{len(rec.get('corrections') or [])} | {_md(promoted_from)} | "
                     f"{_iso_or_raw(rec.get('promoted_utc'))} |")
    lines += ["", "## Recorded corrections", "",
              "Human corrections to reviewed values, applied at promote time from "
              "`data/enrichment_corrections.csv` (doc 08).", ""]
    for rec in records.values():
        corrections = rec.get("corrections") or []
        lines.append(f"### {rec['seed']}")
        lines.append("")
        if not corrections:
            lines += ["None recorded.", ""]
            continue
        sha = rec.get("corrections_file_sha256")
        if sha:
            lines += [f"Corrections file SHA-256: `{sha}`", ""]
        lines += [_correction_line(c) for c in corrections]
        lines.append("")
    text = "\n".join(lines).rstrip("\n") + "\n"
    # dbt reads every .md under seed-paths as a docs file and runs Jinja block extraction on it, so an
    # unbalanced "{%" or "{#" in a correction reason would break every dbt command. Neutralise them.
    for token in JINJA_TOKENS:
        text = text.replace(token, f"&#123;{token[1]}")
    return text


def write_provenance_md(out_dir):
    """Rebuild ENRICHMENT_PROVENANCE.md in out_dir from its sidecars. Returns (path, sidecar count),
    or (None, 0) when there is no sidecar to build from. Touches no CSV."""
    records = read_sidecars(out_dir)
    if not records:
        return None, 0
    path = Path(out_dir) / PROVENANCE_MD
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(render_provenance_md(records))
    return path, len(records)


def sum_usage(usages):
    total = {}
    for u in usages:
        for k, v in u.items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
    return total


STRUCTURED_OUTPUT = "responseMimeType + responseJsonSchema"


def print_request_settings(model, thinking_level, prompt_version, prompt_hash_, max_output_tokens):
    print("--- request settings ---")
    print(f"endpoint:        generateContent (REST, via http_request; explicit User-Agent)")
    print(f"structured out:  {STRUCTURED_OUTPUT}")
    print(f"max output:      {max_output_tokens} tokens (counts thinking tokens too)")
    print(f"model:           {model}")
    print(f"temperature:     not sent (model default 1.0)")
    print(f"thinking level:  {thinking_level or '<--thinking-level, required for a real run>'}")
    print(f"prompt version:  {prompt_version}")
    print(f"prompt hash:     {prompt_hash_}")
