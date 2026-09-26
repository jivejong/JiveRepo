"""Device state carried in each reading: battery and sensor temperature (doc 02 payload).

Doc 02 gives only the fields and ranges; the shapes here are the approved Phase 2 defaults: battery
drains about one point a day and recharges to 100 at 20, and the sensor sits around 40 C with a slow
diurnal swing and small noise. Both are functions of time, so any scan can be regenerated on its own.
"""
import math

BATTERY_FULL = 100.0
BATTERY_RECHARGE_AT = 20.0
BATTERY_DRAIN_PER_DAY = 1.0
TEMP_MEAN_C = 40.0
TEMP_SWING_C = 3.0
TEMP_NOISE_C = 0.4


def battery_pct(t):
    """Integer percent for time t (aware datetime): 100 falling 1 point a day to 20, then back to 100."""
    days = t.timestamp() / 86400.0
    cycle = (BATTERY_FULL - BATTERY_RECHARGE_AT) / BATTERY_DRAIN_PER_DAY  # 80 days
    level = BATTERY_FULL - (days % cycle) * BATTERY_DRAIN_PER_DAY
    return int(round(level))


def sensor_temp_c(t, noise):
    """Sensor temperature for time t. `noise` is a draw from normal(0, 1) supplied by the caller so
    the randomness stays in one place."""
    hour = (t.timestamp() % 86400.0) / 3600.0
    return TEMP_MEAN_C + TEMP_SWING_C * math.sin(2.0 * math.pi * (hour - 9.0) / 24.0) + TEMP_NOISE_C * noise
