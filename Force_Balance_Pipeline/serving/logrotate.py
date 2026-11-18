"""Log rotation for the serving logs (doc 05, "Serving (Phase 7)", Round 10, finding 23).

rotate_if_large(path) is called once at process start, before the first write, for serve.log (serving.publish), deploy.log and dashboard.log
(serving.server). If the file is larger than max_bytes the chain shifts: path.2 -> path.3, path.1 -> path.2, path -> path.1, with the old
path.3 dropped (keep=3). It uses os.replace, never raises (a missing file, or a file another process holds open on Windows, is left alone),
and returns True only if it rotated. Not rotated: logs/agent_decisions.ndjson (the decision record) and the serving.publish_log table.
"""
import os
from pathlib import Path

MAX_BYTES = 5_000_000
KEEP = 3


def rotate_if_large(path, max_bytes=MAX_BYTES, keep=KEEP):
    path = Path(path)
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False                                            # a missing file is a no-op
    try:
        for i in range(keep - 1, 0, -1):                        # path.2 -> path.3, then path.1 -> path.2
            older = path.with_name(f"{path.name}.{i}")
            if older.exists():
                os.replace(older, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        return False
    return True
