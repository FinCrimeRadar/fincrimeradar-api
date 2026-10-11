## Verdict

**FAIL**

Reviewed amended head `7daa0fa20e52901188c284d51476271c4fc9219c` against prior head `4ca9c6787c4328e088fcb93643d39de9ccba8b04`.

Five findings are resolved. One is partially resolved. Two new medium-severity POST defects require correction before implementation.

## Six-finding map

1. **RESOLVED: person-name use and logging boundary**

   New text:

   > “The caller's intent cannot be enforced: the endpoint takes free text, a caller can type a person's name...”

   > “Search terms may be personal data.”

   > “This is a statement about application generated logs only.”

   > “Companies House and its infrastructure may log request URLs...”

   [Lines 407 to 409 and 452 to 458](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L407-L458)

   This replaces the unenforceable intent guarantee, moves supported requests to a JSON body and accurately identifies the upstream disclosure.

2. **RESOLVED: case-folded cache collisions**

   New text:

   > “The key is the exact search string sent upstream... There is no case folding and no other normalisation...”

   [Line 506](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L506)

   The tests now distinguish `Acme Ltd` from `acme ltd`, and `Straße` from `STRASSE`.

3. **RESOLVED: empty and truncation derivation**

   New text:

   > “The result is zero candidates... only when `items` is an empty list and `total_results` is the integer 0.”

   > “If `total_results` is absent, the envelope is incomplete and `truncated` is true.”

   [Lines 445 to 446 and 469 to 470](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L445-L470)

   Invalid, negative, Boolean, inconsistent and missing totals are now covered by explicit tests at [line 531](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L531).

4. **RESOLVED: enumeration exposure**

   New text:

   > “A new, bounded enumeration surface.”

   > “enumerating [company numbers] is not a name index, and the earlier draft sentence that name search ‘adds convenience, not new exposure’ is withdrawn.”

   > “Risk acceptance: Accepted by Pratik...”

   [Lines 539 to 540](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L539-L540)

   Per-client 429 and global-budget 503 are also correctly distinguished at [line 504](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L504).

5. **RESOLVED: bidirectional title isolation**

   New text:

   > “The title is rendered inside a `<bdi>` element... as a separate element from the company number, status, type and date...”

   [Line 513](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L513)

   The browser test now checks visual ordering using `getBoundingClientRect` at [line 535](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L535).

6. **PARTIALLY RESOLVED: drafted messages**

   The accepted search notice is unchanged, and the empty message is now appropriately limited:

   > “No companies were returned for this search.”

   The truncated message is:

   > “The register returned more matches than are shown. Only the first 20 are listed.”

   [Line 515](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L515)

   This is inaccurate when `total_results` is missing. Under line 470, even 1 to 19 returned candidates produce `truncated: true`, but the register has not established that more matches exist or returned more than were shown.

   Required correction: use one message valid for every `truncated` path, such as “This result may be incomplete. At most the first 20 returned companies are shown”, or distinguish cap/count truncation from incomplete metadata.

## New POST findings

### Medium: control characters are normalised before they are rejected

The spec first says:

> “collapse internal runs of whitespace to a single space”

It then applies `str.isprintable()` to the resulting search string and claims that tabs, newlines and U+2028 are rejected.

[Lines 454 to 455](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L454-L455)

Those rules are misordered. For example, `Acme\nLtd` becomes `Acme Ltd`, changing `isprintable()` from false to true.

Required correction: validate the raw `q` string for non-printable and prohibited characters first. Only then strip and collapse permitted ordinary spaces, followed by final length validation.

### Medium: no raw request-body size limit

The parsed search string is capped at 160 code points, but no raw-body byte limit exists in the endpoint contract or tests. FastAPI must receive and parse the JSON body before applying model validation unless an earlier control intervenes. An attacker can therefore submit an oversized JSON or malformed body without making an upstream call.

[POST contract and tests, lines 452 to 459 and 522 to 535](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L452-L535)

Required correction:

- Enforce a small raw-body limit, for example 4 KiB, before JSON parsing.
- Return a generic 413 without echoing content.
- Cover declared oversized bodies, absent or understated `Content-Length`, and streamed or chunked bodies.
- Prove rejection occurs before caching, quota charging and upstream calls.

The current environment uses Starlette 0.37.2 and has no body-limit middleware configured. Current Starlette documents that an effective body limit must count received bytes as well as checking `Content-Length`. [Starlette request-body limit documentation](https://www.starlette.io/middleware/).

### Low: CORS behaviour works but is not specified or tested

A JSON POST from `fincrimeradar.org` triggers an OPTIONS preflight. The existing configuration allows the production origins, POST and request headers. A direct TestClient check returned:

- 200
- `Access-Control-Allow-Origin: https://fincrimeradar.org`
- allowed methods `GET, POST`
- allowed header `content-type`

Starlette documents that CORSMiddleware intercepts qualifying OPTIONS requests and returns 200 or 400 with the appropriate headers. [Starlette CORS documentation](https://www.starlette.io/middleware/).

Required correction: add tests proving:

- the production origin can preflight POST with `content-type`;
- a disallowed origin receives no allow-origin permission;
- preflight consumes no burst allowance and makes no upstream call;
- expected 405, 413, 422 and 429 responses retain the required CORS header for an allowed origin.

### Low: the absolute URL statement is too broad

The spec says:

> “The term is never carried in a query string, so it is never part of a FinCrimeRadar URL.”

Yet it also specifies and tests `GET /api/kyb/search?q=acme`, and permits a query-string `q` on POST while ignoring it.

The 405 GET behaviour itself is correctly specified and tested at [lines 452 and 522](https://github.com/FinCrimeRadar/fincrimeradar-api/blob/7daa0fa20e52901188c284d51476271c4fc9219c/docs/specs/kyb-lab-api-spec.md#L452-L522). Nothing is forwarded.

Required correction: say that the supported frontend and API contract place the term only in the JSON body. Do not claim an untrusted caller cannot place text in a rejected URL.

## Verification

- Exact reviewed range: `4ca9c67...7daa0fa`.
- Only `docs/specs/kyb-lab-api-spec.md` changed.
- `git diff --check` passed.
- Existing CORS preflight was exercised against the current FastAPI application.
- The whitespace-normalisation contradiction was reproduced directly.
- No implementation tests exist because this remains a specification-only branch.