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
3. Open item, must be resolved from a primary Companies House source: the reuse licence and any conditions that apply to data obtained through the Public Data API, including attribution wording. Do not assume the Open Government Licence footer on GOV.UK guidance pages covers API data.
4. Pratik creates a Companies House developer application, generates a REST API key, restricts it where the hub allows, and sets it as `COMPANIES_HOUSE_API_KEY` on the Render service. The key never enters either repository.
5. Record the four queue fields in BACKLOG.md and promote the item to READY.

## 2. Verified source facts this spec relies on

Each was checked against a primary Companies House source on 10 October 2026.

| Fact | Source |
| --- | --- |
| Rate limit is 600 requests per five minute window per application. Exceeding it returns 429 for the rest of the window. Companies House may ban without notice applications that regularly exceed or try to bypass the limit. | developer-specs.company-information.service.gov.uk/guides/rateLimiting |
| API keys must not be embedded in code or stored in the source tree. | developer.company-information.service.gov.uk/developer-guidelines |
| Officer resources carry `identity_verification_details` with `appointment_verification_start_on` (statement supplied), `appointment_verification_end_on` (statement removed), `appointment_verification_statement_due_on` (due date), `identity_verified_on` (date an authorised corporate service provider verified the officer), `authorised_corporate_service_provider_name`, `anti_money_laundering_supervisory_bodies`, `preferred_name`. All optional. | Public Data API officerSummary resource specification |
| PSC resources carry equivalent verification fields for the notification. | Streaming API pscStream resource specification |
| The register is not comprehensive. Companies House does not closely review every document at registration. Accounts receive only basic checks. Companies House accepts no responsibility for errors or omissions. | GOV.UK, Searching the Companies House register (updated 9 June 2026) |

Known pitfall, confirmed in the Companies House developer forum: `identity_verified_on` is populated only for verification through an authorised corporate service provider. Most verified appointments do not carry it. It must never be used as the verified flag.

Not yet verified, Claude Code must check against the official OpenAPI file before coding: every other field name used below, including company profile fields for overdue accounts, overdue confirmation statement, charges and insolvency flags, previous names, and filing history type codes. Download the spec from developer-specs.company-information.service.gov.uk (OpenAPI download link in the left navigation). Code only against fields confirmed there. If a field in this brief does not exist, stop and report it rather than substituting.

## 3. Endpoint

`GET /api/kyb/company/{company_number}` in the existing `fincrimeradar-api` FastAPI service.

### Input validation

1. Strip whitespace, uppercase.
2. If the value is purely numeric and shorter than eight digits, left pad with zeros to eight.
3. Accept only `^[A-Z0-9]{8}$`. Everything else returns 422 with a generic message. No echo of the raw input in the response or logs.

### Upstream calls

Fixed base host `https://api.company-information.service.gov.uk`. The validated company number is the only variable inserted into a path. Never follow URLs or `links` values returned by the upstream API (SSRF control).

1. Company profile. 404 upstream returns 404 to the client.
2. Officers list, one page, maximum page size the API allows. If the total exceeds what was returned, set `truncated: true` on the officers section. No unbounded paging.
3. PSC list and PSC statements list, same paging rule.
4. Filing history, one page, most recent first.
5. Charges and insolvency only if the profile indicates they exist (confirm the flag names in the OpenAPI file). This conserves quota.

Authentication: HTTP Basic, API key as username, empty password, read from the environment at startup. If the variable is missing, the endpoint returns 503 and `/api/health` reports `companies_house_configured: false`. Never log or return the key.

Timeouts: explicit connect and read timeouts on every upstream call. No retries on 429. At most one retry with backoff on connection errors only.

### Quota and abuse protection

1. Global upstream budget well below the Companies House limit, for example 400 upstream requests per five minutes, enforced before each upstream call. When exhausted, return 503 with `Retry-After`.
2. On any upstream 429, stop all upstream calls until the window resets and return 503 with `Retry-After`.
3. Per client IP limit on lookups, for example 10 per minute, returning 429. Use the proxy-aware client IP the service already uses for SAR Sandbox limits if one exists; do not trust raw `X-Forwarded-For` without that.
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
  "company": { name, status, type, incorporated_on, registered_office_changed_recently },
  "indicators": [ Indicator ],
  "officers": { "items": [...], "truncated": bool },
  "pscs": { "items": [...], "statements": [...], "truncated": bool },
  "does_not_establish": [ "string" ],
  "attribution": "string, wording per gate 3"
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

No numeric score, weight, rank, or overall verdict anywhere in the response.

## 5. Identity verification derivation (Codex review required)

Per current appointment (officer not resigned, PSC not ceased), derive exactly one status from `identity_verification_details`:

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
| `recent_registered_office_change` | register_fact | registered office change filings in the last 12 months (confirm type code in the OpenAPI enumerations) |
| `previous_names` | register_fact | profile previous names with dates |
| `insolvency_history` | register_fact | insolvency resource present |
| `outstanding_charges` | register_fact | charges with outstanding status |
| `recently_incorporated` | register_fact | incorporated within the last 12 months, date shown |

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

## 9. Delivery sequence

1. API pull request only. Frontend page is a separate later pull request.
2. `/code-review` on the real pull request, resolve material findings.
3. Pratik runs the manual Codex adversarial review on the final diff.
4. Merge, deploy, then live smoke against production with one real active company and one invalid number, confirming quota headers, response shape and data minimisation on the deployed service (CLAUDE.md section 17).
5. Publication verification sequence for both repositories touched.

## 10. Out of scope for phase 1

Frontend page, shared address density, officer network views, bulk or streaming ingestion, any scoring, any stored history, disqualified director search.
