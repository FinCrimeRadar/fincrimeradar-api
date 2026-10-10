# Companies House KYB Investigation Lab: API phase 1 specification

Status: DRAFT for approval. Not READY until the gates in section 1 are complete.
Author: Claude (chat), 10 October 2026. Implementation owner: Claude Code. Independent review: Codex, outside Claude Code.
Tier: CLAUDE.md section 15 high stakes (new API endpoint, trust boundary, third party data, personal data).

## 0. Product question

Given a UK company number, what does the Companies House register actually establish, what does it not establish, and what should a KYB analyst investigate next.

The output is a set of indicators with source evidence and an explicit "what the register does not establish" panel. It is never a risk score, a pass or fail verdict, or a KYC clearance.

## 1. Gates before implementation starts

1. Pratik approves this specification.
2. Codex adversarial review of sections 5 and 6 (identity verification derivation and indicator wording). These are definitions that determine an outcome, so CLAUDE.md section 7 applies.
3. RESOLVED 10 October 2026 from a primary source: see the copyright row in section 2 and the attribution string in section 4. Limits of what was resolved: that GOV.UK page speaks to the public register, not to the API by name, and the developer guidelines page states no caching, storage, attribution or reuse rules. Do not assume the Open Government Licence footer on GOV.UK guidance pages covers API data.
4. Pratik creates a Companies House developer application, generates a REST API key, restricts it where the hub allows, and sets it as `COMPANIES_HOUSE_API_KEY` on the Render service. The key never enters either repository.
5. Record the four queue fields in BACKLOG.md and promote the item to READY.

## 2. Verified source facts this spec relies on

Each was checked against a primary Companies House source on 10 October 2026.

| Fact | Source |
| --- | --- |
| Rate limit is 600 requests per five minute window per application. Exceeding it returns 429 for the rest of the window. Companies House may ban without notice applications that regularly exceed or try to bypass the limit. | developer-specs.company-information.service.gov.uk/guides/rateLimiting |
| API keys must not be embedded in code or stored in the source tree. | developer.company-information.service.gov.uk/developer-guidelines |
| Officer resources carry `identity_verification_details` with `appointment_verification_start_on` (statement supplied), `appointment_verification_end_on` (statement removed), `appointment_verification_statement_due_on` (due date), `identity_verified_on` (date an authorised corporate service provider verified the officer), `authorised_corporate_service_provider_name`, `anti_money_laundering_supervisory_bodies`, `preferred_name`. All optional. | Public Data API officerSummary resource specification |
| PSC list items and individual PSC resources carry `identity_verification_details` with the same fields as officers plus `appointment_verification_statement_date` (date from which a statement can be supplied for the notification). Corporate entity and legal person PSC detail models carry no verification fields. | Public Data API `pscModels` (`companies-house-swagger/spec/pscModels.json`, `definitions/identityVerificationDetails`; see `kyb-lab-api-verification-table.md`) |
| Register information is made available under approvals issued by Companies House under section 47 of the Copyright, Designs and Patents Act 1988 and Schedule 1 of the Database Regulations (SI 1997/3032). Companies House imposes no rules or requirements on how register information is used. Reusers must take their own legal advice on possible breach of third party copyright. | GOV.UK, Our public task, copyright and Crown copyright, last updated 9 August 2019 (https://www.gov.uk/government/publications/companies-house-accreditation-to-information-fair-traders-scheme/public-task-copyright-and-crown-copyright) |
| The register is not comprehensive. Companies House does not closely review every document at registration. Accounts receive only basic checks. Companies House accepts no responsibility for errors or omissions. | GOV.UK, Searching the Companies House register (updated 9 June 2026) |

Known pitfall, confirmed in the Companies House developer forum: `identity_verified_on` is populated only for verification through an authorised corporate service provider. Most verified appointments do not carry it. It must never be used as the verified flag.

Every other field name used below was checked against the official Companies House Swagger 2.0 files and the `api-enumerations` repository on 10 October 2026. The result for each field, with exact paths, is in `kyb-lab-api-verification-table.md`. Code only against fields confirmed there. If a field does not exist, stop and report it rather than substituting.

## 3. Endpoint

`GET /api/kyb/company/{company_number}` in the existing `fincrimeradar-api` FastAPI service.

### Input validation

1. Strip whitespace, uppercase.
2. If the value is purely numeric and shorter than eight digits, left pad with zeros to eight.
3. Accept only `^[A-Z0-9]{8}$`. Everything else returns 422 with a generic message. No echo of the raw input in the response or logs.

### Upstream calls

Fixed base host `https://api.company-information.service.gov.uk`. The validated company number is the only variable inserted into a path. Never follow URLs or `links` values returned by the upstream API (SSRF control).

1. Company profile. 404 upstream returns 404 to the client.
2. Officers list, one page, `items_per_page=100`. The Swagger and reference pages state no maximum page size, so 100 is a request, not a documented ceiling. Set `truncated: true` when `total_results` (`total_count` for filing history and charges) exceeds the number of items actually returned, so an upstream cap below 100 still produces an honest flag. No unbounded paging.
3. PSC list and PSC statements list, same paging rule. The PSC list declares `items_per_page` and `start_index` as strings in the Swagger, so the upstream projection model coerces them to integers.
4. Filing history, one page, `items_per_page=100`. Sort order is not documented, so do not rely on it: sort the returned items by `date` descending client side before deriving any indicator.
5. Charges and insolvency only if the profile indicates they exist (confirm the flag names in the OpenAPI file). This conserves quota.

Authentication: HTTP Basic, API key as username, empty password, read from the environment at startup. If the variable is missing, the endpoint returns 503 and `/api/health` reports `companies_house_configured: false`. Never log or return the key.

Timeouts: explicit connect and read timeouts on every upstream call. No retries on 429. At most one retry with backoff on connection errors only.

### Quota and abuse protection

1. Global upstream budget of 300 upstream requests per five minutes per instance, enforced before each upstream call. When exhausted, return 503 with `Retry-After`. Two facts justify 300: the Render free plan runs a single instance, and a restart resets the in-memory counters mid-window, so two consecutive process lifetimes can each spend 300 inside one Companies House window, which still totals 600 and stays within the limit. If the service ever runs more than one instance, or moves off the free plan, the budget must be divided or moved to shared storage first.
2. On any upstream 429, stop all upstream calls until the window resets and return 503 with `Retry-After`. If `X-Ratelimit-*` response headers are present, log them. They are not documented in the Swagger or enumerations, so never depend on them for control flow.
3. Per client IP limit on lookups, for example 10 per minute, returning 429. No proxy-aware client IP helper exists in the service (the three existing limiters take the first `X-Forwarded-For` entry, which a client can prepend). Build `client_ip.py` for KYB only: take the Nth-from-right `X-Forwarded-For` entry, N from the `TRUSTED_PROXY_HOPS` environment variable (default 1 for Render), and fall back to `request.client.host` when the header is missing or has fewer than N entries. Do not migrate the existing limiters in this change.
4. Response cache keyed by normalised company number, short TTL (for example 15 minutes), in memory. Document that the cache is per instance.

### Data minimisation (UK GDPR and abuse control)

1. Company centric only. No officer search, no officer appointment lookups across companies, no endpoint that takes a person as input.
2. Project upstream responses through Pydantic models that ignore unknown fields. Return only what the indicators need.
3. Per officer and PSC return: name, role or kind, appointed or notified date, resigned or ceased date, derived verification status and its supporting dates. Do not return dates of birth, nationality, occupation, residential or correspondence addresses, or `preferred_name`.
4. No persistence beyond the in-memory cache. No database writes.

## 4. Response shape

```
{
  "company_number": "string",
  "retrieved_at": "ISO 8601 UTC",
  "source": "Companies House Public Data API",
  "company": { name, status, type, incorporated_on, registered_office_changed_recently },  // registered_office_changed_recently is derived from filing history (section 6), not a profile field
  "indicators": [ Indicator ],
  "officers": { "items": [...], "truncated": bool },
  "pscs": { "items": [...], "statements": [...], "truncated": bool },
  "does_not_establish": [ "string" ],
  "attribution": "Source: Companies House public register, retrieved <retrieved_at>. FinCrimeRadar is not affiliated with or endorsed by Companies House."
}
```

`Indicator`:

```
{
  "id": "stable_snake_case_id",
  "label": "plain English",
  "evidence_state": "register_fact" | "frc_assessment",
  "evidence": { field: value, ... },
  "source_endpoint": "profile" | "officers" | "pscs" | "filing_history" | "charges" | "insolvency",
  "does_not_establish": "string"
}
```

The attribution string is fixed apart from the `retrieved_at` value. No Crown insignia or Companies House branding is used.

No numeric score, weight, rank, or overall verdict anywhere in the response.

## 5. Identity verification derivation (Codex review required)

Per current appointment (officer not resigned, PSC not ceased), derive exactly one status from `identity_verification_details`. The same table applies to officers and to PSCs:

| Condition | Status |
| --- | --- |
| `appointment_verification_start_on` present and `appointment_verification_end_on` absent | `statement_supplied` |
| `appointment_verification_end_on` present | `statement_removed` |
| No start date, `appointment_verification_statement_due_on` present and on or after today (Europe/London) | `statement_due` |
| No start date, due date present and before today | `statement_overdue` |
| `identity_verification_details` absent, or none of the above fields present | `no_verification_data` |

Rules:

1. Use the per appointment due date from the API. Never hard code a transition end date. Secondary sources disagree on whether the transition ends on 17 or 18 November 2026, and the due date varies by appointment.
2. `identity_verified_on` and the ACSP name are displayed as supporting evidence only when present. Their absence never lowers the status.
3. `no_verification_data` is not the same as unverified. Display it as "no verification data returned by the register", not as a failure. Corporate officers and some appointment types fall outside the current regime.
4. Wording must say the statement was supplied to the register. It must not say the person is "verified as legitimate", "cleared", or similar.

PSC rules:

5. The derivation table is applied to PSCs unchanged, using `appointment_verification_start_on`, `appointment_verification_end_on` and `appointment_verification_statement_due_on` from the PSC `identity_verification_details`.
6. `appointment_verification_statement_date` is supporting evidence only, displayed as "statement can be supplied from <date>". It never changes the derived status.
7. Only PSCs of kind `individual-person-with-significant-control` derive from their data. Corporate entity and legal person PSC detail models carry no verification fields in the Public Data API, so PSCs of those kinds always derive to `no_verification_data`. The same applies to every other PSC kind (beneficial owner and super secure kinds), which are treated as outside the individual PSC regime until a source shows otherwise.

Fixed `does_not_establish` text for this indicator (final wording subject to Codex review): "A supplied verification statement means the individual's identity was verified for this appointment. It does not establish who ultimately owns or controls the company, whether the ownership filings are accurate, the source of funds, or that customer due diligence is complete."

## 6. Indicators, phase 1

Report facts and counts only. No thresholds unless a verified source supplies one.

| id | Evidence state | Basis |
| --- | --- | --- |
| `company_status_not_active` | register_fact | profile status |
| `accounts_overdue` | register_fact | profile accounts overdue flag |
| `confirmation_statement_overdue` | register_fact | profile confirmation statement overdue flag |
| `idv_statement_overdue` | register_fact | any current appointment with `statement_overdue` |
| `idv_statement_removed` | register_fact | any current appointment with `statement_removed` |
| `psc_statement_present` | register_fact | PSC statements list non empty; display the statement description from the Companies House enumeration file, never paraphrased |
| `recent_officer_changes` | register_fact | counts of appointments and resignations in the last 12 months, shown as numbers with no "high churn" label |
| `recent_registered_office_change` (PROVISIONAL) | register_fact | filings in the last 12 months where `category == "address"` AND `description` exactly equals one of the six registered office description identifiers below. See the provisional rule after this table. |
| `previous_names` | register_fact | profile previous names with dates |
| `insolvency_history` | register_fact | insolvency resource present |
| `outstanding_charges` | register_fact | charges with outstanding status |
| `recently_incorporated` | register_fact | incorporated within the last 12 months, date shown |

Registered office description identifiers (from `filing_history_descriptions.yml` in `companieshouse/api-enumerations`, pinned commit `05067abc50da2c9277bb5d3504187f55e5ec9e24`, lines 902 to 907 at that commit):

1. `change-registered-office-address-company`
2. `change-registered-office-address-company-with-date-old-address`
3. `change-registered-office-address-company-with-date-old-address-new-address`
4. `change-registered-office-address-limited-liability-partnership`
5. `change-registered-office-address-limited-liability-partnership-with-date-old-address`
6. `change-registered-office-address-limited-liability-partnership-with-date-old-address-new-address`

The overseas company identifier `change-registered-office-addres-overseas-company` (sic) means a place of business address change and is excluded.

Provisional rule: the Swagger types `filingHistoryItem.description` as a plain string and the filing `type` as an unconstrained string, so no OpenAPI document confirms that live responses carry these identifiers. If the first live response shows `description` as rendered text instead of the identifier, disable this indicator and report. Never fall back to category only matching: `category == "address"` also covers filings that are not registered office changes.

Global `does_not_establish` list, grounded in the Companies House disclaimer:

1. The register is not comprehensive and Companies House does not closely review every document at registration.
2. Accounts filed at Companies House receive only basic checks.
3. Absence of an indicator is not evidence that the company is low risk.
4. This readout is not customer due diligence and does not replace it.

`frc_assessment` indicators are out of scope for phase 1. Add them only in a later, separately reviewed change.

## 7. Logging and observability

Structured log per request: request id, normalised company number (a public identifier), outcome code, upstream call count, upstream latency, cache hit or miss, quota remaining. Never log officer or PSC data, the API key, or raw invalid input. Extend `/api/health` with `companies_house_configured` and current quota headroom. No officer data in health output.

## 8. Tests (pytest, mocked upstream, existing CI workflow)

1. Validation: valid eight digit, short numeric padded, prefixed forms such as `SC123456`, lowercase normalised, rejects path traversal, URL encoded input, empty, overlong, unicode.
2. Each indicator fires and does not fire, from fixture responses.
3. Section 5 derivation: one test per table row, plus `identity_verified_on` absent with start date present returns `statement_supplied`, plus resigned officers excluded, plus today boundary in Europe/London.
4. Data minimisation: response never contains date of birth, nationality, occupation, address or preferred name keys, asserted recursively.
5. Upstream failure: 404, 401 (bad key returns 503 without leaking detail), 429 (stops calls, 503 with Retry-After), 5xx, timeout.
6. Missing key fails closed with 503.
7. Global budget and per IP limit enforced. Cache hit makes zero upstream calls.
8. Truncation flag set when totals exceed returned items.
9. Links in upstream responses are never fetched (assert the HTTP client is called only with expected paths).
10. PSC derivation: the table rows apply to an individual PSC; `appointment_verification_statement_date` never changes status; a corporate entity PSC and a legal person PSC each derive to `no_verification_data` (one test per kind), including when verification-like fields are injected into the fixture.
11. Registered office indicator: fires for each of the six identifiers, does not fire for category `address` with any other description, does not fire for the overseas company identifier, and is disabled cleanly when `description` is rendered text.
12. Client IP helper (`tests/test_client_ip.py`): Nth-from-right selection, `TRUSTED_PROXY_HOPS` parsing and default, spoofed prepended entries do not change the key, fallback to `request.client.host` when the header is missing or too short.
13. Filing history is sorted by `date` client side regardless of upstream order; PSC string paging fields are coerced; `truncated` is set from totals versus items returned.

## 9. Delivery sequence

1. API pull request only. Frontend page is a separate later pull request.
2. `/code-review` on the real pull request, resolve material findings.
3. Pratik runs the manual Codex adversarial review on the final diff.
4. Merge, deploy, then live smoke against production with one real active company and one invalid number, confirming response shape and data minimisation on the deployed service. Our own 503 and `Retry-After` behaviour is proven by tests, not by live smoke (CLAUDE.md section 17).
5. Publication verification sequence for both repositories touched.

## 10. Out of scope for phase 1

Frontend page, shared address density, officer network views, bulk or streaming ingestion, any scoring, any stored history, disqualified director search.

## 11. Implementation decisions (approved 10 October 2026)

1. Layout: `routes_kyb_lab.py`, `client_ip.py`, `tests/test_kyb_lab.py`, `tests/test_client_ip.py`, `tests/fixtures/kyb/*.json`. `main.py` changes only to include the router and add the health fields.
2. Tooling: `requirements-dev.txt` with pinned pytest, httpx and ruff. CI gains a step running `ruff check` and `ruff format --check` on the new files only, then `pytest tests/`. Existing CI steps are unchanged. No mypy for now, to avoid blast radius on existing files.
3. Pydantic: our own response models use `strict=True, extra="forbid"`. Upstream projection models use `extra="ignore"`.
4. The three existing rate limiters (`main.py`, `routes_scenario_lab.py`, `routes_sar_sandbox.py`) are not changed here. Their spoofable `X-Forwarded-For` handling is tracked separately in the frontend repository `BACKLOG.md`.
