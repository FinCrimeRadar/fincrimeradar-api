"""KYB Lab tests, one group per section 8 item of docs/specs/kyb-lab-api-spec.md.

The upstream is always a fake. Nothing here calls Companies House.
"""

import copy
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import routes_kyb_lab as kyb
from routes_kyb_lab import UpIdv, derive_idv_status

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "kyb"
SWAGGER = ROOT / "docs" / "specs" / "companies-house-swagger" / "spec"
NUM = "12345678"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
TODAY = date(2026, 10, 11)
TEST_KEY = "test-key-not-a-real-key"

PATHS = {
    "profile": f"/company/{NUM}",
    "officers": f"/company/{NUM}/officers",
    "pscs": f"/company/{NUM}/persons-with-significant-control",
    "statements": f"/company/{NUM}/persons-with-significant-control-statements",
    "filing": f"/company/{NUM}/filing-history",
    "charges": f"/company/{NUM}/charges",
    "insolvency": f"/company/{NUM}/insolvency",
}

EXCLUDED_TYPES = [
    "registered-overseas-entity",
    "uk-establishment",
    "oversea-company",
    "limited-partnership",
    "scottish-partnership",
    "eeig",
    "eeig-establishment",
    "ukeig",
    "icvc-securities",
    "icvc-warrant",
    "icvc-umbrella",
    "investment-company-with-variable-capital",
    "registered-society-non-jurisdictional",
    "industrial-and-provident-society",
    "northern-ireland-other",
    "royal-charter",
    "charitable-incorporated-organisation",
    "scottish-charitable-incorporated-organisation",
    "further-education-or-sixth-form-college-corporation",
    "converted-or-closed",
    "old-public-company",
    "northern-ireland",
    "protected-cell-company",
    "assurance-company",
    "unregistered-company",
    "other",
    "european-public-limited-liability-company-se",
    "united-kingdom-societas",
]

IN_SCOPE = [
    "ltd",
    "plc",
    "private-unlimited",
    "private-unlimited-nsc",
    "private-limited-guarant-nsc",
    "private-limited-guarant-nsc-limited-exemption",
    "private-limited-shares-section-30-exemption",
    "llp",
]

GLOBAL_DNE = [
    "The register is not comprehensive and may contain inaccuracies, errors or omissions. Companies House accepts no responsibility for their consequences.",
    "Companies House checks filings for completeness and validity but does not closely review every document. Accounts receive only basic checks.",
    "Register status does not establish that a company is trading. Dormant accounts are a separate filing fact.",
    "Identity verification records do not establish legitimacy, ownership accuracy or completed CDD.",
    "The absence of an indicator is not evidence of low risk.",
    "This readout is not customer due diligence and does not replace it.",
]

SIX_IDENTIFIERS = [
    "change-registered-office-address-company",
    "change-registered-office-address-company-with-date-old-address",
    "change-registered-office-address-company-with-date-old-address-new-address",
    "change-registered-office-address-limited-liability-partnership",
    "change-registered-office-address-limited-liability-partnership-with-date-old-address",
    "change-registered-office-address-limited-liability-partnership-with-date-old-address-new-address",
]


def fixture(name):
    return copy.deepcopy(
        json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    )


def swagger(name):
    return json.loads((SWAGGER / f"{name}.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class Upstream:
    """Replaces routes_kyb_lab._http_get. Records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params, auth, timeout):
        assert url.startswith(kyb.UPSTREAM_BASE), url
        path = url[len(kyb.UPSTREAM_BASE) :]
        self.calls.append(
            {"path": path, "params": params, "auth": auth, "timeout": timeout}
        )
        result = self.routes.get(path, (404, {}))
        if isinstance(result, Exception):
            raise result
        status, body = result if isinstance(result, tuple) else (200, result)
        return FakeResponse(status, body)

    @property
    def paths(self):
        return [c["path"] for c in self.calls]


def default_routes(**over):
    routes = {
        PATHS["profile"]: fixture("profile_active_ltd"),
        PATHS["officers"]: fixture("officers"),
        PATHS["pscs"]: fixture("psc_list"),
        PATHS["statements"]: fixture("psc_statements"),
        PATHS["filing"]: fixture("filing_history"),
        PATHS["charges"]: fixture("charges"),
        PATHS["insolvency"]: fixture("insolvency"),
    }
    for key, value in over.items():
        routes[PATHS[key]] = value
    return routes


class Ctx:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        app = FastAPI()
        app.include_router(kyb.router)
        self.client = TestClient(app)
        self.upstream = None

    def install(self, routes=None, **over):
        self.upstream = Upstream(
            routes if routes is not None else default_routes(**over)
        )
        self.monkeypatch.setattr(kyb, "_http_get", self.upstream)
        return self.upstream

    def get(self, number=NUM, headers=None):
        return self.client.get(f"/api/kyb/company/{number}", headers=headers or {})

    def body(self, number=NUM, **kw):
        r = self.get(number, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def profile(self, **changes):
        p = fixture("profile_active_ltd")
        p.update(changes)
        return p


@pytest.fixture
def ctx(monkeypatch):
    kyb.reset_state_for_tests()
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", TEST_KEY)
    monkeypatch.setattr(kyb, "_utcnow", lambda: NOW)
    monkeypatch.setattr(kyb.time, "sleep", lambda s: None)
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    yield Ctx(monkeypatch)
    kyb.reset_state_for_tests()


def indicator_ids(body):
    return [i["id"] for i in body["indicators"]]


def officer_by_name(body, name):
    return next(o for o in body["officers"]["items"] if o["name"] == name)


def walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)


# ------------------------------------------------------------------ 1. validation


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12345678", "12345678"),
        ("1", "00000001"),
        ("123456", "00123456"),
        ("  1234567  ", "01234567"),
        ("SC123456", "SC123456"),
        ("sc123456", "SC123456"),
        ("ni0a1b2c", "NI0A1B2C"),
        ("OC301234", "OC301234"),
        ("SC123456\n", "SC123456"),
    ],
)
def test_validation_accepts_and_normalises(raw, expected):
    assert kyb.normalise_company_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "123456789",
        "1234567890123",
        "../../etc/passwd",
        "..%2f..%2fetc",
        "%53C123456",
        "SC12345",
        "SC1234567",
        "SC12 3456",
        "SC12-3456",
        "٠١٢٣٤٥٦٧",
        "ＳＣ１２３４５６",
        "ſc123456",
        "straße12",
        "12345678;DROP",
        "12345678/officers",
        "http://evil.example",
    ],
)
def test_validation_rejects(raw):
    assert kyb.normalise_company_number(raw) is None


@pytest.mark.parametrize(
    "path",
    [
        "bad",
        "SC12",
        "..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2fadmin",
        "a/b/c",
        "x" * 200,
        "%E2%80%AE12345678",
    ],
)
def test_endpoint_returns_generic_422_without_echo(ctx, path):
    up = ctx.install()
    r = ctx.client.get(f"/api/kyb/company/{path}")
    assert r.status_code == 422
    assert r.json() == {"detail": "Invalid company number."}
    assert up.calls == []


def test_endpoint_empty_number_is_422(ctx):
    ctx.install()
    assert ctx.client.get("/api/kyb/company/").status_code == 422


def test_short_numeric_is_padded_before_upstream(ctx):
    up = ctx.install()
    ctx.get("12345678")
    assert up.paths[0] == f"/company/{NUM}"
    up2 = ctx.install(
        routes={"/company/00001234": (404, {})},
    )
    r = ctx.get("1234")
    assert r.status_code == 404
    assert up2.paths == ["/company/00001234"]


def test_invalid_input_is_not_logged(ctx, capsys):
    ctx.install()
    ctx.client.get("/api/kyb/company/not-a-valid-number-SECRETMARKER")
    out = capsys.readouterr().out
    assert "SECRETMARKER" not in out
    assert "invalid_input" in out


# ------------------------------------------------------------------ 2. indicators


def test_accounts_overdue_uses_next_accounts_only(ctx):
    profile = ctx.profile()
    profile["accounts"]["overdue"] = True
    profile["accounts"]["next_accounts"]["overdue"] = False
    ctx.install(profile=profile)
    assert "accounts_overdue" not in indicator_ids(ctx.body())

    kyb.reset_state_for_tests()
    profile["accounts"]["overdue"] = False
    profile["accounts"]["next_accounts"]["overdue"] = True
    ctx.install(profile=profile)
    body = ctx.body()
    assert "accounts_overdue" in indicator_ids(body)
    ind = next(i for i in body["indicators"] if i["id"] == "accounts_overdue")
    assert (
        ind["evidence_state"] == "register_fact" and ind["source_endpoint"] == "profile"
    )


def test_confirmation_statement_overdue_fires_and_not(ctx):
    profile = ctx.profile()
    profile["confirmation_statement"]["overdue"] = True
    ctx.install(profile=profile)
    assert "confirmation_statement_overdue" in indicator_ids(ctx.body())
    kyb.reset_state_for_tests()
    ctx.install()
    assert "confirmation_statement_overdue" not in indicator_ids(ctx.body())


def officers_with(*records):
    o = fixture("officers")
    o["items"] = list(records)
    o["total_results"] = len(records)
    return o


def director(name="DIRECTOR, One", idv=None, **kw):
    rec = {"name": name, "officer_role": "director", "appointed_on": "2020-01-01"}
    rec.update(kw)
    if idv is not None:
        rec["identity_verification_details"] = idv
    return rec


def test_idv_statement_overdue_indicator(ctx):
    ctx.install(
        officers=officers_with(
            director(idv={"appointment_verification_statement_due_on": "2026-09-01"})
        )
    )
    body = ctx.body()
    ind = next(i for i in body["indicators"] if i["id"] == "idv_statement_overdue")
    assert ind["evidence"]["records"] == [
        {"name": "DIRECTOR, One", "source": "officer", "due_on": "2026-09-01"}
    ]
    kyb.reset_state_for_tests()
    ctx.install(
        officers=officers_with(
            director(idv={"appointment_verification_statement_due_on": "2026-12-01"})
        )
    )
    assert "idv_statement_overdue" not in indicator_ids(ctx.body())


def test_idv_statement_removed_indicator(ctx):
    ctx.install(
        officers=officers_with(
            director(
                idv={
                    "appointment_verification_start_on": "2026-01-01",
                    "appointment_verification_end_on": "2026-02-01",
                }
            )
        )
    )
    assert "idv_statement_removed" in indicator_ids(ctx.body())
    kyb.reset_state_for_tests()
    ctx.install(
        officers=officers_with(
            director(idv={"appointment_verification_start_on": "2026-01-01"})
        )
    )
    assert "idv_statement_removed" not in indicator_ids(ctx.body())


def test_idv_indicator_for_psc_records(ctx):
    psc = fixture("psc_list")
    psc["items"][0]["identity_verification_details"] = {
        "appointment_verification_statement_due_on": "2026-09-01"
    }
    ctx.install(pscs=psc)
    ind = next(
        i for i in ctx.body()["indicators"] if i["id"] == "idv_statement_overdue"
    )
    assert ind["evidence"]["records"][0]["source"] == "psc"


def test_previous_names_indicator(ctx):
    profile = ctx.profile(
        previous_company_names=[
            {
                "name": "OLD NAME LTD",
                "effective_from": "2019-03-04",
                "ceased_on": "2021-01-01",
            }
        ]
    )
    ctx.install(profile=profile)
    ind = next(i for i in ctx.body()["indicators"] if i["id"] == "previous_names")
    assert ind["evidence"]["names"] == [
        {
            "name": "OLD NAME LTD",
            "effective_from": "2019-03-04",
            "ceased_on": "2021-01-01",
        }
    ]
    kyb.reset_state_for_tests()
    ctx.install()
    assert "previous_names" not in indicator_ids(ctx.body())


def test_insolvency_history_fires_only_with_link_and_resource(ctx):
    up = ctx.install()
    assert "insolvency_history" in indicator_ids(ctx.body())
    assert PATHS["insolvency"] in up.paths
    # Link present but the resource is a 404: no indicator.
    kyb.reset_state_for_tests()
    ctx.install(insolvency=(404, {}))
    assert "insolvency_history" not in indicator_ids(ctx.body())
    # Link absent but the deprecated flag says true: endpoint never called.
    kyb.reset_state_for_tests()
    profile = ctx.profile()
    del profile["links"]["insolvency"]
    assert profile["has_insolvency_history"] is True
    up = ctx.install(profile=profile)
    assert "insolvency_history" not in indicator_ids(ctx.body())
    assert PATHS["insolvency"] not in up.paths


def test_outstanding_charges_fires_only_with_link_and_outstanding_status(ctx):
    up = ctx.install()
    body = ctx.body()
    ind = next(i for i in body["indicators"] if i["id"] == "outstanding_charges")
    assert ind["evidence"]["outstanding_count"] == 1
    assert PATHS["charges"] in up.paths
    kyb.reset_state_for_tests()
    charges = fixture("charges")
    charges["items"][0]["status"] = "fully-satisfied"
    ctx.install(charges=charges)
    assert "outstanding_charges" not in indicator_ids(ctx.body())
    kyb.reset_state_for_tests()
    profile = ctx.profile()
    del profile["links"]["charges"]
    assert profile["has_charges"] is True
    up = ctx.install(profile=profile)
    assert "outstanding_charges" not in indicator_ids(ctx.body())
    assert PATHS["charges"] not in up.paths


# ------------------------------------------------------------------ 3. derivation


def D(**kw):
    return UpIdv(**kw)


@pytest.mark.parametrize(
    "fields,is_psc",
    [
        ({"appointment_verification_start_on": "2026-10-12"}, False),
        (
            {
                "appointment_verification_end_on": "2026-10-12",
                "appointment_verification_start_on": "2026-01-01",
            },
            False,
        ),
        (
            {
                "appointment_verification_start_on": "2026-05-01",
                "appointment_verification_end_on": "2026-04-01",
            },
            False,
        ),
        ({"appointment_verification_end_on": "2026-04-01"}, False),
        (
            {
                "appointment_verification_start_on": "2026-01-01",
                "appointment_verification_end_on": "2026-02-01",
                "appointment_verification_statement_due_on": "2026-03-01",
            },
            False,
        ),
        (
            {
                "appointment_verification_statement_date": "2026-12-01",
                "appointment_verification_statement_due_on": "2026-11-01",
            },
            True,
        ),
        ({"appointment_verification_start_on": "not-a-date"}, False),
    ],
    ids=[
        "start_after_today",
        "end_after_today",
        "end_before_start",
        "end_without_start",
        "due_after_end",
        "psc_statement_date_after_due",
        "unparseable_date",
    ],
)
def test_8a_each_conflict_trigger(fields, is_psc):
    assert (
        derive_idv_status(D(**fields), is_psc=is_psc, today=TODAY)
        == "verification_data_conflict"
    )


def test_8a_conflict_wins_over_supplied_and_removed():
    supplied_but_future = D(appointment_verification_start_on="2027-01-01")
    assert (
        derive_idv_status(supplied_but_future, is_psc=False, today=TODAY)
        == "verification_data_conflict"
    )
    removed_but_reversed = D(
        appointment_verification_start_on="2026-06-01",
        appointment_verification_end_on="2026-05-01",
    )
    assert (
        derive_idv_status(removed_but_reversed, is_psc=False, today=TODAY)
        == "verification_data_conflict"
    )


def test_psc_statement_date_after_due_only_conflicts_for_psc():
    fields = {
        "appointment_verification_statement_date": "2026-12-01",
        "appointment_verification_statement_due_on": "2026-11-01",
    }
    assert derive_idv_status(D(**fields), is_psc=False, today=TODAY) == "statement_due"


def test_8b_statement_supplied_without_identity_verified_on():
    d = D(appointment_verification_start_on="2026-01-10")
    assert d.identity_verified_on is None
    assert derive_idv_status(d, is_psc=False, today=TODAY) == "statement_supplied"
    assert (
        derive_idv_status(
            D(appointment_verification_start_on="2026-10-11"), is_psc=False, today=TODAY
        )
        == "statement_supplied"
    )


def test_8c_statement_removed():
    d = D(
        appointment_verification_start_on="2026-01-10",
        appointment_verification_end_on="2026-03-01",
    )
    assert derive_idv_status(d, is_psc=False, today=TODAY) == "statement_removed"
    d2 = D(
        appointment_verification_start_on="2026-01-10",
        appointment_verification_end_on="2026-10-11",
    )
    assert derive_idv_status(d2, is_psc=False, today=TODAY) == "statement_removed"


def test_8d_statement_due_including_today():
    assert (
        derive_idv_status(
            D(appointment_verification_statement_due_on="2026-11-18"),
            is_psc=False,
            today=TODAY,
        )
        == "statement_due"
    )
    assert (
        derive_idv_status(
            D(appointment_verification_statement_due_on="2026-10-11"),
            is_psc=False,
            today=TODAY,
        )
        == "statement_due"
    )


def test_8e_statement_overdue():
    assert (
        derive_idv_status(
            D(appointment_verification_statement_due_on="2026-10-10"),
            is_psc=False,
            today=TODAY,
        )
        == "statement_overdue"
    )


def test_8f_no_statement_status_fields_and_acsp_only_shows_evidence(ctx):
    assert (
        derive_idv_status(None, is_psc=False, today=TODAY)
        == "no_statement_status_fields"
    )
    assert (
        derive_idv_status(D(), is_psc=False, today=TODAY)
        == "no_statement_status_fields"
    )
    acsp = {
        "identity_verified_on": "2026-02-01",
        "authorised_corporate_service_provider_name": "Example ACSP Ltd",
        "anti_money_laundering_supervisory_bodies": ["Example Supervisor"],
    }
    assert (
        derive_idv_status(D(**acsp), is_psc=False, today=TODAY)
        == "no_statement_status_fields"
    )
    ctx.install(officers=officers_with(director(idv=acsp)))
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["status"] == "no_statement_status_fields"
    assert rec["evidence"] == acsp


def test_acsp_fields_never_change_a_status(ctx):
    base = {"appointment_verification_start_on": "2026-02-10"}
    with_acsp = dict(
        base,
        identity_verified_on="2026-02-01",
        authorised_corporate_service_provider_name="X",
    )
    assert derive_idv_status(D(**base), is_psc=False, today=TODAY) == derive_idv_status(
        D(**with_acsp), is_psc=False, today=TODAY
    )


def test_today_boundary_uses_europe_london_not_utc(ctx, monkeypatch):
    # 23:30 UTC on 30 June is 00:30 on 1 July in British Summer Time.
    monkeypatch.setattr(
        kyb, "_utcnow", lambda: datetime(2026, 6, 30, 23, 30, tzinfo=UTC)
    )
    ctx.install(
        officers=officers_with(
            director(idv={"appointment_verification_statement_due_on": "2026-06-30"})
        )
    )
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["status"] == "statement_overdue"
    kyb.reset_state_for_tests()
    ctx.install(
        officers=officers_with(
            director(idv={"appointment_verification_statement_due_on": "2026-07-01"})
        )
    )
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["status"] == "statement_due"


def test_statement_date_is_shown_but_never_changes_status(ctx):
    psc = fixture("psc_list")
    ctx.install(pscs=psc)
    rec = ctx.body()["pscs"]["items"][0]["identity_verification"]
    assert rec["status"] == "statement_due"
    assert (
        rec["evidence"]["statement_can_be_supplied_from"]
        == "statement can be supplied from 2025-11-01"
    )
    # Same record without the statement date: same status.
    psc2 = fixture("psc_list")
    del psc2["items"][0]["identity_verification_details"][
        "appointment_verification_statement_date"
    ]
    kyb.reset_state_for_tests()
    ctx.install(pscs=psc2)
    assert (
        ctx.body()["pscs"]["items"][0]["identity_verification"]["status"]
        == "statement_due"
    )


DISPLAY = {
    "statement_supplied": "The register records that an identity verification statement was supplied for this role on 2026-02-10, linking a verified identity to it.",
    "statement_removed": "The register records that the verification statement for this role was removed on 2026-03-01.",
    "verification_data_conflict": "The register returned verification dates that are inconsistent or incomplete. FinCrimeRadar does not interpret them; check the register record directly.",
    "no_statement_status_fields": "No verification statement dates were returned for this role. This does not establish whether the person is verified, unverified, compliant, non-compliant or outside scope.",
    "statement_due": "The register shows a verification statement for this role is due by 2026-11-18. None is recorded as supplied at the time of retrieval.",
    "statement_overdue": "The register shows a verification statement for this role was due by 2026-09-01, and none is recorded as supplied. This is a register record, not a finding that the person is unverified or that an offence has occurred. Check the register record directly before relying on it.",
}


@pytest.mark.parametrize(
    "status,idv",
    [
        ("statement_supplied", {"appointment_verification_start_on": "2026-02-10"}),
        (
            "statement_removed",
            {
                "appointment_verification_start_on": "2026-02-01",
                "appointment_verification_end_on": "2026-03-01",
            },
        ),
        (
            "verification_data_conflict",
            {"appointment_verification_end_on": "2026-03-01"},
        ),
        ("no_statement_status_fields", {}),
        ("statement_due", {"appointment_verification_statement_due_on": "2026-11-18"}),
        (
            "statement_overdue",
            {"appointment_verification_statement_due_on": "2026-09-01"},
        ),
    ],
)
def test_display_wording_is_exact(ctx, status, idv):
    ctx.install(officers=officers_with(director(idv=idv)))
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["status"] == status
    assert rec["display"] == DISPLAY[status]
    assert rec["does_not_establish"] == kyb.IDV_DOES_NOT_ESTABLISH
    assert rec["eligibility"] == "derivation_eligible"


# ------------------------------------------------------------------ 4. eligibility


@pytest.mark.parametrize("role", ["director", "llp-member", "llp-designated-member"])
def test_natural_person_roles_are_derivation_eligible(ctx, role):
    ctx.install(
        officers=officers_with(
            director(
                idv={"appointment_verification_start_on": "2026-02-10"},
                officer_role=role,
            )
        )
    )
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert (
        rec["eligibility"] == "derivation_eligible"
        and rec["status"] == "statement_supplied"
    )


def test_resigned_officer_is_not_interpreted(ctx):
    ctx.install(
        officers=officers_with(
            director(
                resigned_on="2025-01-01",
                idv={"appointment_verification_start_on": "2026-02-10"},
            )
        )
    )
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["eligibility"] == "not_interpreted" and rec["status"] is None
    assert rec["evidence"]["appointment_verification_start_on"] == "2026-02-10"
    assert rec["evidence_label"] == kyb.NOT_INTERPRETED_LABEL


def test_corporate_officer_with_fields_shows_raw_fields_not_a_status(ctx):
    ctx.install()
    body = ctx.body()
    rec = officer_by_name(body, "EXAMPLE NOMINEES LIMITED")["identity_verification"]
    assert rec["eligibility"] == "not_interpreted"
    assert rec["status"] is None
    assert rec["evidence"] == {"appointment_verification_start_on": "2026-03-01"}
    assert (
        rec["evidence_label"]
        == "fields returned by the register; not interpreted in phase 1"
    )


OFFICER_ROLES = swagger("companyOfficerList")["definitions"]["officerSummary"][
    "properties"
]["officer_role"]["enum"]


@pytest.mark.parametrize(
    "role", [r for r in OFFICER_ROLES if r not in kyb.NATURAL_PERSON_OFFICER_ROLES]
)
def test_every_other_officer_role_is_not_interpreted(ctx, role):
    ctx.install(
        officers=officers_with(
            director(
                officer_role=role,
                idv={"appointment_verification_start_on": "2026-02-10"},
            )
        )
    )
    rec = officer_by_name(ctx.body(), "DIRECTOR, One")["identity_verification"]
    assert rec["eligibility"] == "not_interpreted" and rec["status"] is None


def test_individual_psc_derives_and_ceased_psc_does_not(ctx):
    ctx.install()
    rec = ctx.body()["pscs"]["items"][0]["identity_verification"]
    assert (
        rec["eligibility"] == "derivation_eligible" and rec["status"] == "statement_due"
    )
    psc = fixture("psc_list")
    psc["items"][0]["ceased_on"] = "2026-01-01"
    kyb.reset_state_for_tests()
    ctx.install(pscs=psc)
    rec = ctx.body()["pscs"]["items"][0]["identity_verification"]
    assert rec["eligibility"] == "not_interpreted" and rec["status"] is None


PSC_KINDS = swagger("pscModels")["definitions"]["listTwoSummary"]["properties"]["kind"][
    "enum"
]


@pytest.mark.parametrize("kind", [k for k in PSC_KINDS if k != kyb.INDIVIDUAL_PSC_KIND])
def test_each_other_psc_kind_without_fields_is_not_interpreted(ctx, kind):
    psc = fixture("psc_list")
    psc["items"] = [{"name": "OTHER KIND", "kind": kind, "notified_on": "2020-01-01"}]
    ctx.install(pscs=psc)
    rec = ctx.body()["pscs"]["items"][0]["identity_verification"]
    assert rec["eligibility"] == "not_interpreted"
    assert rec["status"] is None
    assert rec["evidence"] == {} and rec["evidence_label"] is None


@pytest.mark.parametrize("kind", [k for k in PSC_KINDS if k != kyb.INDIVIDUAL_PSC_KIND])
def test_non_individual_psc_carrying_fields_shows_raw_fields_only(ctx, kind):
    psc = fixture("psc_list")
    psc["items"] = [
        {
            "name": "OTHER KIND",
            "kind": kind,
            "notified_on": "2020-01-01",
            "identity_verification_details": {
                "appointment_verification_start_on": "2026-02-10"
            },
        }
    ]
    ctx.install(pscs=psc)
    rec = ctx.body()["pscs"]["items"][0]["identity_verification"]
    assert rec["status"] is None and rec["eligibility"] == "not_interpreted"
    assert rec["evidence"] == {"appointment_verification_start_on": "2026-02-10"}
    assert rec["evidence_label"] == kyb.NOT_INTERPRETED_LABEL


# ------------------------------------------------------------------ 5. scope


def test_excluded_type_list_matches_spec_counts():
    assert len(EXCLUDED_TYPES) == 28 and len(IN_SCOPE) == 8
    assert not set(EXCLUDED_TYPES) & set(IN_SCOPE)
    assert set(IN_SCOPE) == set(kyb.IN_SCOPE_TYPES)


@pytest.mark.parametrize("type_", EXCLUDED_TYPES + ["a-type-nobody-has-seen", None])
def test_unsupported_type_returns_raw_facts_only(ctx, type_):
    profile = ctx.profile(type=type_)
    up = ctx.install(profile=profile)
    body = ctx.body()
    assert body["interpretation"] == {
        "scope": "unsupported_entity_type",
        "current_obligations": "not_applicable",
        "notice": "FinCrimeRadar does not interpret indicators for this entity type in phase 1",
    }
    assert body["company"]["type"] == type_
    assert body["company"]["name"] == "EXAMPLE TRADING LIMITED"
    assert body["indicators"] == []
    assert body["officers"]["items"] == [] and body["pscs"]["items"] == []
    assert body["registered_office_change_filings"] is None
    assert up.paths == [PATHS["profile"]]


@pytest.mark.parametrize("type_", IN_SCOPE)
def test_each_in_scope_type_is_interpreted(ctx, type_):
    up = ctx.install(profile=ctx.profile(type=type_))
    body = ctx.body()
    assert body["interpretation"]["scope"] == "interpreted"
    assert len(up.calls) > 1


# ------------------------------------------------------------------ 6. status


def test_dissolved_company_has_no_current_obligation_outputs(ctx):
    profile = ctx.profile(company_status="dissolved", date_of_cessation="2025-12-01")
    profile["accounts"]["next_accounts"]["overdue"] = True
    profile["confirmation_statement"]["overdue"] = True
    officers = officers_with(
        director(idv={"appointment_verification_statement_due_on": "2026-01-01"}),
        director(
            "SECOND, Two",
            idv={
                "appointment_verification_start_on": "2026-01-01",
                "appointment_verification_end_on": "2026-02-01",
            },
        ),
    )
    ctx.install(profile=profile, officers=officers)
    body = ctx.body()
    ids = indicator_ids(body)
    for banned in (
        "accounts_overdue",
        "confirmation_statement_overdue",
        "idv_statement_overdue",
        "idv_statement_removed",
        "company_status_not_active",
    ):
        assert banned not in ids
    assert body["company"]["status"] == "dissolved"
    assert body["interpretation"]["current_obligations"] == "register_reported_only"
    reported = body["register_reported_values"]
    label = "register-reported values; FinCrimeRadar does not interpret current obligations for status dissolved"
    assert reported["label"] == label and body["interpretation"]["notice"] == label
    assert (
        reported["company_status"] == "dissolved"
        and reported["date_of_cessation"] == "2025-12-01"
    )
    assert (
        reported["accounts_next_accounts_overdue"] is True
        and reported["confirmation_statement_overdue"] is True
    )
    for o in body["officers"]["items"]:
        assert o["identity_verification"]["status"] is None
        assert o["identity_verification"]["eligibility"] == "not_interpreted"
    for p in body["pscs"]["items"]:
        assert p["identity_verification"]["status"] is None


def test_active_company_has_no_register_reported_values(ctx):
    ctx.install()
    body = ctx.body()
    assert body["register_reported_values"] is None
    assert body["interpretation"] == {
        "scope": "interpreted",
        "current_obligations": "interpreted",
        "notice": None,
    }


@pytest.mark.parametrize(
    "status",
    ["liquidation", "administration", "registered", "open", "closed", "removed", None],
)
def test_company_status_not_active_indicator_never_exists(ctx, status):
    ctx.install(profile=ctx.profile(company_status=status))
    body = ctx.body()
    assert "company_status_not_active" not in indicator_ids(body)
    assert body["company"]["status"] == status
    assert body["interpretation"]["current_obligations"] == "register_reported_only"


# ------------------------------------------------------------------ 7. PSC statements


def test_active_and_ceased_statements_are_split_with_dates(ctx):
    ctx.install()
    body = ctx.body()
    assert len(body["pscs"]["active_statements"]) == 1
    active = body["pscs"]["active_statements"][0]
    assert active["notified_on"] == "2024-02-02" and active["ceased_on"] is None
    assert [s["ceased_on"] for s in body["pscs"]["ceased_statements"]] == [
        "2022-02-02",
        "2020-06-01",
    ]
    assert {"active_psc_statements", "ceased_psc_statements"} <= set(
        indicator_ids(body)
    )
    ceased_ind = next(
        i for i in body["indicators"] if i["id"] == "ceased_psc_statements"
    )
    assert {"notified_on", "ceased_on"} <= set(ceased_ind["evidence"]["statements"][0])


def test_ceased_only_statements_produce_no_active_statements(ctx):
    statements = fixture("psc_statements")
    statements["items"] = statements["items"][1:]
    ctx.install(statements=statements)
    body = ctx.body()
    assert body["pscs"]["active_statements"] == []
    ids = indicator_ids(body)
    assert "active_psc_statements" not in ids and "ceased_psc_statements" in ids


def test_no_statements_fires_neither_indicator(ctx):
    ctx.install(statements=(404, {}))
    ids = indicator_ids(ctx.body())
    assert "active_psc_statements" not in ids and "ceased_psc_statements" not in ids


def test_statement_descriptions_are_verbatim_from_pinned_enumerations(ctx):
    ctx.install()
    body = ctx.body()
    active = body["pscs"]["active_statements"][0]
    assert (
        active["description"]
        == "SMITH, Alex has failed to comply with a notice given by the company under section 790E or EA of the Act"
    )
    ceased = body["pscs"]["ceased_statements"][0]
    table = kyb.STATEMENT_DESCRIPTIONS
    assert ceased["description"] == table["psc-exists-but-not-identified"]


def test_unknown_statement_code_shows_raw_code_and_no_description(ctx):
    ctx.install()
    unknown = ctx.body()["pscs"]["ceased_statements"][1]
    assert (
        unknown["statement"] == "not-a-known-statement-code"
        and unknown["description"] is None
    )


def test_statement_table_loads_from_the_pinned_file():
    text = (
        ROOT / "docs/specs/companies-house-enumerations/psc_descriptions.yml"
    ).read_text(encoding="utf-8")
    assert len(kyb.STATEMENT_DESCRIPTIONS) == 28
    assert kyb.STATEMENT_DESCRIPTIONS["psc-contacted-but-no-response"] in text
    assert kyb.STATEMENT_DESCRIPTIONS["psc-contacted-but-no-response"] == (
        "The company has given a notice under section 790D or DA of the Act which has not been complied with"
    )


def test_missing_enumerations_file_degrades_to_raw_codes(tmp_path):
    assert kyb._load_statement_descriptions(tmp_path / "missing.yml") == {}


# ------------------------------------------------------------------ 8. dated lists


def test_officer_events_are_dated_sorted_and_unflagged(ctx):
    ctx.install()
    body = ctx.body()
    events = body["officers"]["events"]
    dates = [e["date"] for e in events]
    assert dates == sorted(dates, reverse=True)
    assert {
        "date": "2024-05-31",
        "event": "resigned",
        "name": "JONES, Sam",
        "role": "director",
    } in events
    assert body["company"]["date_of_creation"] == "2019-03-04"
    banned = {
        k for k in walk_keys(body) if "recent" in k.lower() or "churn" in k.lower()
    }
    assert banned == set()
    assert not any(
        i["id"].startswith("recent") or i["id"] == "recently_incorporated"
        for i in body["indicators"]
    )


def test_registered_office_filings_fire_for_each_identifier(ctx):
    for n, ident in enumerate(SIX_IDENTIFIERS):
        kyb.reset_state_for_tests()
        filings = fixture("filing_history")
        filings["items"] = [
            {
                "date": f"2025-01-0{n + 1}",
                "category": "address",
                "description": ident,
                "transaction_id": f"X{n}",
            }
        ]
        ctx.install(filing=filings)
        assert ctx.body()["registered_office_change_filings"]["items"] == [
            {
                "date": f"2025-01-0{n + 1}",
                "transaction_id": f"X{n}",
                "description_identifier": ident,
            }
        ]


def test_registered_office_filings_exclude_every_other_identifier(ctx):
    ctx.install()
    got = ctx.body()["registered_office_change_filings"]["items"]
    assert [f["transaction_id"] for f in got] == ["T3", "T1", "T2"]
    assert all(f["description_identifier"] in SIX_IDENTIFIERS for f in got)


def test_category_address_alone_never_matches(ctx):
    filings = fixture("filing_history")
    filings["items"] = [
        {
            "date": "2026-01-01",
            "category": "address",
            "description": "some-other-address-filing",
            "transaction_id": "Z",
        }
    ]
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"]["items"] == []


def test_identifier_with_wrong_category_never_matches(ctx):
    filings = fixture("filing_history")
    filings["items"] = [
        {
            "date": "2026-01-01",
            "category": "accounts",
            "description": SIX_IDENTIFIERS[0],
            "transaction_id": "Z",
        }
    ]
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"]["items"] == []


def test_rendered_text_description_disables_the_list(ctx):
    filings = fixture("filing_history")
    filings["items"].append(
        {
            "date": "2026-02-02",
            "category": "address",
            "description": "Registered office address changed from A to B on 2 February 2026",
            "transaction_id": "R",
        }
    )
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"] is None


def test_provisional_switch_returns_null(ctx, monkeypatch):
    monkeypatch.setattr(kyb, "REGISTERED_OFFICE_FILINGS_ENABLED", False)
    ctx.install()
    assert ctx.body()["registered_office_change_filings"] is None


# ------------------------------------------------------------------ 9. global text


def test_global_does_not_establish_is_verbatim(ctx):
    ctx.install()
    assert ctx.body()["does_not_establish"] == GLOBAL_DNE


def test_unsupported_type_also_carries_global_text_and_attribution(ctx):
    ctx.install(profile=ctx.profile(type="oversea-company"))
    body = ctx.body()
    assert body["does_not_establish"] == GLOBAL_DNE
    assert body["attribution"] == (
        "Source: Companies House public register, retrieved 2026-10-11T12:00:00Z. "
        "FinCrimeRadar is not affiliated with or endorsed by Companies House."
    )
    assert body["source"] == "Companies House Public Data API"


# ------------------------------------------------------------------ 10. minimisation

FORBIDDEN_KEYS = {
    "date_of_birth",
    "dob",
    "nationality",
    "occupation",
    "address",
    "address_line_1",
    "postal_code",
    "country_of_residence",
    "preferred_name",
    "principal_office_address",
    "registered_office_address",
    "correspondence_address",
    "links",
}


def test_response_never_contains_personal_fields(ctx):
    ctx.install()
    body = ctx.body()
    assert not FORBIDDEN_KEYS & set(walk_keys(body))
    dumped = json.dumps(body)
    assert "PII_" not in dumped
    assert "evil.example" not in dumped


def test_not_interpreted_raw_fields_exclude_preferred_name(ctx):
    officers = officers_with(
        director(
            officer_role="corporate-director",
            idv={
                "appointment_verification_start_on": "2026-02-10",
                "preferred_name": "PII_PREFERRED_NAME_MARKER",
            },
        )
    )
    ctx.install(officers=officers)
    assert "PII_PREFERRED_NAME_MARKER" not in json.dumps(ctx.body())


# ------------------------------------------------------------------ 11. upstream failure


def test_profile_404_is_404(ctx):
    ctx.install(profile=(404, {}))
    r = ctx.get()
    assert r.status_code == 404 and r.json() == {"detail": "Company not found."}


def test_bad_key_401_returns_503_without_leaking_detail(ctx):
    ctx.install(profile=(401, {"error": "invalid api key supplied: " + TEST_KEY}))
    r = ctx.get()
    assert r.status_code == 503
    assert r.json() == {"detail": "Service temporarily unavailable."}
    assert TEST_KEY not in r.text and "key" not in r.text.lower()


def test_upstream_429_stops_all_calls_and_returns_503_retry_after(ctx):
    up = ctx.install(profile=(429, {}))
    r = ctx.get()
    assert r.status_code == 503 and int(r.headers["Retry-After"]) > 0
    first = len(up.calls)
    r2 = ctx.get("87654321")
    assert r2.status_code == 503 and int(r2.headers["Retry-After"]) > 0
    assert len(up.calls) == first  # blocked: zero further upstream calls
    assert kyb.quota_remaining() <= kyb.UPSTREAM_BUDGET_MAX


def test_no_retry_on_429(ctx):
    up = ctx.install(profile=(429, {}))
    ctx.get()
    assert up.paths.count(PATHS["profile"]) == 1


def test_429_block_expires(ctx, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    ctx.install(profile=(429, {}))
    assert ctx.get().status_code == 503
    clock["t"] += kyb.UPSTREAM_429_BLOCK_SECONDS + 1
    ctx.install()
    assert ctx.get().status_code == 200


@pytest.mark.parametrize("status", [500, 502, 503, 400, 418])
def test_upstream_5xx_and_unexpected_are_502(ctx, status):
    ctx.install(profile=(status, {"error": "boom"}))
    r = ctx.get()
    assert r.status_code == 502 and r.json() == {"detail": "Upstream error."}


def test_upstream_timeout_is_504(ctx):
    ctx.install(profile=requests.Timeout("read timed out"))
    r = ctx.get()
    assert r.status_code == 504 and r.json() == {"detail": "Upstream timeout."}


def test_connection_error_retries_once_then_502(ctx):
    up = ctx.install(profile=requests.ConnectionError("down"))
    r = ctx.get()
    assert r.status_code == 502
    assert up.paths.count(PATHS["profile"]) == 2


def test_malformed_upstream_body_is_502(ctx):
    ctx.install(officers={"items": "not-a-list"})
    assert ctx.get().status_code == 502


def test_timeouts_and_auth_are_set_on_every_call(ctx):
    up = ctx.install()
    ctx.get()
    for call in up.calls:
        assert call["timeout"] == (
            kyb.CONNECT_TIMEOUT_SECONDS,
            kyb.READ_TIMEOUT_SECONDS,
        )
        assert call["auth"] == (TEST_KEY, "")


def test_redirects_are_not_followed(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(200, {})

    monkeypatch.setattr(kyb.requests, "get", fake_get)
    kyb._http_get(
        "https://api.company-information.service.gov.uk/x", None, ("k", ""), (1, 1)
    )
    assert seen["allow_redirects"] is False


def test_sub_resource_404_means_empty_not_error(ctx):
    ctx.install(
        officers=(404, {}), pscs=(404, {}), statements=(404, {}), filing=(404, {})
    )
    body = ctx.get().json()
    assert body["officers"]["items"] == [] and body["pscs"]["items"] == []


def test_ratelimit_headers_are_logged_not_depended_on(ctx, capsys):
    def fake(url, params, auth, timeout):
        path = url[len(kyb.UPSTREAM_BASE) :]
        body = default_routes().get(path, {})
        return FakeResponse(200, body, {"X-Ratelimit-Remain": "42", "Other": "x"})

    ctx.monkeypatch.setattr(kyb, "_http_get", fake)
    ctx.body()
    out = capsys.readouterr().out
    assert "x-ratelimit-remain" in out and "42" in out


# ------------------------------------------------------------------ 12. missing key


def test_missing_key_fails_closed_with_503(ctx, monkeypatch):
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", "")
    up = ctx.install()
    r = ctx.get()
    assert r.status_code == 503
    assert up.calls == []
    assert kyb.health_fields()["companies_house_configured"] is False


def test_health_fields_with_key(ctx):
    fields = kyb.health_fields()
    assert fields["companies_house_configured"] is True
    assert fields["companies_house_quota_remaining"] == kyb.UPSTREAM_BUDGET_MAX
    assert set(fields) == {
        "companies_house_configured",
        "companies_house_quota_remaining",
    }


def test_main_app_registers_route_and_health_fields():
    import main

    paths = {r.path for r in main.app.routes}
    assert "/api/kyb/company/{company_number:path}" in paths
    health = TestClient(main.app).get("/api/health").json()
    assert (
        "companies_house_configured" in health
        and "companies_house_quota_remaining" in health
    )
    assert health["status"] == "ok"


# ------------------------------------------------------------------ 13. budget, per IP limit, cache


def test_global_budget_is_enforced_before_each_call(ctx, monkeypatch):
    monkeypatch.setattr(kyb, "UPSTREAM_BUDGET_MAX", 3)
    up = ctx.install()
    r = ctx.get()
    assert r.status_code == 503 and int(r.headers["Retry-After"]) > 0
    assert len(up.calls) == 3
    assert kyb.quota_remaining() == 0


def test_budget_window_rolls(ctx, monkeypatch):
    clock = {"t": 5000.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(kyb, "UPSTREAM_BUDGET_MAX", 3)
    ctx.install()
    assert ctx.get().status_code == 503
    clock["t"] += kyb.UPSTREAM_BUDGET_WINDOW_SECONDS + 1
    monkeypatch.setattr(kyb, "UPSTREAM_BUDGET_MAX", 300)
    assert ctx.get().status_code == 200


def test_budget_default_is_300():
    assert kyb.UPSTREAM_BUDGET_MAX == 300 and kyb.UPSTREAM_BUDGET_WINDOW_SECONDS == 300


def test_cache_hit_makes_zero_upstream_calls(ctx):
    up = ctx.install()
    first = ctx.body()
    calls = len(up.calls)
    assert calls > 0
    second = ctx.body()
    assert len(up.calls) == calls
    assert second == first


def test_cache_expires_after_ttl(ctx, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    up = ctx.install()
    ctx.body()
    n = len(up.calls)
    clock["t"] += kyb.CACHE_TTL_SECONDS + 1
    ctx.body()
    assert len(up.calls) == 2 * n


def test_errors_are_not_cached(ctx):
    ctx.install(profile=(404, {}))
    assert ctx.get().status_code == 404
    ctx.install()
    assert ctx.get().status_code == 200


def test_per_ip_limit_returns_429_on_the_11th(ctx):
    ctx.install()
    for _ in range(10):
        assert ctx.get().status_code == 200
    r = ctx.get()
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_spoofed_x_forwarded_for_cannot_rotate_the_key(ctx):
    ctx.install()
    real = "198.51.100.50"
    for i in range(1, 11):
        r = ctx.get(headers={"X-Forwarded-For": f"203.0.113.{i}, {real}"})
        assert r.status_code == 200
    r = ctx.get(headers={"X-Forwarded-For": f"203.0.113.11, {real}"})
    assert r.status_code == 429


def test_different_real_clients_have_separate_limits(ctx):
    ctx.install()
    for i in range(10):
        assert ctx.get(headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert ctx.get(headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
    assert ctx.get(headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200


def test_ip_limit_window_rolls(ctx, monkeypatch):
    clock = {"t": 10.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    ctx.install()
    for _ in range(10):
        ctx.get()
    assert ctx.get().status_code == 429
    clock["t"] += kyb.IP_LIMIT_WINDOW_SECONDS + 1
    assert ctx.get().status_code == 200


def test_ip_limit_applies_to_invalid_input_too(ctx):
    ctx.install()
    for _ in range(10):
        assert ctx.get("bad").status_code == 422
    assert ctx.get("bad").status_code == 429


# ------------------------------------------------------------------ 14. truncation


def test_truncation_flags_come_from_totals_versus_items(ctx):
    officers = fixture("officers")
    officers["total_results"] = 250
    pscs = fixture("psc_list")
    pscs["total_results"] = "250"
    ctx.install(officers=officers, pscs=pscs)
    body = ctx.body()
    assert body["officers"]["truncated"] is True
    assert body["pscs"]["truncated"] is True


def test_not_truncated_when_totals_match(ctx):
    ctx.install()
    body = ctx.body()
    assert body["officers"]["truncated"] is False
    assert body["pscs"]["truncated"] is False


def test_statements_truncation_marks_pscs_truncated(ctx):
    statements = fixture("psc_statements")
    statements["total_results"] = 500
    ctx.install(statements=statements)
    assert ctx.body()["pscs"]["truncated"] is True


def test_list_calls_request_100_per_page(ctx):
    up = ctx.install()
    ctx.get()
    for call in up.calls:
        if call["path"] != PATHS["profile"] and call["path"] != PATHS["insolvency"]:
            assert call["params"] == {"items_per_page": "100"}, call


# ------------------------------------------------------------------ 15. links never fetched


def test_only_expected_paths_are_called_and_links_are_ignored(ctx):
    up = ctx.install()
    ctx.get()
    assert set(up.paths) <= set(PATHS.values())
    assert not any("evil" in p for p in up.paths)
    assert not any(c["path"].startswith("http") for c in up.calls)


def test_company_number_is_the_only_variable_in_paths(ctx):
    up = ctx.install()
    ctx.get()
    templates = {p.replace(NUM, "{n}") for p in up.paths}
    assert templates == {
        "/company/{n}",
        "/company/{n}/officers",
        "/company/{n}/persons-with-significant-control",
        "/company/{n}/persons-with-significant-control-statements",
        "/company/{n}/filing-history",
        "/company/{n}/charges",
        "/company/{n}/insolvency",
    }


# ------------------------------------------------------------------ 17. sorting and coercion


def test_filing_history_is_sorted_client_side_whatever_the_upstream_order(ctx):
    filings = fixture("filing_history")
    forward = sorted(filings["items"], key=lambda f: f["date"])
    backward = list(reversed(forward))
    for order in (forward, backward):
        kyb.reset_state_for_tests()
        f = copy.deepcopy(filings)
        f["items"] = order
        ctx.install(filing=f)
        assert [
            x["transaction_id"]
            for x in ctx.body()["registered_office_change_filings"]["items"]
        ] == ["T3", "T1", "T2"]


def test_psc_string_paging_fields_are_coerced():
    parsed = kyb.UpPscList.model_validate(
        {"items_per_page": "100", "start_index": "0", "total_results": "7", "items": []}
    )
    assert (parsed.items_per_page, parsed.start_index, parsed.total_results) == (
        100,
        0,
        7,
    )


def test_response_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        kyb.CompanyOut(
            name="x",
            status=None,
            type=None,
            date_of_creation=None,
            date_of_cessation=None,
            surprise=1,
        )


def test_upstream_models_ignore_unknown_fields():
    parsed = kyb.UpOfficer.model_validate(
        {"name": "A", "date_of_birth": {"year": 1970}, "anything": 1}
    )
    assert parsed.name == "A" and not hasattr(parsed, "date_of_birth")


def test_no_score_verdict_or_rank_anywhere(ctx):
    ctx.install()
    keys = {k.lower() for k in walk_keys(ctx.body())}
    for banned in (
        "score",
        "weight",
        "rank",
        "verdict",
        "risk",
        "rating",
        "pass",
        "fail",
    ):
        assert not any(banned == k or k.startswith(banned + "_") for k in keys), banned


# ------------------------------------------------------------------ spec v1.1: filing truncation


def test_registered_office_filings_truncated_when_total_exceeds_returned(ctx):
    filings = fixture("filing_history")
    filings["total_count"] = len(filings["items"]) + 40
    ctx.install(filing=filings)
    got = ctx.body()["registered_office_change_filings"]
    assert got["truncated"] is True
    assert [f["transaction_id"] for f in got["items"]] == ["T3", "T1", "T2"]


def test_registered_office_filings_not_truncated_when_totals_match(ctx):
    filings = fixture("filing_history")
    filings["total_count"] = len(filings["items"])
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"]["truncated"] is False


def test_registered_office_filings_truncated_false_when_total_missing(ctx):
    filings = fixture("filing_history")
    del filings["total_count"]
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"]["truncated"] is False


def test_disabled_list_has_no_truncated_flag(ctx, monkeypatch):
    monkeypatch.setattr(kyb, "REGISTERED_OFFICE_FILINGS_ENABLED", False)
    filings = fixture("filing_history")
    filings["total_count"] = 999
    ctx.install(filing=filings)
    assert ctx.body()["registered_office_change_filings"] is None


