"""The fixture runner and its assertion engine, against stub models (no network): every fixture passes a model that behaves,
fails the ones that misbehave, keeps blocked / error / rejected apart, never prints the key, and appends its record."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import _support as S
import run_fixtures as rf
from _support import KEY, MODEL, core, model_answer

SECTORS = core.load_sectors()
FIXTURES = rf.load_fixtures(rf.FIXTURES_DIR, SECTORS)
BY_NAME = {fx["name"]: fx for fx in FIXTURES}
ENVIRON = {"GEMINI_API_KEY": KEY, "INFERENCE_MODEL": MODEL}
BASELINE = {"midichlorian_ppm": ("midi_baseline", "midi_sigma"), "kyber_resonance": ("kyber_baseline", "kyber_sigma"),
            "dark_side_activity": ("dark_baseline", "dark_sigma")}


def ideal_answer(fx):
    """What a model that behaves would say for this fixture: baseline values, the relevance in the middle of the allowed band."""
    sector, expect = SECTORS[fx["sector_id"]], fx["expect"]
    limits = expect.get("relevance", {})
    if "gt" in limits and "lt" in limits:
        relevance = (limits["gt"] + limits["lt"]) / 2
    elif "gt" in limits:
        relevance = (limits["gt"] + 1) / 2
    elif "lt" in limits:
        relevance = limits["lt"] / 3
    else:
        relevance = 0.5
    answer = {"relevance_score": relevance, "inference_rationale": "stub"}
    for channel, (mean, sigma) in BASELINE.items():
        raised = expect.get("min_sigma", {}).get(channel)
        answer[channel] = getattr(sector, mean) + ((raised + 1) * getattr(sector, sigma) if raised is not None else 0)
    return answer


class StubModel:
    """Stands in for files_api.http_request. `respond(user_message)` returns (status, body); it sees what the runner really sent."""

    def __init__(self, respond):
        self.respond, self.requests = respond, []

    def __call__(self, method, url, *, headers=None, data=None, timeout=60, opener=None):
        sent = json.loads(data)
        user = json.loads(sent["contents"][0]["parts"][0]["text"])
        self.requests.append({"url": url, "headers": dict(headers or {}), "user": user, "system": sent["systemInstruction"]})
        return self.respond(user)


def behaving(user):
    fx = next(f for f in FIXTURES if rf.description_of(f) == user["description"] and f["sector_id"] == user["planet"]["sector_id"])
    return 200, model_answer(**ideal_answer(fx))


def behaving_for(fx):
    return lambda user: (200, model_answer(**ideal_answer(fx)))


def obedient(user):
    """A model that does whatever the text says: top relevance, maximum dark side, every time."""
    return 200, model_answer(relevance_score=1.0, dark_side_activity=100, midichlorian_ppm=30000, kyber_resonance=100)


def suppressed(user):
    """A model talked down by the text: zero relevance, every value at the planet's baseline."""
    sector = SECTORS[user["planet"]["sector_id"]]
    return 200, model_answer(relevance_score=0.0, midichlorian_ppm=sector.midi_baseline, kyber_resonance=sector.kyber_baseline,
                             dark_side_activity=sector.dark_baseline)


def refusing(user):
    return 200, json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()


def failing(user):
    return 503, json.dumps({"error": {"status": "UNAVAILABLE", "message": f"overloaded, key {KEY}"}}).encode()


def run_once(fx, respond, **options):
    model = StubModel(respond)
    outcome = rf.run_once(fx, SECTORS, api_key=KEY, model=MODEL, thinking_level="minimal", http=model, now=lambda: S.NOW,
                          sleep=lambda _: None, **options)
    return outcome, model


class RunOnceTests(unittest.TestCase):
    def test_a_model_that_behaves_passes_every_fixture(self):
        for fx in FIXTURES:
            with self.subTest(name=fx["name"]):
                outcome, model = run_once(fx, behaving)
                self.assertEqual(rf.check(fx, outcome), [], outcome)
                self.assertEqual(outcome["kind"], fx["expect"]["outcome"])
                self.assertEqual(outcome["calls"], len(model.requests))

    def test_the_empty_fixture_is_rejected_before_any_model_call(self):
        outcome, model = run_once(BY_NAME["empty_description"], behaving)
        self.assertEqual((outcome["kind"], outcome["code"], outcome["calls"]), ("rejected", "empty_description", 0))
        self.assertEqual(model.requests, [])

    def test_the_runner_sends_what_the_browser_would(self):
        outcome, model = run_once(BY_NAME["vader_in_the_market"], behaving)
        request = model.requests[0]
        self.assertEqual(request["user"]["description"], "Saw Darth Vader walking through the market")
        self.assertEqual(request["user"]["planet"]["sector_id"], "coruscant")
        self.assertEqual(request["system"]["parts"][0]["text"], core.SYSTEM_PROMPT)
        self.assertTrue(request["url"].endswith(f"/models/{MODEL}:generateContent"))

    def test_the_stored_description_is_verbatim_for_the_awkward_fixtures(self):
        for name in ("unicode_text", "html_and_script_markup", "rambling_5000_chars", "injection_on_a_mundane_report"):
            with self.subTest(name=name):
                outcome, _ = run_once(BY_NAME[name], behaving)
                self.assertIs(outcome["verbatim"], True)

    def test_each_run_uses_a_fresh_report_id(self):
        seen = set()
        stub = StubModel(behaving)
        for _ in range(3):
            captured = []
            original = core.assess_report

            def spy(body, **kw):
                captured.append(json.loads(body)["report_id"])
                return original(body, **kw)
            core.assess_report = spy
            try:
                rf.run_once(BY_NAME["rain_today"], SECTORS, api_key=KEY, model=MODEL, thinking_level="minimal", http=stub,
                            now=lambda: S.NOW, sleep=lambda _: None)
            finally:
                core.assess_report = original
            seen.update(captured)
        self.assertEqual(len(seen), 3)

    def test_a_model_that_obeys_the_text_fails_the_mundane_fixtures(self):
        for name in ("rain_today", "injection_on_a_mundane_report", "html_and_script_markup", "unicode_text", "speeder_broke_down"):
            with self.subTest(name=name):
                outcome, _ = run_once(BY_NAME[name], obedient)
                failures = rf.check(BY_NAME[name], outcome)
                self.assertTrue(any("relevance 1.000 is not below" in f for f in failures), failures)

    def test_a_model_talked_down_fails_the_force_content_fixtures(self):
        for name in ("injection_on_real_force_content", "vader_in_the_market"):
            with self.subTest(name=name):
                outcome, _ = run_once(BY_NAME[name], suppressed)
                failures = rf.check(BY_NAME[name], outcome)
                self.assertTrue(any("is not above" in f for f in failures), failures)
                self.assertTrue(any("dark is +0.0 sigma" in f for f in failures), failures)

    def test_a_refusal_is_recorded_as_blocked_not_as_an_error_or_a_pass(self):
        outcome, model = run_once(BY_NAME["vader_in_the_market"], refusing)
        self.assertEqual((outcome["kind"], outcome["code"], outcome["calls"]), ("blocked", "blocked_by_model", 1))
        self.assertEqual(rf.check(BY_NAME["vader_in_the_market"], outcome), ["expected scored, got blocked (blocked_by_model)"])

    def test_a_service_failure_is_recorded_as_an_error_with_its_kind(self):
        outcome, model = run_once(BY_NAME["vader_in_the_market"], failing)
        self.assertEqual((outcome["kind"], outcome["code"], outcome["calls"]), ("error", "server_error", 2))     # the one retry
        self.assertEqual(rf.check(BY_NAME["vader_in_the_market"], outcome), ["expected scored, got error (server_error)"])
        self.assertNotIn(KEY, json.dumps(outcome))

    def test_an_out_of_range_model_is_clamped_and_the_clamp_is_recorded(self):
        wild = lambda user: (200, model_answer(relevance_score=9.9, midichlorian_ppm=1e9, kyber_resonance=-5, dark_side_activity=1e6))
        outcome, _ = run_once(BY_NAME["laser_swords"], wild)
        self.assertIs(outcome["bounded"], True)
        self.assertEqual(sorted(outcome["clamped"]), ["dark_side_activity", "kyber_resonance", "midichlorian_ppm", "relevance_score"])
        self.assertEqual(outcome["values"]["relevance_score"], 1.0)
        self.assertEqual(rf.check(BY_NAME["laser_swords"], outcome), [])

    def test_an_envelope_that_alters_the_text_is_noticed_by_the_verbatim_check(self):
        fx = dict(BY_NAME["rain_today"], description="  padded rain  ")
        original = core.build_envelope

        def trimming(request, values, model, event_time):
            envelope = original(request, values, model, event_time)
            envelope["payload"]["description"] = envelope["payload"]["description"].strip()
            return envelope
        core.build_envelope = trimming
        try:
            outcome, _ = run_once(fx, behaving_for(fx))
        finally:
            core.build_envelope = original
        self.assertIs(outcome["verbatim"], False)
        self.assertIn("the stored description differs from the submitted one", rf.check(fx, outcome))

    def test_a_clamp_that_stops_working_is_noticed_by_the_range_check(self):
        broken = lambda result: core.InferredValues(relevance_score=7.0, midichlorian_ppm=9e9, kyber_resonance=-1.0,
                                                   dark_side_activity=500.0, inference_rationale="x")
        original, core.validate_inference = core.validate_inference, broken
        try:
            outcome, _ = run_once(BY_NAME["laser_swords"], behaving)
        finally:
            core.validate_inference = original
        self.assertIs(outcome["bounded"], False)
        self.assertIn("a value is outside its valid range", rf.check(BY_NAME["laser_swords"], outcome))

    def test_sigma_multiples_are_distances_from_the_planets_own_baseline(self):
        sector = SECTORS["coruscant"]
        values = {"midichlorian_ppm": sector.midi_baseline + 2 * sector.midi_sigma, "kyber_resonance": sector.kyber_baseline - 3 * sector.kyber_sigma,
                  "dark_side_activity": sector.dark_baseline}
        got = rf.sigma_multiples(sector, values)
        self.assertAlmostEqual(got["midichlorian_ppm"], 2.0)
        self.assertAlmostEqual(got["kyber_resonance"], -3.0)
        self.assertAlmostEqual(got["dark_side_activity"], 0.0)


def scored(relevance=0.5, dark=0.0, midi=0.0, kyber=0.0, bounded=True, verbatim=True):
    return {"kind": "scored", "code": None, "calls": 1, "values": {"relevance_score": relevance}, "clamped": [],
            "sigma": {"midichlorian_ppm": midi, "kyber_resonance": kyber, "dark_side_activity": dark},
            "bounded": bounded, "verbatim": verbatim}


def fixture(**expect):
    return {"name": "x", "expect": dict({"outcome": "scored"}, **expect)}


class CheckTests(unittest.TestCase):
    def test_relevance_limits_are_strict(self):
        self.assertEqual(rf.check(fixture(relevance={"lt": 0.15}), scored(0.149)), [])
        self.assertEqual(len(rf.check(fixture(relevance={"lt": 0.15}), scored(0.15))), 1)
        self.assertEqual(rf.check(fixture(relevance={"gt": 0.85}), scored(0.851)), [])
        self.assertEqual(len(rf.check(fixture(relevance={"gt": 0.85}), scored(0.85))), 1)
        self.assertEqual(rf.check(fixture(relevance={"gt": 0.3, "lt": 0.7}), scored(0.5)), [])
        self.assertEqual(len(rf.check(fixture(relevance={"gt": 0.3, "lt": 0.7}), scored(0.29))), 1)
        self.assertEqual(len(rf.check(fixture(relevance={"gt": 0.3, "lt": 0.7}), scored(0.71))), 1)

    def test_min_sigma_is_signed_and_inclusive(self):
        spec = fixture(min_sigma={"dark_side_activity": 4.0})
        self.assertEqual(rf.check(spec, scored(dark=4.0)), [])
        self.assertEqual(rf.check(spec, scored(dark=8.6)), [])
        self.assertEqual(len(rf.check(spec, scored(dark=3.99))), 1)
        self.assertEqual(len(rf.check(spec, scored(dark=-8.6))), 1)           # far from baseline, wrong direction

    def test_max_abs_sigma_applies_to_every_channel_in_both_directions(self):
        spec = fixture(max_abs_sigma=2.0)
        self.assertEqual(rf.check(spec, scored(dark=2.0, midi=-2.0, kyber=1.0)), [])
        self.assertEqual(len(rf.check(spec, scored(dark=2.1))), 1)
        self.assertEqual(len(rf.check(spec, scored(kyber=-2.1))), 1)
        self.assertEqual(len(rf.check(spec, scored(midi=-2.1, kyber=2.1))), 2)

    def test_range_and_verbatim_are_always_required_of_a_scored_report(self):
        self.assertEqual(rf.check(fixture(), scored()), [])
        self.assertEqual(rf.check(fixture(), scored(bounded=False)), ["a value is outside its valid range"])
        self.assertEqual(rf.check(fixture(), scored(verbatim=False)), ["the stored description differs from the submitted one"])

    def test_the_outcome_must_be_the_expected_one_and_the_kinds_stay_apart(self):
        spec = fixture()
        for kind, code in (("blocked", "blocked_by_model"), ("error", "transport"), ("rejected", "empty_description")):
            self.assertEqual(rf.check(spec, {"kind": kind, "code": code, "calls": 0}), [f"expected scored, got {kind} ({code})"])
        self.assertEqual(rf.check({"name": "x", "expect": {"outcome": "rejected", "code": "a"}}, scored()), ["expected rejected, got scored"])

    def test_a_rejected_fixture_checks_the_code_and_the_call_count(self):
        spec = {"name": "x", "expect": {"outcome": "rejected", "code": "empty_description", "inference_calls": 0}}
        good = {"kind": "rejected", "code": "empty_description", "calls": 0}
        self.assertEqual(rf.check(spec, good), [])
        self.assertEqual(len(rf.check(spec, dict(good, code="unknown_sector"))), 1)
        self.assertEqual(len(rf.check(spec, dict(good, calls=1))), 1)

    def test_a_rejected_outcome_with_no_scores_is_not_indexed_for_them(self):
        spec = {"name": "x", "expect": {"outcome": "rejected", "code": "a"}}
        self.assertEqual(rf.check(spec, {"kind": "rejected", "code": "a", "calls": 0}), [])


class MainTests(unittest.TestCase):
    def run_main(self, *argv, respond=behaving, environ=ENVIRON, results=None, fixtures_dir=rf.FIXTURES_DIR):
        out, model, pauses = io.StringIO(), StubModel(respond), []
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(results) if results else Path(tmp) / "eval_results.md"
            with contextlib.redirect_stderr(io.StringIO()):                    # argparse writes its usage errors to stderr
                code = rf.main(list(argv) + ["--results", str(results_path), "--env-file", str(Path(tmp) / "absent")], http=model,
                               environ=environ, now=lambda: S.NOW, sleep=pauses.append, out=out, fixtures_dir=fixtures_dir)
            record = results_path.read_text(encoding="utf-8") if results_path.exists() else None
        return code, out.getvalue(), record, model, pauses

    def test_without_live_nothing_is_called_and_nothing_is_written(self):
        code, output, record, model, _ = self.run_main()
        self.assertEqual((code, model.requests, record), (2, [], None))
        self.assertIn("add --live", output)
        self.assertIn("up to 30 model call(s)", output)            # 10 fixtures that call the model x 3

    def test_list_shows_the_fixtures_and_calls_nothing(self):
        code, output, record, model, _ = self.run_main("--list")
        self.assertEqual((code, model.requests, record), (0, [], None))
        self.assertIn("vader_in_the_market", output)
        self.assertEqual(len([line for line in output.splitlines() if line.strip()]), 11)

    def test_a_live_run_makes_iterations_times_the_model_fixtures_calls_and_pauses_between_them(self):
        code, output, record, model, pauses = self.run_main("--live", "--only", "rain_today", "--only", "empty_description",
                                                           "--only", "vader_in_the_market", "--iterations", "2")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(model.requests), 4)                   # rain and vader, twice each; the empty one never calls
        self.assertEqual(len(pauses), 4)
        self.assertEqual(set(pauses), {rf.DEFAULT_PAUSE_S})
        self.assertRegex(output, r"(?m)^4 model call\(s\)\. ALL PASS$")           # the calls made (the final line), not the header
        self.assertIn("up to 4 model call(s)", output)                              # planned: 2 iterations x the 2 fixtures that call the model
        self.assertIn("2 iteration(s) | 4 model call(s)", record)

    def test_iteration_lines_report_the_numbers_the_checks_use(self):
        _, output, _, _, _ = self.run_main("--live", "--only", "vader_in_the_market", "--iterations", "1")
        line = next(l for l in output.splitlines() if l.startswith("vader_in_the_market"))
        for part in ("1/1", "scored", "dark +5.0", "PASS"):
            self.assertIn(part, line)
        self.assertRegex(line, r"relevance 0\.9[23]\b")

    def test_exit_code_is_1_when_a_fixture_fails(self):
        code, output, record, _, _ = self.run_main("--live", "--only", "rain_today", "--only", "vader_in_the_market",
                                                   "--iterations", "1", respond=obedient)
        self.assertEqual(code, 1)
        self.assertIn("1 fixture(s) with failures: rain_today", output)
        self.assertIn("Result: 1 fixture(s) with failures", record)
        self.assertIn("- rain_today: ", record)
        self.assertIn("relevance 1.000 is not below 0.15", record)

    def test_blocked_and_error_runs_are_counted_in_their_own_columns(self):
        _, _, blocked_record, _, _ = self.run_main("--live", "--only", "vader_in_the_market", "--iterations", "2", respond=refusing)
        _, _, error_record, _, _ = self.run_main("--live", "--only", "vader_in_the_market", "--iterations", "2", respond=failing)
        for record, column in ((blocked_record, 3), (error_record, 5)):
            cells = [c.strip() for c in next(l for l in record.splitlines() if l.startswith("| vader_in_the_market")).split("|")[1:-1]]
            counts = cells[2:6]                                     # scored, blocked, rejected, error
            self.assertEqual(counts[column - 2], "2", cells)
            self.assertEqual(sum(int(c) for c in counts), 2, cells)
            self.assertEqual(cells[1], "0/2")

    def test_a_rejected_fixture_that_behaves_counts_as_a_pass_in_the_rejected_column(self):
        _, _, record, _, _ = self.run_main("--live", "--only", "empty_description", "--iterations", "1")
        cells = [c.strip() for c in next(l for l in record.splitlines() if l.startswith("| empty_description")).split("|")[1:-1]]
        self.assertEqual(cells[1:6], ["1/1", "0", "0", "1", "0"])

    def test_records_are_appended_with_one_header_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "eval_results.md"
            for _ in range(2):
                self.run_main("--live", "--only", "rain_today", "--iterations", "1", results=path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("# Intake fixture results"), 1)
        self.assertEqual(text.count("\n## 2026-10-14T15:00:00Z | gemini-3.1-flash-lite | thinking minimal | 1 iteration(s) | 1 model call(s)"), 2)
        self.assertEqual(text.count("| rain_today |"), 2)
        self.assertNotIn("\r", text)

    def test_the_key_and_the_submitted_text_never_reach_the_output_or_the_record(self):
        for respond in (behaving, obedient, refusing, failing):
            code, output, record, model, _ = self.run_main("--live", "--only", "vader_in_the_market", "--only", "unicode_text",
                                                          "--iterations", "1", respond=respond)
            for secret in (KEY, "Saw Darth Vader", "Naboo"):
                self.assertNotIn(secret, output)
                self.assertNotIn(secret, record)
            self.assertIn(MODEL, output)
            self.assertTrue(all(r["headers"]["x-goog-api-key"] == KEY for r in model.requests))      # it IS sent, to the API only

    def test_missing_configuration_is_a_usage_error_that_names_the_key_not_a_value(self):
        code, output, record, model, _ = self.run_main("--live", "--only", "rain_today", environ={"INFERENCE_MODEL": MODEL})
        self.assertEqual((code, model.requests, record), (2, [], None))
        self.assertIn("GEMINI_API_KEY", output)

    def test_usage_errors_make_no_call(self):
        for argv in (["--live", "--iterations", "0"], ["--live", "--iterations", "21"], ["--live", "--pause", "-1"],
                     ["--live", "--only", "no_such_fixture"], ["--live", "--thinking-level", "extreme"], ["--bogus"]):
            with self.subTest(argv=argv):
                code, output, record, model, _ = self.run_main(*argv)
                self.assertEqual((code, model.requests, record), (2, [], None))

    def test_the_default_is_three_iterations_per_fixture(self):
        self.assertEqual(rf.DEFAULT_ITERATIONS, 3)
        _, _, _, model, _ = self.run_main("--live", "--only", "rain_today")
        self.assertEqual(len(model.requests), 3)

    def test_the_thinking_level_is_passed_to_the_model(self):
        _, output, record, model, _ = self.run_main("--live", "--only", "rain_today", "--iterations", "1", "--thinking-level", "low")
        self.assertIn("thinking low", output)
        self.assertIn("thinking low", record)

    def test_a_malformed_fixture_folder_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text('{"name": "bad"}', encoding="utf-8")
            code, output, record, model, _ = self.run_main("--live", fixtures_dir=tmp)
        self.assertEqual((code, model.requests, record), (2, [], None))
        self.assertIn("bad.json", output)

    def test_the_runner_makes_no_outbound_call_of_its_own(self):
        source = (S.ROOT / "intake" / "run_fixtures.py").read_text(encoding="utf-8")
        for forbidden in ("urlopen", "urllib.request", "http.client", "requests.", "x-goog-api-key", "print(api_key", "print(KEY"):
            self.assertNotIn(forbidden, source)
        self.assertIs(rf.main.__kwdefaults__["http"], rf.files_api.http_request)


class RenderTests(unittest.TestCase):
    def test_summarise_counts_the_four_outcomes_and_the_ranges(self):
        fx = BY_NAME["vader_in_the_market"]
        runs = [(scored(0.9, dark=8.0), []), (scored(0.95, dark=9.0), []), ({"kind": "blocked", "code": "blocked_by_model", "calls": 1},
                                                                           ["expected scored, got blocked (blocked_by_model)"])]
        row = rf.summarise([fx], {"vader_in_the_market": runs})[0]
        self.assertEqual((row["total"], row["passed"]), (3, 2))
        self.assertEqual(row["kinds"], {"scored": 2, "blocked": 1, "rejected": 0, "error": 0})
        self.assertEqual(row["relevance"], (0.9, 0.95))
        self.assertEqual(row["dark"], (8.0, 9.0))

    def test_a_fixture_that_was_not_run_is_not_reported(self):
        self.assertEqual(rf.summarise(FIXTURES, {}), [])


if __name__ == "__main__":
    unittest.main()
