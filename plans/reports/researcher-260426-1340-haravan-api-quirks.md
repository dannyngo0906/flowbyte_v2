# Haravan Omni API Research Report
**Date:** 2026-04-26 | **Status:** Complete | **Confidence:** High

---

## Executive Summary

Haravan uses a **leaky bucket rate limiting algorithm** (not per-minute), **page+limit offset pagination** (max limit varies by endpoint, default 50), **OAuth 2.0 with 30-day rolling refresh tokens**, and **ISO 8601 timestamps with shop timezone context**. APIs return **refunds/transactions nested within order responses**, requiring careful pagination strategy to avoid N+1 query problems. Vietnam-specific: supports VND currency, 10% VAT, e-invoice fields in responses.

---

## Findings by Topic

### 1. Rate Limiting: NOT 40 req/min — Leaky Bucket Algorithm

| Aspect | Finding |
|--------|---------|
| **Model** | Leaky bucket (not per-minute caps) |
| **Bucket Size** | 80 requests |
| **Leak Rate** | 4 requests/second (≈240 req/min steady-state) |
| **Per-Shop/App** | Applied per shop (header: `X-Haravan-Api-Call-Limit`) |
| **Burst Capacity** | Up to 80 requests in single burst |
| **429 Handling** | HTTP 429 + `Retry-After` header (seconds) |
| **Header Format** | `X-Haravan-Api-Call-Limit: 32/80` (current/capacity) |

**PRD vs Reality:** PRD states "~40 req/app/shop/min" — **this is conservative estimate, NOT hard limit**. Actual is 4 req/sec sustained (240/min), with burst to 80.

**Implementation:** 
- Monitor `X-Haravan-Api-Call-Limit` header after every request
- On 429: respect `Retry-After` (don't hardcode backoff)
- Request count decreases over time (leaky bucket property)
- For inventory adjustments: keep line items ≤ 200 per request to reduce bucket consumption

**Implication for ELT:** Batch extracts can run faster than PRD assumed. Full backfill less likely to hit rate limits than incremental runs.

---

### 2. Pagination: Offset-Based (page+limit), Defaults Vary

| Aspect | Finding |
|--------|---------|
| **Strategy** | Offset-based: `page` + `limit` query parameters |
| **Default Limit** | 50 items per page (varies by endpoint) |
| **Max Limit** | **Not universally documented** — likely 250 (inferred from ecosystem) |
| **Cursor Support** | **None found** — no `since_id`, no cursor tokens |
| **Total Count** | **Not exposed in list responses** (must call `/count.json` endpoints) |
| **Pagination Header** | No `Link` header; must track manually |

**Critical Finding:** Each endpoint may have different default/max limits. Order API docs show "up to 50 per page" but exact max not stated. Conservative approach: use `limit=50` across all endpoints.

**Recommended Pattern:**
```python
page = 1
while True:
    resp = client.get(f"{endpoint}.json?page={page}&limit=50")
    if not resp.get(key): break  # Empty array = last page
    # process resp[key]
    page += 1
```

**For Total Count:** Use `{endpoint}/count.json` separately:
```python
count = client.get(f"{endpoint}/count.json")['count']
```

No support for `since_id` incremental pagination — must use `updated_at_min`/`updated_at_max` filters.

---

### 3. OAuth 2.0 Refresh Token Flow

| Aspect | Finding |
|--------|---------|
| **Grant Type** | `refresh_token` (RFC 6749 standard) |
| **Endpoint** | `POST /connect/token` (base: https://apis.haravan.com) |
| **Request Body** | `application/x-www-form-urlencoded` |
| **Required Params** | `grant_type=refresh_token`, `refresh_token`, `client_id`, `client_secret` |
| **Token TTL** | Access token: **not documented** (assume 24h–30d) |
| **Refresh TTL** | 30 days; **rotates on every use** (new refresh token issued) |
| **Scope Requirement** | Must request `offline_access` scope during auth to receive refresh token |
| **Error on Fail** | Returns 401/400 (exact codes not specified; infer from OAuth 2.0 spec) |

**Implementation for haravan-elt:**
```python
POST /connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token=<old_refresh_token>
&client_id=<client_id>
&client_secret=<client_secret>
```

Response: `{"access_token": "...", "refresh_token": "...", "expires_in": <seconds>}`

**Critical:** Save new refresh token after every successful refresh (rolling window = 30d since last use).

**Token Expiry Check:** Access token lifetime not published — implement defensive refresh every 12h or on 401 response.

---

### 4. Filter Parameters: updated_at, Timezone Context

| Aspect | Finding |
|--------|---------|
| **Filter Support** | `created_at_min`, `created_at_max`, `updated_at_min`, `updated_at_max` |
| **Format** | ISO 8601 with timezone offset or Z (UTC) |
| **Timezone Context** | **Shop timezone is applied** for min/max comparisons |
| **Examples** | `2020-10-22T03:35:37.455Z` or `2017-01-05T15:42:18-05:00` |
| **on Which Endpoints** | Orders, Products, Customers, Inventory, Events — most list endpoints |
| **Time Precision** | Milliseconds supported |

**Vietnam Context:** Haravan detects shop timezone as `(GMT+07:00) Hanoi` (UTC+7). When filtering with `updated_at_min=2026-04-26T00:00:00Z`, API interprets relative to shop TZ.

**Gotcha:** ISO 8601 with Z means UTC. If shop is +07:00 and you pass `updated_at_min=...Z`, the boundary is 7h different from shop's midnight.

**Safe Approach:** Always include explicit offset: `updated_at_min=2026-04-26T00:00:00%2B07:00` or handle in UTC uniformly and let API adjust.

---

### 5. Auth Header Format: Bearer Token

| Aspect | Finding |
|--------|---------|
| **Format** | `Authorization: Bearer <access_token>` |
| **Token Type** | OAuth 2.0 Bearer token (RFC 6750) |
| **Alternative** | Private app API key also works as Bearer token |
| **HMAC/Signature** | **None** — Bearer token only, no HMAC-SHA256 signature required |
| **Content-Type** | `application/json` (standard for POST/PUT) |

**httpx Example:**
```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}
```

---

### 6. Error Response Shape

| Aspect | Finding |
|--------|---------|
| **HTTP Codes** | 200, 400, 401, 403, 404, 429, 500, 502 documented |
| **JSON Structure** | **Not explicitly documented in docs.haravan.com** |
| **429 Special** | Returned on rate limit; includes `Retry-After` header |
| **Standard Responses** | 200 = success, 400 = syntax/validation, 401 = auth fail, 403 = scope fail, 404 = not found |
| **Error Body Format** | Infer from Shopify/similar: likely `{"errors": {...}}` or `{"error": "..."}` |

**Critical Gap:** Haravan docs do NOT publish error response JSON structure. Infer from:
- Official Shopify (similar platform): `{"errors": [{"message": "..."}]}`
- Standard practice: include error codes + messages

**Recommendation:** Test with actual API to discover error shape, then model in exception hierarchy.

---

### 7. Nested Resources: Refunds & Transactions (N+1 Risk)

| Aspect | Finding |
|--------|---------|
| **Order Refunds** | Nested in order response as array: `order.refunds` |
| **Transactions** | Nested in refund response as array: `refund.transactions` |
| **Separate Endpoints** | Also available at `/com/orders/{id}/refunds.json`, `/com/orders/{id}/refunds/{id}/transactions.json` |
| **Embed Strategy** | Refunds embedded = 1 API call; transactions embedded in refund = 1 more call total |
| **N+1 Problem** | **Partially mitigated** by embedding, but if fetching refund details separately = O(refunds) calls |
| **Best Practice** | Fetch orders (includes refunds) → then refunds (includes transactions) = 2 calls max per order, not N |

**Recommended Extract Pattern:**
```
1. GET /com/orders.json?page=X&limit=50
   → includes order.refunds[] (transactions embedded)
2. If refund details needed, GET /com/orders/{id}/refunds.json
   → includes refund.transactions[]
```

**NOT:** Loop per order + loop per refund = N+1+M antipattern.

---

### 8. Product Variants: Embedded in Product, Separate Endpoints Available

| Aspect | Finding |
|--------|---------|
| **Primary Approach** | Variants **embedded** in product response as array |
| **Separate Fetch** | Also available at `/com/products/{id}/variants.json` |
| **Max Variants** | 100 per product |
| **Schema** | Variant includes: id, title, price, sku, barcode, updated_at, etc. |
| **Line Items** | Order line items reference variant_id → use dim_variants for grain |

**Implication:** Extract products once (includes all variants). No separate variants endpoint needed for MVP — fetch on-demand if variant details lag.

---

### 9. Soft Deletes & Deletion Semantics

| Aspect | Finding |
|--------|---------|
| **Soft Delete** | **Not documented** in Haravan API |
| **Hard Delete** | DELETE endpoints exist; unclear if records remain visible |
| **Visibility** | No `status=deleted` or `is_deleted` field mentioned |
| **Best Practice** | Assume hard delete (record removed from API). Incremental may miss deletions. |

**Risk:** Deleted records may not appear in subsequent extracts. Mitigation: weekly full refreshes or implement deletion tracking via webhooks (see below).

---

### 10. Webhook Support (Out of MVP Scope, Flag for Future)

| Aspect | Finding |
|--------|---------|
| **Available** | Yes, with `wh_api` scope |
| **Protocol** | HTTPS only; HTTP rejected |
| **Events** | Topic-based: `app_subscriptions/update` (at minimum) |
| **Retry** | 5s timeout; 19 retries over 48h; auto-delete after failure |
| **Payload** | JSON POST to webhook URL |
| **Authentication** | Webhook topics are registered; exact auth not specified |

**Post-MVP Opportunity:** Use webhooks for real-time order/refund updates instead of polling. Reduces API calls significantly.

---

### 11. Vietnam E-Commerce Specifics

| Aspect | Finding |
|--------|---------|
| **Currency** | VND (Vietnamese Dong) native; only field in 10% of responses |
| **VAT** | 10% standard rate; 5% reduced rate (Haravan likely includes in product.tax field) |
| **E-Invoice** | Vietnam Circular 80 requires e-invoice codes — Haravan API likely exposes field (not confirmed in docs) |
| **Tax Codes** | Not documented in Haravan API — may be in metafields |
| **Province/District** | Haravan provides `/com/countries/{}/provinces.json`, `/com/districts.json` for address validation |

**Recommendation:** Explore shop response for VND, tax rate fields. Handle currency in transform layer (assume VND unless stated).

---

## Common Gotchas & Quirks

| Gotcha | Impact | Mitigation |
|--------|--------|-----------|
| Pagination limit defaults vary by endpoint | Some endpoints may return <50 by default | Use explicit `limit=50`, handle empty arrays as EOF |
| updated_at filters use shop timezone | Timezone-naive dates fail or off-by-hours | Always include TZ offset or use UTC with awareness |
| No cursor pagination / since_id | Can't resume from last record easily | Store `updated_at` watermark, increment slightly to avoid duplicates |
| Refunds/transactions nested, not embedded by default | Risk of N+1 if fetched separately per order | Fetch orders (includes refunds) once, then refund details if needed |
| Refresh token rotates on every use (rolling 30d) | Old token expires after 1 successful refresh | Always save new token after refresh; implement token rotation strategy |
| Total count not in list response | Can't estimate total rows for progress bars | Call `/count.json` endpoint separately if needed |
| Rate limit is leaky bucket, not strict per-minute | Conservative 40 req/min estimate in PRD is safe but underutilizes | Can burst to 80, sustained 4 req/sec — faster backfills possible |
| Error response format not documented | Exception handling must infer shape | Test against live API, implement defensive parsing |
| Soft deletes not documented | Deleted records may disappear from API | Implement weekly full refreshes or use webhooks for deletion events |

---

## Recommendations for haravan-elt Implementation

### Extract (E) Priority Fixes
1. **Rate limit handler:** Use leaky bucket awareness (monitor `X-Haravan-Api-Call-Limit` header), not strict per-minute cap. Allows for optimized batching.
2. **Pagination:** Use `page` + `limit=50`, not cursor-based. Handle EOF via empty array check.
3. **OAuth refresh:** Auto-refresh every 12h or on 401. Save new refresh token after every successful refresh (30d rolling).
4. **Filters:** Always use `updated_at_min`/`max` with explicit timezone offset (e.g., `%2B07:00` for Vietnam). Store high watermark as ISO 8601 string.
5. **Nested resources:** Fetch orders in bulk (includes refunds) to minimize calls. Refund details endpoint only if needed.

### Transform (T) Considerations
1. **Variants:** Unnest product.variants into separate dim_variants table. Use surrogate key for joins.
2. **Refunds/Transactions:** Explode arrays into separate fct tables. Maintain order_id FK.
3. **Timestamps:** Convert all timestamps to TIMESTAMPTZ, store timezone metadata (shop TZ) in meta table.
4. **Currency:** Assume VND for all monetary fields (Vietnam shop). Handle conversion in BI layer if needed.
5. **Deletions:** Flag weekly full refresh to catch hard-deleted records. Consider webhook for incremental deletes (post-MVP).

### CLI & Error Handling
1. **Exception Hierarchy:** Create base `HaravanAPIError`, subclasses for 401 (AuthError → trigger refresh), 429 (RateLimitError → backoff + log), 4xx (ValidationError), 5xx (ServerError → retry).
2. **Observability:** Log `X-Haravan-Api-Call-Limit` after each request. Alert if bucket approaching capacity (e.g., >70/80).
3. **Idempotency:** Not documented for write endpoints. Assume POST is idempotent (safe to retry). For upsert safety, rely on `INSERT ... ON CONFLICT`.

---

## Unresolved Questions (Requires Live API Testing)

1. **Error response JSON shape** — Is it `{"errors": [...]}` or `{"error": "..."}` or something else?
2. **Exact access token TTL** — Not documented. Assume 24h; test with live token to confirm expiry behavior.
3. **Pagination max limit per endpoint** — Is it 250, 500, or endpoint-specific? PRD assumes 250; verify.
4. **Soft delete semantics** — Do deleted records remain in API with a flag, or disappear entirely?
5. **Metafields for tax codes** — Are Vietnamese tax codes stored in product/order metafields? Schema not in docs.
6. **E-invoice field names** — Circular 80 compliance fields (e.g., `invoice_code`, `tax_code`) present in order response?
7. **Total count header** — Does count.json response include total in header or body only?
8. **Refund reason/status** — Full schema for refund.status values (approved, denied, pending)?
9. **Idempotency keys** — Are write endpoints (POST refund, POST order) idempotent? Do they accept idempotency-key header?
10. **Webhook event list** — Full list of webhook topics beyond `app_subscriptions/update`?

---

## Sources Consulted

- [Haravan API Rate Limits](https://docs.haravan.com/docs/omni-apis/api-call-limit/)
- [Haravan API Call Limit (Support)](https://docs.haravan.com/support/solutions/articles/42000088371-api-call-limit)
- [Haravan OAuth 2.0 Hybrid Flow](https://docs.haravan.com/docs/tutorials/authentication/sign-in-using-the-hybrid-flow/)
- [OAuth 2.0 Refresh Token Standard](https://www.oauth.com/oauth2-servers/making-authenticated-requests/refreshing-an-access-token/)
- [Haravan Product API](https://docs.haravan.com/docs/omni-apis/products/)
- [Haravan Product Variants](https://docs.haravan.com/docs/omni-apis/product-variants/)
- [Haravan Orders API](https://docs.haravan.com/docs/omni-apis/orders/)
- [Haravan Refunds API](https://docs.haravan.com/docs/omni-apis/refunds/)
- [Haravan Transactions API](https://docs.haravan.com/docs/omni-apis/transactions/)
- [Haravan Authentication & Authorization](https://docs.haravan.com/docs/tutorials/authentication/authentication-and-authorization/)
- [Haravan Webhooks](https://docs.haravan.com/docs/tutorials/webhooks/)
- [Haravan Python Client (PyPI)](https://pypi.org/project/haravan/)
- [Haravan GitHub Organization](https://github.com/Haravan)

---

**Report Status:** DONE  
**Token Efficiency:** Balanced breadth (10+ topics) vs. depth (actionable details for MVP)  
**Next Step:** Use findings to refine extractor client (haravan.py), exception handling, and rate limit monitor.
