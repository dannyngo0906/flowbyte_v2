# Journal Entries Index

## Overview
Technical journal documenting development challenges, failures, and lessons learned. Each entry provides raw, honest assessment of what went wrong and why.

## November 2025 - Plan Archival Analysis

### Completed Plans
- **2025-11-11-windows-statusline-complete.md**
  - Windows statusline support implementation (all 5 phases completed)
  - Why it succeeded: clear scope, explicit success criteria, user-facing value
  - Impact: shipped feature for Windows users

### Never-Started Plans
- **2025-11-11-planning-skill-never-started.md**
  - Plan to split 115-line SKILL.md into 7 focused references
  - Why it failed: no execution target date, internal optimization without external pressure
  - Lesson: planning without execution scheduling = work that won't happen

### Planning-Complete-But-Not-Implemented Plans
- **2025-11-14-aesthetic-skill-ambitious-planning.md**
  - Combat "AI slop" designs through 4-phase enhancement (12-16 hours)
  - Why it failed: comprehensive research created false completion feeling, scope expanded during planning
  - Lesson: research completion is not work completion

### Under-Review-But-Not-Started Plans
- **2025-11-14-docs-commands-optimization-stuck.md**
  - Reduce /docs:* token consumption 40-60% (identified concrete waste: 38,868 tokens/run)
  - Why it failed: "Under Review" became indefinite limbo, optimization is abstract vs. features
  - Lesson: optimization plans sit forever without explicit execution decision

## October 2025 - Technical Debt & Process Issues

- **2510181655-massive-skills-integration-technical-debt.md**
  - Adding 62,095 lines of Anthropic skills reference implementation
  - Straddling boilerplate vs. reference implementation goals
  - Lesson: clear boundaries between "ClaudeKit code" and "reference materials" needed

- **2510181700-obsession-with-conciseness.md**
  - (Obsession with extreme conciseness creating maintenance debt)

- **2510181710-git-workflow-evolution.md**
  - (Git workflow patterns and evolution)

- **2510181720-release-automation-reality-check.md**
  - (Release automation challenges and reality)

## Reading Guide

### By Topic
- **Process failures**: planning-skill-never-started, aesthetic-skill-ambitious-planning, docs-commands-optimization-stuck
- **Completed work analysis**: windows-statusline-complete
- **System architecture decisions**: massive-skills-integration-technical-debt
- **Team practices**: obsession-with-conciseness, git-workflow-evolution, release-automation-reality-check

### By Lesson Type
- **Why plans fail**: planning-skill, aesthetic-skill, docs-optimization
- **Why plans succeed**: windows-statusline
- **Technical debt**: skills-integration
- **Process evolution**: git-workflow, release-automation

## Key Patterns Identified

1. **Success Factor**: Clear scope + explicit metrics + phase gates + user demand
2. **Failure Pattern**: Planning without execution scheduling → indefinite limbo
3. **Research Trap**: Comprehensive research creates false completion feeling
4. **Priority Creep**: Internal optimization loses to feature work every time
5. **Time Threshold**: Plans over 8 hours without scheduling never execute

## Recommendations for Future Planning

- Plans under 3 hours: include execution date, treat as sprint work
- Plans 3-8 hours: allocate specific day/time before plan completion
- Plans over 8 hours: start with Phase 1 proof of concept only
- Optimization work: must show real before/after measurement
- All plans: must transition from "Planning" to "Executing" or "Deprioritized" within 72 hours

## April 2026 - Haravan ELT Production

- **260426-haravan-elt-bootstrap-m0-m7.md** — Bootstrap toàn bộ pipeline M0–M7, 130 tests pass
- **260427-haravan-live-e2e-and-vps-deploy.md** — VPS deploy + 10 live E2E bugs caught
- **2026-04-27-haravan-elt-phases-08-12-shipped.md** — Phases 08–12 complete, full M0–M7 shipped
- **2026-04-28-inventory-revenue-metabase-questions.md**
  - Cross-reference 539 SKU tồn kho T4 với đơn hàng → 2 Metabase questions
  - Phát hiện `price` vs `price_original` trong Haravan line items
  - Fix `line_total_vnd = price × qty` (giá bán thực, không tính hàng tặng kèm)
  - Thêm `on-run-end` GRANT hook để tự cấp quyền `metabase_reader` sau full-refresh
- **260429-vps-migration-fresh-sync-and-dim-historical-fix.md**
  - Migrate `haravan-elt` từ VPS cũ → VPS mới qua **fresh sync** từ Haravan API (không pg_dump data)
  - Dockerize stack (postgres + metabase) + NPM SSL `metabase.salesai.vn`
  - **Fix orphan refs** trong dims: UNION current state + historical từ line_items + inv_adjustments + sentinel `(unknown)` row → ERROR 4 → 0
  - Mở `on-run-end` grants cho 6 schemas (không chỉ `staging_marts`)
  - Source VPS hardening: bind localhost, delete data sau final snapshot 1.3GB
  - Bootstrap script `install-prod-stack.sh` để pull-and-run trên server tương lai
- **260430-2am-run-audit-dbt-deprecation-retry-budget.md**
  - Audit run 2:00 AM 30/04: 1 HTTP 429 self-recovered, 1 dbt test WARN, 23 deprecation warnings
  - Wrap 23 generic tests vào `arguments:` block (dbt 1.10+) — `accepted_values`, `relationships`, `expression_is_true`
  - Thêm `'pending'` vào accepted_values của `order_transactions.kind` (50% transactions, không phải data corruption)
  - Tăng tenacity retry budget: 5→8 attempts, max backoff 30s→60s — asymmetric upside cho transient outage
  - Bài học: đọc deprecation message thực tế thay vì guess; verify với DB query trước khi propose fix; YAGNI cho 1 lần 429/run

---

Last updated: 2026-04-30
Total entries: 13
