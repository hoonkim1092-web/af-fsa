# Agent Operating Rules

These rules apply to all agents working in this project.

## 1) Startup Routine (Required)
- Before starting work, load latest project context from shared memory.
- Command:
```bash
python workspace_context_cli.py read --project . --period today --mode timeline --lines 30
```
- If no entry exists for today, run:
```bash
python workspace_context_cli.py read --project . --period 7d --mode timeline --lines 30
```

## 2) During Work Logging (Required)
- Save meaningful progress points, not every tiny action.
- Use `SUMMARY`, `DECISION`, `WORK`, `NEXT` types.
- Command example:
```bash
python workspace_context_cli.py save "Implemented shared memory read path" --project . --type WORK
```

## 3) End-of-Session Routine (Required)
- Always save an automatic snapshot at the end of a session.
- Command:
```bash
python workspace_context_cli.py auto --project . --message "end-of-session"
```
- Also save one explicit next-step line:
```bash
python workspace_context_cli.py save "Next: <one clear next action>" --project . --type NEXT
```

## 4) Summary Quality Rule (Light Summary)
- Do not over-compress summaries.
- Keep enough detail so another agent can resume without guessing.
- Minimum summary contents:
  - What was changed
  - Why it was changed
  - Current state/result
  - Next action
- Target length: 3-6 lines per summary entry.

## 5) Project Separation Rule
- Always run commands with the correct `--project` (or current project root).
- Never mix logs between different project folders.

