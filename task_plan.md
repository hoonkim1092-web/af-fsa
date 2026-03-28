# Task Plan

## Goal

Implement the next portability slice for subscription-backed CLI execution in `agent-factory`: split `claude_cli`, `gemini_cli`, and `codex_cli` into explicit providers, allow CLI-only bootstrap without API keys, and route `AgentRunner` through modular CLI adapters without breaking existing SDK paths.

## Phases

| Status | Phase | Notes |
| --- | --- | --- |
| completed | Audit current model/provider flow | `agent_runner.py` mixes router, SDK calls, and Codex path in one file |
| completed | Add failing tests for CLI provider split | Added `tests/test_cli_providers.py` for bootstrap, registry defaults, command building, and runner dispatch |
| completed | Implement provider registry and CLI adapters | Added `core/providers/registry.py`, `core/providers/cli.py`, and package exports |
| completed | Wire runner/bootstrap to CLI providers | `config_paths` now allows CLI bootstrap and `AgentRunner` dispatches explicit CLI providers before SDK fallback |
| completed | Run targeted regression verification | 29 targeted tests passed |

## Decisions

- Keep the existing SDK execution path as the fallback path for now; add CLI providers beside it rather than replacing it in one change.
- Treat CLI selection as an explicit runtime choice via `AGENT_CHAT_PROVIDER`; do not auto-switch to external CLIs based on model name alone.
- Use conservative default CLI commands backed by env overrides so command syntax can evolve without forcing code changes.
- Keep tool orchestration in `agent-factory`; CLI providers receive composed prompts and workspace context, not in-process Python tool functions.
- Leave provider-specific advanced behaviors such as approval mode, tool mirroring, and session hook packs for a later slice once the basic dispatch contract is stable.

## Errors Encountered

| Error | Attempt | Resolution |
| --- | --- | --- |
| `agent_runner.py` currently couples provider selection and execution flow | 1 | Split work into registry, adapter, and runner wiring phases |
| New CLI dispatch test was blocked by `TodoContinuationEnforcer` | 1 | Added a minimal `.todo.md` in the test workspace so the runtime path matches real usage |
