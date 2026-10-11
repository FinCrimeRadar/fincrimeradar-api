Verdict
PASS

1. RESOLVED: missing conflict rules. The derivation now includes: “start, end and due are all present and due is after end” and, for PSCs, “`appointment_verification_statement_date` is after `appointment_verification_statement_due_on`”, both producing `verification_data_conflict` before other rules. [Spec line 184 (line 184)](C:\\Pratik\\FinCrime\\fincrimeradar-api\\docs\\specs\\kyb-lab-api-spec.md:184)
2. RESOLVED: legal-sounding applicability. The response now uses `"eligibility": "derivation_eligible" | "not_interpreted"` and states: “Every other record gets `eligibility: "not_interpreted"` and `status: null`.” [Spec line 137 (line 137)](C:\\Pratik\\FinCrime\\fincrimeradar-api\\docs\\specs\\kyb-lab-api-spec.md:137), [line 176 (line 176)](C:\\Pratik\\FinCrime\\fincrimeradar-api\\docs\\specs\\kyb-lab-api-spec.md:176)
3. RESOLVED: scope overstatement. The spec now says: “Phase 1 interprets only the eight company profile types listed below. It does not cover UK-registered companies generally.” Unknown or non-allowlisted types are unsupported. [Spec line 50 (line 50)](C:\\Pratik\\FinCrime\\fincrimeradar-api\\docs\\specs\\kyb-lab-api-spec.md:50)
4. RESOLVED: deprecated accounts field. The indicator now uses “profile `accounts.next_accounts.overdue` is true” and expressly excludes `accounts.overdue`. [Spec line 219 (line 219)](C:\\Pratik\\FinCrime\\fincrimeradar-api\\docs\\specs\\kyb-lab-api-spec.md:219)

The deprecation sweep introduces no meaning-changing substitution:

* `accounts.next_accounts.overdue` is Companies House’s stated replacement for `accounts.overdue`.
* `links.charges` and `links.insolvency` are used only to decide whether to call the relevant resource. They do not themselves establish outstanding charges or insolvency history.
* The substantive indicators still depend on the returned charges or insolvency resource.
* The five other deprecated fields found by the sweep are unused.

These replacements match the official [Companies House company profile specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companyprofile?v=latest).
