# Phase 08 — Telegram Notifications (M4)

## Context Links

- PRD §4.6 (FR-N1–N4)
- Phase-02 (TelegramClient stub), phase-07 (Pipeline orchestrator hooks)

## Overview

- **Priority:** high
- **Status:** completed (live-channel verification deferred)
- **Effort:** 2 days
- **Description:** Full Telegram notification system: 4 event types (start/success/fail/warning), Markdown templates per PRD §4.6 FR-N3, fail-soft (never crash pipeline). Hook into Pipeline lifecycle. CLI flag `--no-notify` disables.

## Key Insights

- **Fail-soft mandatory** (FR-N4): Telegram outage MUST NOT abort pipeline. All `send()` calls wrapped in try/except, log warning only.
- **Start event only when `triggered_by="cron"`** — manual runs spam too much (FR-N2).
- **Markdown formatting** — Telegram uses different Markdown dialect; escape `_*[]()` in dynamic content. Use `MarkdownV2` or plain Markdown carefully.
- **Traceback truncation:** error messages capped at 1000 chars (matches `meta.run_log.error_message`).
- **Rate-limit warning event:** triggered when `>3 consecutive 429s` observed by HaravanClient — phase-08 implements counter.

## Requirements

**Functional:**
- FR-N1: env vars `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- FR-N2: 4 events (start cron-only, success, failure, warning)
- FR-N3: Markdown templates per PRD spec
- FR-N4: Telegram failure → log only, never raise

**Non-functional:**
- Bot send timeout 10s (don't block pipeline if Telegram slow)

## Architecture

```
TelegramClient (full impl)
  ├─ send(text, parse_mode=...)         ─▶  fail-soft
  ├─ send_start(domain="all", mode)
  ├─ send_success(extract_summary, dbt_summary, duration)
  ├─ send_failure(stage, exc, traceback_short)
  └─ send_warning(reason, details)

Pipeline integration:
  Pipeline.run_all():
    if triggered_by == "cron": telegram.send_start(...)
    try:
       extract_all() → transform() → test()
    except: telegram.send_failure(...) ; raise
    else: telegram.send_success(...)
    if rate_limit_warnings > 3: telegram.send_warning(...)

HaravanClient.rate_limit_counter (new attribute):
  consecutive_429 = 0   ← incremented on 429, reset on 200
  if consecutive_429 >= 3:  emit event (e.g., callback)
```

## Related Code Files

**Create:**
- `src/haravan_elt/notifications.py` — orchestrator-friendly wrapper hosting message templates; pipes events into `TelegramClient`
- `tests/test_telegram.py`
- `tests/test_notifications.py`
- `tests/fixtures/vcr/telegram_send_ok.yaml`
- `tests/fixtures/vcr/telegram_send_500.yaml`

**Modify:**
- `src/haravan_elt/client/telegram.py` — full impl with retry-once, fail-soft, parse_mode
- `src/haravan_elt/client/haravan.py` — add `consecutive_429` counter + `on_rate_limit_warn` callback
- `src/haravan_elt/pipeline.py` — wire start/success/failure/warning calls

## Implementation Steps

1. **`telegram.py` final:**
   ```python
   import httpx, structlog, re
   from typing import Optional

   logger = structlog.get_logger()

   _MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")

   class TelegramClient:
       BASE = "https://api.telegram.org"

       def __init__(self, bot_token: str, chat_id: str, parse_mode: str = "Markdown"):
           self._token = bot_token
           self._chat = chat_id
           self._parse_mode = parse_mode

       def send(self, text: str) -> bool:
           url = f"{self.BASE}/bot{self._token}/sendMessage"
           payload = {"chat_id": self._chat, "text": text, "parse_mode": self._parse_mode,
                      "disable_web_page_preview": True}
           try:
               with httpx.Client(timeout=10.0) as client:
                   resp = client.post(url, json=payload)
               if resp.status_code != 200:
                   logger.warning("telegram_send_non_200", status=resp.status_code, body=resp.text[:200])
                   return False
               return True
           except Exception as exc:
               logger.warning("telegram_send_failed", error=str(exc))
               return False

       @staticmethod
       def md_escape(s: str) -> str:
           """Escape MarkdownV2 special chars. For default Markdown only escape '_*[`'."""
           return s.replace("_", r"\_").replace("*", r"\*").replace("[", r"\[").replace("`", r"\`")
   ```

2. **`notifications.py`** templates:
   ```python
   from .client.telegram import TelegramClient

   class Notifier:
       def __init__(self, telegram: TelegramClient | None):
           self._tg = telegram

       def start(self, mode: str, triggered_by: str) -> None:
           if not self._tg or triggered_by != "cron":
               return
           self._tg.send(f"▶️ *Haravan ELT — Daily Run start*\nMode: `{mode}`")

       def success(self, ext: dict[str, int], dbt: dict, duration: float) -> None:
           if not self._tg:
               return
           lines = ["✅ *Haravan ELT — Daily Run*",
                    f"⏱ Duration: {duration:.1f}s",
                    "📥 Extracted:"]
           for d, n in ext.items():
               lines.append(f"  • {d}: {n:,} rows")
           if dbt.get("models_built") is not None:
               lines.append(f"🔧 dbt: {dbt['models_built']} models, {dbt.get('tests_passed', 0)}/{dbt.get('tests_total', 0)} tests passed")
           self._tg.send("\n".join(lines))

       def failure(self, stage: str, exc: BaseException) -> None:
           if not self._tg:
               return
           tb = (str(exc)[:1000])
           self._tg.send(f"❌ *Haravan ELT — Failure*\nStage: `{stage}`\n```\n{tb}\n```")

       def warning(self, reason: str, details: str = "") -> None:
           if not self._tg:
               return
           self._tg.send(f"⚠️ *Haravan ELT — Warning*\n{reason}\n{details}")
   ```

3. **HaravanClient rate-limit counter:**
   - Add `self.consecutive_429 = 0` in `__init__`
   - In `_request` after 429 handling: `self.consecutive_429 += 1`; on 2xx: `self.consecutive_429 = 0`
   - Pipeline checks `client.consecutive_429 > 3` after extraction → emit `notifier.warning(...)`

4. **Pipeline integration:**
   ```python
   class Pipeline:
       def __init__(self, settings, telegram=None, triggered_by="manual"):
           ...
           self.notifier = Notifier(telegram)

       def run_all(self, mode, since, until, dry_run, no_notify):
           start = time.time()
           self.notifier.start(mode, self.triggered_by)
           try:
               ext = self.extract_all(mode, since, until, dry_run)
               dbt_summary = self.transform()
               duration = time.time() - start
               # Warning detection
               if self.client.consecutive_429 > 3:
                   self.notifier.warning("Rate limit hit > 3 consecutive times")
               # Detect dbt test failure (warning, not failure)
               if dbt_summary.get("tests_failed", 0) > 0:
                   self.notifier.warning(f"{dbt_summary['tests_failed']} dbt tests failed")
               self.notifier.success(ext, dbt_summary, duration)
               return 0
           except Exception as exc:
               self.notifier.failure(stage="run_all", exc=exc)
               return 1
   ```

5. **dbt summary extraction:** parse `dbt/target/run_results.json` after `dbtRunner.invoke` → `{models_built, tests_passed, tests_failed, tests_total}`. Add helper in `dbt_runner.py`:
   ```python
   import json
   def parse_run_results(target_dir: str = "dbt/target") -> dict:
       p = Path(target_dir) / "run_results.json"
       if not p.exists(): return {}
       data = json.loads(p.read_text())
       results = data.get("results", [])
       models = [r for r in results if r["unique_id"].startswith("model.")]
       tests  = [r for r in results if r["unique_id"].startswith("test.")]
       return {
           "models_built": sum(1 for r in models if r["status"] == "success"),
           "tests_total":  len(tests),
           "tests_passed": sum(1 for r in tests if r["status"] == "pass"),
           "tests_failed": sum(1 for r in tests if r["status"] == "fail"),
       }
   ```

6. **Tests:**
   - `test_telegram.py`: VCR replay 200 OK → `send` returns True; 500 → returns False (no raise); timeout → returns False
   - `test_telegram.py`: invalid bot_token → 401, returns False
   - `test_notifications.py`: with `triggered_by="manual"` → `start()` is no-op (assert no API call); with `cron` → calls `send`
   - `test_notifications.py`: failure formatting truncates traceback to 1000 chars
   - Pipeline test: induce ZeroDivisionError mid-pipeline → notifier.failure called once with stage info

## Todo List

- [x] Finalize `client/telegram.py` (`send` fail-soft, escape helpers, MarkdownV2 option)
- [x] Create `notifications.py` with `Notifier` class (4 event methods)
- [x] Add `consecutive_429` counter to `HaravanClient` (also tracks `max_consecutive_429` peak)
- [x] Wire `Notifier` into `Pipeline.run_all` (start cron-only, success, failure, warning)
- [x] Implement `parse_run_results` helper in `dbt_runner.py`
- [x] Record VCR cassettes (telegram 200, 500) — hand-crafted; replayed via `vcr.use_cassette()` to keep plan-specified filenames
- [x] Write tests: `test_telegram_client.py` (12 cases incl. retry/escape/VCR) and `test_notifications.py` (12 cases)
- [x] Manual end-to-end: trigger pipeline with `--triggered-by cron --no-notify=false` and verify start + success messages arrive in test channel — VERIFIED 2026-04-27 (live `TELEGRAM_BOT_TOKEN=8776...334`, chat_id=630545370; HTTP 200 OK on `notify`, `run_start cron`, `run_failure` sends)
- [x] Verify failure path: induce error in extract → failure message arrives — VERIFIED 2026-04-27 (`inventory_locations` KeyError → failure message delivered before fix; later `dbt_build` test failures → second failure message delivered)

## Success Criteria

- All 4 notification types implemented per PRD §4.6 FR-N3 templates
- Pipeline NEVER aborts on Telegram failure (verified by 500 fixture + integration test)
- `--no-notify` flag suppresses all messages
- Manual cron-mode run posts start + success messages with row counts + dbt summary in real Telegram channel
- Test coverage of `notifications.py` ≥85%

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Markdown parse error breaks Telegram message | Med | Low | Catch 400 from Telegram; retry with `parse_mode=None`; log original text |
| Bot blocked by user → 403 each call | Low | Low | Fail-soft already; doc README that bot must be added to chat |
| Long error trace contains backticks → breaks code block | Med | Low | Replace ``` with `'''` or escape before insertion |
| Cron triggered_by detection fails (wrong env var) | Low | Med | Pass `--triggered-by cron` explicitly in `run-daily.sh` |

## Security Considerations

- Bot token via `SecretStr`; never logged
- Chat ID is not secret but treated as such (private chat IDs may be sensitive)
- Error tracebacks may leak internal paths/SQL → acceptable for private team channel; document as private-channel-only

## Next Steps

Unblocks **phase-09** (P1 domains; validate command can publish summary via same Notifier).

## Unresolved Questions

- Q1: Use `Markdown` or `MarkdownV2`? V2 stricter escaping but more reliable. MVP: stick with `Markdown`; switch if formatting breaks.
- Q2: Should warnings (rate limit, dbt test fail) be sent BEFORE success, or appended? Decision: send warnings as separate messages BEFORE final success/failure, so visible even if final fails.
