"""Focused coverage for routes_sar_sandbox.py, added alongside case_sar_003.json.

Calls the route functions directly (list_cases/get_case are plain callables
under FastAPI's APIRouter decorator, no ASGI server or TestClient needed).
Run with pytest, or directly: python test_sar_sandbox.py
"""

import json

from fastapi import HTTPException

from routes_sar_sandbox import (
    _CASES_CACHE,
    get_case,
    get_case_display,
    get_case_full,
    list_cases,
    score_extraction,
)

EXPECTED_DISPLAY_FIELDS = {
    "title", "subject", "activity_window", "transactions",
    "onward_movement", "supporting_facts",
}


def test_existing_and_sar_003_cases_load_via_glob():
    assert {"sar-phase0-001", "sar-002", "sar-003"}.issubset(_CASES_CACHE)


def test_sar_003_appears_in_case_list():
    ids = [c["case_id"] for c in json.loads(list_cases().body)]
    assert ids.count("sar-003") == 1


def test_case_detail_contains_only_whitelisted_fields():
    for case_id in _CASES_CACHE:
        display = json.loads(get_case(case_id).body)
        assert set(display.keys()) == EXPECTED_DISPLAY_FIELDS


def test_answer_key_and_distractors_do_not_leak_to_client():
    for case_id in _CASES_CACHE:
        display = json.loads(get_case(case_id).body)
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
    display = json.loads(get_case("sar-003").body)
    instruction = display["subject"]["practice_instruction"]
    assert instruction == (
        "Identify the funds you suspect may be criminal property and explain "
        "the observed facts supporting that suspicion. Do not state that the "
        "property is criminal property, that a predicate offence occurred, "
        "or that any person is guilty."
    )


def test_sar_003_display_does_not_preanswer_distractors():
    display = json.loads(get_case("sar-003").body)
    review_trigger = display["subject"]["review_trigger"]
    destination_note = display["onward_movement"]["destination_note"]

    assert review_trigger == (
        "A verified law-enforcement information request concerning the director "
        "was received. It disclosed no allegation or finding and prompted this "
        "relationship review."
    )
    assert destination_note == (
        "The destination was identified from the payment details as a UK "
        "solicitor's client account associated with the property matter reference"
    )

    visible_distractor_text = f"{review_trigger} {destination_note}".lower()
    assert "does not establish" not in visible_distractor_text
    assert "neither suspicious nor exculpatory" not in visible_distractor_text
    assert "must not be counted as a red flag" not in visible_distractor_text


def test_unknown_case_is_rejected():
    assert get_case_full("sar-999") is None
    assert get_case_display("sar-999") is None
    try:
        get_case("sar-999")
        raise AssertionError("expected HTTPException for an unknown case_id")
    except HTTPException as exc:
        assert exc.status_code == 404


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
