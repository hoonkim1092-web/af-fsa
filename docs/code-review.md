# Code Review — Living Document

> Auto-updated on every `.py` edit (via Claude Code PostToolUse hook) and AF agent run (via CodeReviewDocHook).
> Persistent across sessions. See `docs/YYYY-MM-DD-code-review.md` for point-in-time snapshots.
>
> **Trigger sources**:
> - Claude Code: `PostToolUse Write|Edit *.py` → `scripts/code_review_updater.py --no-llm`
> - AF agent run: `CodeReviewDocHook.post_execute()` (priority 85) → with LLM review
> - Manual: `python scripts/code_review_updater.py [workspace] [--context "..."]`

---

## 2026-04-03 — Initial state summary

**Context**: Bootstrapped from `2026-04-03-code-review.md` snapshot

**Branch**: `agent-factory_harness_Claude_Setup_and_Pipeline_v1`

**Resolved in this session (10 bugs)**:

| ID | Severity | File | Summary |
|----|----------|------|---------|
| C1 | Critical | `core/fsa_loop.py` | Unreachable else branch — redesign_count/decompose_count logic skipped |
| C2 | Critical | `core/hooks/checkpoint.py` | Non-atomic checkpoint write (data loss on crash) |
| C3 | Critical | `core/hooks/context_fork.py` | LLM timeout leaves daemon thread silently abandoned |
| C4 | Critical | `core/memory_system/issue_tracker.py` | Argument injection via unsanitized git/gh CLI args |
| H1 | High | `core/dynamic_orchestrator.py` | Concurrent state_board mutation without asyncio.Lock |
| H2 | High | `core/agent_runner.py` | Unbounded skill module cache (memory leak + stale modules) |
| H3 | High | `core/ise_redesigner.py` | Silent fallback — caller can't distinguish pivot from redesign failure |
| H4 | High | `core/control/intake.py` | Wrong key `continuity_health` → always "healthy" |
| H5 | High | `core/dashboard.py` | Non-atomic dashboard JSON write (race condition) |
| H6 | High | `core/control_plane_llm.py` | No env-var override for CLI provider detection |

**Feature added**: `core/hooks/code_review_doc.py` — `CodeReviewDocHook` (PRIORITY=85)
- Triggers on successful AF agent runs
- Writes `docs/code_review.md` and `docs/change_history.md` in user project

**Infrastructure added**: `scripts/code_review_updater.py` + `.claude/settings.local.json` PostToolUse hook
- Keeps this file (`docs/code-review.md`) updated on every `.py` edit

---

---

## 2026-04-03 22:02 — `agent-factory_harness_Claude_Setup_and_Pipeline_v1` (7f2379d)

**Context**: Claude Code edit session

**Changed (140)**: `.claude/settings.local.json, .tmp_af_fsa, projects/global_hoon_main/data/memory/general/codex_chat/user_ca7dc36e6f65_20260226_153246_350008.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cabb851c1992_20260309_143912_859945.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cbb54b73614d_20260310_174038_097128.json, projects/global_hoon_main/data/memory/general/codex_chat/user_ccf599881eaa_20260226_153246_242667.json, projects/global_hoon_main/data/memory/general/codex_chat/user_chat_model_routerpy22_ai_938ea52669bb_20260310_144545_412808.json, projects/global_hoon_main/data/memory/general/codex_chat/user_claude_cligemini_clicodex_cli_provider_bcb09452605f_20260309_094923_009767.json, projects/global_hoon_main/data/memory/general/codex_chat/user_claudegeminicodex_end_to_end_fab86188a929_20260309_094923_097039.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_03d7531c11cc_20260309_153446_943072.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_45b75e61e411_20260309_153446_939720.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_681e7aaacae3_20260309_163142_487754.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_a94e32c0bcaa_20260309_143912_957271.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_cli_7f6d0a6cb8ef_20260309_143912_960564.json, projects/global_hoon_main/data/memory/general/codex_chat/user_code_review_fix_plan_80ed8feaa246_20260310_102307_699843.json ... (+125)`

_Review skipped (--no-llm or LLM unavailable)_

---

## 2026-04-03 22:02 — `agent-factory_harness_Claude_Setup_and_Pipeline_v1` (7f2379d)

**Context**: Claude Code edit session

**Changed (140)**: `.claude/settings.local.json, .tmp_af_fsa, projects/global_hoon_main/data/memory/general/codex_chat/user_ca7dc36e6f65_20260226_153246_350008.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cabb851c1992_20260309_143912_859945.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cbb54b73614d_20260310_174038_097128.json, projects/global_hoon_main/data/memory/general/codex_chat/user_ccf599881eaa_20260226_153246_242667.json, projects/global_hoon_main/data/memory/general/codex_chat/user_chat_model_routerpy22_ai_938ea52669bb_20260310_144545_412808.json, projects/global_hoon_main/data/memory/general/codex_chat/user_claude_cligemini_clicodex_cli_provider_bcb09452605f_20260309_094923_009767.json, projects/global_hoon_main/data/memory/general/codex_chat/user_claudegeminicodex_end_to_end_fab86188a929_20260309_094923_097039.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_03d7531c11cc_20260309_153446_943072.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_45b75e61e411_20260309_153446_939720.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_681e7aaacae3_20260309_163142_487754.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_a94e32c0bcaa_20260309_143912_957271.json, projects/global_hoon_main/data/memory/general/codex_chat/user_cli_cli_7f6d0a6cb8ef_20260309_143912_960564.json, projects/global_hoon_main/data/memory/general/codex_chat/user_code_review_fix_plan_80ed8feaa246_20260310_102307_699843.json ... (+125)`

_Review skipped (--no-llm or LLM unavailable)_
