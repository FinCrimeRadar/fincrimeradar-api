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
NUM = "ZZ000001"
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
    ctx.get(NUM.lower())
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
    assert ind["evidence_state"] == "register_fact" and ind["source_endpoints"] == [
        "profile"
    ]


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
    assert body["officers"]["source_status"] == "not_returned"
    assert body["officers"]["truncated"] is None and body["pscs"]["truncated"] is None
    # Spec v1.2 and v1.4: every collection is reported not_returned, never a bare null.
    assert body["registered_office_change_filings"] == {
        "source_status": "not_returned",
        "items": [],
        "truncated": None,
    }
    assert body["charges"] == {"source_status": "not_returned"}
    assert body["insolvency"] == {"source_status": "not_returned"}
    assert body["pscs"]["records_source_status"] == "not_returned"
    assert body["pscs"]["statements_source_status"] == "not_returned"
    assert body["not_returned_notice"] == NOT_RETURNED_NOTICE
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


def test_sub_resource_404_is_not_returned_never_confirmed_empty(ctx):
    ctx.install(
        officers=(404, {}), pscs=(404, {}), statements=(404, {}), filing=(404, {})
    )
    body = ctx.get().json()
    assert body["officers"]["items"] == [] and body["pscs"]["items"] == []
    assert body["officers"]["source_status"] == "not_returned"
    assert body["officers"]["truncated"] is None
    assert body["pscs"]["truncated"] is None


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


# ------------------------------------------------------------------ synthetic bundle replays
# Register shaped bundles that are fully synthetic (spec v1.2, section 8 item
# 26): reserved ZZ company numbers, synthetic names, etags, transaction IDs and
# charge particulars, dates shifted with their relative gaps kept. They keep
# the structure observed live (links keys, enumeration identifiers, 404 on
# empty PSC statements). The live API is never called from tests.

SYNTHETIC = [
    "active_ltd_outstanding_charges",
    "dissolved",
    "active_registered_office_change",
    "open_ended_verification",
]
SYNTHETIC_KEYS = {
    "profile": "profile",
    "officers": "officers",
    "pscs": "pscs",
    "statements": "statements",
    "filing": "filing",
    "charges": "charges",
    "insolvency": "insolvency",
}


def synthetic_bundle(label):
    return json.loads(
        (FIXTURES / f"synthetic_{label}.json").read_text(encoding="utf-8")
    )


def synthetic_routes(bundle):
    n = bundle["company_number"]
    suffix = {
        "profile": "",
        "officers": "/officers",
        "pscs": "/persons-with-significant-control",
        "statements": "/persons-with-significant-control-statements",
        "filing": "/filing-history",
        "charges": "/charges",
        "insolvency": "/insolvency",
    }
    routes = {}
    for key, r in bundle["responses"].items():
        routes[f"/company/{n}{suffix[key]}"] = (r["status"], r["body"] or {})
    return n, routes


@pytest.mark.parametrize("label", SYNTHETIC)
def test_synthetic_replay_is_valid_minimised_and_pseudonymised(ctx, label):
    n, routes = synthetic_routes(synthetic_bundle(label))
    ctx.install(routes=routes)
    body = ctx.body(n)
    assert not FORBIDDEN_KEYS & set(walk_keys(body))
    names = [o["name"] for o in body["officers"]["items"]] + [
        p["name"] for p in body["pscs"]["items"]
    ]
    assert names and all(re.fullmatch(r"(PERSON|ENTITY) [A-Z]", x) for x in names)
    assert body["does_not_establish"] == GLOBAL_DNE


@pytest.mark.parametrize("label", SYNTHETIC)
def test_synthetic_links_keys_drive_the_optional_calls(ctx, label):
    bundle = synthetic_bundle(label)
    n, routes = synthetic_routes(bundle)
    up = ctx.install(routes=routes)
    ctx.body(n)
    links = bundle["responses"]["profile"]["body"]["links"]
    assert (f"/company/{n}/charges" in up.paths) == ("charges" in links)
    assert (f"/company/{n}/insolvency" in up.paths) == ("insolvency" in links)


def test_synthetic_deprecated_has_charges_flag_is_unreliable_and_unused(ctx):
    bundle = synthetic_bundle("active_ltd_outstanding_charges")
    profile = bundle["responses"]["profile"]["body"]
    # Observed live: the deprecated flag is false while the link and the
    # outstanding charges exist. The service must follow the link key.
    assert profile["has_charges"] is False
    assert "charges" in profile["links"]
    n, routes = synthetic_routes(bundle)
    ctx.install(routes=routes)
    body = ctx.body(n)
    assert "outstanding_charges" in indicator_ids(body)


def test_synthetic_dissolved_company_gets_register_reported_values_only(ctx):
    n, routes = synthetic_routes(synthetic_bundle("dissolved"))
    ctx.install(routes=routes)
    body = ctx.body(n)
    assert body["company"]["status"] == "dissolved"
    assert body["interpretation"]["current_obligations"] == "register_reported_only"
    assert not {
        "accounts_overdue",
        "confirmation_statement_overdue",
        "idv_statement_overdue",
    } & set(indicator_ids(body))
    assert all(
        o["identity_verification"]["status"] is None for o in body["officers"]["items"]
    )


def test_synthetic_statements_404_is_not_returned_not_confirmed_empty(ctx):
    bundle = synthetic_bundle("active_registered_office_change")
    assert bundle["responses"]["statements"]["status"] == 404
    n, routes = synthetic_routes(bundle)
    ctx.install(routes=routes)
    body = ctx.body(n)
    assert (
        body["pscs"]["active_statements"] == []
        and body["pscs"]["ceased_statements"] == []
    )
    # Live, this 404 meant the company has no statements. The service cannot
    # tell that from a missing section, so it reports not_returned (spec v1.2).
    assert body["pscs"]["statements_source_status"] == "not_returned"
    assert body["pscs"]["truncated"] is None
    assert "active_psc_statements" not in indicator_ids(body)


def test_synthetic_description_carries_the_enumeration_identifier(ctx):
    bundle = synthetic_bundle("active_registered_office_change")
    items = bundle["responses"]["filing"]["body"]["items"]
    address = [i for i in items if i["category"] == "address"]
    assert address
    for item in address:
        assert " " not in item["description"]
        assert item["description"] in SIX_IDENTIFIERS
    n, routes = synthetic_routes(bundle)
    ctx.install(routes=routes)
    got = ctx.body(n)["registered_office_change_filings"]
    assert got is not None and len(got["items"]) == len(address)
    assert got["truncated"] is False


def test_connect_timeout_is_a_504_and_is_not_retried(ctx):
    up = ctx.install(profile=requests.ConnectTimeout("connect timed out"))
    r = ctx.get()
    assert r.status_code == 504 and r.json() == {"detail": "Upstream timeout."}
    assert up.paths.count(PATHS["profile"]) == 1


# ------------------------------------------------------------------ spec v1.2: source status

NOT_RETURNED_NOTICE = (
    "This register section was not returned. FinCrimeRadar draws no conclusion "
    "from its absence."
)


def overdue_officers():
    return officers_with(
        director(idv={"appointment_verification_statement_due_on": "2026-09-01"})
    )


def overdue_pscs():
    psc = fixture("psc_list")
    psc["items"][0]["identity_verification_details"] = {
        "appointment_verification_statement_due_on": "2026-09-01"
    }
    return psc


# key, baseline override, indicator that must exist when available and vanish when
# the collection is not returned
SOURCE_CASES = [
    ("officers", {"officers": overdue_officers()}, "idv_statement_overdue"),
    ("pscs", {"pscs": overdue_pscs()}, "idv_statement_overdue"),
    ("statements", {}, "active_psc_statements"),
    ("statements", {}, "ceased_psc_statements"),
    ("filing", {}, None),
    ("charges", {}, "outstanding_charges"),
    ("insolvency", {}, "insolvency_history"),
]


def section_of(body, key):
    return {
        "officers": body["officers"]["source_status"],
        "pscs": body["pscs"]["records_source_status"],
        "statements": body["pscs"]["statements_source_status"],
        "filing": body["registered_office_change_filings"]["source_status"],
        "charges": (body["charges"] or {}).get("source_status"),
        "insolvency": (body["insolvency"] or {}).get("source_status"),
    }[key]


@pytest.mark.parametrize("key,base,indicator", SOURCE_CASES)
def test_not_returned_collection_has_null_truncation_and_no_conclusion(
    ctx, key, base, indicator
):
    ctx.install(**base)
    baseline = ctx.body()
    assert section_of(baseline, key) == "available"
    if indicator:
        assert indicator in indicator_ids(baseline)
    else:
        assert baseline["registered_office_change_filings"]["items"]

    kyb.reset_state_for_tests()
    ctx.install(**{**base, key: (404, {})})
    body = ctx.body()
    assert section_of(body, key) == "not_returned"
    assert body["not_returned_notice"] == NOT_RETURNED_NOTICE
    if indicator:
        assert indicator not in indicator_ids(body)
    truncated = {
        "officers": body["officers"]["truncated"],
        "pscs": body["pscs"]["truncated"],
        "statements": body["pscs"]["truncated"],
        "filing": body["registered_office_change_filings"]["truncated"],
    }
    if key in truncated:
        assert truncated[key] is None
    emptied = {
        "officers": body["officers"]["items"] + body["officers"]["events"],
        "pscs": body["pscs"]["items"],
        "statements": body["pscs"]["active_statements"]
        + body["pscs"]["ceased_statements"],
        "filing": body["registered_office_change_filings"]["items"],
    }
    if key in emptied:
        assert emptied[key] == []


def test_pscs_truncated_is_null_if_either_list_is_missing(ctx):
    ctx.install(statements=(404, {}))
    body = ctx.body()
    assert body["pscs"]["records_source_status"] == "available"
    assert body["pscs"]["statements_source_status"] == "not_returned"
    assert body["pscs"]["truncated"] is None
    kyb.reset_state_for_tests()
    ctx.install(pscs=(404, {}))
    assert ctx.body()["pscs"]["truncated"] is None


def test_available_sections_keep_boolean_truncation(ctx):
    ctx.install()
    body = ctx.body()
    assert body["officers"]["truncated"] is False
    assert body["pscs"]["truncated"] is False
    assert body["registered_office_change_filings"]["truncated"] is False
    assert body["not_returned_notice"] == NOT_RETURNED_NOTICE


def test_optional_resources_are_null_without_a_link_and_statused_with_one(ctx):
    profile = ctx.profile()
    del profile["links"]["charges"], profile["links"]["insolvency"]
    ctx.install(profile=profile)
    body = ctx.body()
    assert body["charges"] is None and body["insolvency"] is None
    kyb.reset_state_for_tests()
    ctx.install()
    body = ctx.body()
    assert body["charges"] == {"source_status": "available"}
    assert body["insolvency"] == {"source_status": "available"}


def test_only_the_profile_404_is_a_404_response(ctx):
    ctx.install(profile=(404, {}))
    assert ctx.get().status_code == 404


# ------------------------------------------------------------------ spec v1.2: provenance


def removed_officers():
    return officers_with(
        director(
            idv={
                "appointment_verification_start_on": "2026-01-01",
                "appointment_verification_end_on": "2026-02-01",
            }
        )
    )


def removed_pscs():
    psc = fixture("psc_list")
    psc["items"][0]["identity_verification_details"] = {
        "appointment_verification_start_on": "2026-01-01",
        "appointment_verification_end_on": "2026-02-01",
    }
    return psc


def endpoints_for(body, indicator_id):
    return next(i for i in body["indicators"] if i["id"] == indicator_id)[
        "source_endpoints"
    ]


@pytest.mark.parametrize(
    "indicator,officers,pscs",
    [
        ("idv_statement_overdue", overdue_officers, overdue_pscs),
        ("idv_statement_removed", removed_officers, removed_pscs),
    ],
)
def test_idv_indicator_provenance_officer_only_psc_only_and_mixed(
    ctx, indicator, officers, pscs
):
    quiet_pscs = fixture("psc_list")
    quiet_officers = officers_with()

    ctx.install(officers=officers(), pscs=quiet_pscs)
    assert endpoints_for(ctx.body(), indicator) == ["officers"]

    kyb.reset_state_for_tests()
    ctx.install(officers=quiet_officers, pscs=pscs())
    assert endpoints_for(ctx.body(), indicator) == ["pscs"]

    kyb.reset_state_for_tests()
    ctx.install(officers=officers(), pscs=pscs())
    assert endpoints_for(ctx.body(), indicator) == ["officers", "pscs"]


# ------------------------------------------------------------------ spec v1.2: client allowance


def install_any(ctx):
    """Serve the default fixtures for any company number. Returns the call list."""
    prefix = f"/company/{NUM}"
    by_suffix = {p[len(prefix) :]: v for p, v in default_routes().items()}
    calls = []

    def fake(url, params, auth, timeout):
        path = url[len(kyb.UPSTREAM_BASE) :]
        calls.append(path)
        m = re.fullmatch(r"/company/([A-Z0-9]{8})(/.*)?", path)
        result = by_suffix.get(m.group(2) or "", (404, {}))
        status, body = result if isinstance(result, tuple) else (200, result)
        return FakeResponse(status, copy.deepcopy(body))

    ctx.monkeypatch.setattr(kyb, "_http_get", fake)
    return calls


CLIENT_A = {"X-Forwarded-For": "198.51.100.10"}
CLIENT_B = {"X-Forwarded-For": "198.51.100.11"}


def test_client_is_charged_by_upstream_calls_actually_made(ctx):
    install_any(ctx)
    assert ctx.get(headers=CLIENT_A).status_code == 200
    assert len(kyb._client_calls["198.51.100.10"]) == 7
    # A cache hit makes no upstream call, so it costs nothing.
    assert ctx.get(headers=CLIENT_A).status_code == 200
    assert len(kyb._client_calls["198.51.100.10"]) == 7


def test_unlinked_company_costs_fewer_calls(ctx):
    profile = ctx.profile()
    del profile["links"]["charges"], profile["links"]["insolvency"]
    ctx.install(profile=profile)
    ctx.get(headers=CLIENT_A)
    assert len(kyb._client_calls["198.51.100.10"]) == 5


def test_one_client_cannot_drain_more_than_60_upstream_calls(ctx, monkeypatch):
    assert kyb.CLIENT_ALLOWANCE_MAX == 60 and kyb.CLIENT_ALLOWANCE_WINDOW_SECONDS == 300
    monkeypatch.setattr(kyb, "IP_LIMIT_MAX", 1000)  # isolate the allowance
    calls = install_any(ctx)
    statuses = [
        ctx.get(f"ZZ{i:06d}", headers=CLIENT_A).status_code for i in range(1, 30)
    ]
    assert statuses[:8] == [200] * 8
    assert 429 in statuses and 200 not in statuses[statuses.index(429) :]
    assert len(calls) == 60
    assert kyb.quota_remaining() == kyb.UPSTREAM_BUDGET_MAX - 60
    r = ctx.get("ZZ000099", headers=CLIENT_A)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Other clients and the global pool are untouched.
    assert ctx.get("ZZ000098", headers=CLIENT_B).status_code == 200


def test_allowance_window_rolls(ctx, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(kyb, "IP_LIMIT_MAX", 1000)
    monkeypatch.setattr(kyb, "CLIENT_ALLOWANCE_MAX", 7)
    install_any(ctx)
    assert ctx.get("ZZ000001", headers=CLIENT_A).status_code == 200
    assert ctx.get("ZZ000002", headers=CLIENT_A).status_code == 429
    clock["t"] += kyb.CLIENT_ALLOWANCE_WINDOW_SECONDS + 1
    assert ctx.get("ZZ000002", headers=CLIENT_A).status_code == 200


def test_burst_cap_of_ten_lookups_per_minute_is_kept(ctx):
    assert kyb.IP_LIMIT_MAX == 10 and kyb.IP_LIMIT_WINDOW_SECONDS == 60
    install_any(ctx)
    for _ in range(10):
        assert ctx.get(headers=CLIENT_A).status_code == 200
    assert ctx.get(headers=CLIENT_A).status_code == 429


# ------------------------------------------------------------------ spec v1.2: client IP


def test_cf_connecting_ip_takes_precedence_over_x_forwarded_for(ctx):
    install_any(ctx)
    for i in range(10):
        headers = {
            "CF-Connecting-IP": "203.0.113.50",
            "X-Forwarded-For": f"192.0.2.{i}",
        }
        assert ctx.get(headers=headers).status_code == 200
    headers = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "192.0.2.99"}
    assert ctx.get(headers=headers).status_code == 429
    # A different CF-Connecting-IP is a different client.
    headers = {"CF-Connecting-IP": "203.0.113.51", "X-Forwarded-For": "192.0.2.99"}
    assert ctx.get(headers=headers).status_code == 200


def test_without_cf_connecting_ip_the_nth_from_right_rule_still_applies(ctx):
    install_any(ctx)
    for i in range(10):
        r = ctx.get(headers={"X-Forwarded-For": f"192.0.2.{i}, 198.51.100.60"})
        assert r.status_code == 200
    r = ctx.get(headers={"X-Forwarded-For": "192.0.2.77, 198.51.100.60"})
    assert r.status_code == 429


# ------------------------------------------------------------------ spec v1.2: bounded limiter


def test_limiters_stay_bounded_under_20000_distinct_keys(monkeypatch):
    kyb.reset_state_for_tests()
    monkeypatch.setattr(kyb, "UPSTREAM_BUDGET_MAX", 10**9)
    assert kyb.LIMITER_MAX_KEYS == 10_000
    for i in range(20_000):
        kyb._ip_limited(f"k{i}")
        kyb._take_budget(f"k{i}")
    assert len(kyb._ip_log) <= kyb.LIMITER_MAX_KEYS
    assert len(kyb._client_calls) <= kyb.LIMITER_MAX_KEYS
    # Oldest evicted, newest kept.
    assert "k0" not in kyb._ip_log and "k19999" in kyb._ip_log
    kyb.reset_state_for_tests()


def test_recently_used_key_survives_eviction(monkeypatch):
    kyb.reset_state_for_tests()
    monkeypatch.setattr(kyb, "LIMITER_MAX_KEYS", 3)
    for k in ("a", "b", "c"):
        kyb._ip_limited(k)
    kyb._ip_limited("a")
    kyb._ip_limited("d")
    assert list(kyb._ip_log) == ["c", "a", "d"]
    kyb.reset_state_for_tests()


# ------------------------------------------------------------------ spec v1.2: unparseable dates

IDV_DATE_FIELDS = [
    "appointment_verification_start_on",
    "appointment_verification_end_on",
    "appointment_verification_statement_due_on",
    "appointment_verification_statement_date",
]


@pytest.mark.parametrize("field", IDV_DATE_FIELDS)
@pytest.mark.parametrize(
    "bad", ["not-a-date", "2026-13-01", "2026-02-30", "11/10/2026"]
)
def test_unparseable_date_in_each_field_is_a_conflict_for_a_psc(field, bad):
    d = UpIdv(**{field: bad})
    assert (
        derive_idv_status(d, is_psc=True, today=TODAY) == "verification_data_conflict"
    )


@pytest.mark.parametrize("field", IDV_DATE_FIELDS[:3])
def test_unparseable_date_is_a_conflict_for_an_officer(field):
    d = UpIdv(**{field: "garbage"})
    assert (
        derive_idv_status(d, is_psc=False, today=TODAY) == "verification_data_conflict"
    )


@pytest.mark.parametrize("field", IDV_DATE_FIELDS)
def test_unparseable_date_reaches_the_response_as_a_conflict_not_an_error(ctx, field):
    psc = fixture("psc_list")
    psc["items"][0]["identity_verification_details"] = {field: "garbage"}
    ctx.install(pscs=psc)
    body = ctx.body()
    assert body["pscs"]["items"][0]["identity_verification"]["status"] == (
        "verification_data_conflict"
    )


# ------------------------------------------------------------------ spec v1.2: indicator strings

NINE_DOES_NOT_ESTABLISH = {
    "accounts_overdue": "A register flag that accounts are overdue does not establish the reason, whether the company is trading, or any wrongdoing.",
    "confirmation_statement_overdue": "A register flag that a confirmation statement is overdue does not establish the reason, whether the company is trading, or any wrongdoing.",
    "idv_statement_overdue": "A register record that a statement is overdue is not a finding that the person is unverified or that an offence has occurred.",
    "idv_statement_removed": "A removed statement record does not establish why it was removed or the person's current verification position.",
    "active_psc_statements": "A PSC statement records what the company told the register. It does not establish who owns or controls the company or that the statement is accurate.",
    "ceased_psc_statements": "A ceased statement is a historical register record. It does not establish the current position or that the statement was accurate.",
    "previous_names": "A previous name is a register record. It does not establish why the name changed.",
    "insolvency_history": "An insolvency record does not establish current solvency, cause or outcome.",
    "outstanding_charges": "The register status of a charge does not establish the amount secured, the value of the assets or the company's ability to pay.",
}


def test_all_nine_indicator_does_not_establish_strings_are_exact(ctx):
    profile = ctx.profile()
    profile["accounts"]["next_accounts"]["overdue"] = True
    profile["confirmation_statement"]["overdue"] = True
    profile["previous_company_names"] = [
        {
            "name": "OLD NAME LIMITED",
            "effective_from": "2019-01-01",
            "ceased_on": "2020-01-01",
        }
    ]
    officers = officers_with(*overdue_officers()["items"], *removed_officers()["items"])
    ctx.install(profile=profile, officers=officers)
    got = {i["id"]: i["does_not_establish"] for i in ctx.body()["indicators"]}
    assert got == NINE_DOES_NOT_ESTABLISH


# ------------------------------------------------------------------ spec v1.2: personal fields on every path

PII_MARKERS = ("PII_", "evil.example")
PERSONAL_KEYS = FORBIDDEN_KEYS - {"links"}


def assert_no_personal(payload, where):
    """One recursive assertion for every response path, header and log record."""
    if isinstance(payload, str):
        for marker in PII_MARKERS:
            assert marker not in payload, where
        try:
            parsed = json.loads(payload)
        except ValueError:
            return
        payload = parsed
    assert not PERSONAL_KEYS & set(walk_keys(payload)), where
    text = json.dumps(payload)
    for marker in PII_MARKERS:
        assert marker not in text, where


def hostile(status):
    return (
        status,
        {"error": "PII_UPSTREAM_ERROR_BODY", "links": {"x": "http://evil.example"}},
    )


def log_records(out):
    return [
        json.loads(line[len("kyb_lookup ") :])
        for line in out.splitlines()
        if line.startswith("kyb_lookup ")
    ]


ERROR_PATHS = [
    ("success", 200, {}),
    ("profile_404", 404, {"profile": hostile(404)}),
    ("upstream_500", 502, {"profile": hostile(500)}),
    ("upstream_auth", 503, {"profile": hostile(401)}),
    ("upstream_429", 503, {"profile": hostile(429)}),
    ("upstream_timeout", 504, {"profile": requests.Timeout("PII_TIMEOUT")}),
    ("upstream_conn", 502, {"profile": requests.ConnectionError("PII_CONN")}),
    ("bad_json_shape", 502, {"officers": (200, {"items": "PII_NOT_A_LIST"})}),
]


@pytest.mark.parametrize("name,status,over", ERROR_PATHS)
def test_no_personal_data_on_any_upstream_path_or_log(ctx, capsys, name, status, over):
    ctx.install(**over)
    r = ctx.get()
    assert r.status_code == status, r.text
    assert_no_personal(r.text, name)
    assert_no_personal(dict(r.headers), name)
    records = log_records(capsys.readouterr().out)
    assert records, "a log record is written for every request"
    for rec in records:
        assert_no_personal(rec, name)


def test_no_personal_data_on_local_error_paths_and_logs(ctx, capsys, monkeypatch):
    install_any(ctx)
    cases = []
    cases.append(("invalid", ctx.get("PII_BAD;DROP")))
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", "")
    cases.append(("not_configured", ctx.get()))
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", TEST_KEY)
    monkeypatch.setattr(kyb, "CLIENT_ALLOWANCE_MAX", 1)
    cases.append(("allowance", ctx.get("ZZ000011", headers=CLIENT_A)))
    cases.append(("allowance_blocked", ctx.get("ZZ000012", headers=CLIENT_A)))
    monkeypatch.setattr(kyb, "CLIENT_ALLOWANCE_MAX", 60)
    monkeypatch.setattr(kyb, "IP_LIMIT_MAX", 1)
    ctx.get("ZZ000013", headers=CLIENT_B)
    cases.append(("ip_limit", ctx.get("ZZ000014", headers=CLIENT_B)))
    assert [c[1].status_code for c in cases] == [422, 503, 429, 429, 429]
    for name, r in cases:
        assert_no_personal(r.text, name)
        assert_no_personal(dict(r.headers), name)
    for rec in log_records(capsys.readouterr().out):
        assert_no_personal(rec, "log")


def test_no_personal_data_in_health(ctx):
    import main

    install_any(ctx)
    ctx.get(headers=CLIENT_A)
    r = TestClient(main.app).get("/api/health")
    assert r.status_code == 200
    assert_no_personal(r.text, "health")


# ------------------------------------------------------------------ spec v1.2: synthetic fixtures

COMPANY_NUMBER = re.compile(r"^ZZ\d{6}$")
PATH_NUMBER = re.compile(r"/company/([A-Za-z0-9]+)")


def fixture_company_numbers(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "company_number" and isinstance(v, str):
                yield v
            yield from fixture_company_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from fixture_company_numbers(v)
    elif isinstance(obj, str):
        yield from PATH_NUMBER.findall(obj)


def test_every_fixture_company_number_is_a_reserved_synthetic_number():
    seen = 0
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for number in fixture_company_numbers(data):
            seen += 1
            assert COMPANY_NUMBER.fullmatch(number), path.name
    assert seen >= 4


def test_synthetic_bundles_are_marked_and_distinct():
    numbers = []
    for label in SYNTHETIC:
        bundle = synthetic_bundle(label)
        assert bundle["synthetic"] is True and "captured" not in bundle
        numbers.append(bundle["company_number"])
    assert len(set(numbers)) == len(SYNTHETIC)


def test_no_live_named_fixture_files_remain():
    assert not list(FIXTURES.glob("live_*.json"))


def test_synthetic_bundles_carry_no_register_free_text():
    for label in SYNTHETIC:
        text = (FIXTURES / f"synthetic_{label}.json").read_text(encoding="utf-8")
        for m in re.finditer(r'"(?:name|company_name)": "([^"]*)"', text):
            assert re.fullmatch(
                r"(PERSON|ENTITY|CHARGEE) [A-Z]|SYNTHETIC [A-Z0-9 ]+", m.group(1)
            ), m.group(1)
        assert not re.search(
            r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", text.replace("REDACTED", "")
        )


# ------------------------------------------------------------------ spec v1.4: PSC statements 404 is not_returned


def test_statements_404_is_not_returned_with_null_truncation(ctx):
    ctx.install(statements=(404, {}))
    body = ctx.body()
    pscs = body["pscs"]
    assert pscs["statements_source_status"] == "not_returned"
    assert pscs["records_source_status"] == "available"
    assert pscs["active_statements"] == [] and pscs["ceased_statements"] == []
    assert pscs["truncated"] is None
    ids = indicator_ids(body)
    assert "active_psc_statements" not in ids and "ceased_psc_statements" not in ids
    assert body["not_returned_notice"] == NOT_RETURNED_NOTICE


def test_statements_failure_is_not_a_404_and_never_becomes_a_readout(ctx):
    for failure, status in [(hostile(503), 502), (requests.Timeout("slow"), 504)]:
        kyb.reset_state_for_tests()
        ctx.install(statements=failure)
        r = ctx.get()
        assert r.status_code == status
        assert "pscs" not in r.json()


def test_forged_long_client_headers_never_create_a_long_limiter_key(ctx):
    ctx.install()
    for i in range(300):
        junk = f"{i}" + "9" * 200
        ctx.get(headers={"CF-Connecting-IP": junk, "X-Forwarded-For": junk})
    assert kyb._ip_log and all(len(k) <= 45 for k in kyb._ip_log)
    assert all(len(k) <= 45 for k in kyb._client_calls)


def test_1000_random_addresses_in_one_slash_64_share_one_limit(ctx):
    import random

    ctx.install()
    rng = random.Random(7)
    codes = []
    for _ in range(1000):
        low = rng.getrandbits(64)
        groups = [(low >> s) & 0xFFFF for s in (48, 32, 16, 0)]
        ip = "2001:db8:1:2:" + ":".join(f"{g:x}" for g in groups)
        codes.append(ctx.get(headers={"CF-Connecting-IP": ip}).status_code)
    assert codes[:10] == [200] * 10
    assert set(codes[10:]) == {429}
    assert list(kyb._ip_log) == ["2001:db8:1:2::/64"]


# ------------------------------------------------------------------ spec v1.8: open ended verification end date

START = "2026-06-17"


@pytest.mark.parametrize("is_psc", [False, True])
def test_end_9999_12_31_with_start_is_statement_supplied(is_psc):
    d = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="9999-12-31",
    )
    assert derive_idv_status(d, is_psc=is_psc, today=TODAY) == "statement_supplied"


@pytest.mark.parametrize("is_psc", [False, True])
def test_end_9999_12_31_without_start_is_still_a_conflict(is_psc):
    # An end date with no start date is inconsistent, so the open end is not applied.
    d = D(appointment_verification_end_on="9999-12-31")
    assert (
        derive_idv_status(d, is_psc=is_psc, today=TODAY) == "verification_data_conflict"
    )


def test_end_9999_12_31_without_start_stays_a_conflict_whatever_the_due_date():
    for due in ("2026-12-01", "2026-09-01"):
        d = D(
            appointment_verification_end_on="9999-12-31",
            appointment_verification_statement_due_on=due,
        )
        assert (
            derive_idv_status(d, is_psc=False, today=TODAY)
            == "verification_data_conflict"
        )


@pytest.mark.parametrize("is_psc", [False, True])
def test_start_with_end_9999_12_31_and_a_past_due_date_is_statement_supplied(is_psc):
    d = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="9999-12-31",
        appointment_verification_statement_due_on="2026-09-01",
    )
    assert derive_idv_status(d, is_psc=is_psc, today=TODAY) == "statement_supplied"


@pytest.mark.parametrize("raw", ["99991231", "9999-12-31 ", "9999-12-31T00:00:00"])
def test_only_the_exact_raw_string_is_the_open_end(raw):
    # date.fromisoformat accepts the basic format, so the match must be on the raw string.
    d = D(appointment_verification_start_on=START, appointment_verification_end_on=raw)
    assert (
        derive_idv_status(d, is_psc=False, today=TODAY) == "verification_data_conflict"
    )


@pytest.mark.parametrize("today", [date(2026, 10, 11), date(2030, 1, 1)])
def test_the_open_end_does_not_depend_on_today(today):
    d = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="9999-12-31",
    )
    assert derive_idv_status(d, is_psc=False, today=today) == "statement_supplied"
    future = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="2099-01-01",
    )
    assert (
        derive_idv_status(future, is_psc=False, today=today)
        == "verification_data_conflict"
    )


def test_an_end_date_in_the_past_is_a_removal_not_a_conflict_when_today_passes_it():
    d = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="2099-01-01",
    )
    assert (
        derive_idv_status(d, is_psc=False, today=date(2100, 1, 1))
        == "statement_removed"
    )


def test_open_ended_end_does_not_trigger_the_due_after_end_conflict():
    d = D(
        appointment_verification_start_on=START,
        appointment_verification_end_on="9999-12-31",
        appointment_verification_statement_due_on="2026-12-01",
    )
    assert derive_idv_status(d, is_psc=False, today=TODAY) == "statement_supplied"


@pytest.mark.parametrize("is_psc", [False, True])
def test_other_future_end_dates_stay_a_conflict(is_psc):
    for end in ("2099-01-01", "9999-12-30", "9999-01-01", "2026-10-12"):
        d = D(
            appointment_verification_start_on=START, appointment_verification_end_on=end
        )
        assert (
            derive_idv_status(d, is_psc=is_psc, today=TODAY)
            == "verification_data_conflict"
        ), end


def test_end_before_start_is_still_a_conflict():
    d = D(
        appointment_verification_start_on="2026-05-01",
        appointment_verification_end_on="2026-04-01",
    )
    assert (
        derive_idv_status(d, is_psc=False, today=TODAY) == "verification_data_conflict"
    )


def test_future_start_with_open_ended_end_is_still_a_conflict():
    d = D(
        appointment_verification_start_on="2026-10-12",
        appointment_verification_end_on="9999-12-31",
    )
    assert (
        derive_idv_status(d, is_psc=False, today=TODAY) == "verification_data_conflict"
    )


def test_open_ended_end_replay_and_raw_evidence(ctx):
    n, routes = synthetic_routes(synthetic_bundle("open_ended_verification"))
    ctx.install(routes=routes)
    body = ctx.body(n)
    got = {o["name"]: o["identity_verification"] for o in body["officers"]["items"]}
    assert got["PERSON A"]["status"] == "statement_supplied"
    assert got["PERSON B"]["status"] == "verification_data_conflict"
    assert got["PERSON D"]["status"] == "verification_data_conflict"
    assert got["PERSON E"]["status"] == "verification_data_conflict"
    assert (
        body["pscs"]["items"][0]["identity_verification"]["status"]
        == "statement_supplied"
    )
    # The register value is still shown exactly as returned.
    assert (
        got["PERSON A"]["evidence"]["appointment_verification_end_on"] == "9999-12-31"
    )
    assert got["PERSON A"]["display"] == (
        "The register records that an identity verification statement was supplied "
        f"for this role on {START}, linking a verified identity to it."
    )
    ids = indicator_ids(body)
    assert "idv_statement_removed" not in ids and "idv_statement_overdue" not in ids


def test_open_ended_end_on_a_removed_officer_and_a_ceased_psc_is_not_interpreted(ctx):
    n, routes = synthetic_routes(synthetic_bundle("open_ended_verification"))
    ctx.install(routes=routes)
    body = ctx.body(n)
    officer = next(o for o in body["officers"]["items"] if o["name"] == "PERSON F")
    psc = next(p for p in body["pscs"]["items"] if p["name"] == "PERSON G")
    for rec in (officer, psc):
        iv = rec["identity_verification"]
        assert iv["eligibility"] == "not_interpreted" and iv["status"] is None
        assert iv["evidence"]["appointment_verification_end_on"] == "9999-12-31"
