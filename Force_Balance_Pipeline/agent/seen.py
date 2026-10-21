"""The first-seen store of the human-first window (doc 06 "Human-first window").

A JSON object {disturbance_id: first_seen as ISO-8601 UTC with Z}. The agent records an incident the first time a poll returns it and
decides it only in a later cycle, so a person has the time between two cycles to act first. One file per target
(logs/agent_seen_<target>.json), so dev and prod never mix. load() treats a missing file as empty; a file that is not a JSON object of
strings is treated as empty with one warning. save() writes a temp file in the same folder and replaces the old one, so a crash
leaves the previous file whole.
"""
import json
import os
import tempfile
from pathlib import Path


class SeenStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self, log=lambda message: None):
        """{id: first_seen}; {} for a missing file; {} (and one warning through `log`) for a file that is not a JSON object of strings."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            log(f"agent: {self.path.name} could not be read; treated as empty")
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            log(f"agent: {self.path.name} is not a JSON object of strings; treated as empty")
            return {}
        return data

    def save(self, data):
        """Write `data` atomically: a temp file in the same folder, flushed to disk, then os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, sort_keys=True, indent=0)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
