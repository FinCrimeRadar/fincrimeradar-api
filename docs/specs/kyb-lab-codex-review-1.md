## Verdict

**FAIL**

The core source fields are identified correctly, but sections 5 and 6 contain outcome-defining ambiguities. Contradictory identity verification dates receive conclusive statuses, non-individual records are treated as though an individual was verified, and several indicators are not safe across entity types.

## Findings

### 1. High: contradictory field combinations receive a conclusive status

**Exact spec text:** “Per current appointment ... derive exactly one status” and “`appointment_verification_end_on` present | `statement_removed`”.

The API defines each date and marks every field optional. It does not define precedence or guarantee consistent combinations. Consequently:

- Due today is correctly `statement_due`. The due field is the date “by which” the statement must be supplied, and GOV.UK describes 14-day periods that include their final day.
- Start present with no due date can validly be `statement_supplied`. The fields are optional, and new directors provide their code with the appointment.
- End before start is internally contradictory but becomes `statement_removed`.
- A future start date becomes `statement_supplied`.
- End plus due, with no start, becomes `statement_removed`, even though it could represent incomplete data or a renewed obligation.
- Only `identity_verified_on` or ACSP details present becomes `no_verification_data`, although verification data was returned.

Sources: [officerSummary field definitions](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officersummary?v=latest), [PSC individual field definitions](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individual), [appointment due-date guidance](https://www.gov.uk/guidance/when-you-need-to-verify-your-identity-for-companies-house).

**Required correction:** Add an `indeterminate` or `verification_data_conflict` outcome for impossible or unresolved combinations. Validate chronology and future dates. Do not call the absence of the three appointment-statement fields an absence of all verification data.

### 2. High: the kind-agnostic PSC and officer rule is unsupported

**Exact spec text:** “One derivation applies to every officer and every PSC kind ... There is no special case by kind” and “A PSC of any kind that does carry the fields derives normally.”

Current GOV.UK guidance distinguishes roles and says requirements will begin later for corporate directors, corporate LLP members, limited partnerships and officers of corporate PSCs. The detailed API model for an individual PSC contains identity verification fields, while the corporate-entity and individual beneficial-owner detail models do not. A generic list schema is not evidence that the same legal meaning applies to every kind. [Current scope guidance](https://www.gov.uk/guidance/verifying-your-identity-for-companies-house), [individual PSC model](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individual), [corporate-entity PSC model](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/corporateentity?v=latest), [individual beneficial-owner model](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individualbeneficialowner).

This also makes the fixed wording about “the individual’s identity” nonsensical for a corporate entity or legal person.

**Required correction:** Determine applicability from the documented role and kind. Use an explicit `not_applicable` or `not_yet_in_scope` state where supported. Do not treat synthetic non-individual payloads containing generic list fields as proof that the individual appointment logic applies.

### 3. Medium: `no_verification_data` is materially ambiguous

**Exact spec text:** “Display it as ‘no verification data returned by the register’, not as a failure.”

This wording can be read as either compliant, unverified or failed. It is also false when ACSP verification fields exist but appointment-statement dates do not. Some roles are outside the current regime, and a deceased PSC need not complete verification even though the record remains. [GOV.UK applicability and deceased-PSC guidance](https://www.gov.uk/guidance/when-you-need-to-verify-your-identity-for-companies-house).

**Required correction:** Describe only the absent fact: no appointment verification statement status fields were returned. State that this does not establish whether the person is verified, unverified, compliant, non-compliant or outside scope.

### 4. Medium: the identity wording goes beyond the API fact

**Exact spec text:** “A supplied verification statement means the individual’s identity was verified for this appointment.”

The API field establishes that a statement was supplied for the appointment. GOV.UK explains that an individual verifies once and then uses a personal code to connect that verified identity to each role. It is not a fresh verification “for” each appointment, and Companies House says verification makes impersonation harder, not impossible. [API field meaning](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officersummary?v=latest), [personal-code and role-linking guidance](https://www.gov.uk/guidance/verifying-your-identity-for-companies-house).

The following sentence correctly avoids claims about ultimate ownership, filing accuracy, source of funds and completed CDD.

**Required correction:** Attribute the conclusion to the register: it records that a verification statement was supplied and a verified identity was linked to that role. Explicitly say this is not evidence that the person, entity or activity is legitimate.

### 5. High: `company_status_not_active` has no safe cross-entity definition

**Exact spec text:** “`company_status_not_active` | `register_fact` | profile status”.

The profile schema contains statuses including `active`, `registered`, `open`, `closed`, `removed` and `dissolved`, across company types including LLPs, UK establishments and registered overseas entities. The spec provides no type-aware mapping from those values to “not active”. A simple `status != active` rule would classify `registered` and `open` as adverse. [Company profile status and type schema](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest).

Dormancy is also an accounts type, not a company-status value. Absence of this indicator therefore does not establish that an entity is trading.

**Required correction:** Return the raw status as a register fact. Do not derive “not active” until each status is mapped by entity type from an authoritative source. Clarify that `active` does not establish trading activity and that dormant accounts are a separate fact.

### 6. High: several indicators misdescribe overseas-entity records

**Exact spec text:**

- “`confirmation_statement_overdue`”
- “counts of appointments and resignations”
- “`recently_incorporated`”
- “`psc_statement_present`”

For registered overseas entities, the profile’s `confirmation_statement` object refers to an annual statement, not an ordinary confirmation statement. For managing officers, `appointed_on` and `resigned_on` are notification dates, not necessarily appointment and resignation dates. The PSC endpoint also includes overseas-entity beneficial-owner kinds, which are not PSCs. [Company profile specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest), [officerSummary semantics](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officersummary?v=latest), [PSC and beneficial-owner kinds](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/notificationlist).

`recently_incorporated` is additionally unsupported for registered overseas entities and UK establishments unless `date_of_creation` is authoritatively shown to mean incorporation rather than UK registration or establishment.

**Required correction:** Make labels type-aware. Use annual or update statement terminology for registered overseas entities, notification wording for managing officers, beneficial-owner terminology where applicable, and suppress `recently_incorporated` where the field’s type-specific meaning is unverified.

### 7. Medium: `psc_statement_present` treats ceased statements as current

**Exact spec text:** “PSC statements list non empty”.

The statement resource provides `active_count`, `ceased_count` and `ceased_on`. A non-empty list can therefore contain only ceased statements. [PSC statement list specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/statementlist?v=latest).

**Required correction:** Distinguish active and ceased statements and include their dates. Do not present historical presence as a current register condition.

### 8. Medium: dissolved records can generate misleading current-obligation indicators

**Exact spec text:** “Per current appointment (officer not resigned, PSC not ceased)” and the `accounts_overdue`, `confirmation_statement_overdue` and `idv_statement_overdue` indicators.

Companies House retains dissolved records. Absence of `resigned_on` or `ceased_on` is therefore insufficient to prove a role or obligation remains current after dissolution, removal or closure. [Dissolved-record retention](https://www.gov.uk/guidance/searching-the-companies-house-register), [profile cessation field](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest).

**Required correction:** Gate current-duty language by entity status, or label these values explicitly as register-reported historical fields and show the status and cessation date alongside them.

### 9. Medium: the specification introduces its own risk-adjacent thresholds

**Exact spec text:** “No thresholds unless a verified source supplies one”, but:

- “appointments and resignations in the last 12 months”
- “filings in the last 12 months”
- “incorporated within the last 12 months”

No cited source establishes 12 months as a meaningful KYB threshold. Including binary “recent” indicators gives those events risk salience even without a “high churn” label.

**Required correction:** Either return dated facts without a binary recent indicator, or identify the 12-month rule as an FRC analytical choice requiring separate review. It cannot remain a pure `register_fact`.

### 10. Medium: the global `does_not_establish` list is accurate but insufficient

**Exact spec text:** the four-item list beginning “The register is not comprehensive...”.

The first, second and fourth statements are accurate. However, the official disclaimer also says the service may contain inaccuracies, Companies House accepts no responsibility for consequences of errors or omissions, and users should not treat it as a complete source. Companies House performs completeness and validity checks, so “does not closely review every document” should not imply that no checks occur. [Companies House register disclaimer](https://www.gov.uk/guidance/searching-the-companies-house-register).

**Required correction:** Add that register data may contain inaccuracies and errors or omissions, and that register status, identity statements and indicator absence do not establish legitimacy, trading activity, ownership accuracy or completed CDD. Preserve the fact that Companies House does perform basic completeness and validity checks.

## Direct answers

1. **No.** The ordinary cases are mostly correct, including due today, but contradictory and incomplete combinations are not safely classified.
2. **Yes.** `identity_verified_on` is specifically the ACSP verification date. It is not a universal verified flag, and its absence proves nothing.
3. **Yes.** The principal overstatement is “the individual’s identity was verified for this appointment”, especially when applied to non-individual kinds.
4. **Not safely.** It may describe missing appointment-statement fields, but readers could mistake it for failure, compliance or absence of all identity verification.
5. **Yes.** The 12-month indicators use unsourced thresholds, and multiple indicators are misleading for registered overseas entities, dissolved records and dormant-account contexts.
6. **Accurate but insufficient.** It omits inaccuracies, errors and omissions, status limitations, and the distinction between register facts and legitimacy or trading activity.