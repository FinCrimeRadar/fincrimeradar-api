## Verdict

**FAIL**

The section 5 derivation is implemented correctly, and I found no SSRF, path traversal, runtime personal-field leakage, disclaimer overstatement or truncation defect. However, the 404 handling can produce materially false negative KYB results, and four further corrections are required.

## Findings

### High: Subresource 404s become confirmed-looking empty data

Exact path:

- [`_fetch()` returns `None` for every 404](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:633>).
- Officers, PSCs, statements, filing history and charges then use `... or {}` at [routes_kyb_lab.py:931](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:931>).
- The response consequently emits empty arrays and `truncated: false` at [routes_kyb_lab.py:984](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:984>) and [routes_kyb_lab.py:1074](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:1074>).

For an active company, this can wrongly imply that the register returned no officers, PSCs or statements, and can suppress identity-verification indicators. The global absence disclaimer does not qualify the individual collection as unavailable.

Required correction: preserve availability separately from contents. The smallest robust shape is to add source states, for example:

- `officers.source_status`
- `pscs.records_source_status`
- `pscs.statements_source_status`
- equivalent states for filing history and optional resources

Use values such as `available` and `not_returned`. When `not_returned`, arrays may remain empty for compatibility, but `truncated` must be `null`, and absence-dependent conclusions must not be derived. Amend the spec before changing this contract.

The existing test at [tests/test_kyb_lab.py:1291](<C:\Pratik\FinCrime\fincrimeradar-api\tests\test_kyb_lab.py:1291>) proves the defective behaviour.

::code-comment{title="[P1] Preserve unavailable source state" body="Every subresource 404 becomes None and is then coerced to an empty object. This makes unavailable officer, PSC and filing data indistinguishable from a confirmed empty result and emits false truncation or indicator absence. Add explicit per-resource source status, use null truncation when not returned, and suppress absence-dependent derivations." file="C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py" start=633 end=634 priority=1}

### Medium: One client can exhaust the shared upstream budget

The process budget is 300 calls per five minutes at [routes_kyb_lab.py:479](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:479>), but the client limit counts lookups at [routes_kyb_lab.py:508](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:508>). Each uncached eligible lookup makes five fixed upstream calls and up to seven at [routes_kyb_lab.py:931](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:931>). Thus roughly 43 maximum-cost lookups can exhaust the pool while complying with ten lookups per minute.

Direct impact is feature-wide 503 responses for KYB callers on that process. Other repository routes do not consume this deque, so they are not directly rate-limited. They can still suffer indirectly from shared process pressure.

There is no evidence of a per-request Companies House charge. Key standing becomes a risk if the key is shared, the service runs multiple instances, or restarts reset the process-local counter. Companies House permits 600 requests per application per five minutes and warns that applications which regularly exceed or attempt to bypass the limit may be banned without notice. [Companies House rate-limiting guidance](https://developer-specs.company-information.service.gov.uk/guides/rateLimiting).

Required correction: enforce a five-minute per-client allowance weighted by actual upstream calls and below a safe share of the global budget. If multiple processes can use the key, coordinate the application budget in shared state.

::code-comment{title="[P2] Client allowance can exhaust global budget" body="The per-client control counts lookups, while this shared control counts upstream calls. Since one lookup costs five to seven calls, one unauthenticated client can consume the complete 300-call pool while remaining within the client limit. Add a weighted five-minute client allowance and coordinate quota state if the key can be used by multiple processes." file="C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py" start=479 end=499 priority=2}

### Medium: PSC identity indicators claim the wrong source endpoint

The accumulator retains each record’s source at [routes_kyb_lab.py:993](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:993>), but both aggregate identity indicators hard-code `source_endpoint="officers"` at [routes_kyb_lab.py:1116](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:1116>). PSC-only and mixed results therefore expose contradictory provenance.

Required correction: replace the singular value with an endpoint list derived from contributing records, or split officer and PSC indicators. Add officer-only, PSC-only and mixed-source assertions. The current PSC test at [tests/test_kyb_lab.py:398](<C:\Pratik\FinCrime\fincrimeradar-api\tests\test_kyb_lab.py:398>) checks only nested evidence.

::code-comment{title="[P2] PSC evidence is attributed to officers" body="PSC hits enter the same aggregate, but this indicator always declares officers as its source endpoint. Derive source endpoints from the contributing records or separate the indicators, and test PSC-only and mixed-source cases." file="C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py" start=1116 end=1133 priority=2}

### Medium: Live fixtures remain linkable personal data

The three live bundles retain real company numbers and names plus exact appointment, notification and filing timelines, including:

- [live_active_ltd_outstanding_charges.json:2](<C:\Pratik\FinCrime\fincrimeradar-api\tests\fixtures\kyb\live_active_ltd_outstanding_charges.json:2>)
- [live_active_registered_office_change.json:2](<C:\Pratik\FinCrime\fincrimeradar-api\tests\fixtures\kyb\live_active_registered_office_change.json:2>)
- [live_dissolved.json:2](<C:\Pratik\FinCrime\fincrimeradar-api\tests\fixtures\kyb\live_dissolved.json:2>)

Replacing names with `PERSON A` does not anonymise records that can be joined back to the public register using the company number and exact event dates. The ICO confirms that pseudonymised, identifiable data remains personal data. [ICO pseudonymisation guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/pseudonymisation).

Required correction: replace live company identifiers, names, etags, transaction IDs and person-linked event combinations with synthetic values while preserving tested relationships. Add a fixture test that requires reserved synthetic company numbers and rejects captured live identifiers.

### Low: The client limiter has unbounded key cardinality

At [routes_kyb_lab.py:508](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:508>), every distinct client key creates an entry. Once the map exceeds 5,000 entries, each subsequent request scans the entire map while holding the shared state lock, but active entries remain. The limiter also runs before company-number validation at [routes_kyb_lab.py:1303](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:1303>).

A controlled run with 6,001 distinct active keys retained all 6,001 entries.

Required correction: use a hard-bounded TTL or LRU structure and avoid full-map scanning under the request-path lock. Validate the company number before allocating limiter state where safe.

::code-comment{title="[P3] Limiter state is not actually bounded" body="Crossing 5,000 entries triggers a full scan but removes only expired keys, so active distinct keys continue growing and every request performs linear work under the shared lock. Use a hard-bounded TTL or LRU structure and avoid full-map cleanup in the request path." file="C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py" start=508 end=524 priority=3}

## Confirmed conforming points

- Section 5.1 eligibility and the section 5.2 order match the specification at [routes_kyb_lab.py:675](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:675>) and [routes_kyb_lab.py:1009](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:1009>). All six conflict conditions run first. Invalid start, end, due or PSC statement-open dates become `verification_data_conflict`. Due today remains `statement_due`.
- No prohibited personal field is reachable through the reviewed success, error, log or `/api/health` paths. Upstream models ignore undeclared fields, errors are generic, logs contain operational metadata only, and health exposes only configuration and quota state.
- No SSRF or path traversal was found. The upstream base is fixed, company numbers are strictly normalised, endpoint templates are fixed, and redirects are disabled at [routes_kyb_lab.py:566](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:566>).
- `client_ip.py` selects from the right of the forwarding chain at [client_ip.py:34](<C:\Pratik\FinCrime\fincrimeradar-api\client_ip.py:34>), which rejects caller-prepended entries under the assumed one trusted append. Render states that public traffic passes through Cloudflare and that Cloudflare appends to `X-Forwarded-For`, while recommending its overwritten `CF-Connecting-IP` header where possible. [Render client-IP guidance](https://render.com/articles/host-pocketbase-on-render). The specification’s live spoof test is still required because the exact production chain is not established by repository code.
- The nine per-indicator `does_not_establish` strings are appropriately negative and do not overstate what the register establishes.
- The v1.1 filing-history `truncated` flag correctly compares `total_count` with the total number of filing items returned, not only matching registered-office filings, at [routes_kyb_lab.py:855](<C:\Pratik\FinCrime\fincrimeradar-api\routes_kyb_lab.py:855>).

## Verification

The tests prove substantial behaviour, not only response shape, but they do not prove every requested invariant.

Completed checks:

- Exact head `f5f6c3659ebda7376326a6275662c12629997301` reviewed against base `4e4d10942f68f3293d341b65e735754be8998e3a`.
- Full suite: 258 passed, with 897 deprecation warnings.
- Targeted 404, PSC identity, global-budget and client-limit tests: 4 passed.
- Ruff checking and formatting checks passed.
- Import smoke test passed.
- Working tree remained clean.

Missing or inadequate tests:

- Only one unparseable identity date field is exercised, not all four.
- The 404 test proves the incorrect empty-list behaviour.
- PSC tests do not assert aggregate source provenance.
- The nine exact indicator disclaimer strings are not contract-tested.
- Fixture tests check pseudonym-shaped names, not re-identification through retained identifiers.
- Header tests verify the helper algorithm, not the live Render chain.
- Personal-field checks are not systematically applied to every error, log and health path.

Security scan `93d776fb-4cd2-4f54-9059-41d34d50448c` completed with one medium and one low security finding. Review usage was 433,404 tokens over about 31 minutes.
