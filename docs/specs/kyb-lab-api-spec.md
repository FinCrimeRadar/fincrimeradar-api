# Companies House KYB Investigation Lab: API phase 1 specification

Status: v1.5, pending Codex re-review. v1.4: v1.3 withdrawn per Codex PR review 2 (404 not documented as empty); revisit only with Companies House documentation. Base: FROZEN v1.0 (Codex section 7 review PASSED, reviews 1 to 3), v1.1 filing truncation, the v1.2 remediation of Codex PR review 1 (`kyb-lab-codex-pr-review-1.md`), and the v1.4 remediation of Codex PR review 2 (`kyb-lab-codex-pr-review-2.md`). Any spec change needs a new review.
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

### Scope of interpretation

Phase 1 interprets only the eight company profile types listed below. It does not cover UK-registered companies generally. Scope is decided from the profile `type`, using an allowlist built from the `company_type` mapping in `constants.yml` of `companieshouse/api-enumerations`, pinned to commit `05067abc50da2c9277bb5d3504187f55e5ec9e24`. Any `type` not on the allowlist is unsupported, including a value that appears in neither the Swagger nor the pinned constants.

In scope (8 types, all present in both the Swagger `companyProfile.type` enum and the pinned constants):

| `type` | Register description (constants) |
| --- | --- |
| `ltd` | Private limited company |
| `plc` | Public limited company |
| `private-unlimited` | Private unlimited company |
| `private-unlimited-nsc` | Private unlimited company without share capital |
| `private-limited-guarant-nsc` | Private limited by guarantee without share capital |
| `private-limited-guarant-nsc-limited-exemption` | Private limited company by guarantee without share capital, use of 'Limited' exemption |
| `private-limited-shares-section-30-exemption` | Private limited company, use of 'Limited' exemption |
| `llp` | Limited liability partnership |

Excluded (28 types, every other value in the pinned `company_type` mapping), with the reason each is excluded:

| `type` | Reason |
| --- | --- |
| `registered-overseas-entity`, `uk-establishment`, `oversea-company` | Overseas or foreign structures, excluded by decision |
| `limited-partnership`, `scottish-partnership` | Partnerships that are neither companies nor LLPs |
| `eeig`, `eeig-establishment`, `ukeig` | Economic interest groupings, not companies |
| `icvc-securities`, `icvc-warrant`, `icvc-umbrella`, `investment-company-with-variable-capital` | Investment vehicles with a separate regulatory regime |
| `registered-society-non-jurisdictional`, `industrial-and-provident-society`, `northern-ireland-other` | Societies and credit unions |
| `royal-charter`, `charitable-incorporated-organisation`, `scottish-charitable-incorporated-organisation`, `further-education-or-sixth-form-college-corporation` | Bodies not incorporated under the Companies Acts |
| `converted-or-closed` | Closed record, not a live company |
| `old-public-company`, `northern-ireland`, `protected-cell-company`, `assurance-company`, `unregistered-company`, `other`, `european-public-limited-liability-company-se`, `united-kingdom-societas` | Cannot be placed in the UK company or LLP scope with confidence from the enumerations alone |

An unsupported type returns the raw profile facts plus the notice "FinCrimeRadar does not interpret indicators for this entity type in phase 1". No indicators, no identity verification derivation, and no upstream calls after the profile (this conserves quota).

For an in scope type, `company_status` is returned raw. There is no indicator derived from status. Current obligation outputs (`accounts_overdue`, `confirmation_statement_overdue` and every identity verification status) are derived only when `company_status == "active"`. For any other status, the readout shows the raw register values, together with `company.status` and `company.date_of_cessation` when present, labelled "register-reported values; FinCrimeRadar does not interpret current obligations for status <status>", and no derived status or obligation indicator.

### Upstream calls

Fixed base host `https://api.company-information.service.gov.uk`. The validated company number is the only variable inserted into a path. Never follow URLs or `links` values returned by the upstream API (SSRF control).

1. Company profile. 404 upstream returns 404 to the client. If the `type` is unsupported, stop here.
2. Officers list, one page, `items_per_page=100`. The Swagger and reference pages state no maximum page size, so 100 is a request, not a documented ceiling. Set `truncated: true` when `total_results` (`total_count` for filing history and charges) exceeds the number of items actually returned, so an upstream cap below 100 still produces an honest flag. No unbounded paging.
3. PSC list and PSC statements list, same paging rule. The PSC list declares `items_per_page` and `start_index` as strings in the Swagger, so the upstream projection model coerces them to integers.
4. Filing history, one page, `items_per_page=100`. Sort order is not documented, so do not rely on it: sort the returned items by `date` descending client side before deriving any indicator.
5. Charges only if the profile `links` object contains a `charges` key, and insolvency only if it contains an `insolvency` key. Only key presence is checked: the URL values are never followed or used. The deprecated `has_charges` and `has_insolvency_history` flags are not used. Key presence as a reliable signal is unconfirmed until a live response is seen. This conserves quota.

6. Source status (v1.2). Only a profile 404 returns a 404 response. A 404 on any other call (officers, PSC list, filing history, charges, insolvency) is not evidence that the collection is empty: it means the register did not return that section. Each collection therefore carries `source_status`, `available` when the call returned 200 and `not_returned` when it returned 404. When `not_returned`: the arrays stay empty, `truncated` is `null` (never `false`), and nothing absence-dependent is derived from that collection. No identity verification indicator is computed from a missing officer or PSC list, no `active_psc_statements` or `ceased_psc_statements` conclusion is drawn from a missing statements list, and no outstanding charge or insolvency conclusion is drawn from a missing charges or insolvency resource. The response carries one fixed notice, `not_returned_notice`: "This register section was not returned. FinCrimeRadar draws no conclusion from its absence." Optional resources (charges, insolvency) are requested only when the profile `links` key is present, so for them the response object is `null` when no link was present (not requested) and carries `source_status` when requested. A PSC statements 404 is treated like every other subresource 404: `statements_source_status` is `not_returned`, no statements, and `pscs.truncated` is `null`. Note (observation, not a rule): in the live capture of 11 October 2026 that endpoint returned 404 for a company with no statements, for all 3 captured companies (`kyb-lab-api-verification-table.md`, section "Live verification"). The Companies House specification documents 200 for the statements resource and 404 only as "Resource not found", so a 404 is not documented as an authoritative empty list and a different cause could hide a statement. A statements 5xx, timeout or unparseable body fails the request (502 or 504 with no readout). For an unsupported entity type no collection is requested, so every collection is reported `not_returned` with `truncated: null`: officers, both PSC lists, `registered_office_change_filings` (`{ source_status: "not_returned", items: [], truncated: null }`), `charges` and `insolvency` (`{ source_status: "not_returned" }`). None of these is a bare `null` for an unsupported type.

Authentication: HTTP Basic, API key as username, empty password, read from the environment at startup. If the variable is missing, the endpoint returns 503 and `/api/health` reports `companies_house_configured: false`. Never log or return the key.

Timeouts: explicit connect and read timeouts on every upstream call. No retries on 429. At most one retry with backoff on connection errors only.

### Quota and abuse protection

1. Global upstream budget of 300 upstream requests per five minutes per instance, enforced before each upstream call. When exhausted, return 503 with `Retry-After`. Two facts justify 300: the Render free plan runs a single instance, and a restart resets the in-memory counters mid-window, so two consecutive process lifetimes can each spend 300 inside one Companies House window, which still totals 600 and stays within the limit. If the service ever runs more than one instance, or moves off the free plan, the budget must be divided or moved to shared storage first.
2. On any upstream 429, stop all upstream calls until the window resets and return 503 with `Retry-After`. If `X-Ratelimit-*` response headers are present, log them. They are not documented in the Swagger or enumerations, so never depend on them for control flow.
3. Per client limits (v1.2). Two controls, both keyed on the client IP from item 3a.
   - Weighted allowance: 60 upstream calls per client per five minutes (20% of the global 300), charged by the upstream calls actually made, before each call. A cache hit costs nothing. When a client has spent its allowance the response is 429 with `Retry-After`. This replaces lookups-per-minute as the quota control, because one uncached lookup costs five to seven upstream calls and ten lookups a minute would let a single client drain the global budget in about 43 lookups.
   - Burst cap: 10 lookups per minute, returning 429 on the 11th in the window, checked before input validation as before.
   The budget and both client controls are per instance, in process memory. A single instance is assumed (Render free plan). Shared state (for example Redis) is required before the service scales out, because per instance counters multiply the effective global budget.
   3a. Client IP (`client_ip.py`, KYB only): use `CF-Connecting-IP` when it is present (Render fronts the service with Cloudflare, which sets that header). Otherwise take the Nth-from-right `X-Forwarded-For` entry, N from `TRUSTED_PROXY_HOPS` (default 1), and fall back to `request.client.host` when the header is missing or has fewer than N entries. Do not migrate the existing limiters in this change. If the service is ever reachable without Cloudflare in front, a caller can set `CF-Connecting-IP` themselves, so the header must be ignored in that deployment. Client keys must parse as an IP address; invalid header values are ignored. Each candidate (`CF-Connecting-IP` and the selected `X-Forwarded-For` entry) is stripped, rejected if longer than 45 characters, parsed with `ipaddress.ip_address` and keyed by its normalised string form. An invalid `CF-Connecting-IP` falls through to the `X-Forwarded-For` rule, and an invalid `X-Forwarded-For` entry falls through to `request.client.host`, so no limiter key is longer than 45 characters.
   3b. Limiter state is hard bounded: each client map is an LRU of at most 10,000 keys that evicts the oldest key, with no full map scan under the lock. Eviction forgets a client's counters, so an attacker rotating more than 10,000 keys can reset their own allowance; the per client controls limit honest use and the global budget remains the backstop.
4. Response cache keyed by normalised company number, short TTL (for example 15 minutes), in memory. Document that the cache is per instance.

### Data minimisation (UK GDPR and abuse control)

1. Company centric only. No officer search, no officer appointment lookups across companies, no endpoint that takes a person as input.
2. Project upstream responses through Pydantic models that ignore unknown fields. Return only what the indicators need.
3. Per officer and PSC return: name, role or kind, appointed or notified date, resigned or ceased date, derived verification status (or `not_interpreted`) and its supporting dates. Do not return dates of birth, nationality, occupation, residential or correspondence addresses, or `preferred_name`.
4. No persistence beyond the in-memory cache. No database writes.

## 4. Response shape

```
{
  "company_number": "string",
  "retrieved_at": "ISO 8601 UTC",
  "source": "Companies House Public Data API",
  "company": { name, status, type, date_of_creation, date_of_cessation },   // all returned raw, as the register names them
  "interpretation": {
    "scope": "interpreted" | "unsupported_entity_type",
    "current_obligations": "interpreted" | "register_reported_only" | "not_applicable",
    "notice": "string | null"
  },
  "register_reported_values": { ... } | null,                   // only when current_obligations is register_reported_only
  "indicators": [ Indicator ],
  "officers": { "source_status": "available" | "not_returned", "items": [ OfficerItem ], "events": [ DatedEvent ], "truncated": bool | null },
  "pscs": { "records_source_status": "available" | "not_returned", "statements_source_status": "available" | "not_returned", "items": [ PscItem ], "active_statements": [...], "ceased_statements": [...], "truncated": bool | null },
  "registered_office_change_filings": { "source_status": "available" | "not_returned", "items": [ { "date", "transaction_id", "description_identifier" } ], "truncated": bool | null } | null,   // PROVISIONAL, truncated added in v1.1, source_status in v1.2; an unsupported type returns not_returned, never null (v1.4)
  "charges": { "source_status": "available" | "not_returned" } | null,       // null when a supported type's profile has no charges link; not_returned for an unsupported type (v1.4)
  "insolvency": { "source_status": "available" | "not_returned" } | null,   // null when a supported type's profile has no insolvency link; not_returned for an unsupported type (v1.4)
  "not_returned_notice": "This register section was not returned. FinCrimeRadar draws no conclusion from its absence.",
  "does_not_establish": [ "string" ],
  "attribution": "Source: Companies House public register, retrieved <retrieved_at>. FinCrimeRadar is not affiliated with or endorsed by Companies House."
}
```

`OfficerItem` and `PscItem` each carry `identity_verification`:

```
{
  "eligibility": "derivation_eligible" | "not_interpreted",
  "status": "statement_supplied" | "statement_removed" | "statement_due" | "statement_overdue"
          | "verification_data_conflict" | "no_statement_status_fields" | null,   // null when not_interpreted
  "display": "string",
  "evidence": { raw fields the register returned, minimised per section 3 },
  "evidence_label": "string | null",
  "does_not_establish": "string"
}
```

`truncated` is `null` whenever the collection is `not_returned`. For `pscs` it is `null` if either the PSC list or the statements list is `not_returned`, otherwise it is true if either is truncated.

`DatedEvent`: `{ "date", "event": "appointed" | "resigned", "name", "role" }`, built from the officers items, sorted by date descending. No "recent" flag, no window and no count of recent events anywhere in the response. Any future time window is an `frc_assessment` and needs separate review.

`Indicator`:

```
{
  "id": "stable_snake_case_id",
  "label": "plain English",
  "evidence_state": "register_fact" | "frc_assessment",
  "evidence": { field: value, ... },
  "source_endpoints": [ "profile" | "officers" | "pscs" | "filing_history" | "charges" | "insolvency" ],   // v1.2: list, derived from the contributing records, in the order officers, pscs
  "does_not_establish": "string"
}
```

The attribution string is fixed apart from the `retrieved_at` value. No Crown insignia or Companies House branding is used.

No numeric score, weight, rank, or overall verdict anywhere in the response.

## 5. Identity verification derivation (Codex review required)

### 5.1 Derivation eligibility

A status is derived only for a `derivation_eligible` record, meaning one that meets all of these:

1. The company is in scope under section 3 and `company_status == "active"`.
2. For an officer: the role is exactly `director`, `llp-member` or `llp-designated-member` (the natural-person roles), and `resigned_on` is absent.
3. For a PSC: the kind is exactly `individual-person-with-significant-control`, and `ceased_on` is absent.

Every other record gets `eligibility: "not_interpreted"` and `status: null`. This includes every `corporate-*` role, every other officer role, every other PSC kind, every resigned or ceased record, and every record of a company that is out of scope or not active. If verification fields are present on a not_interpreted record, show them raw with the label "fields returned by the register; not interpreted in phase 1". A status is never derived for them.

### 5.2 Derivation for derivation_eligible records

"Today" is the date of `retrieved_at` in Europe/London. Evaluate in order. The first match wins.

| Order | Condition | Status |
| --- | --- | --- |
| a | Any of: `appointment_verification_start_on` is after today; `appointment_verification_end_on` is after today; end is before start; end is present and start is absent; start, end and due are all present and due is after end; for a PSC, `appointment_verification_statement_date` is after `appointment_verification_statement_due_on` | `verification_data_conflict` |
| b | Start present, end absent | `statement_supplied` |
| c | Start present, end present | `statement_removed` |
| d | Start and end absent, `appointment_verification_statement_due_on` present and on or after today | `statement_due` |
| e | Start and end absent, due present and before today | `statement_overdue` |
| f | Start, end and due all absent | `no_statement_status_fields` |

The same table applies to officers and to PSCs, using the fields in each record's `identity_verification_details`. There is no special case by officer role or PSC kind inside the derivation. Derivation eligibility in 5.1 is the only place role or kind matters.

Rules:

1. Use the per appointment due date from the API. Never hard code a transition end date. Secondary sources disagree on whether the transition ends on 17 or 18 November 2026, and the due date varies by appointment.
2. On every status, show `identity_verified_on`, the ACSP name (`authorised_corporate_service_provider_name`) and `anti_money_laundering_supervisory_bodies` when present, as evidence only. They never change the status, and their absence never lowers it.
3. Show `appointment_verification_statement_date` as "statement can be supplied from <date>". It can trigger `verification_data_conflict` under row a (when it is after the due date) but it never sets any other status.
4. Never use `identity_verified_on` as a verified flag. It is populated only for verification through an authorised corporate service provider.

### 5.3 Display wording

| Status | Display text |
| --- | --- |
| `statement_supplied` | "The register records that an identity verification statement was supplied for this role on <date>, linking a verified identity to it." (`<date>` is `appointment_verification_start_on`) |
| `statement_removed` | "The register records that the verification statement for this role was removed on <date>." (`<date>` is `appointment_verification_end_on`) |
| `verification_data_conflict` | "The register returned verification dates that are inconsistent or incomplete. FinCrimeRadar does not interpret them; check the register record directly." |
| `no_statement_status_fields` | "No verification statement dates were returned for this role. This does not establish whether the person is verified, unverified, compliant, non-compliant or outside scope." |
| `statement_due` | "The register shows a verification statement for this role is due by <date>. None is recorded as supplied at the time of retrieval." (`<date>` is `appointment_verification_statement_due_on`) |
| `statement_overdue` | "The register shows a verification statement for this role was due by <date>, and none is recorded as supplied. This is a register record, not a finding that the person is unverified or that an offence has occurred. Check the register record directly before relying on it." (`<date>` is `appointment_verification_statement_due_on`) |

Per record `does_not_establish`: "A register record of a supplied statement is not evidence that the person, the company or its activity is legitimate. It does not establish who ultimately owns or controls the company, whether ownership filings are accurate, the source of funds, or that customer due diligence is complete."

## 6. Indicators, phase 1

Report facts only. No thresholds, windows or counts of "recent" events. Indicators are produced only for in scope entity types. Indicators marked (active only) are produced only when `company_status == "active"`.

| id | Evidence state | Basis |
| --- | --- | --- |
| `accounts_overdue` (active only) | register_fact | profile `accounts.next_accounts.overdue` is true. The older `accounts.overdue` is deprecated in the Swagger and is not used. |
| `confirmation_statement_overdue` (active only) | register_fact | profile `confirmation_statement.overdue` is true |
| `idv_statement_overdue` (active only) | register_fact | any `derivation_eligible` record derived to `statement_overdue` |
| `idv_statement_removed` (active only) | register_fact | any `derivation_eligible` record derived to `statement_removed` |
| `active_psc_statements` | register_fact | PSC statements with no `ceased_on`; show each statement description verbatim from the enumerations, with its `notified_on` date |
| `ceased_psc_statements` | register_fact | PSC statements with `ceased_on`; show each description verbatim with its `notified_on` and `ceased_on` dates |
| `previous_names` | register_fact | profile previous names with dates |
| `insolvency_history` | register_fact | insolvency resource present |
| `outstanding_charges` | register_fact | charges with status `outstanding` |

Company status is not an indicator. It is returned raw in `company.status`.

PSC statement descriptions: the profile `statement` code maps to the `statement_description` table in `psc_descriptions.yml` at the pinned commit. Show the enumeration text verbatim, substituting only the `{linked_psc_name}` placeholder where the text contains it. For a code not in the table, show the raw code and no description. Never paraphrase.

Dated lists returned instead of thresholds (all in the response, none with a flag):

1. `officers.events`: appointments and resignations with dates.
2. `registered_office_change_filings`: filings where `category == "address"` AND `description` exactly equals one of the six identifiers below, with their dates, as `{ items, truncated }`. `truncated` is true when the filing history `total_count` exceeds the number of filing items returned, the same total versus returned rule as the officers list (v1.1). A true flag means older registered office changes may be missing. PROVISIONAL, see the rule below.
3. `company.date_of_creation`.

Registered office description identifiers (from `filing_history_descriptions.yml` in `companieshouse/api-enumerations`, pinned commit `05067abc50da2c9277bb5d3504187f55e5ec9e24`, lines 902 to 907 at that commit):

1. `change-registered-office-address-company`
2. `change-registered-office-address-company-with-date-old-address`
3. `change-registered-office-address-company-with-date-old-address-new-address`
4. `change-registered-office-address-limited-liability-partnership`
5. `change-registered-office-address-limited-liability-partnership-with-date-old-address`
6. `change-registered-office-address-limited-liability-partnership-with-date-old-address-new-address`

Each of the six was checked in `filing_history_descriptions.yml` at the pinned commit and renders as "Registered office address changed" (with old address, new address and date placeholders in the longer variants). Three are for companies (`...-company...`) and three are for limited liability partnerships (`...-limited-liability-partnership...`).

Excluded identifiers, none of which is a registered office change: the register inspection address variants (`change-sail-address-company...`, `change-sail-address-limited-liability-partnership...`, which render as "Register inspection address has been changed" or "Location of register of charges has been changed"), the register move variants (`move-registers-to-sail-...`, `move-registers-to-registered-office-...`), the registered office situation variants (`change-registered-office-situation-...`, a change of jurisdiction), and `change-registered-office-addres-overseas-company` (sic), which means a place of business address change.

Provisional rule: the Swagger types `filingHistoryItem.description` as a plain string and the filing `type` as an unconstrained string, so no OpenAPI document confirms that live responses carry these identifiers. If the first live response shows `description` as rendered text instead of the identifier, disable the `registered_office_change_filings` list (return null) and report. Never fall back to category only matching: `category == "address"` also covers filings that are not registered office changes.

Global `does_not_establish` list (returned verbatim):

1. The register is not comprehensive and may contain inaccuracies, errors or omissions. Companies House accepts no responsibility for their consequences.
2. Companies House checks filings for completeness and validity but does not closely review every document. Accounts receive only basic checks.
3. Register status does not establish that a company is trading. Dormant accounts are a separate filing fact.
4. Identity verification records do not establish legitimacy, ownership accuracy or completed CDD.
5. The absence of an indicator is not evidence of low risk.
6. This readout is not customer due diligence and does not replace it.

`frc_assessment` indicators are out of scope for phase 1. Add them only in a later, separately reviewed change.

## 7. Logging and observability

Structured log per request: request id, normalised company number (a public identifier), outcome code, upstream call count, upstream latency, cache hit or miss, quota remaining. Never log officer or PSC data, the API key, or raw invalid input. Extend `/api/health` with `companies_house_configured` and current quota headroom. No officer data in health output.

## 8. Tests (pytest, mocked upstream, existing CI workflow)

1. Validation: valid eight digit, short numeric padded, prefixed forms such as `SC123456`, lowercase normalised, rejects path traversal, URL encoded input, empty, overlong, unicode.
2. Each indicator in section 6 fires and does not fire, from fixture responses. `accounts_overdue` is tested against `accounts.next_accounts.overdue` only: a fixture with deprecated `accounts.overdue` true and `next_accounts.overdue` false must not fire it, and the reverse must. Charges and insolvency endpoints are called only when `links.charges` and `links.insolvency` keys are present, and never when only the deprecated `has_charges` or `has_insolvency_history` flags are true.
3. Identity verification derivation, one test per rule:
   - 8a: one test for each of the four conflict triggers (start after today; end after today; end before start; end present and start absent), each asserting `verification_data_conflict`, a fifth test for start, end and due all present with due after end, a sixth test for a PSC whose `appointment_verification_statement_date` is after its `appointment_verification_statement_due_on`, plus a test that conflict wins over a start and end that would otherwise match 8b or 8c
   - 8b `statement_supplied` (start present, end absent), including with `identity_verified_on` absent
   - 8c `statement_removed`
   - 8d `statement_due`, plus a test that a due date equal to today still derives `statement_due`
   - 8e `statement_overdue`
   - 8f `no_statement_status_fields`, plus a test with ACSP fields only, asserting `no_statement_status_fields` with the ACSP evidence shown
   - the today boundary in Europe/London, including a `retrieved_at` instant near midnight UTC in British Summer Time
   - `appointment_verification_statement_date` is shown as "statement can be supplied from <date>", never changes the status on its own, and only produces `verification_data_conflict` when it is after the due date
4. Derivation eligibility: natural-person officer roles `director`, `llp-member` and `llp-designated-member` derive; a resigned officer is `not_interpreted`; a corporate officer (for example `corporate-director`) carrying verification fields is `not_interpreted` with the raw fields shown and the label "fields returned by the register; not interpreted in phase 1"; every other officer role is `not_interpreted`. For PSCs: `individual-person-with-significant-control` derives; a ceased PSC is `not_interpreted`; one fixture per other PSC kind in the Swagger kind enum with no verification fields asserts `not_interpreted`; one non-individual fixture that carries the fields asserts `not_interpreted` with the raw fields shown and no derived status.
5. Scope: one test per excluded `type` in section 3 asserting the notice, the raw profile facts, no indicators, no identity verification derivation and no upstream calls after the profile; an unknown `type` value is treated as unsupported; each of the eight in scope types is interpreted.
6. Status: a dissolved company returns raw `company.status`, no `accounts_overdue` or `confirmation_statement_overdue` indicator, no identity verification status on any record, and `register_reported_values` carrying `company.status`, `company.date_of_cessation` when present, and the label "register-reported values; FinCrimeRadar does not interpret current obligations for status dissolved". No `company_status_not_active` indicator exists for any status.
7. PSC statements: a statement without `ceased_on` appears only under `active_statements` and fires `active_psc_statements`; a ceased only statement list produces no active statements and fires only `ceased_psc_statements`; each statement shows its `notified_on` date (and `ceased_on` where ceased); descriptions are verbatim from the pinned enumerations; an unknown statement code shows the raw code.
8. Dated lists: `officers.events` contains appointments and resignations with dates in descending order and no "recent" flag or count anywhere in the response; `company.date_of_creation` is returned; `registered_office_change_filings` fires for each of the six identifiers, excludes category `address` with any other description, excludes the SAIL, move registers, situation and overseas identifiers, and returns null when `description` is rendered text.
9. Global `does_not_establish` equals the six items in section 6, verbatim.
10. Data minimisation: response never contains date of birth, nationality, occupation, address or preferred name keys, asserted recursively.
11. Upstream failure: 404, 401 (bad key returns 503 without leaking detail), 429 (stops calls, 503 with Retry-After), 5xx, timeout.
12. Missing key fails closed with 503.
13. Global budget and per IP limit enforced. Cache hit makes zero upstream calls.
14. Truncation flag set when totals exceed returned items, including `registered_office_change_filings.truncated` (true when `total_count` exceeds the items returned, false when totals match or `total_count` is absent).
15. Links in upstream responses are never fetched (assert the HTTP client is called only with expected paths).
16. Client IP helper (`tests/test_client_ip.py`): Nth-from-right selection, `TRUSTED_PROXY_HOPS` parsing and default, spoofed prepended entries do not change the key, fallback to `request.client.host` when the header is missing or too short.
17. Filing history is sorted by `date` client side regardless of upstream order; PSC string paging fields are coerced.
18. Source status (v1.2): for each of officers, PSC list, PSC statements, filing history, charges and insolvency, a 404 yields `not_returned`, empty arrays, `truncated: null` and no derived conclusion from that collection; a profile 404 is still a 404; a 200 yields `available`.
19. Provenance (v1.2): `idv_statement_overdue` and `idv_statement_removed` list `officers` for officer only hits, `pscs` for PSC only hits, and both for mixed hits.
20. Weighted allowance (v1.2): a client is charged per upstream call actually made; a client cannot cause more than 60 upstream calls in five minutes; a cache hit costs nothing; the burst cap of 10 lookups per minute still returns 429 on the 11th.
21. Client IP (v1.2, v1.5): `CF-Connecting-IP` takes precedence over `X-Forwarded-For`; without it the Nth-from-right rule and fallback apply. v1.5: an invalid or overlong `CF-Connecting-IP` falls through to `X-Forwarded-For`, an invalid `X-Forwarded-For` entry falls through to the socket address, IPv6 is normalised, and 10,000 forged long values never create a key longer than 45 characters.
22. Limiter bound (v1.2): 20,000 distinct client keys leave each limiter map at or below 10,000 entries.
23. Unparseable dates (v1.2): an unparseable value in each of `appointment_verification_start_on`, `appointment_verification_end_on`, `appointment_verification_statement_due_on` and (PSC) `appointment_verification_statement_date` derives `verification_data_conflict`.
24. Contract (v1.2): the nine indicator `does_not_establish` strings are asserted exactly.
25. Personal fields (v1.2): one recursive personal field assertion applies to the success body, every error body, `/api/health` and every captured log record.
26. Fixtures (v1.2): committed fixtures are synthetic. Every company number under `tests/fixtures/kyb/` matches `^ZZ\d{6}$` (a pattern reserved for fixtures in this repository, not a claim about the register numbering scheme), which excludes every captured live number without naming any. Names, etags, transaction IDs and charge particulars are synthetic and dates are shifted so the tested relationships hold.
27. PSC statements 404 (v1.4, replaces the withdrawn v1.3 rule): a 404 from the statements list gives `statements_source_status: "not_returned"`, empty statement arrays, `pscs.truncated: null` and no `active_psc_statements` or `ceased_psc_statements` indicator. A statements 5xx or timeout fails the request (502 or 504). An officers 404 and a PSC records 404 give `not_returned`.
28. Unsupported entity type (v1.4): `registered_office_change_filings`, `charges` and `insolvency` are `not_returned` objects, never `null`.

## 9. Delivery sequence

1. API pull request only. Frontend page is a separate later pull request.
2. `/code-review` on the real pull request, resolve material findings.
3. Pratik runs the manual Codex adversarial review on the final diff.
4. Merge, deploy, then live smoke against production with one real active company and one invalid number, confirming response shape and data minimisation on the deployed service. Our own 503 and `Retry-After` behaviour is proven by tests, not by live smoke (CLAUDE.md section 17).
5. Live spoof test, run against production after the step 4 smoke check, from one actual source IP: send 11 lookups of the same valid company number within 60 seconds. Every request carries a different forged `CF-Connecting-IP` AND a different forged `X-Forwarded-For` (for example `203.0.113.1` to `203.0.113.11` in each header). Requests 1 to 10 must succeed and request 11 must return 429. This shows Cloudflare replaced the forged `CF-Connecting-IP` and the application saw one stable client key; varying only `X-Forwarded-For` would not prove the `CF-Connecting-IP` trust assumption. Then send one request from a genuinely different egress IP (for example a different network): it must succeed, as the separation control. Any other result blocks release. If request 11 does not return 429, the trusted hop count or the Cloudflare assumption is wrong: set `TRUSTED_PROXY_HOPS` correctly or stop trusting `CF-Connecting-IP`, and repeat. Using one valid company number means requests 2 to 10 are served from the cache and spend no upstream quota.
6. Publication verification sequence for both repositories touched.

## 10. Out of scope for phase 1

Frontend page, shared address density, officer network views, bulk or streaming ingestion, any scoring, any stored history, disqualified director search.

## 11. Implementation decisions (approved 10 October 2026)

1. Layout: `routes_kyb_lab.py`, `client_ip.py`, `tests/test_kyb_lab.py`, `tests/test_client_ip.py`, `tests/fixtures/kyb/*.json`. `main.py` changes only to include the router and add the health fields.
2. Tooling: `requirements-dev.txt` with pinned pytest, httpx and ruff. CI gains a step running `ruff check` and `ruff format --check` on the new files only, then `pytest tests/`. Existing CI steps are unchanged. No mypy for now, to avoid blast radius on existing files.
3. Pydantic: our own response models use `strict=True, extra="forbid"`. Upstream projection models use `extra="ignore"`.
4. The three existing rate limiters (`main.py`, `routes_scenario_lab.py`, `routes_sar_sandbox.py`) are not changed here. Their spoofable `X-Forwarded-For` handling is tracked separately in the frontend repository `BACKLOG.md`.

## 12. Scope decisions recorded 11 October 2026

1. `old-public-company` and `northern-ireland` stay excluded from phase 1 scope. They are legacy categories, and current Northern Ireland companies are registered as `ltd` or `plc`, which are in scope.
2. There is no aggregate `verification_data_conflict` indicator. A conflict is shown only on the individual officer or PSC record, with the display text in section 5.3.

## Appendix: Codex review 1: finding map

Source: the review is saved verbatim as `kyb-lab-codex-review-1.md` (verdict FAIL, ten findings). This map uses the review's own finding numbers. Where a finding asked for a specific correction, the row says how the spec meets it, and any deliberate difference is stated.

| Finding | Severity | Review's required correction | Where the spec answers it | How |
| --- | --- | --- | --- | --- |
| 1 | High | Add an `indeterminate` or `verification_data_conflict` outcome for impossible or unresolved combinations; validate chronology and future dates; do not treat absence of the three statement fields as absence of all verification data | Section 5.2 rows a and f | Row a: start after today, end after today, end before start, or end present with start absent gives `verification_data_conflict`, evaluated first. Row f: start, end and due all absent gives `no_statement_status_fields`, with ACSP evidence shown. Due today stays `statement_due` (row d, on or after today). |
| 2 | High | Determine applicability from the documented role and kind; use an explicit `not_applicable` or `not_yet_in_scope` state; do not treat generic list fields on non-individual payloads as proof | Section 5.1 | Derivation eligibility is role and kind only (`director`, `llp-member`, `llp-designated-member`; PSC kind `individual-person-with-significant-control`). Everything else is `not_interpreted`, not derived, with any raw fields labelled. Difference from the review: the state is named `not_interpreted` (not `not_applicable` or `not_yet_in_scope`), because the spec does not assert why a record is outside the derivation. |
| 3 | Medium | Describe only the absent fact; say it does not establish verified, unverified, compliant, non-compliant or outside scope | Section 5.3, `no_statement_status_fields` | The display text states only the absent fact and lists the outcomes it does not establish, as the review asks. `no_verification_data` is removed. |
| 4 | Medium | Attribute the conclusion to the register; say it is not evidence of legitimacy | Section 5.3, `statement_supplied` and per record `does_not_establish` | "The register records that an identity verification statement was supplied for this role on <date>, linking a verified identity to it." The per record text states a supplied statement is not evidence the person, company or activity is legitimate. |
| 5 | High | Return raw status; do not derive "not active"; say `active` does not establish trading and that dormant accounts are separate | Section 3 (scope), section 6 | `company_status_not_active` is deleted. Status is returned raw. Global `does_not_establish` item 3 states register status does not establish trading and that dormant accounts are a separate filing fact. |
| 6 | High | Make labels type-aware, or suppress indicators whose type-specific meaning is unverified | Section 3 (scope), section 6 | Registered overseas entities, UK establishments and overseas companies are out of scope, so annual statement, managing officer notification and beneficial owner semantics never reach an interpreted output. `recently_incorporated` is deleted. `company.date_of_creation` is returned under the register's own field name and is not labelled as incorporation. |
| 7 | Medium | Distinguish active and ceased statements and include their dates | Section 6 | `active_psc_statements` and `ceased_psc_statements`, each with `notified_on` (and `ceased_on` where ceased). Added on 11 October 2026 after this re-check: the earlier version showed the ceased date only. |
| 8 | Medium | Gate current-duty language by entity status, or label as historical and show status and cessation date | Section 3 (scope), section 4 | Current obligation outputs and every identity verification status are derived only when `company_status == "active"`. For other statuses the raw values are shown with `company.status` and `company.date_of_cessation` and the labelled notice. Added on 11 October 2026 after this re-check: `date_of_cessation` was missing from the earlier version. |
| 9 | Medium | Return dated facts without a binary "recent" indicator, or make the 12 month rule an `frc_assessment` | Sections 4 and 6 | `recent_officer_changes`, `recent_registered_office_change` and `recently_incorporated` are deleted. `officers.events`, `registered_office_change_filings` and `company.date_of_creation` return dated facts with no flag, window or count. Any future window is an `frc_assessment` needing separate review. |
| 10 | Medium | Add inaccuracies, errors and omissions; add that status, identity statements and indicator absence do not establish legitimacy, trading, ownership accuracy or CDD; preserve that basic checks occur | Section 6, global `does_not_establish` | Six items. Item 1 covers inaccuracies, errors and omissions and Companies House's lack of responsibility for their consequences. Item 2 preserves that Companies House checks completeness and validity. Items 3 to 6 cover status, identity records, absence of an indicator and the CDD disclaimer. |

Open points from the review that the spec does not resolve, by design: a deceased PSC need not verify (finding 3) and no register field identifies one, so `no_statement_status_fields` wording covers it only generally; and the "personal code links one verified identity to many roles" explanation (finding 4) is reflected in the display text but is not independently sourced in this spec.

## Appendix: Codex reviews 2 and 3: finding map

Sources: `kyb-lab-codex-review-2.md` (PASS SUBJECT TO CORRECTIONS) and `kyb-lab-codex-review-3.md` (PASS), both saved verbatim. Review 2 marked findings 2 to 10 of review 1 RESOLVED and finding 1 PARTIALLY RESOLVED, then raised three new medium issues. Its "Required before PASS" list has four items. Review 3 confirmed each.

| Review 2 item | Requirement | Spec section | New text | Review 3 |
| --- | --- | --- | --- | --- |
| Finding 1 (partial) | Treat due after end, and PSC statement-open date after due, as `verification_data_conflict` | 5.2 row a, rule 3 | Row a adds "start, end and due are all present and due is after end; for a PSC, `appointment_verification_statement_date` is after `appointment_verification_statement_due_on`". Rule 3: the statement-open date "can trigger `verification_data_conflict` under row a (when it is after the due date) but it never sets any other status". | RESOLVED |
| New issue 1 | Rename `applicable` to `interpreted` or `derivation_eligible`; keep the caveat that a passed due date is not a finding of an offence or non-compliance | 5.1, 4, 6, 8 | State is `derivation_eligible`, response field `eligibility`. The `statement_overdue` text keeps "not a finding that the person is unverified or that an offence has occurred". | RESOLVED |
| New issue 2 | Narrow the scope description to the eight allowlisted types | 3 | "Phase 1 interprets only the eight company profile types listed below. It does not cover UK-registered companies generally." | RESOLVED |
| New issue 3 | Change the accounts overdue basis and tests to `accounts.next_accounts.overdue` | 6, 8, verification table | "profile `accounts.next_accounts.overdue` is true. The older `accounts.overdue` is deprecated in the Swagger and is not used." The deprecation sweep also replaced `has_charges` and `has_insolvency_history` with presence of `links.charges` and `links.insolvency`. | RESOLVED |

## 13. Change log

v1.1 (11 October 2026): one change. `registered_office_change_filings` is now `{ items, truncated }` or null, with `truncated` set by the officers rule applied to filing history (section 4, section 6 dated lists, section 8 item 14). Pending Codex review with the PR diff.
v1.2 (11 October 2026): remediation of Codex PR review 1 (FAIL). Per collection `source_status` and `not_returned_notice` (sections 3, 4); weighted per client allowance of 60 upstream calls per five minutes, burst cap kept, single instance assumption stated (section 3); `source_endpoints` list on indicators (section 4); `CF-Connecting-IP` precedence and a hard bounded limiter (section 3); synthetic fixtures (section 8 item 26); tests 18 to 26. Pending Codex re-review.
v1.3 (11 October 2026): one change. A 404 from the PSC statements list endpoint means `available` and empty (section 3 item 6, section 8 items 18 and 27). Evidence: the live capture returned 404 for companies with no statements (3 of 3 captured). All other subresource 404s stay `not_returned`. Pending Codex re-review.
v1.4 (11 October 2026): remediation of Codex PR review 2 (FAIL). v1.3 withdrawn: a PSC statements 404 is `not_returned` with empty arrays and `truncated: null`, like every other subresource (section 3 item 6, section 8 items 18 and 27). The live observation stays as a note, not a rule; revisit only with Companies House documentation. Unsupported entity types report `registered_office_change_filings`, `charges` and `insolvency` as `not_returned`, never null (section 3 item 6, section 8 item 28). Section 9 step 5 spoof test now forges both `CF-Connecting-IP` and `X-Forwarded-For` on every request and adds a different-egress-IP separation control. Pending Codex re-review.
v1.5 (11 October 2026): client key validation (section 3 item 3a, section 8 item 21). Every header derived client key must parse as an IP address and is stored normalised, at most 45 characters. Prompted by the `/code-review` hardening note on `CF-Connecting-IP`. Pending Codex re-review.

## Appendix: Codex PR review 1: finding map

Source: `kyb-lab-codex-pr-review-1.md`, saved verbatim.

| Finding | Severity | Spec change | Section |
| --- | --- | --- | --- |
| Subresource 404s become confirmed looking empty data | High | `source_status` per collection, `truncated` null, no absence dependent derivation, fixed notice | 3 item 6, 4, 8 item 18 |
| One client can exhaust the shared upstream budget | Medium | 60 call per five minute client allowance weighted by calls made, burst cap kept, single instance assumption, shared state before scale out | 3 item 3, 8 item 20 |
| PSC identity indicators claim the wrong source endpoint | Medium | `source_endpoints` list derived from contributing records | 4, 8 item 19 |
| Live fixtures remain linkable personal data | Medium | Synthetic fixtures, reserved `ZZ` numbers, fixture test | 8 item 26 |
| Client limiter has unbounded key cardinality | Low | Hard bounded LRU of 10,000 keys, no full scan under the lock | 3 item 3b, 8 item 22 |
| Test gaps (four date fields, nine strings, personal fields on every path) | Test | Tests 23 to 25 | 8 |
| `CF-Connecting-IP` recommendation (confirmed conforming point, Render guidance) | Hardening | Precedence added | 3 item 3a, 8 item 21 |
