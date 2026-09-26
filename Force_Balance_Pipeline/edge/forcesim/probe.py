"""SimProbe: one probe's 60-planet sweep as doc 02 envelopes. The live probe simulator and the
backfill generator both call sweep(), so the backfill really is "the same generator code" (doc 07).

With a seed, every random draw is deterministic and keyed by scan index, so a scan can be regenerated
on its own and a regenerated backfill is byte-identical. Without a seed, the walks get a fresh seed
and ULIDs and sensor noise come from random.SystemRandom.
"""
import random
import secrets
from datetime import timedelta

from .device import battery_pct, sensor_temp_c
from .envelope import make_envelope, new_ulid, probe_payload, ts_ms
from .walk import PlanetProbe, normal, stream

READING_SPACING_MS = 50  # readings of one sweep are taken in planet order, 50 ms apart (0-3 s for 60)


class SimProbe:
    def __init__(self, sectors, seed=None, source_id="probe-01", drifts=None):
        self.deterministic = seed is not None
        self.seed = seed if seed is not None else secrets.randbits(63)
        self.source_id = source_id
        self.sectors = list(sectors)
        drifts = drifts or {}
        self.planets = {s.sector_id: PlanetProbe(s, self.seed, drifts.get(s.sector_id, ())) for s in self.sectors}

    def inject(self, sector_id, signature, start_scan, ramp, hold, decay):
        return self.planets[sector_id].inject(signature, start_scan, ramp, hold, decay)

    def _rng(self, purpose, scan_index):
        return stream(self.seed, purpose, scan_index) if self.deterministic else random.SystemRandom()

    def sweep(self, scan_index, scan_time, mode="CONNECTED", is_synthetic=False, synthetic_ingest_ts=None):
        """The 60 envelopes of one scan, in planet order. They share one scan_id (a full ULID)."""
        ulid_rng = self._rng("ulid", scan_index)
        temp_rng = self._rng("temp", scan_index)
        scan_id = new_ulid(ts_ms(scan_time), ulid_rng)
        battery = battery_pct(scan_time)
        envelopes = []
        for j, sector in enumerate(self.sectors):
            event_time = scan_time + timedelta(milliseconds=j * READING_SPACING_MS)
            v = self.planets[sector.sector_id].step(scan_index)
            payload = probe_payload(v["midi"], v["kyber"], v["dark"],
                                    sensor_temp_c(event_time, normal(temp_rng)), battery)
            envelopes.append(make_envelope(
                event_id=new_ulid(ts_ms(event_time), ulid_rng), source_id=self.source_id,
                source_type="probe", event_time=event_time, mode=mode, scan_id=scan_id,
                sector_id=sector.sector_id, payload=payload, is_synthetic=is_synthetic,
                synthetic_ingest_ts=synthetic_ingest_ts))
        return envelopes
