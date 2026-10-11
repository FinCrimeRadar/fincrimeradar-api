"""Companies House KYB company readout, API phase 1.

Spec: docs/specs/kyb-lab-api-spec.md (frozen v1.0). Where this file and the
spec disagree, the spec wins and this file is the bug.

GET /api/kyb/company/{company_number} reads one UK company from the Companies
House Public Data API and returns register facts, dated lists and a small set
of indicators. It never returns a score, a verdict or a clearance. Personal
data is minimised by never modelling it: the upstream models below simply do
not declare date of birth, nationality, occupation, addresses or preferred
name, and extra="ignore" drops them at parse time.

In memory state (cache, upstream budget, per IP counters) is per instance and
resets on restart, see the spec for why the budget is 300 and not 600.
"""

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from client_ip import client_ip

load_dotenv()

# Read at import, like screening.py. Never logged, never returned.
COMPANIES_HOUSE_API_KEY = os.environ.get("COMPANIES_HOUSE_API_KEY", "").strip()

UPSTREAM_BASE = "https://api.company-information.service.gov.uk"
CONNECT_TIMEOUT_SECONDS = 3.05
READ_TIMEOUT_SECONDS = 10
CONNECTION_RETRY_BACKOFF_SECONDS = 0.5
ITEMS_PER_PAGE = "100"

# Companies House allows 600 requests per five minutes per application. 300
# per instance leaves room for a restart that resets the counter mid window.
UPSTREAM_BUDGET_MAX = 300
UPSTREAM_BUDGET_WINDOW_SECONDS = 300
# After an upstream 429 the reset time is unknown (rate limit headers are not
# documented), so stop for a full window.
UPSTREAM_429_BLOCK_SECONDS = 300

IP_LIMIT_MAX = 10
IP_LIMIT_WINDOW_SECONDS = 60

CACHE_TTL_SECONDS = 15 * 60
CACHE_MAX_ENTRIES = 500

LONDON = ZoneInfo("Europe/London")

# Set to False if a live response shows `description` is rendered text rather
# than the enumeration identifier (spec section 6, provisional rule).
REGISTERED_OFFICE_FILINGS_ENABLED = True

IN_SCOPE_TYPES = frozenset(
    {
        "ltd",
        "plc",
        "private-unlimited",
        "private-unlimited-nsc",
        "private-limited-guarant-nsc",
        "private-limited-guarant-nsc-limited-exemption",
        "private-limited-shares-section-30-exemption",
        "llp",
    }
)

UNSUPPORTED_NOTICE = (
    "FinCrimeRadar does not interpret indicators for this entity type in phase 1"
)

REGISTERED_OFFICE_IDENTIFIERS = frozenset(
    {
        "change-registered-office-address-company",
        "change-registered-office-address-company-with-date-old-address",
        "change-registered-office-address-company-with-date-old-address-new-address",
        "change-registered-office-address-limited-liability-partnership",
        "change-registered-office-address-limited-liability-partnership-with-date-old-address",
        "change-registered-office-address-limited-liability-partnership-with-date-old-address-new-address",
    }
)

NATURAL_PERSON_OFFICER_ROLES = frozenset(
    {"director", "llp-member", "llp-designated-member"}
)
INDIVIDUAL_PSC_KIND = "individual-person-with-significant-control"

GLOBAL_DOES_NOT_ESTABLISH = [
    (
        "The register is not comprehensive and may contain inaccuracies, errors or "
        "omissions. Companies House accepts no responsibility for their consequences."
    ),
    (
        "Companies House checks filings for completeness and validity but does not "
        "closely review every document. Accounts receive only basic checks."
    ),
    (
        "Register status does not establish that a company is trading. Dormant "
        "accounts are a separate filing fact."
    ),
    (
        "Identity verification records do not establish legitimacy, ownership "
        "accuracy or completed CDD."
    ),
    "The absence of an indicator is not evidence of low risk.",
    "This readout is not customer due diligence and does not replace it.",
]

IDV_DOES_NOT_ESTABLISH = (
    "A register record of a supplied statement is not evidence that the person, "
    "the company or its activity is legitimate. It does not establish who "
    "ultimately owns or controls the company, whether ownership filings are "
    "accurate, the source of funds, or that customer due diligence is complete."
)

NOT_INTERPRETED_LABEL = "fields returned by the register; not interpreted in phase 1"

ATTRIBUTION = (
    "Source: Companies House public register, retrieved {retrieved_at}. "
    "FinCrimeRadar is not affiliated with or endorsed by Companies House."
)

PSC_DESCRIPTIONS_PATH = (
    Path(__file__).parent
    / "docs"
    / "specs"
    / "companies-house-enumerations"
    / "psc_descriptions.yml"
)

router = APIRouter()


# ---------------------------------------------------------------- PSC statement text


def _load_statement_descriptions(path: Path = PSC_DESCRIPTIONS_PATH) -> dict[str, str]:
    """`statement_description` table from the pinned enumerations file.

    Parsed with a regex so no YAML dependency is needed. An unreadable file
    yields an empty table, which makes every statement show its raw code (the
    spec's behaviour for an unknown code) rather than crash the service import.
    A test pins that the real file loads.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    start = text.find("statement_description:")
    if start == -1:
        return {}
    end = text.find("\nsuper_secure_description:", start)
    block = text[start : end if end != -1 else len(text)]
    row = re.compile(r"^\s+'([^']+)'\s*:\s*\"(.*)\"\s*$")
    out = {}
    for line in block.split("\n")[1:]:
        m = row.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


STATEMENT_DESCRIPTIONS = _load_statement_descriptions()


# ---------------------------------------------------------------- upstream models
# extra="ignore": anything not declared here never reaches the response.


class _Up(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UpIdv(_Up):
    # preferred_name is deliberately not declared.
    appointment_verification_start_on: str | None = None
    appointment_verification_end_on: str | None = None
    appointment_verification_statement_due_on: str | None = None
    appointment_verification_statement_date: str | None = None
    identity_verified_on: str | None = None
    authorised_corporate_service_provider_name: str | None = None
    anti_money_laundering_supervisory_bodies: list[str] | None = None


class UpNextAccounts(_Up):
    overdue: bool | None = None


class UpAccounts(_Up):
    next_accounts: UpNextAccounts | None = None


class UpConfirmation(_Up):
    overdue: bool | None = None


class UpPreviousName(_Up):
    name: str | None = None
    effective_from: str | None = None
    ceased_on: str | None = None


class UpProfile(_Up):
    company_name: str | None = None
    company_status: str | None = None
    type: str | None = None
    date_of_creation: str | None = None
    date_of_cessation: str | None = None
    accounts: UpAccounts | None = None
    confirmation_statement: UpConfirmation | None = None
    previous_company_names: list[UpPreviousName] | None = None
    links: dict[str, Any] | None = None


class UpOfficer(_Up):
    name: str | None = None
    officer_role: str | None = None
    appointed_on: str | None = None
    resigned_on: str | None = None
    identity_verification_details: UpIdv | None = None


class UpOfficerList(_Up):
    items: list[UpOfficer] | None = None
    total_results: int | None = None


class UpPsc(_Up):
    name: str | None = None
    kind: str | None = None
    notified_on: str | None = None
    ceased_on: str | None = None
    identity_verification_details: UpIdv | None = None


class UpPscList(_Up):
    # The Swagger types the PSC list paging fields as strings. Lax mode
    # coerces "5" to 5, so the counts below are integers either way.
    items: list[UpPsc] | None = None
    total_results: int | None = None
    items_per_page: int | None = None
    start_index: int | None = None


class UpStatement(_Up):
    statement: str | None = None
    notified_on: str | None = None
    ceased_on: str | None = None
    linked_psc_name: str | None = None


class UpStatementList(_Up):
    items: list[UpStatement] | None = None
    total_results: int | None = None


class UpFiling(_Up):
    date: str | None = None
    category: str | None = None
    description: str | None = None
    transaction_id: str | None = None


class UpFilingList(_Up):
    items: list[UpFiling] | None = None
    total_count: int | None = None


class UpCharge(_Up):
    status: str | None = None
    charge_number: int | None = None
    created_on: str | None = None


class UpChargeList(_Up):
    items: list[UpCharge] | None = None
    total_count: int | None = None


class UpInsolvencyCase(_Up):
    type: str | None = None
    number: str | None = None


class UpInsolvency(_Up):
    cases: list[UpInsolvencyCase] | None = None
    status: str | list[str] | None = None


# ---------------------------------------------------------------- response models
# strict=True, extra="forbid": a typo in this file fails loudly instead of
# silently shipping a field.


class _Out(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CompanyOut(_Out):
    name: str | None
    status: str | None
    type: str | None
    date_of_creation: str | None
    date_of_cessation: str | None


class InterpretationOut(_Out):
    scope: Literal["interpreted", "unsupported_entity_type"]
    current_obligations: Literal[
        "interpreted", "register_reported_only", "not_applicable"
    ]
    notice: str | None


class RegisterReportedValuesOut(_Out):
    label: str
    company_status: str | None
    date_of_cessation: str | None
    accounts_next_accounts_overdue: bool | None
    confirmation_statement_overdue: bool | None


class IdentityVerificationOut(_Out):
    eligibility: Literal["derivation_eligible", "not_interpreted"]
    status: (
        Literal[
            "statement_supplied",
            "statement_removed",
            "statement_due",
            "statement_overdue",
            "verification_data_conflict",
            "no_statement_status_fields",
        ]
        | None
    )
    display: str
    evidence: dict[str, Any]
    evidence_label: str | None
    does_not_establish: str


class OfficerItemOut(_Out):
    name: str | None
    role: str | None
    appointed_on: str | None
    resigned_on: str | None
    identity_verification: IdentityVerificationOut


class DatedEventOut(_Out):
    date: str
    event: Literal["appointed", "resigned"]
    name: str | None
    role: str | None


class OfficersOut(_Out):
    items: list[OfficerItemOut]
    events: list[DatedEventOut]
    truncated: bool


class PscItemOut(_Out):
    name: str | None
    kind: str | None
    notified_on: str | None
    ceased_on: str | None
    identity_verification: IdentityVerificationOut


class StatementOut(_Out):
    statement: str | None
    description: str | None
    notified_on: str | None
    ceased_on: str | None


class PscsOut(_Out):
    items: list[PscItemOut]
    active_statements: list[StatementOut]
    ceased_statements: list[StatementOut]
    truncated: bool


class IndicatorOut(_Out):
    id: str
    label: str
    evidence_state: Literal["register_fact", "frc_assessment"]
    evidence: dict[str, Any]
    source_endpoint: Literal[
        "profile", "officers", "pscs", "filing_history", "charges", "insolvency"
    ]
    does_not_establish: str


class RegisteredOfficeFilingOut(_Out):
    date: str | None
    transaction_id: str | None
    description_identifier: str


class RegisteredOfficeFilingsOut(_Out):
    items: list[RegisteredOfficeFilingOut]
    truncated: bool


class KybResponse(_Out):
    company_number: str
    retrieved_at: str
    source: str
    company: CompanyOut
    interpretation: InterpretationOut
    register_reported_values: RegisterReportedValuesOut | None
    indicators: list[IndicatorOut]
    officers: OfficersOut
    pscs: PscsOut
    registered_office_change_filings: RegisteredOfficeFilingsOut | None
    does_not_establish: list[str]
    attribution: str


# ---------------------------------------------------------------- state and limits

_state_lock = threading.Lock()
_budget: deque = deque()
_blocked_until = 0.0
_ip_log: dict[str, deque] = {}
_cache: dict[str, tuple[float, dict]] = {}


def _monotonic() -> float:
    return time.monotonic()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Fail(Exception):
    """An error that maps to a client response without leaking upstream detail."""

    def __init__(
        self, status: int, detail: str, outcome: str, retry_after: int | None = None
    ):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.outcome = outcome
        self.retry_after = retry_after

    def to_http(self) -> HTTPException:
        headers = {"Retry-After": str(self.retry_after)} if self.retry_after else None
        return HTTPException(self.status, self.detail, headers=headers)


def _prune_budget(now: float) -> None:
    while _budget and now - _budget[0] >= UPSTREAM_BUDGET_WINDOW_SECONDS:
        _budget.popleft()


def quota_remaining() -> int:
    with _state_lock:
        _prune_budget(_monotonic())
        return max(0, UPSTREAM_BUDGET_MAX - len(_budget))


def _take_budget() -> None:
    """Spend one upstream request or raise a 503 with Retry-After."""
    with _state_lock:
        now = _monotonic()
        if now < _blocked_until:
            raise _Fail(
                503,
                "Service temporarily unavailable.",
                "upstream_blocked",
                max(1, int(_blocked_until - now) + 1),
            )
        _prune_budget(now)
        if len(_budget) >= UPSTREAM_BUDGET_MAX:
            wait = UPSTREAM_BUDGET_WINDOW_SECONDS - (now - _budget[0])
            raise _Fail(
                503,
                "Service temporarily unavailable.",
                "budget_exhausted",
                max(1, int(wait) + 1),
            )
        _budget.append(now)


def _block_upstream() -> None:
    global _blocked_until
    with _state_lock:
        _blocked_until = _monotonic() + UPSTREAM_429_BLOCK_SECONDS


def _ip_limited(key: str) -> bool:
    now = _monotonic()
    with _state_lock:
        log = _ip_log.setdefault(key, deque())
        while log and now - log[0] >= IP_LIMIT_WINDOW_SECONDS:
            log.popleft()
        if len(log) >= IP_LIMIT_MAX:
            return True
        log.append(now)
        if len(_ip_log) > 5000:
            for k in [
                k
                for k, v in _ip_log.items()
                if not v or now - v[-1] >= IP_LIMIT_WINDOW_SECONDS
            ]:
                del _ip_log[k]
        return False


def _cache_get(number: str) -> dict | None:
    now = _monotonic()
    with _state_lock:
        hit = _cache.get(number)
        if hit and hit[0] > now:
            return hit[1]
        _cache.pop(number, None)
        return None


def _cache_put(number: str, body: dict) -> None:
    now = _monotonic()
    with _state_lock:
        for k in [k for k, (exp, _) in _cache.items() if exp <= now]:
            del _cache[k]
        while len(_cache) >= CACHE_MAX_ENTRIES:
            del _cache[next(iter(_cache))]
        _cache[number] = (now + CACHE_TTL_SECONDS, body)


def reset_state_for_tests() -> None:
    global _blocked_until
    with _state_lock:
        _budget.clear()
        _ip_log.clear()
        _cache.clear()
        _blocked_until = 0.0


# ---------------------------------------------------------------- upstream access


def _http_get(url: str, params: dict | None, auth: tuple[str, str], timeout: tuple):
    """The only place the network is touched. Tests replace this function."""
    return requests.get(
        url,
        params=params,
        auth=auth,
        timeout=timeout,
        allow_redirects=False,
        headers={"Accept": "application/json"},
    )


def _ratelimit_headers(resp) -> dict[str, str]:
    try:
        return {
            k.lower(): v
            for k, v in resp.headers.items()
            if k.lower().startswith("x-ratelimit")
        }
    except (AttributeError, TypeError):
        return {}


class _Call:
    """Per request upstream bookkeeping for the structured log."""

    def __init__(self):
        self.count = 0
        self.seconds = 0.0
        self.ratelimit_headers: dict[str, str] = {}


def _fetch(path: str, call: _Call, params: dict | None = None):
    """GET one fixed path on the fixed host.

    Returns the parsed JSON, or None for a 404. `path` is always built from a
    literal template plus the validated company number, never from a `links`
    value in an upstream payload.
    """
    attempts = 0
    while True:
        attempts += 1
        _take_budget()
        call.count += 1
        started = _monotonic()
        try:
            resp = _http_get(
                UPSTREAM_BASE + path,
                params,
                (COMPANIES_HOUSE_API_KEY, ""),
                (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except requests.Timeout:
            # Checked first: ConnectTimeout is both a Timeout and a
            # ConnectionError, and a timeout is never retried (504, no retry).
            call.seconds += _monotonic() - started
            raise _Fail(504, "Upstream timeout.", "upstream_timeout") from None
        except requests.ConnectionError:
            call.seconds += _monotonic() - started
            if attempts == 1:
                time.sleep(CONNECTION_RETRY_BACKOFF_SECONDS)
                continue
            raise _Fail(502, "Upstream error.", "upstream_connection_error") from None
        except requests.RequestException:
            call.seconds += _monotonic() - started
            raise _Fail(502, "Upstream error.", "upstream_error") from None
        call.seconds += _monotonic() - started
        call.ratelimit_headers.update(_ratelimit_headers(resp))
        status = resp.status_code
        if status == 200:
            try:
                return resp.json()
            except ValueError:
                raise _Fail(502, "Upstream error.", "upstream_bad_json") from None
        if status == 404:
            return None
        if status == 429:
            _block_upstream()
            raise _Fail(
                503,
                "Service temporarily unavailable.",
                "upstream_429",
                UPSTREAM_429_BLOCK_SECONDS,
            )
        if status in (401, 403):
            # A bad or revoked key. Never say so to the client.
            raise _Fail(503, "Service temporarily unavailable.", "upstream_auth")
        raise _Fail(502, "Upstream error.", f"upstream_{status}")


def _parse(model, payload, call_outcome: str):
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise _Fail(502, "Upstream error.", call_outcome) from None


# ---------------------------------------------------------------- input


def normalise_company_number(raw: str) -> str | None:
    """Spec section 3, input validation. None means reject with 422."""
    value = raw.strip()
    # Reject non ASCII before upper(): str.upper() can map some non ASCII
    # characters (for example the long s) onto ASCII letters.
    if not value.isascii():
        return None
    value = value.upper()
    if value.isdigit() and len(value) < 8:
        value = value.zfill(8)
    return value if re.fullmatch(r"[A-Z0-9]{8}", value) else None


# ---------------------------------------------------------------- derivation


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def derive_idv_status(
    d: UpIdv | None, *, is_psc: bool, today: date
) -> Literal[
    "statement_supplied",
    "statement_removed",
    "statement_due",
    "statement_overdue",
    "verification_data_conflict",
    "no_statement_status_fields",
]:
    """Spec section 5.2. First match wins, row a (conflict) first."""
    d = d or UpIdv()
    try:
        start = _parse_date(d.appointment_verification_start_on)
        end = _parse_date(d.appointment_verification_end_on)
        due = _parse_date(d.appointment_verification_statement_due_on)
        opens = (
            _parse_date(d.appointment_verification_statement_date) if is_psc else None
        )
    except ValueError:
        # A date the register returned that is not a date is inconsistent data.
        return "verification_data_conflict"
    if (
        (start and start > today)
        or (end and end > today)
        or (start and end and end < start)
        or (end and not start)
        or (start and end and due and due > end)
        or (is_psc and opens and due and opens > due)
    ):
        return "verification_data_conflict"
    if start and not end:
        return "statement_supplied"
    if start and end:
        return "statement_removed"
    if due and due >= today:
        return "statement_due"
    if due and due < today:
        return "statement_overdue"
    return "no_statement_status_fields"


def _idv_display(status: str, d: UpIdv | None) -> str:
    d = d or UpIdv()
    if status == "statement_supplied":
        return (
            "The register records that an identity verification statement was "
            f"supplied for this role on {d.appointment_verification_start_on}, "
            "linking a verified identity to it."
        )
    if status == "statement_removed":
        return (
            "The register records that the verification statement for this role "
            f"was removed on {d.appointment_verification_end_on}."
        )
    if status == "verification_data_conflict":
        return (
            "The register returned verification dates that are inconsistent or "
            "incomplete. FinCrimeRadar does not interpret them; check the "
            "register record directly."
        )
    if status == "statement_due":
        return (
            "The register shows a verification statement for this role is due by "
            f"{d.appointment_verification_statement_due_on}. None is recorded as "
            "supplied at the time of retrieval."
        )
    if status == "statement_overdue":
        return (
            "The register shows a verification statement for this role was due by "
            f"{d.appointment_verification_statement_due_on}, and none is recorded "
            "as supplied. This is a register record, not a finding that the "
            "person is unverified or that an offence has occurred. Check the "
            "register record directly before relying on it."
        )
    return (
        "No verification statement dates were returned for this role. This does "
        "not establish whether the person is verified, unverified, compliant, "
        "non-compliant or outside scope."
    )


def _idv_evidence(d: UpIdv | None) -> dict[str, Any]:
    """Raw verification fields the register returned. Never preferred_name."""
    if d is None:
        return {}
    out = {k: v for k, v in d.model_dump().items() if v is not None and v != []}
    opens = d.appointment_verification_statement_date
    if opens:
        out["statement_can_be_supplied_from"] = (
            f"statement can be supplied from {opens}"
        )
    return out


def build_idv(
    d: UpIdv | None, *, eligible: bool, is_psc: bool, today: date
) -> IdentityVerificationOut:
    evidence = _idv_evidence(d)
    if not eligible:
        return IdentityVerificationOut(
            eligibility="not_interpreted",
            status=None,
            display=(
                "Verification fields were returned by the register for this "
                "record. FinCrimeRadar does not interpret them in phase 1."
                if evidence
                else "FinCrimeRadar does not derive a verification status for "
                "this record in phase 1."
            ),
            evidence=evidence,
            evidence_label=NOT_INTERPRETED_LABEL if evidence else None,
            does_not_establish=IDV_DOES_NOT_ESTABLISH,
        )
    status = derive_idv_status(d, is_psc=is_psc, today=today)
    return IdentityVerificationOut(
        eligibility="derivation_eligible",
        status=status,
        display=_idv_display(status, d),
        evidence=evidence,
        evidence_label=None,
        does_not_establish=IDV_DOES_NOT_ESTABLISH,
    )


def statement_description(code: str | None, linked_psc_name: str | None) -> str | None:
    """Enumeration text verbatim. Only {linked_psc_name} is substituted."""
    if code is None or code not in STATEMENT_DESCRIPTIONS:
        return None
    text = STATEMENT_DESCRIPTIONS[code]
    if "{linked_psc_name}" in text:
        text = text.replace("{linked_psc_name}", linked_psc_name or "")
    return text


# ---------------------------------------------------------------- readout


def _indicator(
    id_: str,
    label: str,
    evidence: dict[str, Any],
    source: str,
    does_not_establish: str,
) -> IndicatorOut:
    return IndicatorOut(
        id=id_,
        label=label,
        evidence_state="register_fact",
        evidence=evidence,
        source_endpoint=source,
        does_not_establish=does_not_establish,
    )


def _company_out(p: UpProfile) -> CompanyOut:
    return CompanyOut(
        name=p.company_name,
        status=p.company_status,
        type=p.type,
        date_of_creation=p.date_of_creation,
        date_of_cessation=p.date_of_cessation,
    )


def _empty_sections():
    return (
        OfficersOut(items=[], events=[], truncated=False),
        PscsOut(items=[], active_statements=[], ceased_statements=[], truncated=False),
    )


def _truncated(total: int | None, returned: int) -> bool:
    return total is not None and total > returned


def _registered_office_filings(
    filings: list[UpFiling], total_count: int | None
) -> RegisteredOfficeFilingsOut | None:
    """Provisional list, spec section 6. None means the indicator is disabled."""
    if not REGISTERED_OFFICE_FILINGS_ENABLED:
        return None
    # Enumeration identifiers never contain whitespace. Rendered text does, so
    # any whitespace in an address filing description means this field is not
    # carrying identifiers and the list cannot be trusted.
    if any(
        f.category == "address" and f.description and re.search(r"\s", f.description)
        for f in filings
    ):
        return None
    hits = [
        RegisteredOfficeFilingOut(
            date=f.date,
            transaction_id=f.transaction_id,
            description_identifier=f.description,
        )
        for f in filings
        if f.category == "address" and f.description in REGISTERED_OFFICE_IDENTIFIERS
    ]
    hits.sort(key=lambda h: (h.date or "", h.transaction_id or ""), reverse=True)
    # Spec v1.1: same total versus returned rule as the officers list. One page
    # of filing history can miss older registered office changes.
    return RegisteredOfficeFilingsOut(
        items=hits, truncated=_truncated(total_count, len(filings))
    )


def build_readout(number: str, retrieved_at: datetime, call: _Call) -> dict:
    retrieved_iso = (
        retrieved_at.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    today = retrieved_at.astimezone(LONDON).date()

    payload = _fetch(f"/company/{number}", call)
    if payload is None:
        raise _Fail(404, "Company not found.", "not_found")
    profile = _parse(UpProfile, payload, "bad_profile")
    company = _company_out(profile)

    def respond(**kw) -> dict:
        officers, pscs = kw.pop("officers", None), kw.pop("pscs", None)
        if officers is None:
            officers, pscs = _empty_sections()
        return KybResponse(
            company_number=number,
            retrieved_at=retrieved_iso,
            source="Companies House Public Data API",
            company=company,
            officers=officers,
            pscs=pscs,
            does_not_establish=list(GLOBAL_DOES_NOT_ESTABLISH),
            attribution=ATTRIBUTION.format(retrieved_at=retrieved_iso),
            **kw,
        ).model_dump()

    if profile.type not in IN_SCOPE_TYPES:
        return respond(
            interpretation=InterpretationOut(
                scope="unsupported_entity_type",
                current_obligations="not_applicable",
                notice=UNSUPPORTED_NOTICE,
            ),
            register_reported_values=None,
            indicators=[],
            registered_office_change_filings=None,
        )

    link_keys = set((profile.links or {}).keys())
    active = profile.company_status == "active"

    officer_list = _parse(
        UpOfficerList,
        _fetch(f"/company/{number}/officers", call, {"items_per_page": ITEMS_PER_PAGE})
        or {},
        "bad_officers",
    )
    psc_list = _parse(
        UpPscList,
        _fetch(
            f"/company/{number}/persons-with-significant-control",
            call,
            {"items_per_page": ITEMS_PER_PAGE},
        )
        or {},
        "bad_pscs",
    )
    statement_list = _parse(
        UpStatementList,
        _fetch(
            f"/company/{number}/persons-with-significant-control-statements",
            call,
            {"items_per_page": ITEMS_PER_PAGE},
        )
        or {},
        "bad_psc_statements",
    )
    filing_list = _parse(
        UpFilingList,
        _fetch(
            f"/company/{number}/filing-history",
            call,
            {"items_per_page": ITEMS_PER_PAGE},
        )
        or {},
        "bad_filing_history",
    )
    charge_list = None
    if "charges" in link_keys:
        charge_list = _parse(
            UpChargeList,
            _fetch(
                f"/company/{number}/charges", call, {"items_per_page": ITEMS_PER_PAGE}
            )
            or {},
            "bad_charges",
        )
    insolvency = None
    if "insolvency" in link_keys:
        raw = _fetch(f"/company/{number}/insolvency", call)
        insolvency = (
            _parse(UpInsolvency, raw, "bad_insolvency") if raw is not None else None
        )

    officers_src = officer_list.items or []
    pscs_src = psc_list.items or []
    statements_src = statement_list.items or []
    filings_src = sorted(
        filing_list.items or [], key=lambda f: f.date or "", reverse=True
    )

    officer_items: list[OfficerItemOut] = []
    pscs_items: list[PscItemOut] = []
    idv_hits: dict[str, list[dict[str, Any]]] = {
        "statement_overdue": [],
        "statement_removed": [],
    }

    def _track(status, name, source, d):
        if status in idv_hits:
            entry = {"name": name, "source": source}
            if status == "statement_overdue":
                entry["due_on"] = (
                    d.appointment_verification_statement_due_on if d else None
                )
            else:
                entry["ended_on"] = d.appointment_verification_end_on if d else None
            idv_hits[status].append(entry)

    for o in officers_src:
        eligible = (
            active
            and o.officer_role in NATURAL_PERSON_OFFICER_ROLES
            and not o.resigned_on
        )
        idv = build_idv(
            o.identity_verification_details,
            eligible=eligible,
            is_psc=False,
            today=today,
        )
        _track(idv.status, o.name, "officer", o.identity_verification_details)
        officer_items.append(
            OfficerItemOut(
                name=o.name,
                role=o.officer_role,
                appointed_on=o.appointed_on,
                resigned_on=o.resigned_on,
                identity_verification=idv,
            )
        )
    for p in pscs_src:
        eligible = active and p.kind == INDIVIDUAL_PSC_KIND and not p.ceased_on
        idv = build_idv(
            p.identity_verification_details, eligible=eligible, is_psc=True, today=today
        )
        _track(idv.status, p.name, "psc", p.identity_verification_details)
        pscs_items.append(
            PscItemOut(
                name=p.name,
                kind=p.kind,
                notified_on=p.notified_on,
                ceased_on=p.ceased_on,
                identity_verification=idv,
            )
        )

    events = [
        DatedEventOut(
            date=o.appointed_on, event="appointed", name=o.name, role=o.officer_role
        )
        for o in officers_src
        if o.appointed_on
    ] + [
        DatedEventOut(
            date=o.resigned_on, event="resigned", name=o.name, role=o.officer_role
        )
        for o in officers_src
        if o.resigned_on
    ]
    events.sort(key=lambda e: (e.date, e.event), reverse=True)

    statement_outs = [
        StatementOut(
            statement=s.statement,
            description=statement_description(s.statement, s.linked_psc_name),
            notified_on=s.notified_on,
            ceased_on=s.ceased_on,
        )
        for s in statements_src
    ]
    active_statements = [s for s in statement_outs if not s.ceased_on]
    ceased_statements = [s for s in statement_outs if s.ceased_on]

    officers = OfficersOut(
        items=officer_items,
        events=events,
        truncated=_truncated(officer_list.total_results, len(officers_src)),
    )
    pscs = PscsOut(
        items=pscs_items,
        active_statements=active_statements,
        ceased_statements=ceased_statements,
        truncated=_truncated(psc_list.total_results, len(pscs_src))
        or _truncated(statement_list.total_results, len(statements_src)),
    )

    indicators: list[IndicatorOut] = []
    if active:
        if (
            profile.accounts
            and profile.accounts.next_accounts
            and profile.accounts.next_accounts.overdue is True
        ):
            indicators.append(
                _indicator(
                    "accounts_overdue",
                    "Accounts are flagged overdue",
                    {"accounts.next_accounts.overdue": True},
                    "profile",
                    "A register flag that accounts are overdue does not establish the reason, whether the company is trading, or any wrongdoing.",
                )
            )
        if (
            profile.confirmation_statement
            and profile.confirmation_statement.overdue is True
        ):
            indicators.append(
                _indicator(
                    "confirmation_statement_overdue",
                    "Confirmation statement is flagged overdue",
                    {"confirmation_statement.overdue": True},
                    "profile",
                    "A register flag that a confirmation statement is overdue does not establish the reason, whether the company is trading, or any wrongdoing.",
                )
            )
        if idv_hits["statement_overdue"]:
            indicators.append(
                _indicator(
                    "idv_statement_overdue",
                    "At least one current role has an identity verification statement past its due date",
                    {"records": idv_hits["statement_overdue"]},
                    "officers",
                    "A register record that a statement is overdue is not a finding that the person is unverified or that an offence has occurred.",
                )
            )
        if idv_hits["statement_removed"]:
            indicators.append(
                _indicator(
                    "idv_statement_removed",
                    "At least one current role has a removed identity verification statement",
                    {"records": idv_hits["statement_removed"]},
                    "officers",
                    "A removed statement record does not establish why it was removed or the person's current verification position.",
                )
            )
    if active_statements:
        indicators.append(
            _indicator(
                "active_psc_statements",
                "The company has PSC statements that have not ceased",
                {
                    "statements": [
                        {
                            "statement": s.statement,
                            "description": s.description,
                            "notified_on": s.notified_on,
                        }
                        for s in active_statements
                    ]
                },
                "pscs",
                "A PSC statement records what the company told the register. It does not establish who owns or controls the company or that the statement is accurate.",
            )
        )
    if ceased_statements:
        indicators.append(
            _indicator(
                "ceased_psc_statements",
                "The company has PSC statements that have ceased",
                {
                    "statements": [
                        {
                            "statement": s.statement,
                            "description": s.description,
                            "notified_on": s.notified_on,
                            "ceased_on": s.ceased_on,
                        }
                        for s in ceased_statements
                    ]
                },
                "pscs",
                "A ceased statement is a historical register record. It does not establish the current position or that the statement was accurate.",
            )
        )
    if profile.previous_company_names:
        indicators.append(
            _indicator(
                "previous_names",
                "The company has previous names",
                {
                    "names": [
                        {
                            "name": n.name,
                            "effective_from": n.effective_from,
                            "ceased_on": n.ceased_on,
                        }
                        for n in profile.previous_company_names
                    ]
                },
                "profile",
                "A previous name is a register record. It does not establish why the name changed.",
            )
        )
    if insolvency is not None:
        indicators.append(
            _indicator(
                "insolvency_history",
                "The register holds an insolvency record",
                {
                    "status": insolvency.status,
                    "cases": [
                        {"type": c.type, "number": c.number}
                        for c in insolvency.cases or []
                    ],
                },
                "insolvency",
                "An insolvency record does not establish current solvency, cause or outcome.",
            )
        )
    if charge_list is not None:
        outstanding = [c for c in charge_list.items or [] if c.status == "outstanding"]
        if outstanding:
            indicators.append(
                _indicator(
                    "outstanding_charges",
                    "The company has charges with status outstanding",
                    {
                        "outstanding_count": len(outstanding),
                        "returned_count": len(charge_list.items or []),
                        "total_count": charge_list.total_count,
                        "charges": [
                            {
                                "charge_number": c.charge_number,
                                "created_on": c.created_on,
                                "status": c.status,
                            }
                            for c in outstanding
                        ],
                    },
                    "charges",
                    "The register status of a charge does not establish the amount secured, the value of the assets or the company's ability to pay.",
                )
            )

    if active:
        interpretation = InterpretationOut(
            scope="interpreted", current_obligations="interpreted", notice=None
        )
        reported = None
    else:
        label = (
            "register-reported values; FinCrimeRadar does not interpret current "
            f"obligations for status {profile.company_status or 'unknown'}"
        )
        interpretation = InterpretationOut(
            scope="interpreted",
            current_obligations="register_reported_only",
            notice=label,
        )
        reported = RegisterReportedValuesOut(
            label=label,
            company_status=profile.company_status,
            date_of_cessation=profile.date_of_cessation,
            accounts_next_accounts_overdue=(
                profile.accounts.next_accounts.overdue
                if profile.accounts and profile.accounts.next_accounts
                else None
            ),
            confirmation_statement_overdue=(
                profile.confirmation_statement.overdue
                if profile.confirmation_statement
                else None
            ),
        )

    return respond(
        interpretation=interpretation,
        register_reported_values=reported,
        indicators=indicators,
        officers=officers,
        pscs=pscs,
        registered_office_change_filings=_registered_office_filings(
            filings_src, filing_list.total_count
        ),
    )


# ---------------------------------------------------------------- endpoint


def _log(
    request_id: str, number: str | None, outcome: str, call: _Call, cache: str
) -> None:
    """One structured line per request. No officer or PSC data, no key, no raw input."""
    print(
        "kyb_lookup "
        + json.dumps(
            {
                "request_id": request_id,
                "company_number": number,
                "outcome": outcome,
                "upstream_calls": call.count,
                "upstream_seconds": round(call.seconds, 3),
                "cache": cache,
                "quota_remaining": quota_remaining(),
                "upstream_ratelimit_headers": call.ratelimit_headers or None,
            }
        ),
        flush=True,
    )


@router.get("/api/kyb/company/{company_number:path}")
def kyb_company(company_number: str, request: Request):
    request_id = uuid.uuid4().hex[:12]
    call = _Call()
    if _ip_limited(client_ip(request)):
        _log(request_id, None, "ip_rate_limited", call, "n/a")
        raise HTTPException(
            429,
            "Lookup limit reached. Try again shortly.",
            headers={"Retry-After": str(IP_LIMIT_WINDOW_SECONDS)},
        )
    number = normalise_company_number(company_number)
    if number is None:
        _log(request_id, None, "invalid_input", call, "n/a")
        raise HTTPException(422, "Invalid company number.")
    if not COMPANIES_HOUSE_API_KEY:
        _log(request_id, number, "not_configured", call, "n/a")
        raise HTTPException(503, "Service not configured.")
    cached = _cache_get(number)
    if cached is not None:
        _log(request_id, number, "ok", call, "hit")
        return cached
    try:
        body = build_readout(number, _utcnow(), call)
    except _Fail as fail:
        _log(request_id, number, fail.outcome, call, "miss")
        raise fail.to_http() from None
    _cache_put(number, body)
    _log(request_id, number, "ok", call, "miss")
    return body


def health_fields() -> dict:
    """Extra /api/health fields. Counts and booleans only, no register data."""
    return {
        "companies_house_configured": bool(COMPANIES_HOUSE_API_KEY),
        "companies_house_quota_remaining": quota_remaining(),
    }
