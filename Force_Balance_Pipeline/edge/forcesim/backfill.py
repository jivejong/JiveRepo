"""The 90-day backfill generator (doc 07, Phase 2 step 5; doc 04, "Backfill window").

It runs the same SimProbe.sweep() as the live probe, one file per scan (60 events), over the window from
window.py, with the texture from backfill_texture.json: drift on a few planets, injected historical
emergencies, one slow riser, and DISCONNECTED gaps followed by a BURST drain. Everything is deterministic
from (seed, window, texture, dim_sector), so a regeneration is byte-identical and `verify` can prove it.

Rows and their simulated ingest time:
  * A normal scan: synthetic_ingest_ts = scan time + 4.0-5.0 s (seeded jitter), so the lag is a few seconds.
  * A gap (DISCONNECTED) keeps every row. Its events are drained when the link returns, `reconnect_lead_seconds`
    before the next scan, in batches of `batch_events` in event_time order, `batch_pause_seconds` apart. Each
    keeps mode DISCONNECTED (doc 02: a buffered reading keeps its mode) and gets the time its batch was
    published (+1 s).
  * The scan taken while a drain is still running carries mode BURST (doc 02: scans taken during a drain).

Standard library only.
"""
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .constants import CHANNELS, SCANS_PER_DAY
from .envelope import check_envelope, format_ts, new_ulid, to_ndjson_line, ts_ms
from .probe import SimProbe
from .sectors import DEFAULT_SEED as DIM_SECTOR_PATH
from .sectors import sha256_file
from .signatures import (ANOMALY_THRESHOLD, EMERGENCY_THRESHOLD, MARGIN_K, PRODUCIBLE, SUSTAINED_SCANS, classify,
                         imbalance_score, margin_factor, target_for)
from .walk import Drift, stream
from .window import INTERVAL, SCANS, BackfillWindow, OverlapGuard

GENERATOR_VERSION = 1
MANIFEST_VERSION = 1
TEXTURE_VERSION = 1
SOURCE_ID = "probe-01"
DEFAULT_RUN_SEED = 20260926
NORMAL_LATENCY_MS = 4000      # a scan reaches bronze 4.0-5.0 s after it starts (the last reading is at +2.95 s)
LATENCY_JITTER_MS = 1000
DRAIN_LATENCY = timedelta(seconds=1)
REPLAYED_LAG_SECONDS = 1800   # doc 03: is_replayed = ingest_lag_seconds > 1800
CONTENT_HASH_METHOD = ("sha256 over the files in ascending file-name order; for each file: its name, a NUL, its byte "
                       "length in decimal, a NUL, then its bytes")


class BackfillError(ValueError):
    """The texture, the window or the generated data is not acceptable."""


# ---- texture ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DriftSpec:
    sector: str
    channel: str
    total_sigma: float


@dataclass(frozen=True)
class EmergencySpec:
    sector: str
    signature: str
    day: int            # days before the window end, -90..-1
    scan_of_day: int    # 0-95
    ramp: int
    hold: int
    decay: int

    @property
    def start_scan(self):
        return SCANS + self.day * SCANS_PER_DAY + self.scan_of_day

    @property
    def end_scan(self):  # exclusive
        return self.start_scan + self.ramp + self.hold + self.decay


@dataclass(frozen=True)
class GapSpec:
    day: int
    scan_of_day: int
    scans: int

    @property
    def first(self):
        return SCANS + self.day * SCANS_PER_DAY + self.scan_of_day

    @property
    def end(self):  # exclusive: the scan taken as the link returns
        return self.first + self.scans


@dataclass(frozen=True)
class Texture:
    drifts: tuple
    emergencies: tuple
    riser: DriftSpec
    gaps: tuple
    batch_events: int
    batch_pause_seconds: int
    reconnect_lead_seconds: int


def _require(condition, message):
    if not condition:
        raise BackfillError(message)


def _int(obj, key, where, low=None, high=None):
    value = obj.get(key)
    _require(isinstance(value, int) and not isinstance(value, bool), f"{where}.{key} must be an integer")
    _require(low is None or value >= low, f"{where}.{key} must be at least {low}")
    _require(high is None or value <= high, f"{where}.{key} must be at most {high}")
    return value


def _drift(obj, where, sector_ids):
    _require(obj.get("sector") in sector_ids, f"{where}.sector {obj.get('sector')!r} is not a sector")
    _require(obj.get("channel") in CHANNELS, f"{where}.channel must be one of {CHANNELS}")
    total = obj.get("total_sigma")
    _require(isinstance(total, (int, float)) and not isinstance(total, bool) and total != 0,
             f"{where}.total_sigma must be a non-zero number")
    return DriftSpec(obj["sector"], obj["channel"], float(total))


def load_texture(path, sectors):
    """The texture file as a Texture, or BackfillError. `sectors` are the sectors the run will use."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    sector_ids = {s.sector_id for s in sectors}
    _require(obj.get("version") == TEXTURE_VERSION, f"texture version must be {TEXTURE_VERSION}")
    drifts = tuple(_drift(d, f"drifts[{i}]", sector_ids) for i, d in enumerate(obj.get("drifts", [])))
    riser = _drift(obj.get("riser") or {}, "riser", sector_ids)
    _require(riser.channel == "dark", "the slow riser is a dark side trend")
    emergencies = []
    for i, e in enumerate(obj.get("emergencies", [])):
        where = f"emergencies[{i}]"
        _require(e.get("sector") in sector_ids, f"{where}.sector {e.get('sector')!r} is not a sector")
        _require(e.get("signature") in PRODUCIBLE, f"{where}.signature must be one of {PRODUCIBLE}")
        spec = EmergencySpec(e["sector"], e["signature"], _int(e, "day", where, -90, -1),
                             _int(e, "scan_of_day", where, 0, SCANS_PER_DAY - 1), _int(e, "ramp", where, 1),
                             _int(e, "hold", where, SUSTAINED_SCANS), _int(e, "decay", where, 1))
        _require(spec.end_scan <= SCANS, f"{where} runs past the end of the window")
        emergencies.append(spec)
    gaps = []
    for i, g in enumerate(obj.get("gaps", [])):
        where = f"gaps[{i}]"
        spec = GapSpec(_int(g, "day", where, -90, -1), _int(g, "scan_of_day", where, 0, SCANS_PER_DAY - 1),
                       _int(g, "scans", where, 1))
        _require(spec.first >= 1 and spec.end < SCANS, f"{where} must leave a scan before it and the recovery scan "
                                                       "inside the window")
        gaps.append(spec)
    burst = obj.get("burst") or {}
    texture = Texture(drifts, tuple(emergencies), riser, tuple(sorted(gaps, key=lambda g: g.first)),
                      _int(burst, "batch_events", "burst", 1), _int(burst, "batch_pause_seconds", "burst", 1),
                      _int(burst, "reconnect_lead_seconds", "burst", 0))
    ordered = texture.gaps
    for a, b in zip(ordered, ordered[1:]):
        _require(b.first > a.end, f"the gap on day {b.day} starts before the drain of the gap on day {a.day} is over")
    for e in texture.emergencies:
        for g in ordered:
            _require(e.end_scan <= g.first or e.start_scan > g.end,
                     f"the {e.signature} emergency on {e.sector} overlaps the gap on day {g.day}")
    return texture


# ---- gaps, modes and the simulated ingest time ----------------------------------------------------------
@dataclass(frozen=True)
class GapPlan:
    first: int
    end: int
    reconnect: datetime
    events: int
    batches: int
    drain_end: datetime


class Plan:
    """Which scans are DISCONNECTED or BURST and when each event reaches bronze."""

    def __init__(self, window, texture, seed, planets):
        self.seed = seed
        self.planets = planets
        self.times = window.scan_times()
        self.batch_events = texture.batch_events
        self.pause = timedelta(seconds=texture.batch_pause_seconds)
        self._scan_ingest = {}
        self.gaps = []
        for g in texture.gaps:
            events = g.scans * planets
            batches = -(-events // texture.batch_events)
            reconnect = self.times[g.end] - timedelta(seconds=texture.reconnect_lead_seconds)
            drain_end = reconnect + batches * self.pause
            if drain_end >= self.times[g.end] + INTERVAL:
                raise BackfillError(f"the drain of the gap on day {g.day} ({batches} batches of "
                                    f"{texture.batch_events}, {texture.batch_pause_seconds} s apart) is still running "
                                    "at the scan after the recovery scan")
            self.gaps.append(GapPlan(g.first, g.end, reconnect, events, batches, drain_end))

    def gap_of(self, i):
        for g in self.gaps:
            if g.first <= i < g.end:
                return g
        return None

    def mode(self, i):
        if self.gap_of(i) is not None:
            return "DISCONNECTED"
        if any(g.reconnect <= self.times[i] < g.drain_end for g in self.gaps):
            return "BURST"
        return "CONNECTED"

    def scan_ingest(self, i):
        if i not in self._scan_ingest:
            jitter = int(stream(self.seed, "latency", i).random() * LATENCY_JITTER_MS)
            self._scan_ingest[i] = self.times[i] + timedelta(milliseconds=NORMAL_LATENCY_MS + jitter)
        return self._scan_ingest[i]

    def event_ingest(self, i, j):
        gap = self.gap_of(i)
        if gap is None:
            return self.scan_ingest(i)
        ordinal = (i - gap.first) * self.planets + j
        return gap.reconnect + (ordinal // self.batch_events) * self.pause + DRAIN_LATENCY


# ---- the run ------------------------------------------------------------------------------------------------
def _parse_ts(text):
    """An envelope timestamp (ISO 8601 UTC, milliseconds, Z) as an aware datetime."""
    if not text.endswith("Z"):
        raise BackfillError(f"{text!r} is not a UTC timestamp ending in Z")
    return datetime.fromisoformat(text[:-1] + "+00:00")


class ContentHash:
    """The manifest's content hash, fed one file at a time in ascending file-name order."""

    def __init__(self):
        self._h = hashlib.sha256()
        self._last = None
        self.files = 0
        self.bytes = 0

    def add(self, name, data):
        if self._last is not None and name <= self._last:
            raise BackfillError(f"{name} does not sort after {self._last}: file names must ascend with scan time")
        self._last = name
        self._h.update(name.encode("utf-8") + bytes([0]) + str(len(data)).encode("ascii") + bytes([0]) + data)
        self.files += 1
        self.bytes += len(data)

    def hexdigest(self):
        return self._h.hexdigest()


class Backfill:
    """One backfill run. files() yields (file name, bytes) for the scans in `scan_range` (all by default)
    in scan order; the planets are stepped through every scan either way, so a slice is identical to the same
    files of a full run. After files() is exhausted, report() describes the whole window."""

    def __init__(self, sectors, window, texture, seed, source_id=SOURCE_ID, scan_range=None):
        self.sectors = list(sectors)
        self.window = window.validate() if isinstance(window, BackfillWindow) else window
        self.texture = texture
        self.seed = seed
        self.source_id = source_id
        self.scan_range = scan_range or (0, SCANS)
        _require(0 <= self.scan_range[0] < self.scan_range[1] <= SCANS, f"scan range {self.scan_range} is outside 0-{SCANS}")
        self.plan = Plan(self.window, texture, seed, len(self.sectors))
        self.series = {s.sector_id: {ch: [] for ch in CHANNELS} for s in self.sectors}
        self.mode_rows = {}
        self.lag_rows = 0
        self.max_lag_seconds = 0.0
        self.rows = 0
        self.probe = None
        self._done = False

    def _build_probe(self):
        drifts = {}
        for d in self.texture.drifts + (self.texture.riser,):
            drifts.setdefault(d.sector, []).append(Drift(d.channel, d.total_sigma, 0, SCANS - 1))
        probe = SimProbe(self.sectors, seed=self.seed, source_id=self.source_id, drifts=drifts)
        for e in self.texture.emergencies:
            probe.inject(e.sector, e.signature, e.start_scan, e.ramp, e.hold, e.decay)
        return probe

    def file_name(self, i):
        ulid = new_ulid(ts_ms(self.plan.times[i]), stream(self.seed, "file", i))
        return f"{self.source_id}-{ulid}.ndjson"

    def files(self):
        self.probe = probe = self._build_probe()
        guard = OverlapGuard(self.window.earliest_live)
        first, stop = self.scan_range
        for i, scan_time in enumerate(self.plan.times):
            mode = self.plan.mode(i)
            envelopes = probe.sweep(i, scan_time, mode=mode, is_synthetic=True,
                                    synthetic_ingest_ts=self.plan.scan_ingest(i))
            lines = []
            for j, e in enumerate(envelopes):
                event_time = _parse_ts(e["event_time"])
                guard.check(event_time)
                ingest = self.plan.event_ingest(i, j)
                if ingest < event_time:
                    raise BackfillError(f"scan {i}: event {j} would be ingested before it happened")
                e["synthetic_ingest_ts"] = format_ts(ingest)
                problems = check_envelope(e)
                if problems:
                    raise BackfillError(f"scan {i}, {e['sector_id']}: {problems}")
                lag = (ingest - event_time).total_seconds()
                self.lag_rows += lag > REPLAYED_LAG_SECONDS
                self.max_lag_seconds = max(self.max_lag_seconds, lag)
                for ch, key in (("midi", "midichlorian_ppm"), ("kyber", "kyber_resonance"), ("dark", "dark_side_activity")):
                    self.series[e["sector_id"]][ch].append(e["payload"][key])
                self.mode_rows[mode] = self.mode_rows.get(mode, 0) + 1
                self.rows += 1
                lines.append(to_ndjson_line(e))
            if first <= i < stop:
                yield self.file_name(i), "".join(lines).encode("utf-8")
        self._done = True

    # ---- report -----------------------------------------------------------------------------------------
    def scores(self):
        """(z-scores, composites) per sector: every reading against the mean and sample SD of its channel over
        the whole window (what the 90-day baseline is once the backfill is loaded), all rows kept."""
        if not self._done:
            raise BackfillError("the scores need the whole window: exhaust files() first")
        composites = {}
        zs = {}
        for s in self.sectors:
            cols = self.series[s.sector_id]
            stat = {}
            for ch in CHANNELS:
                xs = cols[ch]
                mean = math.fsum(xs) / len(xs)
                sd = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (len(xs) - 1))
                stat[ch] = (mean, sd)
            z = {ch: [(x - stat[ch][0]) / stat[ch][1] for x in cols[ch]] for ch in CHANNELS}
            zs[s.sector_id] = z
            composites[s.sector_id] = [imbalance_score(a, b, c) for a, b, c in zip(z["midi"], z["kyber"], z["dark"])]
        return zs, composites

    def report(self):
        if not self._done:
            raise BackfillError("the report needs the whole window: exhaust files() first")
        zs, composites = self.scores()
        by_id = {s.sector_id: s for s in self.sectors}

        def r(x):
            return round(x, 3)

        emergencies = []
        for spec in self.texture.emergencies:
            z, comp, pop = zs[spec.sector], composites[spec.sector], by_id[spec.sector].population
            hold_scans = []
            for i in range(spec.start_scan + spec.ramp, spec.start_scan + spec.ramp + spec.hold):
                hold_scans.append({"scan_utc": format_ts(self.plan.times[i]), "z_midi": r(z["midi"][i]),
                                   "z_kyber": r(z["kyber"][i]), "z_dark": r(z["dark"][i]),
                                   "composite": r(comp[i]),
                                   "classification": classify(z["midi"][i], z["kyber"][i], z["dark"][i], pop)})
            run = best = 0
            for h in hold_scans:
                run = run + 1 if (h["composite"] > EMERGENCY_THRESHOLD and h["classification"] == spec.signature) else 0
                best = max(best, run)
            emergencies.append({"sector": spec.sector, "signature": spec.signature,
                                "start_utc": format_ts(self.plan.times[spec.start_scan]), "ramp": spec.ramp,
                                "hold": spec.hold, "decay": spec.decay, "hold_scans": hold_scans,
                                "min_hold_composite": min(h["composite"] for h in hold_scans),
                                "consecutive_scans_at_emergency_level_as_signature": best,
                                "all_hold_scans_classify_as_signature":
                                    all(h["classification"] == spec.signature for h in hold_scans)})

        rs = self.texture.riser.sector
        z, comp = zs[rs], composites[rs]
        last_day = slice(SCANS - SCANS_PER_DAY, SCANS)
        day_z = {ch: math.fsum(z[ch][last_day]) / SCANS_PER_DAY for ch in CHANNELS}
        others = sorted(max(c) for sid, c in composites.items() if sid != rs)
        riser = {"sector": rs, "channel": "dark", "total_sigma": self.texture.riser.total_sigma,
                 "spike_probability": by_id[rs].dark_spike_probability,
                 "last_scan_z_dark": r(z["dark"][-1]), "last_scan_composite": r(comp[-1]),
                 "last_day_mean_z_dark": r(day_z["dark"]),
                 "last_day_mean_composite": r(imbalance_score(day_z["midi"], day_z["kyber"], day_z["dark"])),
                 "trend_below_anomaly": imbalance_score(day_z["midi"], day_z["kyber"], day_z["dark"]) < ANOMALY_THRESHOLD,
                 "max_composite": r(max(comp)),
                 "scans_over_anomaly": sum(c > ANOMALY_THRESHOLD for c in comp),
                 "consecutive_pairs_over_anomaly": _pairs(comp, ANOMALY_THRESHOLD),
                 "other_planets_max_composite": {"min": r(others[0]), "median": r(others[len(others) // 2]),
                                                 "max": r(others[-1])}}

        injected = {s: set() for s in by_id}
        ambient_cover = {s: set() for s in by_id}
        ambient_episodes = []
        ambient_ranges = []
        for sid, planet in self.probe.planets.items():
            for e in planet.episodes:
                (injected if e.injected else ambient_cover)[sid].update(range(e.start, e.end))
                if not e.injected:
                    ambient_episodes.append(sid)
                    ambient_ranges.append((sid, e.start, min(e.end, SCANS)))
        n_scans = SCANS * len(self.sectors)
        ambient = {"episodes": len(ambient_episodes), "planets_with_episodes": len(set(ambient_episodes)),
                   "expected_episodes": r(sum(s.dark_spike_probability for s in self.sectors) * SCANS / SCANS_PER_DAY)}
        for name, threshold in (("anomaly", ANOMALY_THRESHOLD), ("emergency", EMERGENCY_THRESHOLD)):
            allc = sum(sum(c > threshold for c in comp) for comp in composites.values())
            outside_inj = outside_all = pairs = pairs_outside_all = 0
            for sid, comp in composites.items():
                over = [c > threshold for c in comp]
                outside_inj += sum(o and i not in injected[sid] for i, o in enumerate(over))
                outside_all += sum(o and i not in injected[sid] and i not in ambient_cover[sid]
                                   for i, o in enumerate(over))
                pairs += _pairs(comp, threshold)
                pairs_outside_all += sum(over[i] and over[i + 1] and not {i, i + 1} & (injected[sid] | ambient_cover[sid])
                                         for i in range(len(over) - 1))
            ambient[f"scans_over_{name}"] = {"all": allc, "outside_injected_episodes": outside_inj,
                                            "outside_all_episodes": outside_all, "share_of_all_scans": round(allc / n_scans, 4)}
            ambient[f"consecutive_pairs_over_{name}"] = {"all": pairs, "outside_all_episodes": pairs_outside_all}
            if name == "emergency":
                runs = sum(noise_counts(comp, threshold, injected[sid] | ambient_cover[sid])[2]
                           for sid, comp in composites.items())
                ambient["sustained_runs_over_emergency_outside_all_episodes"] = runs
                fired = sum(_fires(composites[sid][a:b], threshold) for sid, a, b in ambient_ranges)
                ambient["ambient_episodes_firing_sustained_over_emergency"] = {"fired": fired, "of": len(ambient_ranges)}

        gaps = []
        for g in self.plan.gaps:
            gaps.append({"first_scan_utc": format_ts(self.plan.times[g.first]), "scans": g.end - g.first,
                         "rows": g.events, "recovery_scan_utc": format_ts(self.plan.times[g.end]),
                         "reconnect_utc": format_ts(g.reconnect), "drain_batches": g.batches,
                         "drain_end_utc": format_ts(g.drain_end)})
        return {"baseline": "mean and sample SD of the emitted readings over all scans of the window, all rows "
                            "kept, including gap scans",
                "modes": dict(sorted(self.mode_rows.items())),
                "rows_with_lag_over_1800_s": self.lag_rows, "max_lag_seconds": r(self.max_lag_seconds),
                "gaps": gaps, "emergencies": emergencies, "riser": riser, "ambient": ambient}


def _pairs(values, threshold):
    return sum(a > threshold and b > threshold for a, b in zip(values, values[1:]))


def noise_counts(series, threshold, blocked=frozenset()):
    """(scans, pairs, runs) for the scans of `series` over `threshold` whose index is not in `blocked`: the
    scans, the pairs of consecutive scans, and the runs of SUSTAINED_SCANS or more consecutive scans (a run is
    counted once, when it reaches SUSTAINED_SCANS). A blocked scan or one at or under the threshold ends a run."""
    scans = pairs = runs = length = 0
    for i, value in enumerate(series):
        if value > threshold and i not in blocked:
            scans += 1
            length += 1
            pairs += length >= 2
            runs += length == SUSTAINED_SCANS
        else:
            length = 0
    return scans, pairs, runs


def _fires(values, threshold):
    """True if `values` has SUSTAINED_SCANS consecutive scans over the threshold."""
    length = 0
    for v in values:
        length = length + 1 if v > threshold else 0
        if length >= SUSTAINED_SCANS:
            return True
    return False


# ---- generate, manifest, verify -----------------------------------------------------------------------------
def repo_relative(path):
    """A repo-relative POSIX path when the file is inside the project, else the bare file name (manifests must not
    hold machine paths)."""
    root = Path(__file__).resolve().parents[2]
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(path).name


def current_thresholds():
    """The doc 03 thresholds (as mirrored in signatures.py) that the injection targets are derived from."""
    return {"anomaly": ANOMALY_THRESHOLD, "emergency": EMERGENCY_THRESHOLD, "sustained_scans": SUSTAINED_SCANS,
            "margin_k": MARGIN_K, "margin": round(margin_factor(), 6)}


def build_manifest(run, content_hash, texture_path, sector_path, partial):
    first, stop = run.scan_range
    return {
        "manifest_version": MANIFEST_VERSION, "generator_version": GENERATOR_VERSION, "source_id": run.source_id,
        "seed": run.seed, "window": run.window.to_manifest(),
        "texture": {"path": repo_relative(texture_path), "sha256": sha256_file(texture_path)},
        "dim_sector": {"path": repo_relative(sector_path), "sha256": sha256_file(sector_path),
                       "rows": len(run.sectors)},
        "thresholds": current_thresholds(),
        "targets": {e.signature: target_for(e.signature) for e in run.texture.emergencies},
        "scan_range": {"first": first, "last_exclusive": stop, "partial": partial},
        "files": content_hash.files, "rows": content_hash.files * len(run.sectors), "lines_per_file": len(run.sectors),
        "bytes": content_hash.bytes,
        "content_hash": {"algorithm": "sha256", "method": CONTENT_HASH_METHOD, "value": content_hash.hexdigest()},
        "report": run.report(),
    }


def generate(sectors, window, texture, texture_path, seed, out_dir, scan_range=None,
             sector_path=DIM_SECTOR_PATH):
    """Write the files under out_dir/scans/ and return the manifest (the caller decides where to save it).
    Refuses a non-empty out_dir."""
    out = Path(out_dir)
    scans_dir = out / "scans"
    if scans_dir.exists() and any(scans_dir.iterdir()):
        raise BackfillError(f"{scans_dir} is not empty; generate never overwrites")
    scans_dir.mkdir(parents=True, exist_ok=True)
    run = Backfill(sectors, window, texture, seed, scan_range=scan_range)
    content = ContentHash()
    for name, data in run.files():
        with (scans_dir / name).open("wb") as f:
            f.write(data)
        content.add(name, data)
    return build_manifest(run, content, texture_path, sector_path, partial=scan_range is not None)


def manifest_text(manifest):
    return json.dumps(manifest, indent=2) + chr(10)


def hash_directory(scans_dir):
    content = ContentHash()
    for path in sorted(Path(scans_dir).glob("*.ndjson"), key=lambda p: p.name):
        content.add(path.name, path.read_bytes())
    return content


def verify(manifest, sectors, texture_path, out_dir=None, sector_path=DIM_SECTOR_PATH, regenerate=True):
    """Problems found (empty when the backfill is exactly what the manifest records): the inputs still hash to the
    recorded values, a regeneration from them has the recorded content hash, and, if out_dir is given, the files
    on disk do too. regenerate=False (needs out_dir) skips the regeneration: a quick check that the files on disk
    are still the ones the manifest describes."""
    if not regenerate and out_dir is None:
        raise BackfillError("a check without a regeneration needs the directory of files to check")
    problems = []
    if sha256_file(texture_path) != manifest["texture"]["sha256"]:
        problems.append("the texture file changed since the manifest was written")
    if sha256_file(sector_path) != manifest["dim_sector"]["sha256"]:
        problems.append("dim_sector.csv changed since the manifest was written (doc 08: this invalidates the backfill)")
    if manifest.get("thresholds") != current_thresholds():
        problems.append(f"the doc 03 thresholds changed since the manifest was written ({manifest.get('thresholds')} "
                        f"then, {current_thresholds()} now): the injection targets are derived from them, so a "
                        "regeneration is not comparable")
    if problems:
        return problems
    recorded = manifest["content_hash"]["value"]
    if regenerate:
        w = manifest["window"]
        window = BackfillWindow(end=_parse_ts(w["window_end_utc"]),
                                earliest_live=_parse_ts(w["earliest_live_event_time_utc"]))
        rng = manifest["scan_range"]
        run = Backfill(sectors, window, load_texture(texture_path, sectors), manifest["seed"],
                       source_id=manifest["source_id"], scan_range=(rng["first"], rng["last_exclusive"]))
        content = ContentHash()
        for name, data in run.files():
            content.add(name, data)
        if content.hexdigest() != recorded or content.files != manifest["files"]:
            problems.append(f"the regeneration differs: {content.files} files, hash {content.hexdigest()}, manifest "
                            f"has {manifest['files']} files, hash {recorded}")
        if run.report() != manifest["report"]:
            problems.append("the regenerated report differs from the manifest's")
    if out_dir is not None:
        disk = hash_directory(Path(out_dir) / "scans")
        if disk.hexdigest() != recorded or disk.files != manifest["files"]:
            problems.append(f"the files on disk differ: {disk.files} files, hash {disk.hexdigest()}, manifest has "
                            f"{manifest['files']} files, hash {recorded}")
    return problems


def format_report(manifest):
    """A readable summary of a manifest for the console."""
    rep, w = manifest["report"], manifest["window"]
    out = [f"window {w['window_start_utc']} .. {w['window_end_utc']} (exclusive); last scan {w['last_scan_utc']}; "
           f"earliest live event {w['earliest_live_event_time_utc']}",
           f"seed {manifest['seed']}; files {manifest['files']}; rows {manifest['rows']}; bytes {manifest['bytes']}; "
           f"scan range {manifest['scan_range']}",
           f"thresholds {manifest['thresholds']}; targets {manifest['targets']}",
           f"content hash {manifest['content_hash']['value']}",
           f"rows by mode {rep['modes']}; rows with lag > {REPLAYED_LAG_SECONDS} s: {rep['rows_with_lag_over_1800_s']}; "
           f"max lag {rep['max_lag_seconds']} s", "", "gaps:"]
    for g in rep["gaps"]:
        out.append(f"  {g['first_scan_utc']}  {g['scans']} scans, {g['rows']} rows; link back {g['reconnect_utc']}, "
                   f"{g['drain_batches']} batches, drain ends {g['drain_end_utc']}; BURST scan {g['recovery_scan_utc']}")
    out += ["", "injected emergencies (z-scores against the 90-day window baseline, hold scans):"]
    for e in rep["emergencies"]:
        out.append(f"  {e['sector']} {e['signature']} from {e['start_utc']} (ramp {e['ramp']}, hold {e['hold']}, "
                   f"decay {e['decay']}); consecutive emergency-level scans classified as the signature: "
                   f"{e['consecutive_scans_at_emergency_level_as_signature']}")
        for h in e["hold_scans"]:
            out.append(f"    {h['scan_utc']}  z_midi {h['z_midi']:+.3f}  z_kyber {h['z_kyber']:+.3f}  "
                       f"z_dark {h['z_dark']:+.3f}  composite {h['composite']:.3f}  {h['classification']}")
    rs = rep["riser"]
    out += ["", f"slow riser {rs['sector']} (dark +{rs['total_sigma']} sigma over the window, spike probability "
                f"{rs['spike_probability']}):",
            f"  last scan: z_dark {rs['last_scan_z_dark']:+.3f}, composite {rs['last_scan_composite']:.3f}",
            f"  last day's mean: z_dark {rs['last_day_mean_z_dark']:+.3f}, composite {rs['last_day_mean_composite']:.3f}",
            f"  trend check, the last day's mean composite is under {ANOMALY_THRESHOLD}: {rs['trend_below_anomaly']}",
            f"  for reference, single scans (noise crosses this on every planet): max composite over the window "
            f"{rs['max_composite']:.3f}; scans over {ANOMALY_THRESHOLD}: {rs['scans_over_anomaly']} "
            f"({rs['consecutive_pairs_over_anomaly']} consecutive pairs)",
            f"  the other planets' max composite: {rs['other_planets_max_composite']}"]
    a = rep["ambient"]
    out += ["", f"ambient dark spike episodes: {a['episodes']} on {a['planets_with_episodes']} planets "
                f"(expected about {a['expected_episodes']})"]
    for name in ("anomaly", "emergency"):
        out.append(f"scans over the {name} threshold: {a[f'scans_over_{name}']}; consecutive pairs: "
                   f"{a[f'consecutive_pairs_over_{name}']}")
    firing = a["ambient_episodes_firing_sustained_over_emergency"]
    out.append(f"noise-only sustained runs over the emergency threshold: "
               f"{a['sustained_runs_over_emergency_outside_all_episodes']}; ambient episodes firing (sustained): "
               f"{firing['fired']} of {firing['of']}")
    return chr(10).join(out)
