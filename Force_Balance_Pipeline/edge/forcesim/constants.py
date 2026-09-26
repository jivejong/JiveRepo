"""Constants shared across forcesim (docs 02, 03, 04)."""
import math

CHANNELS = ("midi", "kyber", "dark")

# Valid ranges, doc 02 ("Payload - probe"). Readings are clamped to these (doc 04).
VALID_RANGE = {"midi": (0.0, 30000.0), "kyber": (0.0, 100.0), "dark": (0.0, 100.0)}

SCANS_PER_DAY = 96  # one sweep every 15 minutes (doc 04)

# Mean-reverting AR(1) walk (doc 04). value = prev + normal(0, STEP_SD) + K * (baseline - prev), so
# the deviation from baseline follows x_t = PHI * x_{t-1} + e_t. STEP_SD is scaled so the stationary
# SD equals the enrichment sigma: sigma * sqrt(1 - PHI**2) = sigma * sqrt(K * (2 - K)) = 0.5268 sigma.
# With step noise equal to sigma the stationary SD would be sigma / sqrt(1 - PHI**2) = 1.898 sigma.
K = 0.15
PHI = 1.0 - K
STEP_FACTOR = math.sqrt(K * (2.0 - K))
