## Verdict

**FAIL**

Four of the five previous findings are resolved. The original High source-availability finding remains partially resolved because v1.3 converts an undocumented PSC statements 404 into a confirmed-looking empty result. A separate unsupported-entity response-shape mismatch also remains.

## Previous findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Subresource 404s rendered as confirmed empty data | **PARTIALLY RESOLVED** | Ordinary collection 404s now become `not_returned` at [routes_kyb_lab.py:928](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:928>), with null truncation at [routes_kyb_lab.py:917](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:917>). The behavioural test is [tests/test_kyb_lab.py:1795](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:1795>). However, PSC statements pass `empty_on_404=True` at [routes_kyb_lab.py:1044](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:1044>), and [tests/test_kyb_lab.py:2276](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:2276>) requires `available`, empty arrays and `truncated: false`. |
| 2 | One client can exhaust the shared upstream budget | **RESOLVED** | `_take_budget()` charges immediately before every outbound attempt at [routes_kyb_lab.py:524](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:524>) and [routes_kyb_lab.py:647](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:647>). Tests cover seven-call and five-call lookups, cache hits and exactly 60 calls at [tests/test_kyb_lab.py:1944](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:1944>) and [tests/test_kyb_lab.py:1961](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:1961>). The latter includes a partial ninth lookup after eight seven-call lookups, so partial charging is proved. Connection retries are also charged because the call sits inside the retry loop. |
| 3 | PSC identity indicators claim the officers endpoint | **RESOLVED** | Contributing sources are derived at [routes_kyb_lab.py:943](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:943>) and used at [routes_kyb_lab.py:1215](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:1215>). Officer-only, PSC-only and mixed cases are tested at [tests/test_kyb_lab.py:1901](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:1901>). |
| 4 | Live fixtures remain linkable personal data | **RESOLVED** | Current fixture tests enforce reserved `ZZ` numbers, synthetic markers, no `live_*` files and constrained free text at [tests/test_kyb_lab.py:2238](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:2238>). I scanned all 16 PR-only commits: zero `live_*` paths and zero matches for the 97 non-placeholder identifiers extracted from the removed live bundles. The old fixture commit and old PR head are not ancestors of the current head. |
| 5 | Client limiter has unbounded key cardinality | **RESOLVED** | Both maps use hard-capped LRU insertion at [routes_kyb_lab.py:508](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:508>). The 20,000-key and recent-use eviction tests are at [tests/test_kyb_lab.py:2028](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:2028>). |

## Required corrections

1. **High: PSC statements 404 still overstates availability.** Companies House documents 200 as returning the statements resource and 404 only as “Resource not found”. It does not document 404 as an authoritative empty list. The three-company live sample demonstrates observed behaviour, not a universal API contract. [Companies House PSC statements specification](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/persons-with-significant-control/list-statements).

   A different-cause or inconsistent 404 could therefore hide an active or ceased PSC statement while the response says `available`, returns empty arrays and sets `truncated: false`. Treat it as `not_returned`, with empty arrays and `truncated: null`, unless Companies House provides authoritative confirmation of the equivalence.

2. **Medium: unsupported entity sections contradict v1.2.** The spec says: “For an unsupported entity type no collection is requested, so every collection is reported `not_returned` with `truncated: null`.”

   The implementation instead returns `registered_office_change_filings`, `charges` and `insolvency` as null at [routes_kyb_lab.py:1018](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:1018>), and the test explicitly requires those null values at [tests/test_kyb_lab.py:862](<C:/Pratik/FinCrime/fincrimeradar-api/tests/test_kyb_lab.py:862>). Align the implementation and test with the spec, or narrow the spec so it explicitly says which unsupported-type sections remain null.

For genuine `not_returned` states, absence-dependent indicators are suppressed correctly. `pscs.truncated` also becomes null when an availability flag is false at [routes_kyb_lab.py:1168](<C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py:1168>). The remaining failure is that v1.3 deliberately prevents a statements 404 from entering that state.

## CF-Connecting-IP

Unconditional trust is acceptable only under the stated public Render topology. Render states that public traffic passes through Cloudflare and that Cloudflare overwrites caller-supplied `CF-Connecting-IP`. [Render guidance](https://render.com/articles/host-pocketbase-on-render).

The production spoof test should, from one actual source IP and after a successful smoke check, send 11 requests for the same valid company within 60 seconds. Every request must contain:

- A different forged `CF-Connecting-IP`.
- A different forged `X-Forwarded-For`.

The first 10 should succeed and the eleventh must return 429. This proves Cloudflare replaced the forged CF header and the application saw one stable client key. Varying only `X-Forwarded-For`, as currently specified, does not prove the unconditional CF-header trust assumption. A request from a genuinely different egress IP should then succeed as the separation control.

## Verification

- Exact reviewed head: `ad4d855cf8d48b0c42387ad3d1738de513c90f1f`.
- `330 passed` in the pytest suite.
- `62 passed` in the SAR sandbox harness.
- Exact PR-scoped Ruff lint and format checks passed.
- Whole-repository Ruff is not clean: 42 diagnostics and 8 files would be reformatted.
- Working tree remained clean.
- Completed security diff scan found no reportable security vulnerability. The FAIL is for analyst-facing source integrity and contract compliance.

::code-comment{title="[P1] PSC 404 is not documented as empty" body="Companies House documents this response only as Resource not found. Converting every statements 404 to available, empty and complete can conceal a material PSC statement if the 404 has another cause. Return not_returned with null truncation unless an authoritative upstream contract confirms the equivalence." file="C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py" start=933 end=939 priority=1}

::code-comment{title="[P2] Unsupported response contradicts v1.2" body="The v1.2 spec says every unrequested collection for an unsupported entity is reported not_returned, but this path leaves filing history, charges and insolvency null. Align the response and test with the spec, or narrow the specification explicitly." file="C:/Pratik/FinCrime/fincrimeradar-api/routes_kyb_lab.py" start=1018 end=1028 priority=2}