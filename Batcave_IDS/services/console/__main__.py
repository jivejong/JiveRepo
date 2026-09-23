"""CLI entry for `make console` (Phase 8). Runs the console backend on :8090
- needs the honeypot and Redpanda already up (`make dev-up`), same as
`make attack`. See docs/01's Architecture section for the port table."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("services.console.app:app", host="0.0.0.0", port=8090)
