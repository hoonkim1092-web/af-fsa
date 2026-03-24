# Findings

## CLI Provider Split

- `core/agent_runner.py` currently decides provider choice in `ModelRouter.pick`, performs a direct OpenAI Codex call in `_run_with_codex`, and otherwise falls back to Gemini SDK in `run`.
- `core/config_paths.py` fails fast unless `GOOGLE_API_KEY` or `OPENAI_API_KEY` exists, which blocks subscription-backed CLI usage before the runner is even initialized.
- Existing session bridge work already distinguishes `claude`, `gemini`, and `codex` at the transcript layer; the missing piece is execution-time provider separation, not memory-side separation.
- The safest first slice is explicit CLI providers only: `claude_cli`, `gemini_cli`, `codex_cli`. Existing `claude`/`gemini`/`codex` semantics remain as-is to avoid a broad behavior change.
- Runtime command defaults should be overridable from env because CLI syntax and local wrappers can vary across machines.
- The new CLI layer can stay provider-neutral if the request contract is only `model`, `system_prompt`, `task_input`, and `workspace`. That is enough for a first subscription-backed path and does not require changing the run transcript schema.
- `AgentRunner` should try explicit CLI providers first, then fall back to old SDK paths only if those providers fail and API-backed execution is still possible.

- `agent-factory` already had two overlapping hook definitions in `core/hooks/base.py` and `core/hooks/event_bus.py`. Consolidating those responsibilities makes lifecycle expansion tractable.
- A dedicated manifest store can be added without affecting scheduling as long as it never mutates the task queue directly and treats in-flight work as interrupted work on resume.
- `DynamicOrchestrator` does not need to be re-architected to gain continuity. A manifest sidecar plus lightweight snapshots is enough for the first slice.
- `AgentRunner` had only coarse pre/post execution hooks. Adding `pre_tool_call` and `post_tool_call` gives a clean extension point for future provider/session hooks and tool guardrails.
- The repository had tracked `tests/_tmp/**` fixture outputs and `tests/test_out.md`, which are test artifacts rather than source tests. They can be removed safely once `tests/_tmp/` is ignored.
- Existing helper scripts like `tests/verify_audit_hash.py` and `tests/run_verify_agent.py` should stay; they are referenced elsewhere and are not merely leftovers.
