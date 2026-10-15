"""Shared test support for the agent: import paths and the repo root. The same approach as intake/tests/_support.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))                 # the agent package: `import agent.constraints`
sys.path.insert(0, str(ROOT / "scripts"))     # build_starship_seed and starship_roster
