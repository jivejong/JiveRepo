"""The threshold analysis behind doc 03's anomaly and emergency thresholds (tuned 2026-09-26, PHASE2-RESULTS.md).

Given a finished backfill run (a Backfill whose files() has been exhausted), it reports for each candidate
threshold, from the readings as generated:
  * noise-only exceedances: scans over the threshold, and sustained runs (SUSTAINED_SCANS or more consecutive
    scans over it), outside every injected and ambient-spike episode, galaxy-wide over the window;
  * detection: how many ambient spike episodes and injected emergencies have a sustained run over the threshold
    (for the anomaly table: a single scan over it);
  * the injection targets recomputed at that threshold and whether each texture planet can still host its own.
The injected columns describe the data as generated, so they are meaningful for the threshold the run was
generated with; the recomputed targets are derived, not simulated.

It recommends nothing: the thresholds come from a false-alarm target chosen outside the analysis. Standard
library only.
"""
from .backfill import noise_counts
from .constants import CHANNELS, VALID_RANGE
from .signatures import (SUSTAINED_SCANS, REQUIRES_POPULATION, InjectionRefused, imbalance_score, margin_factor,
                         target_for)
from .window import SCANS

EMERGENCY_RANGE = (4.5, 8.0, 0.25)
ANOMALY_RANGE = (3.0, 8.0, 0.25)


def frange(first, last, step):
    """first, first + step, ... up to last inclusive, rounded so 0.25 steps are exact."""
    out, x, i = [], first, 0
    while x <= last + 1e-9:
        out.append(round(x, 2))
        i += 1
        x = first + i * step
    return out


def _peak_sustained(values):
    """The highest threshold at which `values` still has SUSTAINED_SCANS consecutive scans over it."""
    if len(values) < SUSTAINED_SCANS:
        return 0.0
    return max(min(values[i:i + SUSTAINED_SCANS]) for i in range(len(values) - SUSTAINED_SCANS + 1))


def _feasible(sector, signature, target):
    for channel in CHANNELS:
        baseline, sigma = sector.channel(channel)
        low, high = VALID_RANGE[channel]
        if not low <= baseline + target[channel] * sigma <= high:
            return False
    need = REQUIRES_POPULATION.get(signature)
    return not (need is not None and not (sector.population is not None and sector.population > need))


def analyze(run, emergency_range=EMERGENCY_RANGE, anomaly_range=ANOMALY_RANGE):
    """The analysis of an exhausted Backfill as a dict (see format_markdown for the tables)."""
    _, composites = run.scores()
    by_id = {s.sector_id: s for s in run.sectors}
    covered = {sid: set() for sid in by_id}
    ambient, injected = [], []
    for sid, planet in run.probe.planets.items():
        for e in planet.episodes:
            covered[sid].update(range(e.start, e.end))
            (injected if e.injected else ambient).append((sid, e.start, min(e.end, SCANS), e.signature))
    peak_run = lambda ep: _peak_sustained(composites[ep[0]][ep[1]:ep[2]])          # noqa: E731
    peak_scan = lambda ep: max(composites[ep[0]][ep[1]:ep[2]])                      # noqa: E731

    def noise(threshold):
        counts = [noise_counts(series, threshold, covered[sid]) for sid, series in composites.items()]
        return tuple(sum(c[k] for c in counts) for k in range(3))

    n_planets, days = len(run.sectors), SCANS / 96
    emergency = []
    for t in frange(*emergency_range):
        scans, pairs, runs = noise(t)
        emergency.append({"threshold": t, "noise_scans": scans, "noise_pairs": pairs, "noise_runs": runs,
                          "runs_per_week": round(runs / days * 7, 2),
                          "ambient_firing": sum(peak_run(e) > t for e in ambient), "ambient": len(ambient),
                          "injected_firing": sum(peak_run(e) > t for e in injected), "injected": len(injected)})
    anomaly = []
    for t in frange(*anomaly_range):
        scans = noise(t)[0]
        anomaly.append({"threshold": t, "noise_scans": scans, "share": scans / (n_planets * SCANS),
                        "per_planet_per_day": scans / (n_planets * days),
                        "ambient_with_scan": sum(peak_scan(e) > t for e in ambient), "ambient": len(ambient),
                        "injected_with_scan": sum(peak_scan(e) > t for e in injected), "injected": len(injected)})
    targets = []
    for t in frange(*emergency_range):
        row = {"threshold": t, "signatures": []}
        for spec in run.texture.emergencies:
            try:
                target = target_for(spec.signature, threshold=t)
            except InjectionRefused:
                row["signatures"].append({"signature": spec.signature, "sector": spec.sector, "target": None})
                continue
            row["signatures"].append({
                "signature": spec.signature, "sector": spec.sector,
                "target": (target["midi"], target["kyber"], target["dark"]),
                "composite": imbalance_score(target["midi"], target["kyber"], target["dark"]),
                "feasible_on_texture_planet": _feasible(by_id[spec.sector], spec.signature, target),
                "hosts": sum(_feasible(s, spec.signature, target) for s in run.sectors)})
        row["all_feasible"] = all(s.get("feasible_on_texture_planet") for s in row["signatures"])
        targets.append(row)
    return {"planets": n_planets, "scans_per_planet": SCANS, "ambient_episodes": len(ambient),
            "injected_episodes": len(injected), "margin": margin_factor(),
            "ambient_sustained_peaks": sorted(peak_run(e) for e in ambient),
            "injected_sustained_peaks": {f"{e[0]} {e[3]}": peak_run(e) for e in injected},
            "emergency": emergency, "anomaly": anomaly, "targets": targets}


def format_markdown(result):
    """The analysis as Markdown tables."""
    out = [f"Baseline: the window baseline of the generated backfill; {result['planets']} planets x "
           f"{result['scans_per_planet']} scans. Noise-only = outside every injected and ambient-spike episode. "
           f"Ambient episodes: {result['ambient_episodes']}; injected: {result['injected_episodes']}.", "",
           "EMERGENCY threshold (sustained = " + str(SUSTAINED_SCANS) + "+ consecutive scans over it)",
           "| T | noise scans | noise sustained pairs | noise sustained runs | runs per week | ambient episodes firing "
           "| injected firing (data as generated) |", "|---|---|---|---|---|---|---|"]
    for r in result["emergency"]:
        out.append(f"| {r['threshold']:.2f} | {r['noise_scans']:,} | {r['noise_pairs']:,} | {r['noise_runs']:,} | "
                   f"{r['runs_per_week']:.2f} | {r['ambient_firing']}/{r['ambient']} "
                   f"({r['ambient_firing'] / max(r['ambient'], 1):.0%}) | {r['injected_firing']}/{r['injected']} |")
    out += ["", "ANOMALY threshold (per-scan rate)",
            "| T | noise scans | share of all scans | per planet per day | ambient episodes with a scan over it "
            "| injected with a scan over it |", "|---|---|---|---|---|---|"]
    for r in result["anomaly"]:
        out.append(f"| {r['threshold']:.2f} | {r['noise_scans']:,} | {r['share']:.3%} | {r['per_planet_per_day']:.2f} | "
                   f"{r['ambient_with_scan']}/{r['ambient']} ({r['ambient_with_scan'] / max(r['ambient'], 1):.0%}) | "
                   f"{r['injected_with_scan']}/{r['injected']} |")
    out += ["", f"Injection targets recomputed at each emergency threshold (margin M = {result['margin']:.4f}): "
                "whole sigmas (midi, kyber, dark), nominal composite, feasibility on the texture planet, hosts of 60"]
    names = [s["signature"] for s in result["targets"][0]["signatures"]]
    out += ["| T | " + " | ".join(names) + " | all feasible |", "|---|" + "---|" * (len(names) + 1)]
    for row in result["targets"]:
        cells = []
        for s in row["signatures"]:
            if s["target"] is None:
                cells.append("no target")
            else:
                m, k, d = s["target"]
                cells.append(f"({m:+d},{k:+d},{d:+d}) {s['composite']:.2f}; {s['sector']} "
                             f"{'ok' if s['feasible_on_texture_planet'] else 'NO'}; hosts {s['hosts']}/{result['planets']}")
        out.append(f"| {row['threshold']:.2f} | " + " | ".join(cells) + f" | {'yes' if row['all_feasible'] else 'NO'} |")
    return chr(10).join(out) + chr(10)
