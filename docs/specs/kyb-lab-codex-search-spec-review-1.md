## Verdict

**FAIL**

Reviewed branch head `4ca9c6787c4328e088fcb93643d39de9ccba8b04` against `main` at `d8d66e91ee0956e3f70e5c9fc4a5e4306b41d388`.

The field projection is appropriately narrow and the endpoint cannot directly return officer or PSC records. However, the logging promise, cache equivalence, completeness derivation and enumeration conclusion require correction before implementation.

## Findings

### High: A person-name query is possible and the length-only logging guarantee is not established

Exact text:

- “never with a person's name supplied as the intent of the feature” at [section 14.1, line 404](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L404)
- `GET /api/kyb/search?q=<text>` and “The raw text is never logged” at [lines 448 to 452](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L448-L452)

The endpoint accepts arbitrary printable text. A caller can submit a person's name, and a company title may itself contain a person's name. The fixed `/search/companies` path prevents this becoming an officer or PSC lookup, but intent cannot be enforced and the endpoint can be used to discover company candidates associated with a name.

The GET query also places the term in the request URL. Uvicorn access logging is disabled, but that does not prove the term is absent from infrastructure, proxy or upstream request logs. Render documents HTTP request logging of the requested URL on applicable plans. [Render logging documentation](https://render.com/docs/logging).

Required correction:

- Replace the unenforceable intent guarantee with the enforceable boundary: only `/search/companies` is called and no officer, PSC or person fields are returned.
- Treat search terms as potentially personal data.
- Either accept and document URL-level processing and retention at each boundary, or use a POST body for the public FinCrimeRadar request.
- Qualify “never logged” as application-generated logs only unless platform and upstream logging have also been established.

### Medium: Case-folded caching can return results for a different query

Exact text:

> “Cache: keyed by the normalised query ... case folded with `str.casefold`”

[Section 14.6, line 495](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L495)

The upstream request retains the query's case, while the cache key applies additional case folding. Companies House does not document case-fold equivalence. Distinct accepted inputs can collide. For example, `Straße` and `STRASSE` both case-fold to `strasse` but are different upstream query strings.

Required correction: key the cache by the exact string forwarded upstream after whitespace normalisation. Remove case folding unless the case-folded string is also the exact value sent upstream. Amend the cache test accordingly.

The 1,000-entry LRU and 160-code-point input cap otherwise bound cache cardinality and key size adequately.

### Medium: Empty and truncation rules do not fail closed for inconsistent envelopes

Exact text:

- “A 200 whose `items` is an empty list is zero candidates” at [line 459](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L459)
- If `total_results` is unavailable, `truncated` is true “only when 20 candidates came back” at [line 442](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L442)

The observed no-match shape was specifically `items: []` plus `total_results: 0`. An empty array with a positive, missing or invalid total is inconsistent, not proven empty.

Similarly, when `total_results` is absent or invalid, returning fewer than 20 items does not establish that further results do not exist. The official resource makes both `items` and `total_results` optional. [Companies House CompanySearch resource](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companysearch?v=latest).

Required correction:

- Recognise no-match only when `items` is an empty list and `total_results` is integer zero.
- Fail with 502 on inconsistent or invalid totals, or conservatively set `truncated: true`.
- Do not return `truncated: false` from an incomplete envelope.

### Medium: The enumeration conclusion understates the new exposure

Exact text:

> “company numbers are already sequential and enumerable, so name search adds convenience, not new exposure”

[Section 14.9, line 530](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L530)

This does add a new exposure through FinCrimeRadar:

- targeted name-to-company discovery;
- up to 20 candidates per upstream call;
- unauthenticated use of FinCrimeRadar's Companies House key;
- possible searches using a person's name.

Sequential company-number enumeration is not equivalent to a name index. The spec itself acknowledges that search is more efficient per upstream call.

The shared burst counter is the right default. Search cannot drain the upstream call budget faster than a five-to-seven-call company readout: one client can make at most ten search calls per minute, while lookups can consume the 60-call allowance in about ten uncached readouts. Search does, however, enumerate substantially more records per budget unit.

Required correction: describe this as a new, bounded enumeration surface and record an explicit risk acceptance. Consider a lower search-specific allowance if 20 candidates per call is outside the accepted exposure. Do not give search a separate higher burst counter.

Also correct [line 493](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L493): per-client exhaustion returns 429, but global-budget exhaustion returns 503 under section 3.

### Low: `textContent` does not prevent bidirectional visual spoofing

Exact text:

> “returned exactly as received ... rendered by the frontend only through `textContent`”

[Line 488](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L488)

The hostile-title test expressly includes right-to-left override characters at [line 518](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/4ca9c6787c4328e088fcb93643d39de9ccba8b04/docs/specs/kyb-lab-api-spec.md#L518). `textContent` prevents HTML execution, but not visual reordering of adjacent company numbers or status fields.

Required correction: require directional isolation, such as a separate `<bdi>` element or `unicode-bidi: isolate`, and test that surrounding trusted fields retain their visual order. The title can remain byte-identical.

### Low: Two of the three drafted messages are not ready

- **Search notice: acceptable.** “Several companies can share a similar name, and a name match is not an identity check. Confirm the company number before relying on a result.” It does not imply that confirming the number completes KYB.
- **Truncation sentence: not reviewable as drafted.** Section 14.7 only describes “a line saying more companies may match and to add more of the name”. No exact string is specified. Advising the user to add more of the name assumes an undocumented matching behaviour.
- **Empty sentence: overstated.** “No companies matched” implies an exhaustive conclusion. Use wording limited to the response, such as “No companies were returned for this search.”

## Conforming points

- The official endpoint is specifically `/search/companies`; officer, PSC, disqualified-officer and combined search paths are explicitly prohibited. [Companies House search-company reference](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/search-companies).
- Dropping addresses, snippets, match offsets, descriptions, links, cessation dates and kind removes the material unnecessary fields.
- The five retained register fields are proportionate for candidate identification. The derived scope flag is conservative because unknown or missing types are unsupported.
- Fixed host and path, parameter encoding, printable-character validation, generic validation errors and bounded cache state provide adequate injection and cache-volume controls.
- No paging and the shared burst counter are appropriate.
- The observed no-match capture supports an empty result only when the complete observed shape is enforced.

## Verification

- Confirmed the branch changed only the specification and verification table.
- Compared exact range `d8d66e9...4ca9c67`.
- Checked the vendored Companies House `search.json` against the current official search reference and resource page.
- Confirmed Python `isprintable()` rejects the listed control, zero-width, bidirectional and line-separator test values.
- Confirmed the `str.casefold` collision with two distinct accepted strings.
- `git diff --check` passed.
- No implementation tests exist or were run because v1.9 is specification-only.