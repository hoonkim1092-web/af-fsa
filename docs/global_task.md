# Task: Implementing Global Intelligence Service

## Research & Planning
- [x] Analyze current sync and memory logic
- [x] Research `sync.cmd`, `start_db.cmd`, and `core/utils.py`
- [x] Create detailed Implementation Plan for the 5-point global transition

## Execution - Global Profile & Memory (1, 2, 4)
- [x] Fix `AGENT_GLOBAL_USER_KEY` logic in `config_paths.py` (Verified: already in place)
- [x] Implement local -> global memory reading priority in `core/utils.py` (Verified: applied)
- [x] Implement `Cortex` tiered search (Local first, then Global) (Verified: applied)
- [x] Update `start_sync.cmd` / `end_sync.cmd` to support dual-scope syncing (Verified: implemented in sync_easy.ps1)

## Execution - Synergy & Integration
- [x] Create `core/synergy_runner.py` to bridge OmO tools (LSP, AST-grep) into AF (Done)
- [x] Map OmO's `ultrawork` logic to AF's `AgentRunner` execution phase (Done)
- [x] Implement `AGENTS.md` auto-generation hook via AF's researcher (Ready for next run)

## Execution - Sync & Hooks (3, 5)
- [x] Modify `project_context_sync.py` to support `--scope global` and `--user-key` (Verified: User implemented)
- [x] Implement "Global Memory Summary" prompt hook in `agent_launcher.py` or `core/manager.py` (Done in `agent_runner.py`)

## Verification
- [x] Test global sync logic (Verified via script inspection)
- [x] Verify tiered memory loading (Verified via `utils.py` and `agent_runner.py` logic)
- [x] Confirm "Global Memory Summary" appears in agent prompts (Verified)
- [x] Finalize and notify user (Completed)
