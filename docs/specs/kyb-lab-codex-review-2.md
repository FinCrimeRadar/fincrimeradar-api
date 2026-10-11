## Verdict

**PASS SUBJECT TO CORRECTIONS**

Nine findings are resolved. Finding 1 is only partially resolved because two ambiguous date combinations remain. Three further corrections are required for the applicability label, scope description and overdue-accounts field.

## Original findings

1. **PARTIALLY RESOLVED**

   New text: “Any of: `appointment_verification_start_on` is after today; `appointment_verification_end_on` is after today; end is before start; end is present and start is absent | `verification_data_conflict`” and “Start, end and due all absent | `no_statement_status_fields`”. [Section 5.2](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:180)

   This resolves future start/end dates, end before start, end without start, ACSP-only evidence and due today. Two unresolved combinations remain:

   - Start, end and due are present, with due after end. Rule c returns `statement_removed`, although the later due date could represent a renewed obligation.
   - For PSCs, `appointment_verification_statement_date` after the due date is internally inconsistent, but rule 3 says it “never changes the status”.

   The API defines these fields individually but supplies no lifecycle precedence. [Officer field definitions](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officersummary?v=latest), [PSC field definitions](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individual).

   **Required correction:** Treat due after end, and PSC statement-open date after due, as `verification_data_conflict`, or document a primary-source lifecycle rule proving another interpretation.

2. **RESOLVED**

   New text restricts interpretation to `director`, `llp-member`, `llp-designated-member` and `individual-person-with-significant-control`. Every other role or kind is `not_interpreted`, even when verification fields are returned. [Section 5.1](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:168)

   This addresses the unsupported kind-agnostic derivation. The current role selection aligns with GOV.UK guidance for directors, members and PSCs, while corporate directors and corporate LLP members remain later-phase roles. [GOV.UK identity-verification scope](https://www.gov.uk/guidance/verifying-your-identity-for-companies-house).

3. **RESOLVED**

   New text: “No verification statement dates were returned for this role. This does not establish whether the person is verified, unverified, compliant, non-compliant or outside scope.” [Section 5.3](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:207)

   This now describes the absent fields without implying failure or compliance.

4. **RESOLVED**

   New text: “The register records that an identity verification statement was supplied for this role on `<date>`, linking a verified identity to it.” It also says that this is not evidence that the person, company or activity is legitimate. [Section 5.3](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:204)

   This accurately distinguishes identity verification from legitimacy, ownership accuracy and CDD. GOV.UK confirms that a personal code connects one verified identity to each role. [Personal-code guidance](https://www.gov.uk/guidance/companies-house-personal-codes-for-identity-verification).

5. **RESOLVED**

   New text: “`company_status` is returned raw. There is no indicator derived from status.” [Scope rule](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:80)

   `company_status_not_active` has been removed. The disclaimer now states that register status does not establish trading and dormant accounts are separate. [Section 6](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:258)

6. **RESOLVED**

   New text excludes `registered-overseas-entity`, `uk-establishment` and `oversea-company`, with no interpretation or further upstream calls. [Scope allowlist](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:65)

   This prevents the previous confirmation-statement, managing-officer, beneficial-owner and incorporation-label errors. `date_of_creation` is now returned under its raw API field name.

7. **RESOLVED**

   New text separates `active_psc_statements` from `ceased_psc_statements`, including `notified_on` and `ceased_on`. [Indicator table](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:223)

   This matches the API’s active and ceased statement model. [PSC statement specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/statementlist?v=latest).

8. **RESOLVED**

   New text derives current obligations only when `company_status == "active"`. Other statuses receive raw values, status, cessation date and a notice that current obligations are not interpreted. [Scope rule](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:80)

   This prevents dissolved, removed and closed records from producing current-obligation conclusions.

9. **RESOLVED**

   New text: “No thresholds, windows or counts of ‘recent’ events.” Officer events, registered-office filings and `date_of_creation` are returned as dated facts only. [Sections 4 and 6](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:147)

   The unsupported 12-month thresholds have been removed.

10. **RESOLVED**

    The six-item disclaimer now covers inaccuracies, errors, omissions, Companies House checking limitations, dormant accounts, legitimacy, ownership accuracy, indicator absence and CDD. [Global disclaimer](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:254)

    This substantially reflects the official disclaimer. [Searching the Companies House register](https://www.gov.uk/guidance/searching-the-companies-house-register).

## New issues

### Medium: `applicability: "applicable"` overstates what the data establishes

Exact text: “For a PSC: the kind is exactly `individual-person-with-significant-control`, and `ceased_on` is absent.”

GOV.UK expressly says a deceased PSC need not complete verification and may remain on the register until probate or letters of administration are received. Therefore individual kind plus no `ceased_on` does not prove that verification requirements apply. [GOV.UK due-date guidance](https://www.gov.uk/guidance/when-you-need-to-verify-your-identity-for-companies-house).

**Required correction:** Rename `applicable` to `interpreted` or `derivation_eligible`. Preserve the existing caveat that a passed due date is not a finding of an offence or non-compliance.

### Medium: the A1 scope description is broader than its allowlist

Exact text: “Phase 1 interprets only UK-registered companies and limited liability partnerships.”

The allowlist then excludes company types including `old-public-company`, `protected-cell-company`, `assurance-company` and `northern-ireland`. Exclusion is a legitimate conservative choice, but the introductory sentence implies broader coverage than the eight listed types.

**Required correction:** State that phase 1 interprets only the eight listed company and LLP profile types. Do not describe this as covering UK-registered companies generally.

### Medium: the accounts-overdue indicator uses the deprecated field

Exact text: “profile `accounts.overdue` is true”. [Indicator table](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:219)

The official schema marks `accounts.overdue` deprecated and instructs consumers to use `accounts.next_accounts.overdue`. [Company profile specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest).

**Required correction:** Change the basis and implementation tests to `accounts.next_accounts.overdue`.

## Required before PASS

- Complete the two remaining conflict checks in section 5.2.
- Replace the legal-sounding `applicable` state with an interpretation or derivation state.
- Narrow the scope description to the eight allowlisted types.
- Use `accounts.next_accounts.overdue`.
