"""The control topic (doc 04): an injection or a forced mode, validated in full before anything happens; only what signatures.py can produce
is injectable; a bad message is refused with a reason and never raises into the probe loop. Offline."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_support import SECTORS  # noqa: E402
from probe import control  # noqa: E402
from probe.control import DEFAULT_DECAY, DEFAULT_HOLD, DEFAULT_RAMP, ControlError, parse  # noqa: E402


def message(**kw):
    return json.dumps(kw).encode()


class InjectTests(unittest.TestCase):
    def test_the_doc_message_is_accepted_with_the_default_episode_shape(self):
        command = parse(message(inject="spike", sector_id="tatooine", signature="sith_presence"), SECTORS)
        self.assertEqual(command, {"kind": "inject", "sector_id": "tatooine", "signature": "sith_presence",
                                   "ramp": 2, "hold": 4, "decay": 3})
        self.assertEqual((DEFAULT_RAMP, DEFAULT_HOLD, DEFAULT_DECAY), (2, 4, 3))

    def test_ramp_hold_and_decay_can_be_overridden(self):
        command = parse(message(inject="spike", sector_id="kamino", signature="force_drain", ramp=1, hold=6, decay=5), SECTORS)
        self.assertEqual((command["ramp"], command["hold"], command["decay"]), (1, 6, 5))

    def test_every_producible_signature_is_accepted_on_a_planet_that_can_host_it(self):
        from forcesim.signatures import PRODUCIBLE, InjectionRefused, check_injection
        for signature in PRODUCIBLE:
            host = next(s for s in SECTORS if _hosts(s, signature))
            with self.subTest(signature):
                self.assertEqual(parse(message(inject="spike", sector_id=host.sector_id, signature=signature), SECTORS)["signature"], signature)

    def test_veiled_presence_is_not_injectable_in_phase_3(self):
        with self.assertRaisesRegex(ControlError, "signature must be one of"):
            parse(message(inject="spike", sector_id="tatooine", signature="veiled_presence"), SECTORS)

    def test_an_infeasible_injection_is_refused_with_the_planets_reason(self):
        with self.assertRaisesRegex(ControlError, "outside the valid range"):
            parse(message(inject="spike", sector_id="utapau", signature="kyber_cache"), SECTORS)
        with self.assertRaisesRegex(ControlError, "population"):
            parse(message(inject="spike", sector_id="tatooine", signature="civil_unrest"), SECTORS)

    def test_bad_messages_are_refused_with_a_reason(self):
        cases = {
            b"not json": "not JSON",
            b"[1]": "not a JSON object",
            message(): "exactly one of inject or mode",
            message(inject="spike", mode="STEALTH"): "exactly one of inject or mode",
            message(inject="flood", sector_id="tatooine", signature="sith_presence"): 'inject must be "spike"',
            message(inject="spike", sector_id="nowhere", signature="sith_presence"): "is not a sector",
            message(inject="spike", sector_id="tatooine", signature="made_up"): "signature must be one of",
            message(inject="spike", sector_id="tatooine", signature="sith_presence", hold=1): "hold must be an integer from 2",
            message(inject="spike", sector_id="tatooine", signature="sith_presence", ramp=0): "ramp must be an integer",
            message(inject="spike", sector_id="tatooine", signature="sith_presence", ramp=True): "ramp must be an integer",
            message(inject="spike", sector_id="tatooine", signature="sith_presence", decay=1000): "decay must be an integer",
            message(inject="spike", sector_id="tatooine", signature="sith_presence", severity=5): "unknown keys",
        }
        for payload, needle in cases.items():
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ControlError, needle):
                    parse(payload, SECTORS)

    def test_a_hold_must_last_the_sustained_scans_of_doc_03(self):
        from forcesim.signatures import SUSTAINED_SCANS
        parse(message(inject="spike", sector_id="tatooine", signature="sith_presence", hold=SUSTAINED_SCANS), SECTORS)
        with self.assertRaises(ControlError):
            parse(message(inject="spike", sector_id="tatooine", signature="sith_presence", hold=SUSTAINED_SCANS - 1), SECTORS)


def _hosts(sector, signature):
    from forcesim.signatures import InjectionRefused, check_injection
    try:
        check_injection(sector, signature)
        return True
    except InjectionRefused:
        return False


class ModeTests(unittest.TestCase):
    def test_the_doc_message(self):
        self.assertEqual(parse(message(mode="DISCONNECTED", for_seconds=2700), SECTORS),
                         {"kind": "mode", "mode": "DISCONNECTED", "for_seconds": 2700})

    def test_for_seconds_is_optional(self):
        self.assertEqual(parse(message(mode="STEALTH"), SECTORS)["for_seconds"], None)

    def test_only_the_forceable_modes(self):
        for good in ("CONNECTED", "DISCONNECTED", "STEALTH"):
            self.assertEqual(parse(message(mode=good), SECTORS)["mode"], good)
        for bad in ("BURST", "SLEEPING", "", None, 3):
            with self.subTest(bad):
                with self.assertRaisesRegex(ControlError, "mode must be one of"):
                    parse(message(mode=bad), SECTORS)

    def test_bad_durations_are_refused(self):
        for bad in (0, -5, 7 * 86400 + 1, 1.5, "60", True):
            with self.subTest(bad):
                with self.assertRaisesRegex(ControlError, "for_seconds must be an integer"):
                    parse(message(mode="STEALTH", for_seconds=bad), SECTORS)

    def test_unknown_keys_are_refused(self):
        with self.assertRaisesRegex(ControlError, "unknown keys"):
            parse(message(mode="STEALTH", inject_now=True), SECTORS)


if __name__ == "__main__":
    unittest.main()
