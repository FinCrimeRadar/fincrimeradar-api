## Verdict

**PASS**

No high, medium or low severity finding requires correction. Neither feature needs to be taken offline.

## Review results

1. **PSC statements 404: PASS.** Every collection uses the same 404 path: `_fetch()` returns `None`, then `_get_collection()` returns `available=False` ([routes_kyb_lab.py:942](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:942)). PSC statements have no exception. The output is `not_returned`, empty arrays and `truncated: null`. Behaviour is proved at [test_kyb_lab.py:2291](C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:2291). No remaining runtime 404-to-empty path was found.

2. **Unsupported entity types: PASS.** Officers, both PSC resources, filings, charges and insolvency are structured `not_returned` objects at [routes_kyb_lab.py:916](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:916) and [routes_kyb_lab.py:1029](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:1029). The parametrised test covers every excluded type, an unknown type and a missing type at [test_kyb_lab.py:861](C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:861). `register_reported_values` remains null, but it is not a collection.

3. **Client keys: PASS, with a residual availability limitation.** Parsing, the 45-character input cap, scope-ID rejection, IPv4-mapped collapse and IPv6 `/64` grouping are correctly implemented at [client_ip.py:42](C:/Pratik/FinCrime/fincrimeradar-api/client_ip.py:42). CF, XFF and socket fallback order is correct at [client_ip.py:65](C:/Pratik/FinCrime/fincrimeradar-api/client_ip.py:65). Caller-controlled values cannot create oversized keys, and both maps are capped at 10,000 entries at [routes_kyb_lab.py:508](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:508).

   A caller controlling a `/48` can still rotate across approximately 65,536 `/64` keys and force LRU eviction. The 300-call global budget prevents unbounded Companies House use and protects the upstream key. This is principally KYB availability risk, but sufficiently high request volume could also consume the shared process CPU and bandwidth and therefore affect other routes. This limitation is already expressly documented in spec section 3.3b. Required correction: none for the present release. Defence-in-depth would be an edge-level route limit or a process-wide lookup-start cap. Offline before correction: **No**.

4. **Cloudflare trust and spoof test: PASS.** Render states that all inbound web-service requests pass through Cloudflare. [Render uptime guidance](https://render.com/docs/uptime-best-practices). I independently reproduced:

   - Forged `CF-Connecting-IP`: Cloudflare `403`, `Server: cloudflare`, body `error code: 1000`.
   - Eleven requests with rotating XFF: ten `200` responses followed by `429`.

   Section 9 step 5 at [kyb-lab-api-spec.md:330](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:330) now proves the claimed public deployment property. Unconditional CF trust remains deployment-specific and must be removed if the service moves away from Render’s Cloudflare boundary. I did not independently repeat the different-egress phone control.

5. **`9999-12-31` sentinel: PASS.** Companies House documents `appointment_verification_start_on` as the statement-supplied date and `appointment_verification_end_on` as the removal date, but does not document the sentinel convention. [Officer resource](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/officerlist?v=latest), [PSC resource](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/individual?v=latest). No conflicting definition was found in the official vendored OpenAPI or enumerations.

   The narrow inference is adequately supported by the live cross-check: all ten current Tesco directors carry the sentinel in the API projection, while the register shows ten “Verification requirements complete” records. [Tesco register page](https://find-and-update.company-information.service.gov.uk/company/00445790/officers).

   The rule is correctly ordered at [routes_kyb_lab.py:771](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:771). It applies only to the exact raw value with a start date, before conflict evaluation. Tests cover no-start conflicts, past due dates, other future ends, future starts, chronology, raw evidence and resigned or ceased records at [test_kyb_lab.py:2344](C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:2344). The display text at [routes_kyb_lab.py:795](C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:795) accurately describes the documented start field and does not assert that `9999-12-31` itself has an officially defined meaning.

6. **Frontend XSS: PASS.** API values reach the DOM through `textContent` or text nodes. Object evidence keys are also text. Error messages are frontend constants. No API-controlled value reaches an HTML-writing sink, event handler or attribute URL. See [kyb-lab.js:53](C:/Pratik/FinCrime/fincrimeradar-repo/js/kyb-lab.js:53) and [kyb-lab.js:87](C:/Pratik/FinCrime/fincrimeradar-repo/js/kyb-lab.js:87). The hostile fixture exercises company, officer, PSC, evidence, notice, attribution, charge, insolvency and register-reported-value paths at [test_kyb_lab_browser.py:101](C:/Pratik/FinCrime/fincrimeradar-repo/scripts/test_kyb_lab_browser.py:101).

7. **Links and requests: PASS.** The only result link uses the locally validated number and is omitted unless the response number matches exactly at [kyb-lab.js:378](C:/Pratik/FinCrime/fincrimeradar-repo/js/kyb-lab.js:378). Lookup requests use only the fixed API origin and encoded input at [kyb-lab.js:456](C:/Pratik/FinCrime/fincrimeradar-repo/js/kyb-lab.js:456). No response field controls a URL or request.

8. **Verbatim rule: PASS.** `label()` is used only for field keys and enum codes. API prose goes through identity conversion and text nodes. The browser test preserves leading spaces, doubled spaces and Unicode punctuation byte-identically at [test_kyb_lab_browser.py:646](C:/Pratik/FinCrime/fincrimeradar-repo/scripts/test_kyb_lab_browser.py:646).

9. **Frontend conclusions: PASS.** Frontend-authored text distinguishes available-empty from `not_returned`, adds no conclusion for charges or insolvency, omits null optional sections, and renders ordinary null values as `-`. No sentence asserts a register fact that the API did not establish.

10. **Privacy: qualified PASS.** No KYB API request occurs on page load. `/api/health` fires once on first input focus at [kyb-lab.js:512](C:/Pratik/FinCrime/fincrimeradar-repo/js/kyb-lab.js:512). However, Google Funding Choices, Google Tag Manager and Google Fonts are requested immediately by [kyb-lab.html:12](C:/Pratik/FinCrime/fincrimeradar-repo/kyb-lab.html:12). Therefore only the narrower claim “no request is made to the API” is true. Those third-party requests contain no company number, and consent storage defaults to denied. Required correction: none because the repository wording already makes the narrower API claim. Offline: **No**.

11. **Tests: behavioural, not shape-only.** API tests exercise derivation, suppression and limiter state. The browser suite executes the DOM, hostile payloads, URLs, error paths, focus, pagination and byte-identical prose. The fixture generator independently reproduced all fixtures from current API main and reported no response change from `be04f3a`.

## Verification

- API: **363 passed**.
- API Ruff and format checks: passed.
- Frontend static security checker: passed.
- Frontend browser regression: passed.
- Fixture manifest and cross-repository freshness: passed.
- Live frontend HTML and JavaScript match current remote main.
- Live Tesco response: ten current directors, ten sentinels, ten `statement_supplied`.
- Current remote heads are later documentation-only descendants: API `4f71f65`, frontend `d1a9249`.

The API health response does not expose a build SHA, so the exact deployed API commit cannot be independently derived from `/api/health`; live behaviour matches the reviewed implementation.
