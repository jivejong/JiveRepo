"""Load the frozen dim_sector seed (doc 04: reading parameters come from dim_sector at startup)."""
import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .constants import CHANNELS

DEFAULT_SEED = Path(__file__).resolve().parents[2] / "warehouse" / "dbt" / "seeds" / "dim_sector.csv"
EXPECTED_ROWS = 60  # doc 03: dim_sector has 60 rows; the probe sweeps all of them (doc 04)


@dataclass(frozen=True)
class Sector:
    sector_id: str
    sector_name: str
    population: Optional[float]
    midi_baseline: float
    midi_sigma: float
    kyber_baseline: float
    kyber_sigma: float
    dark_baseline: float
    dark_sigma: float
    dark_spike_probability: float  # read as a per-day episode rate (doc 04, doc 08 note)
    is_unknown: bool

    def channel(self, name: str) -> Tuple[float, float]:
        """(baseline, sigma) of one channel: 'midi', 'kyber' or 'dark'."""
        if name not in CHANNELS:
            raise KeyError(name)
        return getattr(self, f"{name}_baseline"), getattr(self, f"{name}_sigma")


def _float(text: str) -> float:
    return float(text)


def load_sectors(path=DEFAULT_SEED) -> List[Sector]:
    """The 60 sectors in seed order. Refuses a seed that does not look like dim_sector."""
    with Path(path).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    sectors = []
    for r in rows:
        sectors.append(Sector(
            sector_id=r["sector_id"], sector_name=r["sector_name"],
            population=_float(r["population"]) if r["population"] not in ("", None) else None,
            midi_baseline=_float(r["midi_baseline"]), midi_sigma=_float(r["midi_sigma"]),
            kyber_baseline=_float(r["kyber_baseline"]), kyber_sigma=_float(r["kyber_sigma"]),
            dark_baseline=_float(r["dark_baseline"]), dark_sigma=_float(r["dark_sigma"]),
            dark_spike_probability=_float(r["dark_spike_probability"]),
            is_unknown=r["is_unknown"] == "true"))
    ids = [s.sector_id for s in sectors]
    if len(sectors) != EXPECTED_ROWS or len(set(ids)) != len(ids):
        raise ValueError(f"{path}: expected {EXPECTED_ROWS} unique sectors, found {len(sectors)}")
    if sum(s.is_unknown for s in sectors) != 1:
        raise ValueError(f"{path}: expected exactly one is_unknown row")
    for s in sectors:
        if min(s.midi_sigma, s.kyber_sigma, s.dark_sigma) <= 0 or not 0.0 <= s.dark_spike_probability <= 1.0:
            raise ValueError(f"{path}: {s.sector_id} has a non-positive sigma or a bad spike probability")
    return sectors


def sha256_file(path=DEFAULT_SEED) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
