"""Focused coverage for routes_sar_sandbox.py, added alongside case_sar_003.json.

Calls the route functions directly (list_cases/get_case are plain callables
under FastAPI's APIRouter decorator, no ASGI server or TestClient needed).
Run with pytest, or directly: python test_sar_sandbox.py
"""

import contextlib
import copy
import io
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import routes_sar_sandbox
from routes_sar_sandbox import (
    _CASES_CACHE,
    ExtractionResult,
    ExtractRequest,
    SarCase,
    extract,
    get_case,
    get_case_display,
    get_case_full,
    get_load_status,
    list_cases,
    score_extraction,
)

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"

EXPECTED_DISPLAY_FIELDS = {
    "title", "subject", "activity_window", "transactions",
    "onward_movement", "supporting_facts",
}


def get_display_json(case_id):
    return json.loads(get_case(case_id).body)


def test_display_output_is_byte_identical_to_the_pre_pydantic_golden_fixtures():
    # Golden fixtures captured from get_case(id).body on main at 6e67875,
    # before the declarative pydantic schema replaced the hand rolled
    # _validate_case/_display_fields. Any difference here is a regression
    # in what the three real, already shipped cases render as, not just a
    # schema change: this is a blocker to report, never a reason to update
    # these files. Byte identical, not just equal after reparsing, so a
    # key order or separator change would also be caught.
    golden_files = {
        "sar-002": "sar-002.json",
        "sar-003": "sar-003.json",
        "sar-phase0-001": "sar-phase0-001.json",
    }
    for case_id, filename in golden_files.items():
        golden = (FIXTURES_DIR / filename).read_bytes()
        actual = get_case(case_id).body
        assert actual == golden, f"{case_id} display output changed from the golden fixture"


def test_existing_and_sar_003_cases_load_via_glob():
    assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(_CASES_CACHE)


def test_sar_003_appears_in_case_list():
    ids = [c["case_id"] for c in json.loads(list_cases().body)]
    assert ids.count("sar-003") == 1


def test_case_detail_contains_only_whitelisted_fields():
    for case_id in _CASES_CACHE:
        display = get_display_json(case_id)
        assert set(display.keys()) == EXPECTED_DISPLAY_FIELDS


def test_get_case_full_returns_isolated_copy():
    # get_case_full builds its dict fresh from the cached SarCase model on
    # every call (case.model_dump(by_alias=True)), so mutating the returned
    # dict must never reach the cache. A fresh call, and get_display_json,
    # must both come back unchanged.
    sentinel = "server-side-answer-key-probe"
    case = get_case_full("sar-003")
    case["subject"]["entity_name"] = sentinel
    case["activity_window"]["start"] = sentinel
    case["transactions"][0]["description"] = sentinel
    case["onward_movement"]["destination_note"] = sentinel
    case["red_flags"][0]["label"] = sentinel

    fresh_full = get_case_full("sar-003")
    fresh_display = get_display_json("sar-003")

    assert sentinel not in json.dumps(fresh_full)
    assert sentinel not in json.dumps(fresh_display)


def test_answer_key_and_distractors_do_not_leak_to_client():
    for case_id in _CASES_CACHE:
        display = get_display_json(case_id)
        assert "red_flags" not in display
        assert "distractor_facts" not in display

        dumped = json.dumps(display)
        full = get_case_full(case_id)
        for red_flag in full["red_flags"]:
            assert red_flag["label"] not in dumped
        for distractor in full["distractor_facts"]:
            assert distractor not in dumped


def test_sar_003_scoring_uses_only_the_six_approved_indicator_ids():
    case = get_case_full("sar-003")
    valid_ids = {rf["id"] for rf in case["red_flags"]}
    assert valid_ids == {"rf1", "rf2", "rf3", "rf4", "rf5", "rf6"}

    # The two approved distractor themes (the law-enforcement enquiry and the
    # solicitor client-account status) must never enter the scored answer
    # key, so their labels must not appear anywhere in it, silently or
    # otherwise.
    combined_labels = " ".join(rf["label"].lower() for rf in case["red_flags"])
    assert "law-enforcement" not in combined_labels
    assert "law enforcement" not in combined_labels
    assert "solicitor" not in combined_labels

    extraction = {
        "five_ws": {w: {"addressed": True, "quote": "x"} for w in ["who", "what", "when", "where", "why"]},
        "red_flags_mentioned": ["rf1", "rf3", "rf6", "not-a-real-id"],
        "transaction_detail_cited": True,
        "speculative_phrases": [],
        "sections_present": {"intro": True, "investigative_body": True, "final_disposition": True},
    }
    scoring = score_extraction(extraction, case["red_flags"])

    # 3 valid ids matched (rf1, rf3, rf6) at 5 points each; the unmatched
    # not-a-real-id must not contribute, proving the score only trusts ids
    # that actually appear in this case's own answer key.
    assert scoring["red_flags_score"] == 15
    assert scoring["total"] == 85
    assert scoring["structural_incomplete"] is False


def test_sar_003_display_includes_practice_instruction():
    display = get_display_json("sar-003")
    instruction = display["subject"]["practice_instruction"]
    assert instruction == (
        "Identify the funds you suspect may be criminal property and explain "
        "the observed facts supporting that suspicion. Do not state that the "
        "property is criminal property, that a predicate offence occurred, "
        "or that any person is guilty."
    )


def test_sar_003_display_does_not_preanswer_distractors():
    display = get_display_json("sar-003")
    review_trigger = display["subject"]["review_trigger"]
    destination_note = display["onward_movement"]["destination_note"]

    assert display["title"] == "The Information Request"
    assert review_trigger == (
        "A verified law-enforcement information request concerning the director "
        "was received. It disclosed no allegation or finding and prompted this "
        "relationship review."
    )
    assert destination_note == (
        "The destination was identified from the payment details as a UK "
        "solicitor's client account associated with the property matter reference"
    )



def test_visible_case_briefs_do_not_state_distractor_judgements():
    judgement_terms = (
        "suspicion",
        "suspicious",
        "red flag",
        "exculpatory",
        "not a basis",
    )

    for case_id in _CASES_CACHE:
        display = get_display_json(case_id)
        subject = dict(display["subject"])
        subject.pop("practice_instruction", None)
        factual_brief = {**display, "subject": subject}
        visible_case_text = json.dumps(factual_brief).lower()

        for judgement_term in judgement_terms:
            assert judgement_term not in visible_case_text, (
                f"{case_id} exposes distractor judgement language: "
                f"{judgement_term}"
            )


def test_unknown_case_is_rejected():
    assert get_case_full("sar-999") is None
    assert get_case_display("sar-999") is None
    try:
        get_case("sar-999")
        raise AssertionError("expected HTTPException for an unknown case_id")
    except HTTPException as exc:
        assert exc.status_code == 404


def test_every_committed_case_is_valid():
    case_paths = sorted(routes_sar_sandbox.CASE_DIR.glob(routes_sar_sandbox.CASE_GLOB))
    assert case_paths, "no case_sar_*.json files found to validate"
    for path in case_paths:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            SarCase.model_validate(data)
        except ValidationError as exc:
            raise AssertionError(f"{path.name} failed validation: {exc.errors(include_input=False)}")
    assert routes_sar_sandbox._INVALID_CASES == 0


def test_every_committed_case_renders_display():
    for case_id in _CASES_CACHE:
        display = get_case_display(case_id)
        assert display is not None
        json.dumps(display)


@contextlib.contextmanager
def _tmp_case_dir(extra_files):
    """Copies every real case_sar_*.json into a tmp dir untouched, adds the
    given extra files, points routes_sar_sandbox at the tmp dir for the
    duration, runs _load_cases against it, then restores the real CASE_DIR
    and the real _CASES_CACHE/_INVALID_CASES. Never edits a real case file.
    Yields (cases, invalid_count, captured_log_text).
    Not safe under a parallel test runner: mutates module globals with no lock."""
    real_case_dir = routes_sar_sandbox.CASE_DIR
    real_cases_cache = routes_sar_sandbox._CASES_CACHE
    real_invalid_cases = routes_sar_sandbox._INVALID_CASES

    tmp_dir = tempfile.mkdtemp(prefix="sar_case_test_")
    try:
        for path in real_case_dir.glob(routes_sar_sandbox.CASE_GLOB):
            shutil.copy(path, Path(tmp_dir) / path.name)
        for filename, content in extra_files.items():
            target = Path(tmp_dir) / filename
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")

        routes_sar_sandbox.CASE_DIR = Path(tmp_dir)
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            cases, invalid_count = routes_sar_sandbox._load_cases()
        routes_sar_sandbox._CASES_CACHE = cases
        routes_sar_sandbox._INVALID_CASES = invalid_count
        yield cases, invalid_count, log.getvalue()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        routes_sar_sandbox.CASE_DIR = real_case_dir
        routes_sar_sandbox._CASES_CACHE = real_cases_cache
        routes_sar_sandbox._INVALID_CASES = real_invalid_cases


def test_case_missing_onward_movement_is_excluded_and_logged():
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-broken-missing-field"
    broken.pop("onward_movement")

    with _tmp_case_dir({"case_sar_zzz_broken.json": json.dumps(broken)}) as (cases, invalid_count, log):
        assert "sar-broken-missing-field" not in cases
        assert invalid_count == 1
        assert "onward_movement" in log
        assert "case_sar_zzz_broken.json" in log
        # valid cases still load and serve
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_case_with_malformed_json_is_excluded_and_logged():
    with _tmp_case_dir({"case_sar_zzz_malformed.json": "{not valid json"}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "could not read or parse file" in log
        assert "case_sar_zzz_malformed.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_case_with_non_utf8_bytes_is_excluded_and_logged():
    # Regression test for a /code-review finding on this same branch:
    # open()'s utf-8 decoding happens lazily as json.load() reads the file,
    # so a stray non-utf-8 byte raises UnicodeDecodeError, not
    # json.JSONDecodeError. Catching only JSONDecodeError left this path
    # able to crash the whole app on import, exactly what this PR exists
    # to close.
    with _tmp_case_dir({"case_sar_zzz_badbytes.json": b"\xff\xfe not valid utf-8"}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "could not read or parse file" in log
        assert "UnicodeDecodeError" in log
        assert "case_sar_zzz_badbytes.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


class _RecursionErrorOnContent:
    """Wraps the real json module, raising RecursionError only when loads()
    is called with one exact sentinel text and delegating everything else
    (including json.dumps used elsewhere in this test file, and the real
    parse for every other case file _load_cases reads in the same pass) to
    the real module. _load_cases reads the file itself now and calls
    json.loads(text, ...), so the target is matched on content, not on a
    file object's name the way an earlier version of this wrapper did."""

    def __init__(self, real_json, target_text):
        self._real = real_json
        self._target_text = target_text

    def loads(self, text, *args, **kwargs):
        if text == self._target_text:
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")
        return self._real.loads(text, *args, **kwargs)

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def test_case_with_deeply_nested_json_is_excluded_and_logged():
    # Regression test for a second /code-review finding on this same
    # branch: CPython's json decoder raises RecursionError, not a
    # ValueError or OSError subclass, on pathologically deep nesting. A
    # narrower except (OSError, ValueError) still left this path able to
    # crash the whole app on import.
    # The actual nesting depth needed to trigger RecursionError depends on
    # the platform's C stack: a depth that reliably raised it locally
    # parsed clean on the CI runner instead, so this test no longer
    # depends on hitting that threshold. It replaces the json name inside
    # routes_sar_sandbox's own module namespace only (not the shared json
    # module every other import of it sees) with a wrapper that raises
    # RecursionError for one sentinel content string and defers to the
    # real json module for every other file _load_cases reads, so this is
    # deterministic on every platform, not tuned to one machine's stack
    # depth.
    target_name = "case_sar_zzz_deepnest.json"
    sentinel_text = '{"sentinel": "trigger-recursion-error"}'
    real_json = routes_sar_sandbox.json
    routes_sar_sandbox.json = _RecursionErrorOnContent(real_json, sentinel_text)
    try:
        with _tmp_case_dir({target_name: sentinel_text}) as (cases, invalid_count, log):
            assert invalid_count == 1
            assert "could not read or parse file" in log
            assert "RecursionError" in log
            assert target_name in log
            assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
            assert get_case_display("sar-003") is not None
    finally:
        routes_sar_sandbox.json = real_json


def test_committed_case_files_are_all_well_under_the_size_cap():
    for path in sorted(routes_sar_sandbox.CASE_DIR.glob(routes_sar_sandbox.CASE_GLOB)):
        size = path.stat().st_size
        assert size < routes_sar_sandbox._CASE_FILE_SIZE_CAP_BYTES, (
            f"{path.name} is {size} bytes, at or over the "
            f"{routes_sar_sandbox._CASE_FILE_SIZE_CAP_BYTES} byte cap"
        )


def test_oversized_case_file_is_excluded_and_logged():
    # Adversarial review finding 2 on this PR: validation checked type and
    # non-emptiness but had no upper bound on content size, so a huge but
    # well-formed case file would pass and bloat every response that
    # serves it. One byte over the cap, valid JSON otherwise, to isolate
    # the size check from the parse/validation checks covered elsewhere.
    cap = 64 * 1024
    padding = "x" * cap
    oversized = json.dumps({"case_id": "sar-huge", "padding": padding})
    assert len(oversized.encode("utf-8")) > cap

    with _tmp_case_dir({"case_sar_zzz_oversized.json": oversized}) as (cases, invalid_count, log):
        assert "sar-huge" not in cases
        assert invalid_count == 1
        assert "size cap" in log
        assert "case_sar_zzz_oversized.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_case_with_non_dict_top_level_list_is_excluded_and_logged():
    # SarCase.model_validate must return a ValidationError, never raise
    # anything else, for any valid json.loads result whose top level is
    # not an object. Pydantic's own error type for this is "model_type".
    with _tmp_case_dir({"case_sar_zzz_toplevel_list.json": json.dumps([1, 2, 3])}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "model_type" in log
        assert "case_sar_zzz_toplevel_list.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_case_with_non_dict_top_level_number_is_excluded_and_logged():
    with _tmp_case_dir({"case_sar_zzz_toplevel_number.json": "42"}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "model_type" in log
        assert "case_sar_zzz_toplevel_number.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_answer_key_content_nested_under_subject_field_is_excluded():
    # F1 (external review): a case file crafted so subject.entity_name
    # itself carries a nested object containing red_flags and
    # distractor_facts content, trying to smuggle answer key material
    # through a field the schema expects to be a plain string. Strict
    # typing rejects the wrong shape outright, so the whole file is
    # excluded and the sentinel can never reach any display output.
    broken = dict(get_case_full("sar-002"))
    sentinel = "SMUGGLED-ANSWER-KEY-SENTINEL"
    broken["case_id"] = "sar-smuggle-test"
    broken["subject"] = dict(broken["subject"])
    broken["subject"]["entity_name"] = {
        "red_flags": [{"id": "rf1", "label": sentinel}],
        "distractor_facts": [sentinel],
    }

    with _tmp_case_dir({"case_sar_zzz_smuggle.json": json.dumps(broken)}) as (cases, invalid_count, log):
        assert "sar-smuggle-test" not in cases
        assert invalid_count == 1
        assert "subject.entity_name" in log
        assert sentinel not in log
        for case_id in cases:
            assert sentinel not in json.dumps(get_case_display(case_id))


def test_deeply_nested_array_in_a_display_leaf_is_excluded():
    # F2: a 5000 deep nested array standing in for a plain string display
    # field. This depth does not necessarily raise RecursionError on every
    # machine (confirmed it parses clean here), so this exercises the
    # fallback that matters regardless: SarCase's strict string typing
    # rejects the wrong shape, excluding the file either way.
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-deepleaf-test"
    text = json.dumps(broken).replace(
        '"Personal current account customer"', "[" * 5000 + "]" * 5000, 1
    )

    with _tmp_case_dir({"case_sar_zzz_deepleaf.json": text}) as (cases, invalid_count, log):
        assert "sar-deepleaf-test" not in cases
        assert invalid_count == 1
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_nan_in_amount_gbp_is_excluded():
    # F2: json.loads's parse_constant hook rejects the bare NaN token
    # before it ever reaches pydantic, wherever it appears in the file.
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-nan-test"
    text = json.dumps(broken).replace("480", "NaN", 1)

    with _tmp_case_dir({"case_sar_zzz_nan.json": text}) as (cases, invalid_count, log):
        assert "sar-nan-test" not in cases
        assert invalid_count == 1
        assert "disallowed JSON constant" in log
        assert "NaN" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_infinity_in_amount_gbp_is_excluded():
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-infinity-test"
    text = json.dumps(broken).replace("480", "Infinity", 1)

    with _tmp_case_dir({"case_sar_zzz_infinity.json": text}) as (cases, invalid_count, log):
        assert "sar-infinity-test" not in cases
        assert invalid_count == 1
        assert "disallowed JSON constant" in log
        assert "Infinity" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_case_id_with_trailing_whitespace_is_rejected():
    # F3: rejected, never stripped or normalised. The real sar-002 file
    # still loads unaffected; this one is simply excluded.
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-002 "

    with _tmp_case_dir({"case_sar_zzz_trailing_space.json": json.dumps(broken)}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "case_id" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_whitespace_only_case_id_is_rejected():
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "   "

    with _tmp_case_dir({"case_sar_zzz_whitespace_id.json": json.dumps(broken)}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "case_id" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_case_id_failing_the_pattern_is_rejected():
    for bad_id in ["SAR-002", "sar_002", "not-sar-prefixed", "sar-", "sar--002", "sar-002-", "sar"]:
        broken = dict(get_case_full("sar-002"))
        broken["case_id"] = bad_id
        with _tmp_case_dir({"case_sar_zzz_badid.json": json.dumps(broken)}) as (cases, invalid_count, log):
            assert invalid_count == 1, f"{bad_id!r} should have been rejected by the case_id pattern"
            assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


def test_unknown_extra_key_anywhere_is_excluded():
    # extra="forbid" on every model: an unrecognised field anywhere in the
    # document, not only at the top level, excludes the whole file.
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-extrakey-test"
    broken["subject"] = dict(broken["subject"])
    broken["subject"]["unexpected_field"] = "should not be accepted"

    with _tmp_case_dir({"case_sar_zzz_extrakey.json": json.dumps(broken)}) as (cases, invalid_count, log):
        assert "sar-extrakey-test" not in cases
        assert invalid_count == 1
        assert "subject.unexpected_field" in log
        assert "extra_forbidden" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)


class _CountingJson:
    """Wraps the real json module, counting calls to loads() so a test can
    confirm parsing was never attempted, without changing behaviour."""

    def __init__(self, real_json):
        self._real = real_json
        self.load_calls = 0

    def loads(self, *args, **kwargs):
        self.load_calls += 1
        return self._real.loads(*args, **kwargs)

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def test_file_grown_past_the_cap_is_excluded_before_json_parsing_is_attempted():
    # F5: simulates a file whose read() returns more than the cap
    # regardless of what is actually committed on disk, by monkeypatching
    # the open name inside routes_sar_sandbox's own module namespace to
    # return a fake file object for one target only, real open for
    # everything else. Confirms json.loads is called exactly three times,
    # once per real committed case, never for the grown target.
    cap = routes_sar_sandbox._CASE_FILE_SIZE_CAP_BYTES
    target_name = "case_sar_zzz_grown.json"
    real_open = open

    class _GrownFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, n=-1):
            return b"x" * (cap + 1)

    def fake_open(path, mode="r", *args, **kwargs):
        if target_name in str(path):
            return _GrownFile()
        return real_open(path, mode, *args, **kwargs)

    real_json = routes_sar_sandbox.json
    counting_json = _CountingJson(real_json)
    routes_sar_sandbox.json = counting_json
    routes_sar_sandbox.open = fake_open
    try:
        with _tmp_case_dir({target_name: "{}"}) as (cases, invalid_count, log):
            assert invalid_count == 1
            assert "size cap" in log
            assert target_name in log
            assert counting_json.load_calls == 3, (
                "json.loads must be called only for the three real cases, "
                "never for the file whose read() exceeded the cap"
            )
            assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
    finally:
        routes_sar_sandbox.json = real_json
        del routes_sar_sandbox.open


def test_case_with_duplicate_case_id_is_excluded_and_logged():
    # Fails closed: both files claiming sar-002 are excluded, not just the
    # second one processed. Filename order is not a correctness signal, so
    # letting one silently win regardless of which file is actually right
    # would be worse than dropping both and forcing a human to look.
    duplicate = dict(get_case_full("sar-002"))
    duplicate["title"] = "DUPLICATE PROBE TITLE, MUST NOT WIN"

    with _tmp_case_dir({"case_sar_zzz_duplicate.json": json.dumps(duplicate)}) as (cases, invalid_count, log):
        assert invalid_count == 2
        assert "claimed by multiple files" in log
        assert "sar-002" in log
        assert "case_sar_002.json" in log
        assert "case_sar_zzz_duplicate.json" in log
        assert "sar-002" not in cases
        assert {"sar-phase0-001", "sar-003"}.issubset(cases)
        assert get_case_display("sar-002") is None
        assert get_case_display("sar-003") is not None


def test_get_load_status_with_all_cases_valid():
    status = get_load_status()
    assert status == {"cases_loaded": 3, "cases_invalid": 0, "load_error": False}


def test_get_load_status_with_one_invalid_case():
    broken = dict(get_case_full("sar-002"))
    broken["case_id"] = "sar-broken-for-health-status"
    broken.pop("onward_movement")

    with _tmp_case_dir({"case_sar_zzz_broken_health.json": json.dumps(broken)}):
        status = get_load_status()
        assert status["cases_loaded"] == 3
        assert status["cases_invalid"] == 1
        assert status["load_error"] is False


def test_health_endpoint_includes_sar_sandbox_counts_only():
    # main.py must not read routes_sar_sandbox's private globals directly,
    # only through get_load_status, and /api/health must stay 200 with its
    # existing fields intact regardless of the SAR sandbox load state.
    from fastapi.testclient import TestClient
    from main import app

    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "screening_backend" in body
    assert "api_key_configured" in body
    assert body["sar_sandbox"] == {"cases_loaded": 3, "cases_invalid": 0, "load_error": False}


def test_existing_cases_remain_unchanged():
    phase0 = get_case_full("sar-phase0-001")
    assert phase0["title"] == "The Consultancy Invoice Loop"
    assert [rf["id"] for rf in phase0["red_flags"]] == ["rf1", "rf2", "rf3", "rf4", "rf5", "rf6"]
    assert len(phase0["distractor_facts"]) == 2

    weekend_courier = get_case_full("sar-002")
    assert weekend_courier["title"] == "The Weekend Courier"
    assert [rf["id"] for rf in weekend_courier["red_flags"]] == ["rfA", "rfB", "rfC", "rfD", "rfE", "rfF"]
    assert len(weekend_courier["distractor_facts"]) == 2


class _FakeExtractRequestContext:
    """Minimal stand-in for FastAPI's Request, just enough for
    _extract_rate_limited's headers.get and client.host lookups."""

    headers = {}
    client = None


def test_extract_response_never_leaks_unverified_model_content():
    # F4: a mocked model result puts a real red flag label and a
    # distractor string into every free text field it can, none of them
    # genuine substrings of the submitted narrative. None of that
    # invented content may reach the client: red_flags_mentioned must
    # hold only ids the case's own answer key actually has, and the
    # speculative phrase must be dropped before scoring ever sees it, so
    # it cannot move speculative_score either.
    case = get_case_full("sar-003")
    real_red_flag_label = case["red_flags"][0]["label"]
    distractor_text = case["distractor_facts"][0]
    poison = real_red_flag_label + " :: " + distractor_text

    poisoned_result = ExtractionResult(
        five_ws={
            w: {"addressed": True, "quote": poison}
            for w in ["who", "what", "when", "where", "why"]
        },
        red_flags_mentioned=["rf1", "rf2", "rf1", "not-a-real-id"],
        transaction_detail_cited=True,
        transaction_detail_quote=poison,
        speculative_phrases=[poison],
    )

    real_call_once = routes_sar_sandbox._call_extraction_once
    routes_sar_sandbox._call_extraction_once = lambda case_arg, req_arg: poisoned_result
    try:
        req = ExtractRequest(
            case_id="sar-003",
            intro="A short generic intro naming no poisoned content.",
            investigative_body="A short generic investigative body naming no poisoned content.",
            final_disposition="A short generic disposition naming no poisoned content.",
        )
        response = extract(req, _FakeExtractRequestContext())
        body = response.model_dump()
    finally:
        routes_sar_sandbox._call_extraction_once = real_call_once

    dumped = json.dumps(body)
    assert real_red_flag_label not in dumped
    assert distractor_text not in dumped
    assert poison not in dumped

    # deduplicated, order preserved, the invalid id dropped
    assert body["extraction"]["red_flags_mentioned"] == ["rf1", "rf2"]
    for entry in body["extraction"]["five_ws"].values():
        assert entry["quote"] is None
        # review finding 1: a quote that fails verification must clear the
        # paired boolean too, not just its own quote text
        assert entry["addressed"] is False
    assert body["extraction"]["transaction_detail_quote"] is None
    assert body["extraction"]["transaction_detail_cited"] is False
    assert body["extraction"]["speculative_phrases"] == []
    # zero verified speculative phrases scores as clean language, the
    # unverified one must not have been counted against the trainee
    assert body["scoring"]["speculative_score"] == 10


def test_score_parity_for_the_23_sep_production_payloads():
    # F4 score parity: the three 23 Sep production smoke submissions (A,
    # B1, B2), their actual submitted narratives and their actual recorded
    # extraction JSON, replayed through score_extraction directly, before
    # and after adding _project_verified_content's filtering. A change
    # would only appear if a recorded phrase was not an exact substring of
    # its own recorded narrative. All three were already clean, so the
    # expected result is no change at all.
    red_flags = get_case_full("sar-003")["red_flags"]

    payload_a_narrative = dict(
        case_id="sar-003",
        intro=(
            "This report concerns a customer of the firm. Activity on the account "
            "appeared unusual and inconsistent with what we would expect."
        ),
        investigative_body=(
            "The customer received several payments that did not seem to fit their "
            "profile. We were not satisfied with the explanation provided and "
            "consider the activity suspicious."
        ),
        final_disposition="We suspect the funds may be the proceeds of crime.",
    )
    payload_a_extraction = {
        "five_ws": {
            "who": {"addressed": False, "quote": None},
            "what": {"addressed": False, "quote": None},
            "when": {"addressed": False, "quote": None},
            "where": {"addressed": False, "quote": None},
            "why": {"addressed": False, "quote": None},
        },
        "red_flags_mentioned": [],
        "transaction_detail_cited": False,
        "transaction_detail_quote": None,
        "speculative_phrases": ["We suspect the funds may be the proceeds of crime."],
        "sections_present": {"intro": True, "investigative_body": True, "final_disposition": True},
    }

    payload_b_narrative = dict(
        case_id="sar-003",
        intro=(
            "This SAR concerns Aldercroft Property Services Ltd. The firm received "
            "a verified law enforcement information request about the director."
        ),
        investigative_body=(
            "Because law enforcement has shown interest in the director, we consider "
            "the account activity suspicious. The request indicates the director may "
            "be involved in criminal activity."
        ),
        final_disposition="We suspect money laundering on the basis of the law enforcement interest.",
    )
    payload_b1_extraction = {
        "five_ws": {
            "who": {"addressed": True, "quote": "This SAR concerns Aldercroft Property Services Ltd."},
            "what": {"addressed": False, "quote": None},
            "when": {"addressed": False, "quote": None},
            "where": {"addressed": False, "quote": None},
            "why": {
                "addressed": True,
                "quote": "Because law enforcement has shown interest in the director, we consider the account activity suspicious.",
            },
        },
        "red_flags_mentioned": [],
        "transaction_detail_cited": False,
        "transaction_detail_quote": None,
        "speculative_phrases": [
            "The request indicates the director may be involved in criminal activity.",
            "We suspect money laundering on the basis of the law enforcement interest.",
        ],
        "sections_present": {"intro": True, "investigative_body": True, "final_disposition": True},
    }
    payload_b2_extraction = {
        "five_ws": {
            "who": {"addressed": True, "quote": "This SAR concerns Aldercroft Property Services Ltd."},
            "what": {"addressed": False, "quote": None},
            "when": {"addressed": False, "quote": None},
            "where": {"addressed": False, "quote": None},
            "why": {
                "addressed": True,
                "quote": "The request indicates the director may be involved in criminal activity.",
            },
        },
        "red_flags_mentioned": [],
        "transaction_detail_cited": False,
        "transaction_detail_quote": None,
        "speculative_phrases": [
            "The request indicates the director may be involved in criminal activity.",
            "We suspect money laundering on the basis of the law enforcement interest.",
        ],
        "sections_present": {"intro": True, "investigative_body": True, "final_disposition": True},
    }

    cases = {
        "A": (payload_a_narrative, payload_a_extraction, 5),
        "B1": (payload_b_narrative, payload_b1_extraction, 25),
        "B2": (payload_b_narrative, payload_b2_extraction, 25),
    }

    for name, (narrative, extraction, recorded_total) in cases.items():
        before = score_extraction(copy.deepcopy(extraction), red_flags)
        assert before["total"] == recorded_total, (
            f"{name}: recorded total does not match a fresh score_extraction "
            f"call on the same recorded extraction, before any change here"
        )

        req = ExtractRequest(**narrative)
        filtered = routes_sar_sandbox._project_verified_content(
            copy.deepcopy(extraction), req, red_flags
        )
        after = score_extraction(filtered, red_flags)

        assert after["total"] == before["total"], (
            f"{name}: score changed after adding response projection "
            f"(before={before['total']}, after={after['total']}). All three payloads' "
            f"phrases were exact substrings of their own recorded narrative, so no "
            f"change was expected; a change here means one of them was not."
        )


def test_normalise_for_match_collapses_whitespace_and_casefolds():
    assert routes_sar_sandbox._normalise_for_match("  Hello   World  ") == "hello world"
    assert routes_sar_sandbox._normalise_for_match("Hello\tWorld\n") == "hello world"


def test_normalise_for_match_folds_curly_quotes_to_straight():
    assert routes_sar_sandbox._normalise_for_match("“Hello”") == '"hello"'
    assert routes_sar_sandbox._normalise_for_match("It’s fine") == "it's fine"


def test_normalise_for_match_folds_dashes_to_hyphen():
    assert routes_sar_sandbox._normalise_for_match("law–enforcement") == "law-enforcement"
    assert routes_sar_sandbox._normalise_for_match("law—enforcement") == "law-enforcement"
    assert routes_sar_sandbox._normalise_for_match("law-enforcement") == "law-enforcement"


def test_normalise_for_match_is_case_insensitive():
    assert routes_sar_sandbox._normalise_for_match("EXAMPLE TRADING LTD") == "example trading ltd"


def test_verify_and_locate_rejects_a_candidate_under_three_words():
    # The 3-word floor lives in _verify_and_locate, not _normalise_for_match
    # itself, since it is a rule about how much a match is worth, not about
    # how text is normalised. Covered here alongside the normalisation
    # tests above per review finding 2.
    sections = ["The firm reviewed the account activity thoroughly."]
    verified, span = routes_sar_sandbox._verify_and_locate("The firm", sections)
    assert verified is False
    assert span is None

    verified, span = routes_sar_sandbox._verify_and_locate("The firm reviewed", sections)
    assert verified is True
    assert span == "The firm reviewed"


def test_verify_and_locate_matches_cosmetic_variants_but_withholds_the_span():
    sections = ["The report concerns a law–enforcement request about the director."]
    # the model's quote uses a plain hyphen where the narrative has an en
    # dash: normalisation must still verify it, but since it is not the
    # exact original substring, no display text is returned
    verified, span = routes_sar_sandbox._verify_and_locate(
        "a law-enforcement request about the director", sections
    )
    assert verified is True
    assert span is None


def test_verify_and_locate_matches_a_phrase_spanning_a_section_boundary():
    sections = [
        "The firm reviewed the account",
        "activity over the review period.",
    ]
    verified, span = routes_sar_sandbox._verify_and_locate(
        "the account activity over the review period", sections
    )
    assert verified is True
    assert span == "the account activity over the review period"


def _sequential_extraction_mock(results):
    responses = iter(results)
    return lambda case_arg, req_arg: next(responses)


def test_majority_vote_fabricated_quote_from_majority_does_not_earn_credit():
    # F4 review finding 3: exercise _extract_with_consistency's 3-run
    # majority-vote merge path, not just the common 2-run agreement path.
    # Two of three runs agree "who" is addressed and cite the same
    # fabricated quote (not present anywhere in the narrative); the third
    # disagrees on transaction_detail_cited, forcing the tie-break third
    # call. The merge inherits the majority's fabricated quote, but
    # _project_verified_content must still clear both the quote and the
    # addressed boolean: agreement among the model's own runs is not
    # evidence a quote is genuine.
    fabricated_who_quote = "The customer confirmed foreign beneficiary details by phone"

    def result(who_addressed, who_quote, transaction_cited):
        return ExtractionResult(
            five_ws={
                "who": {"addressed": who_addressed, "quote": who_quote},
                "what": {"addressed": False, "quote": None},
                "when": {"addressed": False, "quote": None},
                "where": {"addressed": False, "quote": None},
                "why": {"addressed": False, "quote": None},
            },
            red_flags_mentioned=[],
            transaction_detail_cited=transaction_cited,
            transaction_detail_quote=None,
            speculative_phrases=[],
        )

    real_call_once = routes_sar_sandbox._call_extraction_once
    routes_sar_sandbox._call_extraction_once = _sequential_extraction_mock([
        result(True, fabricated_who_quote, True),
        result(True, fabricated_who_quote, False),  # disagrees, forces run3
        result(False, None, False),
    ])
    try:
        req = ExtractRequest(
            case_id="sar-003",
            intro="A short generic intro naming no poisoned content.",
            investigative_body="A short generic investigative body naming no poisoned content.",
            final_disposition="A short generic disposition naming no poisoned content.",
        )
        response = extract(req, _FakeExtractRequestContext())
        body = response.model_dump()
    finally:
        routes_sar_sandbox._call_extraction_once = real_call_once

    assert body["extraction"]["five_ws"]["who"]["addressed"] is False
    assert body["extraction"]["five_ws"]["who"]["quote"] is None


def test_majority_vote_genuine_quote_survives_a_lone_fabricating_run():
    genuine_who_quote = "This SAR concerns Example Trading Ltd"
    fabricated_who_quote = "The customer confirmed foreign beneficiary details by phone"

    def result(who_addressed, who_quote, transaction_cited):
        return ExtractionResult(
            five_ws={
                "who": {"addressed": who_addressed, "quote": who_quote},
                "what": {"addressed": False, "quote": None},
                "when": {"addressed": False, "quote": None},
                "where": {"addressed": False, "quote": None},
                "why": {"addressed": False, "quote": None},
            },
            red_flags_mentioned=[],
            transaction_detail_cited=transaction_cited,
            transaction_detail_quote=None,
            speculative_phrases=[],
        )

    real_call_once = routes_sar_sandbox._call_extraction_once
    routes_sar_sandbox._call_extraction_once = _sequential_extraction_mock([
        result(True, genuine_who_quote, True),
        result(True, genuine_who_quote, False),  # disagrees, forces run3
        result(True, fabricated_who_quote, False),  # the lone fabricator
    ])
    try:
        req = ExtractRequest(
            case_id="sar-003",
            intro="This SAR concerns Example Trading Ltd, a customer of the firm.",
            investigative_body="A short generic investigative body naming no poisoned content.",
            final_disposition="A short generic disposition naming no poisoned content.",
        )
        response = extract(req, _FakeExtractRequestContext())
        body = response.model_dump()
    finally:
        routes_sar_sandbox._call_extraction_once = real_call_once

    assert body["extraction"]["five_ws"]["who"]["addressed"] is True
    assert body["extraction"]["five_ws"]["who"]["quote"] == genuine_who_quote
    assert fabricated_who_quote not in json.dumps(body)


def test_majority_vote_excludes_poisoned_content_confined_to_the_disagreeing_run():
    case = get_case_full("sar-003")
    real_red_flag_label = case["red_flags"][0]["label"]
    distractor_text = case["distractor_facts"][0]
    poison = real_red_flag_label + " :: " + distractor_text

    def clean_five_ws(why_addressed=False, why_quote=None):
        return {
            "who": {"addressed": False, "quote": None},
            "what": {"addressed": False, "quote": None},
            "when": {"addressed": False, "quote": None},
            "where": {"addressed": False, "quote": None},
            "why": {"addressed": why_addressed, "quote": why_quote},
        }

    run1 = ExtractionResult(
        five_ws=clean_five_ws(),
        red_flags_mentioned=[],
        transaction_detail_cited=False,
        transaction_detail_quote=None,
        speculative_phrases=[],
    )
    run2 = ExtractionResult(
        five_ws=clean_five_ws(),
        red_flags_mentioned=[],
        transaction_detail_cited=False,
        transaction_detail_quote=None,
        speculative_phrases=["a phrase not actually in the narrative"],  # differs in count, forces run3
    )
    run3 = ExtractionResult(
        five_ws=clean_five_ws(why_addressed=True, why_quote=poison),
        red_flags_mentioned=["rf1"],
        transaction_detail_cited=True,
        transaction_detail_quote=poison,
        speculative_phrases=[poison],
    )

    real_call_once = routes_sar_sandbox._call_extraction_once
    routes_sar_sandbox._call_extraction_once = _sequential_extraction_mock([run1, run2, run3])
    try:
        req = ExtractRequest(
            case_id="sar-003",
            intro="A short generic intro naming no poisoned content.",
            investigative_body="A short generic investigative body naming no poisoned content.",
            final_disposition="A short generic disposition naming no poisoned content.",
        )
        response = extract(req, _FakeExtractRequestContext())
        body = response.model_dump()
    finally:
        routes_sar_sandbox._call_extraction_once = real_call_once

    dumped = json.dumps(body)
    assert real_red_flag_label not in dumped
    assert distractor_text not in dumped
    assert poison not in dumped
    assert body["extraction"]["red_flags_mentioned"] == []
    assert body["extraction"]["five_ws"]["why"]["addressed"] is False
    assert body["extraction"]["five_ws"]["why"]["quote"] is None
    assert body["extraction"]["transaction_detail_cited"] is False
    assert body["extraction"]["transaction_detail_quote"] is None
    assert body["extraction"]["speculative_phrases"] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"{len(tests)} test(s) passed.")
