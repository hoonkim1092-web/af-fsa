# Progress

## 2026-03-09

- Started CLI provider separation phase for `claude_cli`, `gemini_cli`, and `codex_cli`.
- Audited current execution path: provider choice, bootstrap gating, and SDK fallbacks are all concentrated in `core/agent_runner.py` and `core/config_paths.py`.
- Chosen first implementation slice:
  1. Add failing tests for keyless CLI bootstrap and runner dispatch.
  2. Introduce modular provider registry and CLI adapter modules.
  3. Wire `AgentRunner` to explicit CLI providers before SDK fallbacks.
- Added `core/providers` with env-overridable default commands for Claude, Gemini, and Codex CLIs.
- Updated bootstrap gating so explicit CLI provider configuration can start without `GOOGLE_API_KEY` or `OPENAI_API_KEY`.
- Verified focused regression suite: `29 passed`.

## 2026-03-09

- Loaded and followed `brainstorming`, `test-driven-development`, and `planning-with-files`.
- Audited current hook/orchestrator integration and confirmed the safe implementation slice:
  - manifest continuity
  - modular hook bus
  - tool lifecycle interception
- Added failing tests first:
  - `tests/test_hook_event_bus.py`
  - `tests/test_orchestrator_manifest.py`
- Implemented:
  - `core/continuity/manifest_store.py`
  - `core/continuity/__init__.py`
  - `core/hooks/base.py`
  - `core/hooks/guardrails.py`
  - refactored `core/hooks/event_bus.py`
  - integrated snapshots into `core/dynamic_orchestrator.py`
  - integrated `pre_tool_call` / `post_tool_call` into `core/agent_runner.py`
- Updated `tests/conftest.py` so test collection does not fail on key-gated imports.
- Removed tracked test artifact files under `tests/_tmp/**` and `tests/test_out.md`, and added ignore rules.
- Verified with:
  - `python -m pytest tests/test_hook_event_bus.py tests/test_orchestrator_manifest.py tests/test_dynamic_orchestrator_workspace_scope.py tests/test_runner_contracts.py tests/test_project_pipeline.py tests/test_session_bridge.py tests/test_project_context_sync.py`
  - Result: `23 passed`
