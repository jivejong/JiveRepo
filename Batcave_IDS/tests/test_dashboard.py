"""dashboard/app.py — run headlessly via Streamlit's own AppTest (no
browser needed), against whatever real warehouse exists at test time. This
is a real execution of the whole script, not a mock: every panel's SQL runs
against the actual `data/warehouse.duckdb`, and Altair's chart construction
runs for real. A regression here is a script that would show a stack trace
to a real viewer.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
DASHBOARD_PATH = str(REPO_ROOT / "dashboard" / "app.py")

pytestmark = pytest.mark.skipif(
    not WAREHOUSE_PATH.exists(),
    reason="no warehouse.duckdb - run `make transform` first, same precondition as `make eval`",
)


def test_dashboard_runs_with_no_exceptions():
    at = AppTest.from_file(DASHBOARD_PATH)
    at.run(timeout=30)
    assert list(at.exception) == []


def test_dashboard_renders_all_seven_panels():
    at = AppTest.from_file(DASHBOARD_PATH)
    at.run(timeout=30)
    headers = {h.value for h in at.header}
    assert headers == {
        "Kill chain funnel",
        "Technique efficacy",
        "Retry vs. pivot by archetype (ground truth)",
        "Detection coverage",
        "Suspect ranking",
        "Reconstruction comparison",
        "Remediation",
    }
