"""Focused coverage for routes_sar_sandbox.py, added alongside case_sar_003.json.

Calls the route functions directly (list_cases/get_case are plain callables
under FastAPI's APIRouter decorator, no ASGI server or TestClient needed).
Run with pytest, or directly: python test_sar_sandbox.py
"""

import contextlib
import io
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException

import routes_sar_sandbox
from routes_sar_sandbox import (
    _CASES_CACHE,
    _validate_case,
    get_case,
    get_case_display,
    get_case_full,
    get_load_status,
    list_cases,
    score_extraction,
)

EXPECTED_DISPLAY_FIELDS = {
    "title", "subject", "activity_window", "transactions",
    "onward_movement", "supporting_facts",
}


def get_display_json(case_id):
    return json.loads(get_case(case_id).body)


def test_existing_and_sar_003_cases_load_via_glob():
    assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(_CASES_CACHE)


def test_sar_003_appears_in_case_list():
    ids = [c["case_id"] for c in json.loads(list_cases().body)]
    assert ids.count("sar-003") == 1


def test_case_detail_contains_only_whitelisted_fields():
    for case_id in _CASES_CACHE:
        display = get_display_json(case_id)
        assert set(display.keys()) == EXPECTED_DISPLAY_FIELDS


def test_nested_case_fields_are_excluded_by_default():
    case = get_case_full("sar-003")
    sentinel = "server-side-answer-key-probe"
    probe_locations = [
        case["subject"],
        case["activity_window"],
        case["transactions"][0],
        case["onward_movement"],
    ]

    try:
        for record in probe_locations:
            record["answer_key_probe"] = sentinel
        assert sentinel not in json.dumps(get_display_json("sar-003"))
    finally:
        for record in probe_locations:
            record.pop("answer_key_probe", None)


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
            case = json.load(f)
        errors = _validate_case(case, path)
        assert errors == [], f"{path.name} failed validation: {errors}"
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


class _RecursionErrorOnTarget:
    """Wraps the real json module, raising RecursionError only for a named
    target file and delegating everything else (including json.dumps used
    elsewhere in this test file, and the real load for every other case
    file _load_cases reads in the same pass) to the real module."""

    def __init__(self, real_json, target_name):
        self._real = real_json
        self._target_name = target_name

    def load(self, f):
        if self._target_name in getattr(f, "name", ""):
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")
        return self._real.load(f)

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
    # RecursionError for one named file and defers to the real json module
    # for every other file _load_cases reads, so this is deterministic on
    # every platform, not tuned to one machine's stack depth.
    target_name = "case_sar_zzz_deepnest.json"
    real_json = routes_sar_sandbox.json
    routes_sar_sandbox.json = _RecursionErrorOnTarget(real_json, target_name)
    try:
        with _tmp_case_dir({target_name: "[1, 2, 3]"}) as (cases, invalid_count, log):
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
    # _validate_case must return an error, never raise, for any valid
    # json.load result whose top level is not an object.
    with _tmp_case_dir({"case_sar_zzz_toplevel_list.json": json.dumps([1, 2, 3])}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "top level must be an object" in log
        assert "case_sar_zzz_toplevel_list.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


def test_case_with_non_dict_top_level_number_is_excluded_and_logged():
    with _tmp_case_dir({"case_sar_zzz_toplevel_number.json": "42"}) as (cases, invalid_count, log):
        assert invalid_count == 1
        assert "top level must be an object" in log
        assert "case_sar_zzz_toplevel_number.json" in log
        assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(cases)
        assert get_case_display("sar-003") is not None


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"{len(tests)} test(s) passed.")
