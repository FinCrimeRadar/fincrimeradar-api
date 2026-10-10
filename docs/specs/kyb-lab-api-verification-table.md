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

| Spec use | Result | Exact location |
| --- | --- | --- |
| Company profile | EXISTS | `GET /company/{company_number}`, `spec/companyProfile.json#get`. Documented responses 200, 401, 404 only. 429 is not documented in the Swagger. |
| Officers list | EXISTS | `GET /company/{company_number}/officers`, `spec/companyOfficerList.json#listCompanyOfficers` |
| PSC list | EXISTS | `GET /company/{company_number}/persons-with-significant-control`, `spec/psc.json#listCompanyPSC` |
| PSC statements list | EXISTS | `GET /company/{company_number}/persons-with-significant-control-statements`, `spec/psc.json#listCompanyPSCStatements` |
| Filing history | EXISTS | `GET /company/{company_number}/filing-history`, `spec/filingHistory.json#listFilingHistory` |
| Charges | EXISTS | `GET /company/{company_number}/charges`, `spec/charges.json#chargeList` |
| Insolvency | EXISTS | `GET /company/{company_number}/insolvency`, `spec/insolvency.json#insolvencyCase`, response schema `spec/insolvency.json#definitions/companyInsolvency` (properties `etag`, `cases`, `status`) |

## Company profile fields (`spec/companyProfile.json#definitions`)

| Spec field | Result | Path |
| --- | --- | --- |
| company status | EXISTS | `companyProfile.company_status`, enum: active, dissolved, liquidation, receivership, administration, voluntary-arrangement, converted-closed, insolvency-proceedings, registered, removed, closed, open |
| company type | EXISTS | `companyProfile.type` (enum, 33 values) |
| incorporated on | EXISTS | `companyProfile.date_of_creation` (string; format not declared as date) |
| accounts overdue | EXISTS | `accountsInformation.overdue` (boolean), reached as `companyProfile.accounts.overdue`. Also `nextAccounts.overdue` at `accounts.next_accounts.overdue`. |
| confirmation statement overdue | EXISTS | `confirmationOfStatementInformation.overdue` (boolean), reached as `companyProfile.confirmation_statement.overdue` |
| charges flag | EXISTS | `companyProfile.has_charges` (boolean) |
| insolvency flag | EXISTS | `companyProfile.has_insolvency_history` (boolean). `has_been_liquidated` also exists. |
| previous names | EXISTS | `companyProfile.previous_company_names[]` with `name`, `effective_from`, `ceased_on` (all strings) |
| registered office changed recently | DOES NOT EXIST as a profile field | The spec lists it under `company`. It must be derived from filing history. See filing history below. |

## Officers (`spec/companyOfficerList.json`)

| Spec use | Result | Path |
| --- | --- | --- |
| page size param | EXISTS | query `items_per_page` (integer). No maximum is stated in the Swagger or on the reference page. |
| start index param | EXISTS | query `start_index` (integer) |
| other params | EXISTS | `register_type`, `register_view` (strings), `order_by` enum appointed_on, resigned_on, surname |
| totals | EXISTS | `officerList.total_results`, `active_count`, `resigned_count`, `items_per_page`, `start_index`. `inactive_count` does not exist. |
| name, role | EXISTS | `officerSummary.name`, `officerSummary.officer_role` (enum, 30 values) |
| appointed, resigned | EXISTS | `officerSummary.appointed_on`, `officerSummary.resigned_on` |
| identity verification | EXISTS (with a structural quirk) | `officerSummary.identity_verification_details`. Declared `type: object` with an `items.$ref` to `models/officerChanges.json#definitions/identityVerificationDetails`. The target is a normal object schema. |
| verification fields | EXISTS | `models/officerChanges.json#definitions/identityVerificationDetails`: `appointment_verification_start_on`, `appointment_verification_end_on`, `appointment_verification_statement_due_on`, `identity_verified_on`, `authorised_corporate_service_provider_name`, `anti_money_laundering_supervisory_bodies`, `preferred_name`. All dates are `format: date`. All optional. |

## PSCs (`spec/psc.json`, `spec/pscModels.json#definitions`)

| Spec use | Result | Path |
| --- | --- | --- |
| list page params | DIFFERS | `items_per_page` and `start_index` are declared `type: string` on the PSC list (integer on officers and filing history). `register_view` is also present. |
| statements list params | DIFFERS | `items_per_page` and `start_index` integer, but `register_view` is declared with `type: query` (a Swagger defect, not a real type). |
| totals | EXISTS | `list.total_results`, `active_count`, `ceased_count`, `items_per_page`, `start_index` |
| name, kind | EXISTS | `listTwoSummary.name`, `listTwoSummary.kind` (enum, 8 values incl. super-secure kinds) |
| notified, ceased | EXISTS | `listTwoSummary.notified_on`, `ceased_on`, `ceased` (boolean) |
| identity verification | EXISTS | `listTwoSummary.identity_verification_details` refs `pscModels.json#definitions/identityVerificationDetails`. Also on `individual`. ABSENT on `corporateEntity` and `legalPerson` detail models. |
| verification fields | DIFFERS from officers | Same set as officers plus `appointment_verification_statement_date` ("the date from which an identity verification statement can be supplied for the notification"). `appointment_verification_statement_due_on` exists and means "the date by which a statement must be supplied". |
| statement list items | EXISTS | `statementList.items[]` is `statement`: `kind`, `notified_on`, `ceased_on`, `statement` (string code), `restrictions_notice_withdrawal_reason`, `linked_psc_name`. `statement` has no enum in the Swagger. |
| statement descriptions | EXISTS in enumerations | `companies-house-enumerations/psc_descriptions.yml`, key `statement_description`, keyed by the `statement` code. Upstream: https://github.com/companieshouse/api-enumerations/blob/main/psc_descriptions.yml (saved copy is pinned to the commit in `SOURCE_COMMIT.txt`). |

## Filing history (`spec/filingHistory.json`)

| Spec use | Result | Path |
| --- | --- | --- |
| params | EXISTS | `category` (comma separated), `items_per_page`, `start_index` |
| ordering | NOT STATED | The Swagger does not state sort order. "Most recent first" in the spec is not confirmed by the OpenAPI file. |
| totals | EXISTS | `filingHistoryList.total_count`, `items_per_page`, `start_index` |
| item fields | EXISTS | `filingHistoryItem.type`, `category`, `date`, `description`, `transaction_id` |
| category for registered office | EXISTS | `filingHistoryItem.category` enum includes `address` |
| type codes for registered office change | DOES NOT EXIST in the OpenAPI | `filingHistoryItem.type` is an unconstrained string. No type code list exists in the Swagger, and `AD01` appears in none of the saved files. |
| alternative identifier | EXISTS in enumerations | `filing_history_descriptions.yml` holds description identifiers, e.g. `change-registered-office-address-company`, `...-with-date-old-address`, `...-with-date-old-address-new-address`, `change-registered-office-address-limited-liability-partnership` (and variants). `filingHistoryItem.description` is typed `string`, and whether it carries these identifiers or rendered text is not stated in the Swagger. Not yet confirmed against a live response. |

## Charges and insolvency

| Spec use | Result | Path |
| --- | --- | --- |
| charge status values | EXISTS | `spec/charges.json#definitions/chargeDetails.status` enum: outstanding, fully-satisfied, part-satisfied, satisfied |
| totals | DIFFERS | `chargeList.total_count`, `satisfied_count`, `part_satisfied_count`, and `unfiletered_count` (the Swagger spells it that way). Paging params are declared with `title` rather than `name`. |
| insolvency present | EXISTS | profile `has_insolvency_history`, plus `companyInsolvency.cases[]` (`type` enum 12 values, `dates`, `practitioners`, `number`) and `companyInsolvency.status` |

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
