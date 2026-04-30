# Audit run 2:00 AM 30/04 — dbt deprecation + retry budget

**Date**: 2026-04-30
**Component**: haravan-elt (dbt YAML + client retry)
**Status**: Completed (commits `cdef95e`, `c9b08ae` pushed lên `feat/haravan-elt`)

---

## Bối cảnh

User yêu cầu kiểm tra tình hình đồng bộ run 2:00 AM (GMT+7) ngày 30/04. SSH vào VPS `vmadmin@160.25.81.157` (`vpsn8n`), xem `journalctl -u haravan-elt.service`.

Run thực tế **thành công** (8 phút 25 giây, 25 models built, PASS=150) nhưng có 3 dấu hiệu cần xử lý:

1. **`inventory_locations`** — tổng quota Haravan đạt 80/80, 1 HTTP 429, retry 2s thành công, mất 6.5 phút (trong tổng 8 phút run)
2. **dbt test WARN** — `accepted_values` của `stg_haravan__order_transactions.kind` báo có 1 giá trị không thuộc accepted list
3. **dbt deprecation** — `MissingArgumentsPropertyInGenericTestDeprecation: 23 occurrences`

---

## Plan ban đầu sai 2 chỗ — phải sửa lại

### Sai #1: Fix 1 over-engineered

Plan đầu đề xuất viết adaptive throttle (~15 dòng Python) trong `_monitor_quota()` để slow down khi quota ≥ 70% capacity.

Sau khi đếm lại log: **chỉ 1 HTTP 429 duy nhất trong toàn run**. Pipeline đã có tenacity retry 5 attempts + Retry-After + exponential backoff. Một 429 lẻ tự recover trong 2 giây = pattern bình thường, không phải bug.

→ **YAGNI**: bỏ Fix 1.

### Sai #2: Fix 3 hiểu sai deprecation

Plan đầu cho rằng `MissingArgumentsPropertyInGenericTestDeprecation` là về vị trí `quote: true` trong `accepted_values` test. SAI.

Đọc kỹ message log:
```
Found top-level arguments to test `dbt_utils.expression_is_true` defined on...
- MissingArgumentsPropertyInGenericTestDeprecation: 23 occurrences
```

Đây là deprecation dbt 1.10+: **TẤT CẢ** test arguments phải wrap trong `arguments:` block, không chỉ `quote`. Ảnh hưởng `accepted_values`, `relationships`, `dbt_utils.expression_is_true`.

Đếm lại:
- `_stg_haravan__models.yml`: 5 tests (4 expression_is_true + 1 accepted_values)
- `_marts_core.yml`: 18 tests (16 relationships + 2 expression_is_true)
- **Tổng = 23** → khớp chính xác với log.

---

## Diễn biến

### Verify trước khi sửa
```sql
-- Xác định kind value bất thường
SELECT kind, COUNT(*) FROM staging_staging.stg_haravan__order_transactions
GROUP BY kind ORDER BY COUNT(*) DESC;
-- pending  | 77103   ← không trong accepted list
-- capture  | 76063
-- refund   |   807
```

`pending` chiếm **50% transactions** — chắc chắn là Haravan kind hợp lệ (giao dịch đang chờ), không phải data corruption.

### Implementation

**Commit 1** — `fix(dbt): wrap test args in arguments block + add pending kind`:
```yaml
# BEFORE (deprecated)
- relationships:
    to: ref('dim_customers')
    field: customer_key

# AFTER
- relationships:
    arguments:
      to: ref('dim_customers')
      field: customer_key
```

Thêm `'pending'` vào accepted_values của `kind`. Sửa 23 tests trên 2 files theo cùng pattern.

**Commit 2** — `fix(client): extend retry budget to 8 attempts with 60s max backoff`:

User hỏi: thay vì lower rate limit xuống 3 req/sec (chậm 30%), có cách nào auto-retry an toàn hơn không?

Trả lời: hệ thống ĐÃ có tenacity retry 5 attempts, 1+2+4+8+16=31s tổng wait. Đề xuất tăng lên 8 attempts + max=60s = ~121s tổng wait. **1 dòng code, không trade-off speed**, asymmetric upside (cứu pipeline nếu Haravan có outage 30-60s).

```python
# haravan.py:88-90
@retry(
    stop=stop_after_attempt(8),                          # was 5
    wait=wait_exponential(multiplier=2, min=1, max=60),  # was max=30
```

### Verify trên VPS
```
dbt parse           → 0 deprecation warnings  (was 23)
dbt test full       → PASS=124 WARN=0          (was WARN=1)
pytest test_haravan_client → 13/13 pass
Python import       → OK
```

### Deploy
- scp 3 files lên VPS `/tmp/`
- `sudo cp` vào `/opt/haravan-elt/`, `chown elt:elt`
- Verify với `dbt parse` + `dbt test` trực tiếp trên VPS

---

## Bài học

### 1. Đọc kỹ deprecation message thực tế, không guess
Tôi suýt sửa sai 23 tests vì assume sai về deprecation. Lần sau: copy nguyên văn message log → search docs → confirm pattern → mới đề xuất fix.

### 2. Verify với data thực, không assume
Plan ban đầu nói "có 1 transaction với kind bất thường". Thực tế query DB: **77,103 rows** với `kind=pending` (50% tổng). Nếu không query, sẽ đề xuất delete row hoặc fix data — sai hoàn toàn. Đúng phải là sửa schema (thêm vào accepted list).

### 3. YAGNI vs reflexive over-engineering
Reflex đầu tiên thấy 429 = "implement adaptive throttle". Đếm lại: 1 lần 429/run, retry tự fix sau 2s, total run 8 phút bình thường = system đang work as designed. **Đừng fix thứ không vỡ**.

### 4. Asymmetric payoff khi tinh chỉnh resilience
Tăng retry budget (5→8 attempts, 30s→60s max) là 1 dòng code, không slow down success path. Nếu Haravan có 60s transient outage (rare nhưng possible), cứu được nguyên pipeline khỏi fail giữa đêm. Cost ~0, benefit lớn → no-brainer.

### 5. Plan mode + verify song song = catch sai sót sớm
Sau plan đầu, user yêu cầu "kiểm tra plan có chính xác chưa". Đó là lúc tôi phát hiện 2 sai sót lớn (Fix 1 over-engineered, Fix 3 hiểu sai). Nếu skip review step này, đã commit 23 sai trên 23 tests + viết 15 dòng Python không cần thiết.

---

## Files changed

| File | Thay đổi | Commit |
|------|----------|--------|
| `dbt/models/staging/haravan/_stg_haravan__models.yml` | Wrap 5 tests vào `arguments:` + thêm `pending` | `cdef95e` |
| `dbt/models/marts/core/_marts_core.yml` | Wrap 18 tests vào `arguments:` | `cdef95e` |
| `src/haravan_elt/client/haravan.py` | Retry: 5→8 attempts, max backoff 30s→60s | `c9b08ae` |
