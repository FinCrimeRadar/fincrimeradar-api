# Companies House KYB Investigation Lab: API phase 1 specification

Status: v1.8, Codex post-merge review PASSED 11 Oct 2026. v1.9 DRAFT (section 14, company name search, spec only, no code) is pending the Codex spec review; nothing outside section 14 and the change log changed. v1.4: v1.3 withdrawn per Codex PR review 2 (404 not documented as empty); revisit only with Companies House documentation. Base: FROZEN v1.0 (Codex section 7 review PASSED, reviews 1 to 3), v1.1 filing truncation, the v1.2 remediation of Codex PR review 1 (`kyb-lab-codex-pr-review-1.md`), and the v1.4 remediation of Codex PR review 2 (`kyb-lab-codex-pr-review-2.md`). Any spec change needs a new review.
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
| The register's own officers page, https://find-and-update.company-information.service.gov.uk/company/00445790/officers (retrieved 11 October 2026): the current directors each show the wording "Verified" and "Verification requirements complete"; three show an authorised corporate service provider name with a check date (25 July 2025, 29 July 2025, 5 September 2025), which match the API's `identity_verified_on` and `authorised_corporate_service_provider_name` for the same appointments. The API returns `appointment_verification_end_on` `9999-12-31` and a start date `2026-07-01` for those same 10 directors (API response retrieved 2026-10-11T19:03:26Z). The page lists exactly 10 current directors, matching the 10 current directors in the API response; an earlier automated reading of the page showed 11 because it counted the secretary. Resolved, confirmed by Pratik on 11 October 2026. | Companies House register page; not the API specification, which does not define `9999-12-31` |

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
   3a. Client IP (`client_ip.py`, KYB only): use `CF-Connecting-IP` when it is present (Render fronts the service with Cloudflare, which sets that header). Otherwise take the Nth-from-right `X-Forwarded-For` entry, N from `TRUSTED_PROXY_HOPS` (default 1), and fall back to `request.client.host` when the header is missing or has fewer than N entries. Do not migrate the existing limiters in this change. If the service is ever reachable without Cloudflare in front, a caller can set `CF-Connecting-IP` themselves, so the header must be ignored in that deployment. Client keys must parse as an IP address; invalid header values are ignored. Each candidate (`CF-Connecting-IP` and the selected `X-Forwarded-For` entry) is stripped, rejected if longer than 45 characters, parsed with `ipaddress.ip_address` and keyed by its normalised string form. An invalid `CF-Connecting-IP` falls through to the `X-Forwarded-For` rule, and an invalid `X-Forwarded-For` entry falls through to `request.client.host`, so no limiter key is longer than 45 characters. IPv4-mapped IPv6 keys as IPv4; other IPv6 keys by /64 prefix. After a successful parse an IPv4-mapped IPv6 address (`ipv4_mapped` set) is keyed by its IPv4 string, any other IPv6 address by its /64 network (`ipaddress.ip_network(f"{ip}/64", strict=False)`, for example `2001:db8:1:2::/64`), and IPv4 is unchanged. The same normalisation applies to the `request.client.host` fallback, so every key path is consistent. A socket host that is not an IP address is used as is.
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

"Today" is the date of `retrieved_at` in Europe/London. Before any condition below is evaluated (v1.8): an `appointment_verification_end_on` that is exactly the string `9999-12-31` (matched on the raw value, not the parsed date) and comes with a start date is treated as absent (no end date). With no start date it is not treated as absent, so row a applies (end present and start absent is a conflict). Every other future end date is unchanged and stays `verification_data_conflict` under row a. The same rule applies to officers and PSCs, and the raw value is still shown unchanged in `evidence`. Evaluate in order. The first match wins.

| Order | Condition | Status |
| --- | --- | --- |
| a | Any of: `appointment_verification_start_on` is after today; `appointment_verification_end_on` is after today; end is before start; end is present and start is absent; start, end and due are all present and due is after end; for a PSC, `appointment_verification_statement_date` is after `appointment_verification_statement_due_on` | `verification_data_conflict` |
| b | Start present, end absent | `statement_supplied` |
| c | Start present, end present | `statement_removed` |
| d | Start and end absent, `appointment_verification_statement_due_on` present and on or after today | `statement_due` |
| e | Start and end absent, due present and before today | `statement_overdue` |
| f | Start, end and due all absent | `no_statement_status_fields` |

The same table applies to officers and to PSCs, using the fields in each record's `identity_verification_details`. There is no special case by officer role or PSC kind inside the derivation. Derivation eligibility in 5.1 is the only place role or kind matters.

Open ended end date (v1.8). Evidence, 11 October 2026: in the live response for a large listed company (retrieved 2026-10-11T18:13:17Z, 74 officers, no PSC records) the officers returned `appointment_verification_end_on` as `9999-12-31` for 10 officers (each with `appointment_verification_start_on` `2026-07-01`) and no end date for the other 64. Before v1.8 the 10 were classified `verification_data_conflict` because the end date was after today. Companies House documents the field only as "The date on which the identity verification statement was removed for the appointment." (officerList resource, https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officerlist?v=latest) and "The date on which the identity verification statement was removed for the notification" (PSC individual resource, https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individual?v=latest, and the vendored `pscModels.json`). The public data API specification, the enumerations and the forum threads read do not mention `9999-12-31`. A Companies House staff member wrote in the forum thread https://forum.companieshouse.gov.uk/t/more-information-on-the-new-elements-in-the-officers-and-psc-apis/12142 that verification statements can be revoked, which is why the end date exists, and gave no detail on how it is populated. One forum post by a user, not Companies House staff (24 November 2025, https://forum.companieshouse.gov.uk/t/officer-appointment-verified-but-identify-is-not-verified/12446), shows a record with `"appointment_verification_end_on": "9999-12-31"` and no Companies House reply defines it. Primary source observation, 11 October 2026 (section 2): the register's own officers page for the same company shows each current director as verified, and the three directors with an authorised corporate service provider show the same provider names and check dates that the API returns in `identity_verified_on`. So the sentinel reading is supported by an observation of the register, but it remains undocumented in Companies House's API specification. The rule is limited to the one exact raw string, applies only when a start date is present, and every other future end date still surfaces as a conflict. For the 18 October Codex review.

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
21. Client IP (v1.2, v1.5): `CF-Connecting-IP` takes precedence over `X-Forwarded-For`; without it the Nth-from-right rule and fallback apply. v1.5: an invalid or overlong `CF-Connecting-IP` falls through to `X-Forwarded-For`, an invalid `X-Forwarded-For` entry falls through to the socket address, IPv6 is normalised, and 10,000 forged long values never create a key longer than 45 characters. v1.6: `::ffff:1.2.3.4` and `1.2.3.4` give the same key; two addresses in one /64 give the same key and different /64s give different keys; 1,000 random addresses in one /64 exhaust that single key's burst limit; the socket fallback is normalised the same way.
22. Limiter bound (v1.2): 20,000 distinct client keys leave each limiter map at or below 10,000 entries.
23. Unparseable dates (v1.2): an unparseable value in each of `appointment_verification_start_on`, `appointment_verification_end_on`, `appointment_verification_statement_due_on` and (PSC) `appointment_verification_statement_date` derives `verification_data_conflict`.
24. Contract (v1.2): the nine indicator `does_not_establish` strings are asserted exactly.
25. Personal fields (v1.2): one recursive personal field assertion applies to the success body, every error body, `/api/health` and every captured log record.
26. Fixtures (v1.2): committed fixtures are synthetic. Every company number under `tests/fixtures/kyb/` matches `^ZZ\d{6}$` (a pattern reserved for fixtures in this repository, not a claim about the register numbering scheme), which excludes every captured live number without naming any. Names, etags, transaction IDs and charge particulars are synthetic and dates are shifted so the tested relationships hold.
27. PSC statements 404 (v1.4, replaces the withdrawn v1.3 rule): a 404 from the statements list gives `statements_source_status: "not_returned"`, empty statement arrays, `pscs.truncated: null` and no `active_psc_statements` or `ceased_psc_statements` indicator. A statements 5xx or timeout fails the request (502 or 504). An officers 404 and a PSC records 404 give `not_returned`.
28. Unsupported entity type (v1.4): `registered_office_change_filings`, `charges` and `insolvency` are `not_returned` objects, never `null`.
29. Open ended verification end date (v1.8): start present with end `9999-12-31` (raw string) gives `statement_supplied` for an officer and a PSC, with or without a past due date; end `9999-12-31` with no start stays `verification_data_conflict` whatever the due date; `99991231`, `9999-12-31 ` and `9999-12-31T00:00:00` are not the open end; `2099-01-01`, `9999-12-30`, `9999-01-01` and `2026-10-12` stay conflicts; an end before the start stays a conflict; a future start stays a conflict; the result does not depend on today (two values tested); a resigned officer or ceased PSC with the value stays `not_interpreted`; the raw end value is still shown in `evidence`; a synthetic bundle carries the value.

## 9. Delivery sequence

1. API pull request only. Frontend page is a separate later pull request.
2. `/code-review` on the real pull request, resolve material findings.
3. Pratik runs the manual Codex adversarial review on the final diff.
4. Merge, deploy, then live smoke against production with one real active company and one invalid number, confirming response shape and data minimisation on the deployed service. Our own 503 and `Retry-After` behaviour is proven by tests, not by live smoke (CLAUDE.md section 17).
5. Live spoof test (v1.7, matches production), run against production after the step 4 smoke check, from one actual source IP. Use one valid company number, so requests after the first are served from the cache and spend no upstream quota.
   (a) Forged `CF-Connecting-IP`: a request carrying a client supplied `CF-Connecting-IP` must be rejected by Cloudflare itself with HTTP 403 and the body `error code: 1000`, before the application sees it. This also holds when `X-Forwarded-For` is forged in the same request. Any other result means the edge does not protect that header: block release and stop trusting `CF-Connecting-IP`.
   (b) Forged `X-Forwarded-For` rotation: send 11 requests within 60 seconds, each with a different forged `X-Forwarded-For` and no `CF-Connecting-IP`. Count the requests this source IP already made in the previous 60 seconds. The first 10 minus that count succeed and every later one returns 429 with `Retry-After`. The cap must be hit at the expected count whatever the forged values are, which shows that rotation creates no new keys and that the application keys on the address Cloudflare supplies.
   (c) Separation control: one request from a genuinely different egress IP (for example a phone on mobile data with Wi-Fi off) must succeed while the first source is rate limited.
   Any other result blocks release. If (b) shows new keys per forged value, the trusted hop count is wrong: set `TRUSTED_PROXY_HOPS` correctly and repeat.
   Evidence, 11 October 2026, production at a989238 (17:22 to 17:23 UTC): (a) 11 requests each with a different forged `CF-Connecting-IP` and `X-Forwarded-For` all returned 403 from Cloudflare (`Server: cloudflare`, `error code: 1000`), and a request with only a forged `CF-Connecting-IP` also returned 403, while a request with only a forged `X-Forwarded-For` returned 200. (b) 11 requests with rotating forged `X-Forwarded-For` returned 200 for requests 1 to 8 and 429 for requests 9 to 11 with `retry-after: 60`; two earlier requests from the same source were already inside the 60 second window, so the cap of 10 was reached at the 9th, as expected. (c) Pratik's phone on mobile data with Wi-Fi off loaded a lookup successfully (result reported by Pratik). The earlier wording of this step, which forged both headers and expected requests 1 to 10 to succeed, is withdrawn: Cloudflare rejects a forged `CF-Connecting-IP` before the application, so that sequence cannot pass.
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
v1.6 (11 October 2026): client key normalisation (section 3 item 3a, section 8 item 21). IPv4-mapped IPv6 keys as IPv4, other IPv6 by /64 prefix, including the socket fallback, so a caller cannot rotate keys inside one address block. Pending Codex re-review.
v1.7 (11 October 2026): test procedure only, no logic change. Section 9 step 5 rewritten to match production: forged `CF-Connecting-IP` is rejected by Cloudflare with 403 (error 1000), `X-Forwarded-For` rotation creates no new keys, a separate egress IP succeeds. Evidence recorded from the 11 October production run. For the combined Codex review on 18 October 2026.
v1.8 (11 October 2026): one derivation rule. In section 5.2 an `appointment_verification_end_on` that is exactly the raw string `9999-12-31` and has a start date is treated as absent before the conflict checks, for officers and PSCs; with no start date it stays a conflict, and every other future end date stays a conflict. Evidence: the live API response (10 directors classified as conflicts) and the register's own officers page (section 2). Companies House does not document the value in its API specification. Tests: section 8 item 29. For the 18 October Codex review.
v1.9 DRAFT (11 October 2026): new section 14, company name search (first draft `GET /api/kyb/search?q=`, replaced by `POST /api/kyb/search` with a JSON body in the corrections below): one upstream `/search/companies` call with `items_per_page=20` and no paging, five candidate fields plus a scope flag, shared quota, burst and a short cache. Spec only; person or officer name search is out of scope and must not be built. Updated the same day from nine live calls (shape only, 14.2a): default page size 20, `total_results` is the whole count, a no match result is a 200 with an empty `items`, the number query returns that company as the single result, a 1 and a 200 character query are both accepted (total 10000), `company_status` can fall outside its documented enum. Pending the Codex spec review.
v1.9 DRAFT, corrections after Codex search spec review 1 (FAIL, 11 October 2026): POST body instead of a query string, enforceable boundary instead of an intent guarantee, logging qualified to application logs, exact string cache key, fail closed envelope rules, a rewritten enumeration risk with a recorded acceptance, `<bdi>` for titles and exact messages. Finding map in the appendix after section 14. Pending the Codex re-review.
v1.9 DRAFT, corrections after Codex search spec review 2 (FAIL, 11 October 2026): two truncation messages chosen by path with the register's reported total returned as `reported_total`, the raw `q` validated before any normalisation (space only normalisation), a 4 KiB raw body limit enforced by counting received bytes on the search route only (413), CORS behaviour specified and tested, and the URL wording limited to the supported contract. Finding map in the second appendix after section 14. Two open questions then settled by Pratik (11 October 2026): rejected requests (405, 413, 422) count toward the per client burst limit because the burst check runs before the body is read, and the invented partial page 502 rule is removed (a reported total larger than the number returned is always `truncated: true`, message "The register reports {total} matches. Only {shown} are shown."). Pending the Codex re-review.

## 14. Company name search (v1.9 DRAFT, spec only, no code)

Status: draft, corrected after Codex search spec reviews 1 and 2 (both FAIL, `kyb-lab-codex-search-spec-review-1.md` and `-2.md`) and the two open questions settled by Pratik on 11 October 2026, for the Codex re-review. Nothing in this section is implemented. Tier: CLAUDE.md section 15 (new API endpoint, untrusted third party text, quota and abuse surface).

### 14.1 Purpose and scope

A visitor who does not know a company number types part of a company name, sees a short list of candidate companies, picks one, and the existing `GET /api/kyb/company/{company_number}` readout runs unchanged. The search returns candidates only. It never returns a readout, an indicator or a verification status.

Out of scope, and not to be built: **person or officer name search** (UK GDPR and people dossier risk), PSC search, disqualified officer search, the combined search (`/search`), advanced and dissolved company search, address search, fuzzy or phonetic matching of our own, paging, and the `restrictions` parameter (including the name availability search).

**The enforceable boundary.** The caller's intent cannot be enforced: the endpoint takes free text, a caller can type a person's name, and a company title may itself contain a person's name. What the service does enforce is: (1) it calls only the fixed path `/search/companies` and no other search path; (2) it never requests, parses, stores or returns an officer, PSC, disqualified officer or other person field; and (3) the response carries the five company fields of 14.5 and nothing else. The endpoint can still be used to discover company candidates associated with a name, which is recorded as a risk in 14.9.

**Search terms may be personal data.** A term typed by a visitor is treated as potentially personal data under UK GDPR, with the handling in 14.3.
### 14.2 Verified source facts (read 11 October 2026)

Sources: the vendored `docs/specs/companies-house-swagger/spec/search.json` (operation `searchCompanies`, definitions `CompanySearch`, `CompanySearchItems`, `CommonSearch`, `CommonSearchItems`, `registeredOfficeAddress`, `MatchesModel`, `LinksModel`), the developer reference https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/search-companies and the resource page https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companysearch?v=latest. Nine live calls were then made on 11 October 2026 (six, then three to recover probes whose output was lost) and only their shape is recorded (14.2a); no live response is stored in this repository.

| Fact | Where it is documented |
| --- | --- |
| `GET https://api.company-information.service.gov.uk/search/companies`. The vendored file keys the operation by name (`searchCompanies`) and does not state the path; the path is from the developer reference page. | developer reference |
| Query parameters: `q` (string, required, "The term being searched for."), `items_per_page` (integer, "The number of search results to return per page."), `start_index` (integer, "The index of the first result item to return."), `restrictions` (string, space separated enumerable options). Responses: 200 "Search company", 401 "Not authorised". | `search.json#/searchCompanies/get/parameters`, `#/responses` |
| Envelope: `kind` (`search#companies`), `items` (array), `total_results` (integer, "The number of further search results available for the current search."), `start_index`, `items_per_page`, `etag`. The documentation lists no `page_number`; the live API returns one (14.2a). | `search.json#/definitions/CompanySearch`, `#/definitions/CommonSearch` |
| Item fields this spec would use: `company_number` (string, "The company registration / incorporation number of the company."), `title` (string, "The title of the search result."), `company_status` (string enum: active, dissolved, liquidation, receivership, administration, voluntary-arrangement, converted-closed, insolvency-proceedings, registered, removed), `company_type` (string enum of 26 values in the vendored file), `date_of_creation` (string, format date, "The date the company was created."). All five are in the schema `required` list. | `search.json#/definitions/CompanySearchItems/properties` and `/required`, `#/definitions/CommonSearchItems/properties/title` |
| Item fields this spec drops: `address` (registered office address object), `address_snippet`, `snippet`, `description`, `description_identifier`, `matches`, `links` (including `self`, which must never be followed), `date_of_cessation`, `kind`. | same |
| The existing upstream rules still apply to the search call: fixed host, no redirects, no link following, key based authorisation, and the 600 per five minute application rate limit (a search counts against the same limit). | section 3 and section 2 |

### 14.2a Live observations (11 October 2026, nine calls, shape only)

Nine calls were made with the production key (`GET /search/companies`): six, then three more after the output of the last three probes of the first run (the company number match, the 1 character query and the 200 character query) was lost to output truncation in the session. The three recovery calls wrote their raw output to a scratch file that was deleted after the shape was read. Recorded: status codes, key names, counts and yes or no facts. No title, address or other register data is stored.

| Probe | Observed |
| --- | --- |
| a. `q` of a common company name (5 characters), no `items_per_page` | 200, `application/json`. Top level keys: `items`, `items_per_page`, `kind`, `page_number`, `start_index`, `total_results`. `kind` is `search#companies`. `items_per_page` 20 and `page_number` 1 although neither was sent, so the default page size is 20. `total_results` 356. `items` has 20 entries. The `X-Ratelimit-*` headers are present. |
| b. same `q`, `items_per_page=20` | Identical envelope and count (20 items, `total_results` 356). So `total_results` is the whole match count, not a count of results beyond the page (that would be 336), and the drafted reading of "further" results is wrong. |
| Items in a and b | 13 keys seen: `address`, `address_snippet`, `company_number`, `company_status`, `company_type`, `date_of_creation`, `description`, `description_identifier`, `kind` (`searchresults#company`), `links`, `matches`, `snippet`, `title`. The key sets are not identical across items (`snippet` and `matches` are absent on some), so only the five fields in the schema `required` list can be relied on. `company_number` is 8 characters in all 20. `date_of_creation` is present in all 20. `address` is an object in all 20. 4 distinct `company_type` values in the 20. |
| `company_status` values seen | `active`, `closed`, `dissolved`, `liquidation`, `open`. `closed` and `open` are **not** in the documented `company_status` enum (10 values), so the field is passed through as a bounded string and never matched against the enum. |
| Order | Not sorted by title. The first item has the query text in its title and 19 of the 20 titles contain it (the other matched on other indexed text). No exact title match was seen in the first 20. No ordering claim is made. |
| c. nonsense string with no match (13 characters) | 200 with the same six key envelope, `items` present as an empty array, `total_results` 0, `items_per_page` 20, `page_number` 1. A no match result is therefore an explicit 200 with an empty `items`, not a 404 and not an absent key. |
| d. an 8 digit company number as `q`, `items_per_page=20` (run twice) | 200. `items` has 1 entry, `total_results` 1, status `active`. **The single result's `company_number` equals the queried number** (recorded on the second run). The same six key envelope. |
| e. 1 character `q`, `items_per_page=20` | 200, the same six key envelope, `items` has 20 entries and `total_results` is 10000. The upstream accepts a 1 character query and matches very broadly. |
| f. 200 character `q` (a repeated phrase), `items_per_page=20` | 200, the same six key envelope, `items` has 20 entries and `total_results` is 10000, the same figure as the 1 character query. The upstream accepts a 200 character query. The identical total for two very different queries suggests the reported count is capped at 10000 or that the long query matched on a single term; neither is documented. |
### 14.2b Still undocumented or unobserved, so not relied on

1. Maximum `items_per_page` and the maximum `start_index`: not stated by Companies House and not tested. Non staff forum users report a maximum of 100 and a 1000 result ceiling with HTTP 416 (voracityemail, 7 November 2025, https://forum.companieshouse.gov.uk/t/using-pagination-on-search-companies/12373; madski, 30 January 2020, https://forum.companieshouse.gov.uk/t/api-company-search-limitation/2824), unconfirmed. The default of 20 is now observed. **Treatment:** always send an explicit `items_per_page=20`, never send `start_index`.
2. Sort order: observed not alphabetical and apparently by relevance, but not documented. **Treatment:** show candidates in the order returned and make no claim about ranking.
3. Minimum and maximum `q` length: not documented. Observed: the upstream accepted 1 and 200 characters with a 200 and 20 items each (probes e and f). **Treatment:** the 2 to 160 character bounds in 14.3 stay this service's own choice. The minimum of 2 is because a 1 character query matched 10000 companies and returns an arbitrary 20; the maximum of 160 is a generous cap on text we pass upstream and is not claimed to be a register limit.
4. Matching rule (prefix, token, fuzzy): not documented; probe f suggests a long query is not matched as an exact phrase. `total_results` of 10000 in probes e and f may be a ceiling on the reported count (undocumented), which does not change the `truncated` rule. **Treatment:** no user facing text describes how matching works or how many companies match.
5. Company number in `q`: observed to work. An 8 digit number returned exactly one result whose `company_number` equals the number (probe d). **Treatment:** the service does not special case digits and does not merge this with the company number path. The company number form (`GET /api/kyb/company/{number}`) stays the only number path, and the name search never routes a number to the readout on its own.
6. No match result: observed as 200 with `items` an empty array **and** `total_results` 0 (probe c). **Treatment:** that complete shape, `items` an empty list and `total_results` the integer 0, is zero candidates (available and empty). An empty list with a positive, missing or invalid total is inconsistent, not proven empty, and gives 502. A 200 whose `items` is absent or not a list, and a 404, also give 502, never an empty list (Codex PR review 2, Codex search spec review 1).
7. `total_results` is the whole match count (probe b). The official resource makes `items` and `total_results` optional, so an envelope can be incomplete. **Treatment:** see 14.4 item 4: an invalid or inconsistent total gives 502, a missing total gives `truncated: true`, and `truncated` is never false unless the envelope proves completeness (`total_results` is an integer equal to the number of items returned). The count itself is not returned.
8. `company_type` enum: 26 values in the vendored Swagger against 23 on the developer resource page, and `company_status` carries values outside its documented enum (probe a). **Treatment:** both are passed through as bounded strings, `scope` is decided by membership of the 8 type allowlist, and an unknown value is outside the allowlist.
9. `page_number` is returned by the live API but absent from the documentation. **Treatment:** the upstream model does not declare it and the response never carries it.
10. How a name search counts against the 600 per five minute application limit: not stated. **Treatment:** one search is one upstream call against the shared budget.
### 14.3 Endpoint, input and privacy

`POST /api/kyb/search` with a JSON body `{"q": "<text>"}`. The supported frontend and API contract place the search term only in the JSON body. A query-string `q` on any request is ignored or rejected and never forwarded. A GET to this path returns 405 (the route accepts POST only). The body must be a JSON object with exactly the key `q` (a string); any other key, a non string `q` or a body that is not JSON gives 422. No other field is read, and a client supplied `start_index`, `items_per_page`, `page` or `restrictions` is rejected the same way (422), never forwarded.

1. **Validation order.** (a) Validate the raw `q` exactly as received, before any normalisation: it must be a string and `str.isprintable()` must be true for the whole value, which rejects a tab, a newline, U+2028 and every other control, line separator, zero width, bidirectional control and non ASCII space character (the ordinary space U+0020 is printable and allowed). `Acme\nLtd` is therefore rejected, not turned into `Acme Ltd`. (b) Only then strip leading and trailing U+0020 and collapse runs of U+0020 to a single space; no other character is touched. The result is the **search string**: the same value is length checked, cached and sent upstream. (c) Finally the search string must be 2 to 160 characters long (code points), which also rejects an empty or all space input.
2. Any failure of item 1, and any 422, returns a fixed generic message. The framework's default 422 body, which echoes the input, must be replaced, so nothing of the input is echoed.
3. **Logging.** The term is never written to application logs: no log record, exception message, header or metric produced by this service carries it, and the structured log line holds `query_length` (the search string length), the outcome, the upstream call count and the cache result, and nothing derived from the text (no hash). This is a statement about application generated logs only. Uvicorn access logging is disabled (`--no-access-log`) and the term is in the request body, not the URL, so a request URL log on our side does not contain it. No claim is made about Cloudflare or Render processing of a request in transit beyond the URL.
4. **Companies House receives the term.** The service passes the search string to Companies House in the query string of `GET /search/companies?q=...`. Companies House and its infrastructure may log request URLs, and the term may therefore be retained there under its own policies. FinCrimeRadar does not control that. The privacy page needs a sentence about it (BACKLOG item, wording for Pratik's approval).
5. The cache of 14.6 holds the search string in process memory for at most 300 seconds. That is the only retention by this service.
6. Missing `COMPANIES_HOUSE_API_KEY`: 503 with the existing generic message, exactly as the company lookup.
7. **Burst check first, then the raw body limit.** The per client burst check (10 starts per minute, shared with the company lookup) runs before the method or the body is examined, exactly as the lookup route checks it before validation. Every request to this route therefore counts toward the burst limit, including those that are then rejected with 405, 413 or 422, and a client over the limit gets 429 whatever it sent. No upstream allowance, global budget, cache entry or upstream call is charged or made for a rejected request. Decision by Pratik, 11 October 2026. The raw request body is then limited to 4 KiB (4096 bytes), enforced before any JSON parsing by counting the bytes actually received from the stream. `Content-Length` is never trusted: a declared length over the limit is rejected at once, and an absent, understated or chunked body is counted as it streams and aborted when it passes 4096 bytes. The control is scoped to the search route only (it is not a global middleware, so other routes such as `/api/chat` keep their own limits), for example a route level ASGI wrapper that counts received bytes. The response is 413 with a fixed generic message that echoes nothing, and the term is never read into the application.
8. **CORS.** The existing app level CORS configuration applies unchanged: the production origins (`https://fincrimeradar.org`, `https://www.fincrimeradar.org`) may preflight and send `POST` with `Content-Type`. A disallowed origin receives no `Access-Control-Allow-Origin`. A preflight (`OPTIONS`) is answered by the CORS layer, is not a lookup start, consumes no burst allowance and makes no upstream call. The error responses 405, 413, 422 and 429 produced for an allowed origin carry `Access-Control-Allow-Origin`, so the frontend can read the status.

Live note: the length bounds are not from Companies House (14.2b item 3).

### 14.4 Upstream call

Exactly one call per cache miss: `GET /search/companies?q=<search string>&items_per_page=20`. No `start_index`, no `restrictions`, no retry on a successful status, and the existing connection error retry rule applies unchanged. The request goes through the same `_fetch` as every other call, so the fixed host, no redirect, timeout, 429 block and per call quota charging all apply.

1. Status mapping is the company lookup mapping with one difference: the lookup's 404 handling (not found, or `not_returned` for a sub resource) does not apply, and a 404 here gives 502. 401 or 403 gives 503; 429 starts the 300 second block and gives 429 with `Retry-After`; any other status gives 502; a timeout gives 504.
2. A 200 body that does not parse into the upstream model, or whose `items` is absent or not a list, gives 502.
3. **Empty.** The result is zero candidates (available and empty) only when `items` is an empty list and `total_results` is the integer 0. `items` empty with any other total, missing or invalid, gives 502.
4. **Totals.** Let `n` be the number of items the upstream returned and `c` the number of candidates kept (`n` capped at 20). If `total_results` is present it must be an integer that is not a boolean, is not negative and is not less than `n`; otherwise 502 (invalid or inconsistent). If `total_results` is absent, the envelope is incomplete: `truncated` is true and `reported_total` is null. If it is valid, `reported_total` is that integer, `truncated` is true whenever it is larger than `c` (including when fewer than 20 candidates came back), and `truncated` is false only when it equals `c`. There is no further consistency rule about a page that is not full. `truncated` is never false from an incomplete envelope.

### 14.5 Response

```
{
  "retrieved_at": "ISO 8601 UTC",
  "source": "Companies House Public Data API",
  "candidates": [ Candidate ],
  "truncated": bool,
  "reported_total": "integer | null (the register's own total_results, null when it was not reported)",
  "search_notice": "string, fixed",
  "attribution": "Source: Companies House public register, retrieved <retrieved_at>. FinCrimeRadar is not affiliated with or endorsed by Companies House."
}

Candidate = {
  "company_number": "^[A-Z0-9]{8}$",
  "title": "string, at most 500 characters",
  "company_status": "string | null",
  "company_type": "string | null",
  "date_of_creation": "string | null",
  "scope": "interpreted" | "unsupported_entity_type"
}
```

1. At most 20 candidates, in the order returned. If the upstream returns more, the extra items are dropped.
2. Only these six keys exist on a candidate. The response models are strict with `extra="forbid"`, and the upstream model never declares `address`, `address_snippet`, `snippet`, `description`, `description_identifier`, `matches`, `links`, `date_of_cessation`, `kind`, `etag` or `page_number`, so they cannot reach the response, a log or the cache. No officer, PSC or person field is requested, parsed, stored or returned.
3. `scope` is `interpreted` when `company_type` is in the 8 type allowlist of section 3 and `unsupported_entity_type` otherwise (including a missing type). The frontend shows the second as "Not interpreted in phase 1", the section 3 phrase. Such a candidate can still be picked and the readout then returns the unsupported type notice.
4. `company_number` must match `^[A-Z0-9]{8}$` after upper casing. A candidate that fails it, a `title` over 500 characters or a non string value in any of the six fields gives 502 for the whole response. Nothing is silently dropped or repaired, because a short list that quietly omits a company would look complete.
5. `search_notice` is a fixed string, accepted as drafted by Codex search spec review 1: "Several companies can share a similar name, and a name match is not an identity check. Confirm the company number before relying on a result."
6. `title` and every other string are untrusted register text. They are stored, cached and returned exactly as received (no trimming, no escaping, no HTML handling), and the frontend renders them only through `textContent`, inside a `<bdi>` element (14.7).
7. No score, rank, ordering claim, address, officer or person field is returned. The only count is `reported_total`, the register's own `total_results` passed through as an integer (or null when absent), which the frontend renders as text in the truncated message of 14.7. The upstream `page_number` is never returned.

### 14.6 Quota, burst and cache

1. A cache miss spends one upstream call, charged to the same per client weighted allowance (60 calls per five minutes) before the call, and to the same global budget (300 per five minutes). **Per client allowance exhaustion returns 429 with `Retry-After`; global budget exhaustion returns 503 with `Retry-After` (section 3 item 1).**
2. The same burst limit applies and shares one counter with the company lookup: 10 starts per minute per client in total, checked before validation. A search followed by a pick therefore uses two of the ten, so five complete name flows per minute. Search never gets a separate or higher burst counter. Codex search spec review 1 also suggested considering a lower search specific allowance if 20 candidates per call is outside the accepted exposure; none is proposed in this draft (see 14.9).
3. **Cache.** The key is the exact search string sent upstream (14.3 item 1: U+0020 stripped at the ends and runs of U+0020 collapsed to one space) and nothing else. There is no case folding and no other normalisation, so `Acme Ltd` and `acme ltd` are two entries, and `Straße` and `STRASSE` are two entries, because the upstream may not treat them as equivalent. The value is the parsed response, TTL 300 seconds (shorter than the 15 minute company cache because the candidate list is a hint), bounded LRU of 1,000 entries with a key of at most 160 code points. The key holds the visitor's text in process memory only and is never logged or exported. A hit spends no upstream call or allowance but counts toward the burst limit. A failed or partial lookup is not cached.
4. A single instance is still assumed (section 3 item 3b); the limits reset on restart.

### 14.7 Frontend flow (`kyb-lab.html`, for the later build)

1. A second form, "Find a company by name", sits above the company number form, with its own input and a 2 to 160 character hint, and no search as you type: a request is sent only on submit, as a `POST` with a JSON body. The API warm up still happens on first focus of either input and nothing is requested on page load.
2. The candidate list shows, as text only, the title, the company number, the status code and type code (formatted as codes under the existing rule) and the creation date, with "Not interpreted in phase 1" on `unsupported_entity_type` candidates, the fixed `search_notice` and, when `truncated`, the truncated message below.
3. **Directional isolation.** The title is rendered inside a `<bdi>` element (with `unicode-bidi: isolate` as a fallback), as a separate element from the company number, status, type and date, so that a right to left override or other bidirectional control in a title cannot reorder the trusted fields beside it. The title text stays byte for byte as received.
4. Picking a candidate is a button. It puts that candidate's `company_number` in the company number field and runs the existing `GET /api/kyb/company/{number}` lookup. Titles are never links, and no URL from the response is ever used.
5. Exact messages. Empty: "No companies were returned for this search." Truncated, chosen by the path in the response: when `reported_total` is an integer larger than the number of candidates shown, "The register reports {total} matches. Only {shown} are shown." (`{total}` is `reported_total` and `{shown}` is the number of candidates returned, both inserted as text, never as markup); when `truncated` is true and `reported_total` is null, "The register did not report a total, so this list may be incomplete." Errors: 422 "Enter 2 to 160 characters of text"; 413 "That search is too long"; 429 "Too many lookups; try again in a minute"; 503 "The register lookup is temporarily unavailable"; network, timeout and other failures use the existing messages. The empty and truncated lines make no claim about the matching behaviour or about the whole register.
6. The page never offers a person, officer or PSC search and says it searches company names only. The privacy page is updated before launch (BACKLOG).

### 14.8 Tests required (written with the build, none exist yet)

API tests (`tests/test_kyb_lab.py` or a new file):

1. Method and body: `GET /api/kyb/search?q=acme` returns 405 and nothing is forwarded; a `q` in the query string of a POST is ignored and never forwarded; a POST body with an extra key (`start_index`, `items_per_page`, `page`, `restrictions` or any other), a non string `q`, a missing `q` or a non JSON body gives 422 with the fixed generic message and **no echo of the input anywhere in the body**.
2. Input validation and order: **`Acme\nLtd` gives 422** (the raw value is validated before normalisation), and so do `Acme\tLtd`, `Acme\u2028Ltd`, `Acme\u00a0Ltd` (a non ASCII space), `\x00`, U+200B and U+202E anywhere in the string; 1 and 161 characters rejected, 2 and 160 accepted; leading and trailing U+0020 stripped and internal runs of U+0020 collapsed before the length check; a string of only spaces rejected; accented and non Latin letters accepted.
3. Logging: with every log record, header and exception message captured, the term never appears (and no hash of it), and `query_length` does.
4. Upstream shape: exactly one upstream call per miss, to `/search/companies`, whose params are exactly `{"q": <search string>, "items_per_page": 20}`; no other search path is ever called.
5. No paging: an upstream that returns 50 items gives 20 candidates and `truncated` true; the response has no `start_index`, `total_results`, `page_number`, `next` or page key.
6. Field minimisation: a recursive assertion over the whole response, the cache entry and every captured log record finds no `address`, `address_snippet`, `snippet`, `description`, `description_identifier`, `matches`, `links`, `date_of_cessation`, `etag`, `kind` or `page_number` key at any depth, and none of the upstream marker values from a hostile fixture, and no officer, PSC or person field.
7. Scope flag: each of the 8 allowlisted types gives `interpreted`; excluded, unknown and missing types give `unsupported_entity_type`.
8. Quota: a miss charges one allowance unit and one global unit; a hit charges none; a client over 60 calls gets 429 with `Retry-After`; an exhausted global budget gives 503 with `Retry-After`; the burst counter is shared with the company lookup (10 mixed calls in a minute, the 11th is 429); an upstream 429 starts the shared 300 second block.
9. Cache key: the key is the exact search string sent upstream. `Acme Ltd` and `acme ltd` make two upstream calls; `Straße` and `STRASSE` make two; `Acme  Ltd` (two spaces) and `Acme Ltd` share one entry; the TTL expires at 300 seconds; the bound is 1,000 entries.
10. Envelope: `items` empty with `total_results` 0 gives zero candidates, `truncated` false and `reported_total` 0; `items` empty with a positive, missing, negative, boolean or string total gives 502; a non empty list with a missing total gives `truncated` true and `reported_total` null; a total smaller than the number of items returned, negative, a boolean, a float or a string gives 502; a total equal to the number of candidates gives `truncated` false and `reported_total` equal to it; a larger total gives `truncated` true and the integer in `reported_total`, **both with 20 candidates returned (total 356) and with fewer than 20 returned (for example 5 returned and a total of 7)**, with no 502 in either case; 50 upstream items with total 50 give 20 candidates, true and 50; `items` absent or not a list gives 502; `truncated` is never false for an incomplete envelope.
11. Failure mapping: 404 gives 502 and is not cached; 401 gives 503; timeout gives 504; an unparseable body, a bad `company_number`, a 501 character title and a non string field each give 502; an upstream `company_status` outside the documented enum (for example `open`) is passed through; the missing key gives 503.
12. Hostile titles: a title containing `<script>`, an `<img onerror>`, quotes, control characters, a 500 character string and right to left override characters is returned byte for byte and the response stays valid JSON.
13. Burst first, then the raw body limit (route scoped, 4096 bytes): each of (a) a declared `Content-Length` over the limit, (b) no `Content-Length` with a body over the limit, (c) a `Content-Length` smaller than the body actually sent, and (d) a chunked or streamed body over the limit returns 413 with a fixed generic message that contains none of the body; in every case nothing is cached, the per client allowance and the global budget are untouched, no upstream call is made, and the term does not appear in any log record. **The burst counter is charged for each of them: ten oversized bodies in a minute from one client return 413, and the eleventh returns 429; a flood of 422 and of GET (405) requests reaches 429 the same way, and a client already over the burst limit gets 429 for an oversized body.** A body of exactly 4096 bytes is accepted for parsing and a body of 4097 bytes is not. Another route (for example the SAR extract route) is unaffected by the limit.
14. CORS: from `https://fincrimeradar.org` an `OPTIONS` preflight for `POST` with `Content-Type: application/json` returns 200 with `Access-Control-Allow-Origin` for that origin and allows `POST` and the `content-type` header; a disallowed origin gets no `Access-Control-Allow-Origin`; a preflight consumes no burst allowance (ten preflights followed by ten real searches all succeed) and makes no upstream call; and for the allowed origin the 405, 413, 422 and 429 responses each carry `Access-Control-Allow-Origin`.

Frontend tests (`scripts/test_kyb_lab_browser.py`, with candidate fixtures generated by `scripts/generate_kyb_api_fixtures.py` from the API): hostile titles render as literal text with no injected element; **titles sit inside `<bdi>` and, with a title that starts with U+202E and one that ends with it, the trusted fields keep their visual order (the company number, status, type and date elements stay to the right of the title element in left to right layout, measured with `getBoundingClientRect`, the same order as for a plain title of the same length)**; the request is a POST with a JSON body and no query string; picking a candidate runs the company lookup for exactly that number; no API request before focus and exactly one health request on first focus; every error code; the exact empty message and each of the two truncated messages of 14.7 (a reported total larger than the candidates shown, with `{total}` and `{shown}` rendered as text, and a missing total), each tested on its own path, with `{shown}` rendered as 20 and as a number below 20, plus the 413 message; the `Not interpreted in phase 1` label; 390px and 1440px layout.

### 14.9 Risks and open questions for the Codex re-review

1. **A new, bounded enumeration surface.** Name search is not equivalent to the existing number lookup. It adds: name to company discovery (find companies by what they are called, including by a person's name); up to 20 candidates per upstream call, against several calls per readout; unauthenticated use of FinCrimeRadar's Companies House key by anyone who can reach the page; and the possibility of queries that are a person's name. Company numbers can be enumerated (a Claude chat session's statement, not independently verified), but enumerating them is not a name index, and the earlier draft sentence that name search "adds convenience, not new exposure" is withdrawn. The surface is bounded by: 20 candidates per call and no paging, the per client allowance (60 calls per five minutes), the shared burst counter (10 starts per minute, so at most 10 searches per minute per client address key), the global budget of 300 calls per five minutes per instance, the 300 second cache, and the controls of 14.3. None of these stops a determined actor using many addresses; that residual risk is the same as section 3 item 3b.
   **Risk acceptance: Accepted by Pratik, 11 October 2026, at 20 candidates under the shared burst counter.** A lower search specific allowance remains an available later control if 20 candidates per call proves outside the accepted exposure; a separate or higher burst counter for search is not to be introduced.
2. The reported maximum `items_per_page` and any ceiling are forum observations from non staff users; the design avoids relying on them (explicit `items_per_page=20`, no `start_index`). The default, the empty result shape, the meaning of `total_results` and the number query are observed (14.2a). Still unobserved: the maximum `items_per_page` and any `start_index` ceiling, which the design does not use.
3. Searches share the burst counter with lookups, as Codex search spec review 1 agreed. A search plus a pick is two of the ten.
4. The messages are fixed strings (14.5 item 5 and 14.7 item 5). The only one that states a register fact is the truncated message with a total, which restates the register's own reported integer and the number of candidates shown, nothing computed by this service; the other truncated message states only that no total was reported.
5. A name can resemble a company the visitor does not mean. The `search_notice` and the requirement to pick explicitly are the only controls; there is no confidence score and none may be added.
6. Titles are untrusted free text from the register and may contain a person's name when a company is named after one. They are shown as the register shows them, inside `<bdi>`, and are neither enriched nor searched further.
7. Search terms may be personal data and Companies House receives them in a request URL (14.3). The privacy page update is tracked in BACKLOG.

## Appendix: Codex search spec review 1: finding map

| Finding | Severity | Correction | Where |
| --- | --- | --- | --- |
| A person name query is possible and the length only logging guarantee is not established | High | The intent guarantee is replaced by the enforceable boundary (only `/search/companies`, no officer, PSC or person fields). Terms are treated as potentially personal data. The public request is `POST /api/kyb/search` with a JSON body and never a query string, and a GET returns 405. Logging is qualified to application logs only, and the Companies House request URL disclosure is documented. A BACKLOG item tracks the privacy page wording. | 14.1, 14.3, 14.7 item 6, 14.8 items 1 to 3 |
| Case folded caching can return results for a different query | Medium | The cache key is the exact string sent upstream after whitespace normalisation. No case folding. The cache test is amended with the `Straße` and `STRASSE` pair. | 14.6 item 3, 14.8 item 9 |
| Empty and truncation rules do not fail closed for inconsistent envelopes | Medium | Empty only when `items` is an empty list and `total_results` is the integer 0. An invalid or inconsistent total gives 502. A missing total gives `truncated: true`. `truncated` is never false from an incomplete envelope. | 14.2b items 6 and 7, 14.4 items 3 and 4, 14.8 item 10 |
| The enumeration conclusion understates the new exposure | Medium | 14.9 item 1 is rewritten as a new, bounded enumeration surface, the earlier sentence is withdrawn, and a risk acceptance line is recorded. Exhaustion codes corrected: per client 429, global 503. The shared burst counter is kept and a separate higher counter is excluded. | 14.6 items 1 and 2, 14.9 item 1 |
| `textContent` does not prevent bidirectional visual spoofing | Low | Titles are rendered inside `<bdi>` (with `unicode-bidi: isolate`), as separate elements from the trusted fields, and a test measures that the trusted fields keep their visual order with right to left override titles. | 14.5 item 6, 14.7 item 3, 14.8 frontend tests |
| Two of the three drafted messages are not ready | Low | `search_notice` kept as accepted. Exact strings fixed: empty "No companies were returned for this search."; truncated "The register returned more matches than are shown. Only the first 20 are listed." (superseded by the two truncated messages of review 2) | 14.5 item 5, 14.7 item 5 |
## Appendix: Codex search spec review 2: finding map

Review 2 resolved five of the six review 1 findings (person name boundary and logging, cache key, empty and truncation derivation, enumeration exposure, bidirectional isolation), found the drafted messages partially resolved and raised four new points.

| Finding | Severity | Correction | Where |
| --- | --- | --- | --- |
| The truncated message is inaccurate when `total_results` is missing | Medium (partially resolved message) | Two messages chosen by path, a reported total larger than the candidates shown and a missing total. The response carries `reported_total` (integer or null) so the frontend can render the integer as text. Each path is tested. **Superseded by Pratik's decision of 11 October 2026 (below):** the first message now ends "Only {shown} are shown." and the invented partial page 502 rule is removed. | 14.4 item 4, 14.5, 14.7 item 5, 14.8 items 10 and frontend tests |
| Control characters are normalised before they are rejected | Medium | The raw `q` is validated first (`str.isprintable()`), then stripped and collapsed on U+0020 only, then length checked 2 to 160. `Acme\nLtd` gives 422. | 14.3 item 1, 14.8 item 2 |
| No raw request body size limit | Medium | A 4 KiB limit counted on received bytes (never trusting `Content-Length`), before JSON parsing, scoped to the search route, answered 413 with a generic message. Tests cover declared oversized, absent, understated and streamed bodies, each proving no cache write, no allowance or global charge and no upstream call. **Burst treatment settled by Pratik's decision of 11 October 2026 (below):** the burst check runs first, so rejected requests count toward it. | 14.3 item 7, 14.8 item 13 |
| CORS behaviour is not specified or tested | Low | Specified for the production origins, a disallowed origin, preflight cost and the CORS header on 405, 413, 422 and 429, with tests. | 14.3 item 8, 14.8 item 14 |
| The absolute URL statement is too broad | Low | Replaced with: "The supported frontend and API contract place the search term only in the JSON body. A query-string `q` on any request is ignored or rejected and never forwarded." | 14.3 opening paragraph |

### Decisions by Pratik, 11 October 2026 (the two open questions of review 2)

| Question | Decision | Where |
| --- | --- | --- |
| Should an oversized, invalid or wrong method request count toward the burst counter? | Yes. The per client burst check runs before the body is read, so 413, 422 and 405 requests count toward the burst limit, consistent with the lookup route. No upstream allowance, global budget, cache or upstream call is charged or made for them. An oversized body flood hits 429 at the burst limit, and the tests assert it. | 14.3 item 7, 14.8 item 13 |
| Should a page that is not full although a larger total is reported be a 502? | No. The invented partial page 502 rule is removed. A reported total larger than the number returned is always `truncated: true`, with the message "The register reports {total} matches. Only {shown} are shown." (`{shown}` is the count returned); the missing total message is kept. Tests cover 20 shown and fewer than 20 shown. | 14.4 item 4, 14.7 item 5, 14.8 item 10 and frontend tests |

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
