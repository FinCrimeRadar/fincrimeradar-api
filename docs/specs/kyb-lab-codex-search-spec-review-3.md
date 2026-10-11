## Verdict

**FAIL**

Reviewed exact range `7daa0fa20e52901188c284d51476271c4fc9219c..623d8bef7401463220e439a514100cfae51c5844`.

The requested corrections are mostly resolved, but two medium issues remain.

## Findings

### Medium: the count contract contradicts itself

The amended response now returns:

> `"reported_total": "integer | null..."`

and displays:

> “The register reports {total} matches. Only {shown} are shown.”

[Spec lines 473, 483 and 519](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:473)

However, earlier text still says:

> “no user facing text describes ... how many companies match.”

> “The count itself is not returned.”

[Spec lines 444 and 447](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:444)

Required correction: update those two statements. State that `reported_total` is the register-reported value, may be subject to the undocumented apparent ceiling already recorded in the spec, and is not FinCrimeRadar’s independently established match count.

### Medium: the body guard does not bound slow bodies

The new rule counts streamed bytes and aborts above 4096 bytes:

> “an absent, understated or chunked body is counted as it streams and aborted when it passes 4096 bytes.”

The test matrix covers declared, absent, understated and chunked lengths, plus 4096 and 4097 byte boundaries. [Spec lines 461 and 538](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:461)

This prevents size bypasses, including chunked framing, but a client can remain at or below 4096 bytes and deliver the body indefinitely. Uvicorn documents its default keep-alive timeout as applying between requests, not as an in-progress request-body deadline, and the production command has no concurrency limit. [Uvicorn server behaviour](https://www.uvicorn.org/server-behavior/)

Required correction:

- Define an overall or idle body-receive deadline, or cite and test an equivalent production edge control.
- Add a slow-stream test proving timeout, no parsing, no cache or upstream quota charge, and no upstream call.
- Replace “the term is never read into the application”. The ASGI wrapper necessarily receives up to 4097 bytes. The accurate claim is that an oversized term is not parsed or passed to the route handler.

Compression is not presently a direct bypass because pinned Starlette 0.37.2 passes the received body directly to `json.loads` and does not decompress request content. However, the contract and test matrix do not lock this down. Require rejection of non-identity `Content-Encoding`, or explicitly limit the decompressed representation before parsing, and add a compressed-body test. Starlette’s body-limit guidance confirms that effective enforcement must count actual ASGI-received bytes rather than trusting `Content-Length`. [Starlette middleware documentation](https://www.starlette.io/middleware/)

## Requested confirmations

- **Finding 6: RESOLVED.** The reported-total path uses “The register reports {total} matches. Only {shown} are shown.” The missing-total path uses “The register did not report a total, so this list may be incomplete.” [Spec line 519](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:519) Both paths, including 20 shown and fewer than 20 shown, are required in the API and browser tests at lines 535 and 541.

- **Validation order: RESOLVED.** Raw `q` is checked with `str.isprintable()` before U+0020-only normalisation and final length validation. [Spec line 455](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:455) The test matrix covers newline, tab, U+2028, NBSP, zero-width and bidirectional controls at line 527.

- **4 KiB received-byte limit: PARTIALLY RESOLVED.** Content-Length and chunked bypasses are covered. Compression policy and slow-body duration are not.

- **CORS contract and tests: RESOLVED.** Allowed and disallowed origins, preflight exemption, no upstream call, and CORS headers on 405, 413, 422 and 429 are specified. [Spec lines 462 and 539](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:462)

- **URL wording: RESOLVED.** It now correctly limits the claim to the supported contract and says query-string `q` is ignored or rejected, not that callers cannot place it there. [Spec line 453](C:/Pratik/FinCrime/fincrimeradar-api/docs/specs/kyb-lab-api-spec.md:453)

## Decisions

- **Burst counting for 405, 413 and 422: acceptable.** Counting rejected attempts against the burst limit prevents invalid requests bypassing that control, while correctly leaving weighted allowance and global upstream budget untouched. Implementation must use a path-scoped ASGI control before routing to count 405 responses, while placing it behind CORS or explicitly exempting OPTIONS. The specified tests should detect incorrect ordering.

- **Partial-page rule: correct.** A short page with `total_results > shown` is not inherently an invalid upstream envelope. `truncated: true` with the source-attributed `{total}/{shown}` message is preferable to the withdrawn invented 502 rule. The contradictory count statements identified above must still be removed.

## Verification

- Remote branch remained at `623d8bef7401463220e439a514100cfae51c5844`.
- Only the specification and saved review document changed.
- `git diff --check` passed.
- Representative `str.isprintable()` cases were exercised.
- Existing CORS middleware returned 200 with the production allow-origin header and returned no allow-origin header for a disallowed origin.
- No implementation tests exist yet because this remains specification-only.
