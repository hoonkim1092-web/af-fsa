# Findings

- Continuous-Claude-v3, as documented in its GitHub repository and README on 2026-03-09, is a long-running Claude Code operating model centered on hook-driven automation, continuity artifacts, task isolation, and persistent execution state.
- The repository's `.claude/settings.json` shows a concrete lifecycle model with `UserPromptSubmit`, `PreCompact`, and `Stop` hooks plus an allowlist-style permission surface. That lifecycle model is more explicit than `agent-factory`'s current CLI wrapper approach.
- `agent-factory` already has strong equivalents for several core ideas:
  - `core/project_pipeline.py` for staged planning-to-execution flow
  - `core/dynamic_orchestrator.py` for multi-agent coordination, retries, and evaluator-driven progression
  - `core/memory.py` for local/global file-backed memory resolution
  - `scripts/session_bridge.py` for provider-agnostic CLI session ingestion
- The biggest gaps are not planning or orchestration. They are lifecycle hooks, compaction/resume continuity, explicit long-running session supervision, and a durable execution ledger.
- The most valuable synergy is combining Continuous-Claude-v3's hook discipline and continuity strategy with `agent-factory`'s existing multi-agent project pipeline and provider-agnostic memory ingestion.
- The least desirable porting choice is a Claude-only transplant. `agent-factory` should expose a generic event bus so Claude, Codex, and Gemini adapters can share the same continuity and supervision core.
- PostgreSQL appears useful for telemetry, reconciliation, and recovery, but it should be introduced as an optional backend behind a ledger interface rather than replacing the current file memory model upfront.
