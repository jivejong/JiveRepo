"""The probe's runtime (doc 04, Phase 3): the clock gate, the SQLite buffer, the mode controller, fault injection, the control
topic, the MQTT publisher and the loop that ties them together. Standard library plus paho-mqtt, so it runs on the Pi 3.

The generator is forcesim (the same one the backfill uses); this package decides WHEN a scan is taken, in WHICH mode, what happens to it
(buffer, publish, confirm, drain) and what is logged. Every source of time and every network call is injectable, so the whole
runtime is tested offline on a fake clock.
"""
