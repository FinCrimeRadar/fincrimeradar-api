# KYB Lab API phase 1: field verification table

Verified 10 October 2026 against the official Companies House Public Data API Swagger 2.0 files and the Companies House `api-enumerations` repository. Spec: `kyb-lab-api-spec.md`. No endpoint code has been written.

## Sources saved in this repository

| What | Saved at | Origin |
| --- | --- | --- |
| Swagger 2.0 root index (the "OpenAPI specification" link) | `companies-house-public-data-api.swagger.json` | https://developer-specs.company-information.service.gov.uk/api.ch.gov.uk-specifications/swagger-2.0/spec/swagger.json |
| Referenced resource specs (23 files) | `companies-house-swagger/spec/*.json`, `companies-house-swagger/models/*.json` | same host, same `swagger-2.0/spec` and `swagger-2.0/models` paths |
| Enumerations | `companies-house-enumerations/*.yml`, commit in `SOURCE_COMMIT.txt` | https://github.com/companieshouse/api-enumerations at the pinned commit |

Structural note: the root file is only an index. Every path is a `$ref` to a separate file, and every `$ref` is written against `http://127.0.0.1:10000/...`, which is a placeholder host. The 23 referenced files were fetched from the real host and every `$ref` resolves locally. Paths below are written `file#path`, relative to `companies-house-swagger/`.

Result legend: EXISTS, DIFFERS (exists but not as the spec describes), DOES NOT EXIST.

## Endpoints

| Spec use | Result | Exact location | Deprecated |
| --- | --- | --- | --- |
| Company profile | EXISTS | `GET /company/{company_number}`, `spec/companyProfile.json#get`. Documented responses 200, 401, 404 only. 429 is not documented in the Swagger. | No |
| Officers list | EXISTS | `GET /company/{company_number}/officers`, `spec/companyOfficerList.json#listCompanyOfficers` | No |
| PSC list | EXISTS | `GET /company/{company_number}/persons-with-significant-control`, `spec/psc.json#listCompanyPSC` | No |
| PSC statements list | EXISTS | `GET /company/{company_number}/persons-with-significant-control-statements`, `spec/psc.json#listCompanyPSCStatements` | No |
| Filing history | EXISTS | `GET /company/{company_number}/filing-history`, `spec/filingHistory.json#listFilingHistory` | No |
| Charges | EXISTS | `GET /company/{company_number}/charges`, `spec/charges.json#chargeList` | No |
| Insolvency | EXISTS | `GET /company/{company_number}/insolvency`, `spec/insolvency.json#insolvencyCase`, response schema `spec/insolvency.json#definitions/companyInsolvency` (properties `etag`, `cases`, `status`) | No |

## Company profile fields (`spec/companyProfile.json#definitions`)

| Spec field | Result | Path | Deprecated |
| --- | --- | --- | --- |
| company status | EXISTS | `companyProfile.company_status`, enum: active, dissolved, liquidation, receivership, administration, voluntary-arrangement, converted-closed, insolvency-proceedings, registered, removed, closed, open | No |
| company type | EXISTS | `companyProfile.type` (enum, 33 values) | No |
| incorporated on | EXISTS | `companyProfile.date_of_creation` (string; format not declared as date) | No |
| accounts overdue | EXISTS, DEPRECATED | `accountsInformation.overdue` (boolean), reached as `companyProfile.accounts.overdue`. Also `nextAccounts.overdue` at `accounts.next_accounts.overdue`. | Yes. Use `accounts.next_accounts.overdue` |
| confirmation statement overdue | EXISTS | `confirmationOfStatementInformation.overdue` (boolean), reached as `companyProfile.confirmation_statement.overdue` | No |
| charges flag | EXISTS, DEPRECATED | `companyProfile.has_charges` (boolean) | Yes. Use `links.charges` |
| insolvency flag | EXISTS, DEPRECATED | `companyProfile.has_insolvency_history` (boolean). `has_been_liquidated` also exists. | Yes. Use `links.insolvency` |
| accounts overdue (successor) | EXISTS | `accountsInformation.next_accounts` is `nextAccounts`, with `nextAccounts.overdue` (boolean), reached as `companyProfile.accounts.next_accounts.overdue`. This is the field the swagger names as the replacement for `accounts.overdue`. | No |
| charges present (successor) | EXISTS | `linksType.charges` (string URL), reached as `companyProfile.links.charges`. The spec checks only that the key is present and never follows the URL. | No |
| insolvency present (successor) | EXISTS | `linksType.insolvency` (string URL), reached as `companyProfile.links.insolvency`. The spec checks only that the key is present and never follows the URL. | No |
| date of cessation | EXISTS | `companyProfile.date_of_cessation` (string) | No |
| previous names | EXISTS | `companyProfile.previous_company_names[]` with `name`, `effective_from`, `ceased_on` (all strings) | No |
| registered office changed recently | DOES NOT EXIST as a profile field | The spec lists it under `company`. It must be derived from filing history. See filing history below. | No |

## Officers (`spec/companyOfficerList.json`)

| Spec use | Result | Path | Deprecated |
| --- | --- | --- | --- |
| page size param | EXISTS | query `items_per_page` (integer). No maximum is stated in the Swagger or on the reference page. | No |
| start index param | EXISTS | query `start_index` (integer) | No |
| other params | EXISTS | `register_type`, `register_view` (strings), `order_by` enum appointed_on, resigned_on, surname | No |
| totals | EXISTS | `officerList.total_results`, `active_count`, `resigned_count`, `items_per_page`, `start_index`. `inactive_count` does not exist. | No |
| name, role | EXISTS | `officerSummary.name`, `officerSummary.officer_role` (enum, 30 values) | No |
| appointed, resigned | EXISTS | `officerSummary.appointed_on`, `officerSummary.resigned_on` | No |
| identity verification | EXISTS (with a structural quirk) | `officerSummary.identity_verification_details`. Declared `type: object` with an `items.$ref` to `models/officerChanges.json#definitions/identityVerificationDetails`. The target is a normal object schema. | No |
| verification fields | EXISTS | `models/officerChanges.json#definitions/identityVerificationDetails`: `appointment_verification_start_on`, `appointment_verification_end_on`, `appointment_verification_statement_due_on`, `identity_verified_on`, `authorised_corporate_service_provider_name`, `anti_money_laundering_supervisory_bodies`, `preferred_name`. All dates are `format: date`. All optional. | No |

## PSCs (`spec/psc.json`, `spec/pscModels.json#definitions`)

| Spec use | Result | Path | Deprecated |
| --- | --- | --- | --- |
| list page params | DIFFERS | `items_per_page` and `start_index` are declared `type: string` on the PSC list (integer on officers and filing history). `register_view` is also present. | No |
| statements list params | DIFFERS | `items_per_page` and `start_index` integer, but `register_view` is declared with `type: query` (a Swagger defect, not a real type). | No |
| totals | EXISTS | `list.total_results`, `active_count`, `ceased_count`, `items_per_page`, `start_index` | No |
| name, kind | EXISTS | `listTwoSummary.name`, `listTwoSummary.kind` (enum, 8 values incl. super-secure kinds) | No |
| notified, ceased | EXISTS | `listTwoSummary.notified_on`, `ceased_on`, `ceased` (boolean) | No |
| identity verification | EXISTS | `listTwoSummary.identity_verification_details` refs `pscModels.json#definitions/identityVerificationDetails`. Also on `individual`. ABSENT on `corporateEntity` and `legalPerson` detail models. | No |
| verification fields | DIFFERS from officers | Same set as officers plus `appointment_verification_statement_date` ("the date from which an identity verification statement can be supplied for the notification"). `appointment_verification_statement_due_on` exists and means "the date by which a statement must be supplied". | No |
| statement list items | EXISTS | `statementList.items[]` is `statement`: `kind`, `notified_on`, `ceased_on`, `statement` (string code), `restrictions_notice_withdrawal_reason`, `linked_psc_name`. `statement` has no enum in the Swagger. | No |
| statement descriptions | EXISTS in enumerations | `companies-house-enumerations/psc_descriptions.yml`, key `statement_description`, keyed by the `statement` code. Upstream: https://github.com/companieshouse/api-enumerations/blob/main/psc_descriptions.yml (saved copy is pinned to the commit in `SOURCE_COMMIT.txt`). | No |

## Filing history (`spec/filingHistory.json`)

| Spec use | Result | Path | Deprecated |
| --- | --- | --- | --- |
| params | EXISTS | `category` (comma separated), `items_per_page`, `start_index` | No |
| ordering | NOT STATED | The Swagger does not state sort order. "Most recent first" in the spec is not confirmed by the OpenAPI file. | No |
| totals | EXISTS | `filingHistoryList.total_count`, `items_per_page`, `start_index` | No |
| item fields | EXISTS | `filingHistoryItem.type`, `category`, `date`, `description`, `transaction_id` | No |
| category for registered office | EXISTS | `filingHistoryItem.category` enum includes `address` | No |
| type codes for registered office change | DOES NOT EXIST in the OpenAPI | `filingHistoryItem.type` is an unconstrained string. No type code list exists in the Swagger, and `AD01` appears in none of the saved files. | No |
| alternative identifier | EXISTS in enumerations | `filing_history_descriptions.yml` holds description identifiers, e.g. `change-registered-office-address-company`, `...-with-date-old-address`, `...-with-date-old-address-new-address`, `change-registered-office-address-limited-liability-partnership` (and variants). `filingHistoryItem.description` is typed `string`, and whether it carries these identifiers or rendered text is not stated in the Swagger. Not yet confirmed against a live response. | No |

## Charges and insolvency

| Spec use | Result | Path | Deprecated |
| --- | --- | --- | --- |
| charge status values | EXISTS | `spec/charges.json#definitions/chargeDetails.status` enum: outstanding, fully-satisfied, part-satisfied, satisfied | No |
| totals | DIFFERS | `chargeList.total_count`, `satisfied_count`, `part_satisfied_count`, and `unfiletered_count` (the Swagger spells it that way). Paging params are declared with `title` rather than `name`. | No |
| insolvency present | EXISTS | profile `has_insolvency_history`, plus `companyInsolvency.cases[]` (`type` enum 12 values, `dates`, `practitioners`, `number`) and `companyInsolvency.status` | No |

## Rate limit

| Fact | Result | Source |
| --- | --- | --- |
| 600 requests per five minutes, 429 for the rest of the window | EXISTS | https://developer-specs.company-information.service.gov.uk/guides/rateLimiting |
| rate limit response headers, `Retry-After` on 429 | DOES NOT EXIST in the saved files | Neither the Swagger nor the enumerations document any rate limit header. The spec's "quota headers" smoke check cannot be written against documented fields. |

## Spec items that do not exist or differ (do not substitute)

1. Registered office change filing type codes: not in the OpenAPI. Needs a decision, see the report.
2. `registered_office_changed_recently` is not a profile field; it must be derived.
3. PSC verification differs from officers: extra `appointment_verification_statement_date`, and absent on corporate entity and legal person detail models. Spec section 5 states only the officer fields.
4. Filing history sort order is not documented.
5. Maximum page size is not documented for any list endpoint.
6. 429 and rate limit headers are not documented in the Swagger.
7. The OpenAPI deliverable is Swagger 2.0 split across 24 files with placeholder hosts, not one OpenAPI document.

## Deprecation sweep (11 October 2026)

Method: every `description` in all 23 saved Swagger files, and every query, path and body parameter, was searched for "deprecated", "obsolete", "superseded", "no longer", "replaced by" and "use ... instead". Eight properties carry a deprecation note, all in `spec/companyProfile.json`. None of the other 22 files contains one. The enumerations repository files were not part of this sweep.

| Deprecated property | Swagger note | Used by the spec | Action |
| --- | --- | --- | --- |
| `accountsInformation.overdue` (`accounts.overdue`) | Deprecated. Please use accounts.next_accounts.overdue | Yes, `accounts_overdue` indicator | Replaced by `accounts.next_accounts.overdue` |
| `companyProfile.has_charges` | Deprecated. Please use links.charges | Yes, to decide whether to call the charges endpoint | Replaced by presence of `links.charges` |
| `companyProfile.has_insolvency_history` | Deprecated. Please use links.insolvency | Yes, to decide whether to call the insolvency endpoint | Replaced by presence of `links.insolvency` |
| `companyProfile.has_been_liquidated` | Deprecated. Please use links.insolvency | No | None |
| `companyProfile.is_community_interest_company` | Deprecated. Please use subtype | No | None |
| `accountsInformation.next_due` | Deprecated. Please use accounts.next_accounts.due_on | No | None |
| `accountsInformation.next_made_up_to` | Deprecated. Please use accounts.next_accounts.period_end_on | No | None |
| `lastAccounts.made_up_to` | Deprecated. Please use accounts.last_accounts.period_end_on | No | None |

Caveat: the successor `links.charges` and `links.insolvency` are URL strings. Whether the register includes the key only when the resource has content is not stated in the Swagger. A live response is needed to confirm that key presence is a reliable signal; if it is not, call the endpoint and treat a 404 as "none".

## Live verification (11 October 2026)

Method: authenticated calls to the live Public Data API from a local run, roughly 170 requests in total. Raw responses were kept outside the repository. The committed fixtures `tests/fixtures/kyb/synthetic_*.json` are fully synthetic since spec v1.2 (reserved `ZZ` company numbers, synthetic names, etags, transaction IDs and charge particulars, shifted dates). They keep the structure the live capture showed. No live response is committed.

| Question | Result | Sample |
| --- | --- | --- |
| Does `filing-history` `description` carry the enumeration identifier? | Yes. Address category items carried identifiers such as `change-registered-office-address-company-with-date-old-address-new-address` (type `AD01`), never rendered text. The PROVISIONAL indicator is confirmed for the company identifiers. | 486 filing items across 30 active ltd companies; 19 `AD01` items with an identifier |
| Is the LLP variant confirmed live? | No. No LLP was sampled, so the three LLP identifiers are verified against the enumerations only. | none |
| Other address category items observed | `AD02 change-sail-address-company` and `AD03 move-registers-to-sail-company` (both correctly excluded) and three items of type `287` with description `legacy`. The `legacy` items are older registered office changes and are not matched by the six identifiers, so very old changes are not listed. | 5 of 24 address items |
| Is absence of `links.charges` reliable? | Yes in this sample. All 21 companies without the key returned 200 from `/charges` with `total_count` 0. | 21 companies |
| Is presence of `links.charges` reliable? | Yes. 9 of 9 returned 200 with charges (7 with outstanding charges, 2 with none outstanding). | 9 companies |
| Is the deprecated `has_charges` reliable? | No. It was false for 9 of 30 companies that had the `links.charges` key, including companies with outstanding charges. | 30 companies |
| `links.insolvency` | Present for 6 of 6 companies in liquidation (all returned 200; 5 with one case, 1 with zero cases). Absent for 6 of 6 companies tested without it, and `/insolvency` returned 404 for each. An insolvency resource with zero cases still fires `insolvency_history` under "insolvency resource present". | 12 companies |
| `/persons-with-significant-control-statements` when none exist | 404, for all 3 captured companies. Since spec v1.4 this is reported as `not_returned` (empty arrays, `truncated: null`), not as an empty list: Companies House documents the 404 only as "Resource not found" (Codex PR review 2). | 3 companies |
| `X-Ratelimit-*` response headers | `X-Ratelimit-Limit` (600), `X-Ratelimit-Remain` (counts down per request), `X-Ratelimit-Reset` (epoch seconds), `X-Ratelimit-Window` (`5m`). They are logged only and never used for control. | all calls |
| Filing history total versus returned | At least one sampled company had `total_count` above the 100 returned, so the truncation flag added in v1.1 matches a real case. | 30 companies |

## Company name search (11 October 2026, for spec section 14)

Static facts were checked against `spec/search.json` (vendored) and the developer specs pages named in spec section 14.2. Then six live calls were made on 11 October 2026 and only the shape is recorded here: no title, address or other register data is stored in this repository.

| Question | Result | Path or page |
| --- | --- | --- |
| Endpoint path | `GET /search/companies` (host `api.company-information.service.gov.uk`). The vendored file keys it as operation `searchCompanies` and does not state the path. | developer reference page |
| Parameters | `q` (required string), `items_per_page` (integer), `start_index` (integer), `restrictions` (string). No default, minimum or maximum is stated for any of them. | `search.json#/searchCompanies/get/parameters` |
| Default page size | Documented: none. Live (probe a, no `items_per_page` sent): the response carried `items_per_page` 20 and returned 20 items. | live, 11 October 2026 |
| Envelope keys | Live: `items`, `items_per_page`, `kind` (`search#companies`), `page_number`, `start_index`, `total_results`. `page_number` is not in the documentation. | live; `search.json#/definitions/CompanySearch`, `CommonSearch` |
| Meaning of `total_results` | Documented as "further" results (ambiguous). Live: 356 for a common name with 20 items returned, identical with and without `items_per_page=20`. So it is the whole match count. | live probes a and b |
| No match result | Live (probe c, 13 character nonsense string): 200, the same six key envelope, `items` present and empty, `total_results` 0. Not a 404. | live probe c |
| Company number as `q` | Live (probe d, 8 digits): 200, 1 item, `total_results` 1, status `active`. That the item is the queried company was not recorded. | live probe d |
| 1 character and 200 character `q` | Not observed: the output of those two probes was lost to output truncation in the session and the six call limit was then reached. Re-run before the build. | none |
| Item keys | Live: 13 keys (`address`, `address_snippet`, `company_number`, `company_status`, `company_type`, `date_of_creation`, `description`, `description_identifier`, `kind`, `links`, `matches`, `snippet`, `title`). Key sets differ between items (`snippet` and `matches` are missing on some). `company_number` 8 characters, `date_of_creation` present and `address` an object in all 20. | live probes a and b |
| Fields used | `items[].company_number`, `items[].title`, `items[].company_status`, `items[].company_type`, `items[].date_of_creation`. All five are listed as required in the schema. | `search.json#/definitions/CompanySearchItems`, `CommonSearchItems/properties/title` |
| Fields dropped | `items[].address`, `address_snippet`, `snippet`, `description`, `description_identifier`, `matches`, `links`, `date_of_cessation`, `kind`; envelope `etag`, `kind`, `page_number`, `start_index`, `total_results`. | same |
| `company_status` values | Documented enum has 10 values. Live values seen: `active`, `closed`, `dissolved`, `liquidation`, `open`. `closed` and `open` are outside the documented enum. | `CompanySearchItems/properties/company_status/enum`; live probes a and b |
| `company_type` enum | 26 values in the vendored file, 23 stated on the developer resource page. 4 distinct values in the first 20 results of probe a. The allowlist of section 3 is used by membership only. | `CompanySearchItems/properties/company_type/enum` |
| Order | Live: not sorted by title; the first item has the query text in its title and 19 of 20 titles contain it. Not documented. | live probe a |
| Documented limits | None. Forum users (not Companies House staff) report maximum 100 and a 1000 result ceiling with HTTP 416; not tested and not confirmed by Companies House. | spec 14.2b item 1 |
| Not documented | maximum `items_per_page`, maximum `start_index`, `q` length limits, matching rule, how the search counts against the 600 per five minute limit. | spec 14.2b |
