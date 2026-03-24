# Continuous-Claude-v3 -> agent-factory Port Design

Date: 2026-03-09

## 1. Objective

This document proposes how to import the useful operating patterns of Continuous-Claude-v3 into `agent-factory` without turning `agent-factory` into a Claude-only system.

The target outcome is not "clone Continuous-Claude-v3". The target outcome is:

- preserve `agent-factory` as the main orchestration system,
- add a hook-driven lifecycle that can work with Claude Code and other CLIs,
- improve continuity across compaction, long-running sessions, and restarts,
- formalize task/worktree execution state,
- gain autonomous loop resilience without sacrificing provider portability.

## 2. Source Basis

Analysis in this document is based on the following primary sources as observed on 2026-03-09:

- Continuous-Claude-v3 repository: <https://github.com/Parallax-Advanced-Research/Continuous-Claude-v3>
- Continuous-Claude-v3 README: <https://raw.githubusercontent.com/Parallax-Advanced-Research/Continuous-Claude-v3/main/README.md>
- Continuous-Claude-v3 Claude hook config: <https://raw.githubusercontent.com/Parallax-Advanced-Research/Continuous-Claude-v3/main/.claude/settings.json>

`agent-factory` comparison points were derived from:

- `core/project_pipeline.py`
- `core/dynamic_orchestrator.py`
- `core/memory.py`
- `scripts/session_bridge.py`

## 3. Continuous-Claude-v3 Structural Reading

Continuous-Claude-v3 is best understood as a continuously running Claude Code operating environment, not merely a prompt pack or a single agent script. Its architecture appears to be organized around five ideas.

### 3.1 Hook-driven lifecycle

The repository uses Claude Code hooks as first-class lifecycle boundaries. The observed hook configuration includes:

- `UserPromptSubmit`
- `PreCompact`
- `Stop`

This means the system can intercept key moments before prompt handling, before context compaction, and when the run stops. That is materially different from a simple wrapper script because it gives the runtime deterministic places to save state, enrich context, and prepare a restart.

### 3.2 Continuity across compaction and long sessions

The README positions the system as an agent that can continue operating over long spans. The practical implication is that it treats context loss as a routine event, not an exception. Continuity artifacts are therefore part of the core runtime contract.

Observed design intent:

- snapshot important state before compaction,
- resume from durable memory rather than raw chat alone,
- maintain an execution narrative across long sessions.

### 3.3 Long-running autonomous loop

Continuous-Claude-v3 is designed around a "works while you sleep" model. That implies:

- a supervisor process or tmux-backed session persistence,
- autonomous iteration over tasks,
- a restartable loop rather than a one-shot request-response execution model.

This is not just a CLI convenience. It changes which system responsibilities matter most: task ledgering, failure recovery, and resumability.

### 3.4 Worktree and task isolation

The system appears to use Git/worktree isolation to keep long-running autonomous work bounded. This reduces branch collision risk and makes autonomous task assignment more tractable.

### 3.5 Tooling, MCP, and persistent infrastructure

The repository signals integration with MCP servers and broader infrastructure such as containers and database-backed services. The interesting architectural point is not the specific tools, but the fact that tools are governed through explicit project permissions and a persistent runtime environment.

## 4. Current agent-factory Baseline

`agent-factory` already contains several foundational pieces that Continuous-Claude-v3 would otherwise need to build.

### 4.1 Project pipeline

`core/project_pipeline.py` already structures work as:

1. intake,
2. planning/research materialization,
3. role and subtask planning,
4. orchestrated execution.

This is stronger than a plain continuous loop because it gives `agent-factory` a pre-existing planning and decomposition layer.

### 4.2 Dynamic multi-agent orchestrator

`core/dynamic_orchestrator.py` already provides:

- subtask queueing,
- agent assignment,
- evaluator feedback,
- retry and pivot logic,
- state board tracking.

This means `agent-factory` does not need Continuous-Claude-v3's autonomy model as a replacement. It needs it as a resilience and lifecycle extension.

### 4.3 File-backed local/global memory

`core/memory.py` already resolves memory from local and global stores. That is highly compatible with a continuity-first operating model. The missing piece is not memory existence, but stronger lifecycle capture around when memory should be snapshotted and restored.

### 4.4 Provider-agnostic session ingestion

`scripts/session_bridge.py` already gives `agent-factory` a portable ingestion point for external CLI sessions. This is the clearest place to avoid a Claude-only import path.

## 5. Mapping: Continuous-Claude-v3 vs agent-factory

| Continuous-Claude-v3 capability | Current `agent-factory` equivalent | Gap | Port decision |
| --- | --- | --- | --- |
| Claude hook lifecycle | CLI wrappers and ad hoc entrypoints | No unified lifecycle event bus | Add a provider-neutral hook event bus |
| Pre-compaction continuity snapshot | File/global memory exists | No formal `PreCompact` continuity contract | Add continuity manager and snapshot schema |
| Long-running autonomous session | Orchestrator exists, but mostly request-scoped | Weak supervisor/restart model | Add runtime supervisor over orchestrator |
| Worktree/task isolation | Project pipeline and git-aware flows exist | No unified execution ledger per task/worktree | Add run ledger with task/worktree identity |
| Tool/MCP governance | Existing scripts and provider bridges | No shared permission abstraction | Add policy bridge and adapter configs |
| Persistent telemetry/recovery store | File-backed JSON memory | Limited reconciliation and observability | Add optional DB-backed ledger later |

## 6. Port Principle

Import the operating model, not the product shape.

That means:

- port lifecycle contracts,
- port continuity behavior,
- port supervision patterns,
- port explicit execution bookkeeping,
- do not port Claude-specific assumptions into the core.

## 7. Proposed Port Architecture

### 7.1 Layer A: Hook Event Bus Compatibility Layer

Create a normalized event model inside `agent-factory`:

- `prompt_submit`
- `pre_compact`
- `stop`
- `resume`
- `task_checkpoint`

The main goal is to decouple lifecycle logic from any one CLI. Claude Code adapters can map native hooks into these events. Codex and Gemini wrappers can emit the same events from their own interception points.

Suggested additions:

- `core/hook_events.py`
- `core/hook_dispatcher.py`
- `scripts/providers/claude_hook_adapter.py`
- `scripts/providers/codex_hook_adapter.py`
- `scripts/providers/gemini_hook_adapter.py`

### 7.2 Layer B: Continuity Manager

Introduce a continuity manager responsible for durable execution continuity rather than generic memory storage.

Responsibilities:

- snapshot active objective, current subtask, last evaluator state, and next intended action,
- summarize volatile context before compaction,
- restore minimal execution state on resume,
- persist a compact "why the system is here" record.

Suggested additions:

- `core/continuity_manager.py`
- `core/continuity_schema.py`

Suggested persisted artifacts per run:

- `continuity_state.json`
- `resume_brief.md`
- `event_log.jsonl`

### 7.3 Layer C: Runtime Supervisor

Wrap the current orchestrator in a restartable supervisor.

Responsibilities:

- own the outer autonomous loop,
- restart failed or interrupted runs,
- reload continuity state,
- rate-limit retries,
- enforce stop conditions and budgets.

Suggested additions:

- `core/runtime_supervisor.py`
- `core/runtime_policies.py`

This is the layer that imports Continuous-Claude-v3's "continuous worker" value without replacing `core/dynamic_orchestrator.py`.

### 7.4 Layer D: Run Ledger and Worktree Binding

Create a durable execution ledger that binds:

- project id,
- run id,
- task id,
- worktree path,
- branch,
- provider session ids,
- continuity artifact paths,
- final outcome status.

Suggested additions:

- `core/run_ledger.py`
- `core/worktree_registry.py`

Initial backend should be file-backed JSONL or SQLite. PostgreSQL should remain optional until there is demonstrated operational need.

### 7.5 Layer E: MCP and Permission Policy Bridge

Continuous-Claude-v3 shows the value of explicit hook-time permissions and MCP enablement. `agent-factory` should model this as policy, not as a provider-specific config file.

Responsibilities:

- define allowed tool sets per run mode,
- expose provider-specific permission translation,
- track which MCP/tool policies were active for a given run.

Suggested additions:

- `core/tool_policy.py`
- `configs/provider_policies/claude.json`
- `configs/provider_policies/codex.json`
- `configs/provider_policies/gemini.json`

## 8. Synergy Analysis

### 8.1 Why this is a strong fit

The systems are complementary.

Continuous-Claude-v3 contributes:

- deterministic lifecycle hooks,
- continuity discipline,
- autonomous long-session posture,
- worktree-centered operating habits.

`agent-factory` contributes:

- stronger multi-agent planning and decomposition,
- project-scoped local/global memory,
- evaluator-driven orchestration,
- provider-agnostic session bridging.

Together, the combined system is stronger than either architecture alone.

### 8.2 Concrete synergies

#### Synergy A: Hook discipline + multi-agent orchestration

Continuous-Claude-v3's hook model gives clean moments to checkpoint and inject context. `agent-factory` already has richer subtask orchestration. Combining them yields a system that can continue multi-agent work with less state drift after compaction or interruption.

#### Synergy B: Continuity artifacts + existing memory system

`agent-factory` already stores memory, but not all memory is execution continuity. Adding a continuity manager separates durable execution intent from general memory. This reduces "I have data but I do not know what I was doing" failures.

#### Synergy C: Worktree isolation + project pipeline

`agent-factory` already knows how to plan and execute tasks. Binding those tasks to explicit worktree identities makes autonomous execution safer and easier to audit.

#### Synergy D: Claude-native hooks + provider-neutral bridges

Continuous-Claude-v3 is Claude-centric. `agent-factory` can generalize the lifecycle model so Claude gains first-class hook support while Codex and Gemini benefit from the same continuity and ledger model through adapters.

#### Synergy E: Optional DB telemetry + file-first memory

Continuous-Claude-v3 suggests the value of persistent operational state. `agent-factory` can adopt that in stages: keep file-first memory for simplicity, then add DB-backed observability only where queryability and recovery need it.

## 9. Recommended Port Scope

### Port now

- hook event bus,
- continuity manager,
- runtime supervisor,
- run ledger,
- provider policy abstraction.

### Port later

- PostgreSQL-backed telemetry,
- advanced MCP policy synchronization,
- container-native worker management,
- distributed run coordination.

### Do not port directly

- tmux-specific assumptions,
- Claude-only config shape as the core runtime contract,
- database-first memory replacement,
- tool permissions hardcoded into one provider file.

## 10. Detailed Migration Phases

### Phase 0: Shadow Analysis Mode

Goal:
Understand existing run boundaries and session surfaces without changing execution semantics.

Build:

- instrument current entrypoints to emit normalized lifecycle events,
- log event traces per run,
- capture current restart/failure cases.

Deliverables:

- `core/hook_events.py`
- `event_log.jsonl` generation
- baseline report of current lifecycle coverage

Success criteria:

- every run records `prompt_submit` and `stop`,
- Claude, Codex, and Gemini adapters can all emit the same normalized event schema,
- no behavioral regressions in current pipeline.

### Phase 1: Hook Compatibility Layer

Goal:
Make hook semantics a first-class internal contract.

Build:

- provider adapters for Claude/Codex/Gemini,
- hook dispatcher that fans out to continuity, logging, and policy modules,
- provider config templates.

Deliverables:

- `core/hook_dispatcher.py`
- provider adapters
- provider policy config skeletons

Success criteria:

- Claude native hooks map cleanly into internal events,
- non-Claude providers can simulate equivalent events,
- all hook executions are idempotent and auditable.

### Phase 2: Continuity Snapshot and Resume

Goal:
Survive compaction and interrupted long sessions.

Build:

- continuity state schema,
- pre-compaction summarizer,
- resume loader,
- minimal execution brief generator.

Deliverables:

- `core/continuity_manager.py`
- `continuity_state.json`
- `resume_brief.md`

Success criteria:

- interrupted runs can resume with correct task identity and next action,
- compaction no longer causes silent loss of execution intent,
- evaluator state and orchestrator cursor are preserved.

### Phase 3: Runtime Supervisor

Goal:
Shift from request-scoped orchestration to controlled long-running execution.

Build:

- outer loop supervisor,
- retry budgets,
- idle/wake logic,
- stop and escalation policies.

Deliverables:

- `core/runtime_supervisor.py`
- runtime policy schema
- supervisor status surface in run artifacts

Success criteria:

- the supervisor can restart eligible runs from continuity state,
- failures are bounded by policy rather than looping forever,
- long-running execution can be paused and resumed safely.

### Phase 4: Task Ledger and Worktree Binding

Goal:
Make autonomous work auditable and safe.

Build:

- task-to-worktree binding,
- branch/worktree registry,
- persistent run ledger,
- explicit terminal states.

Deliverables:

- `core/run_ledger.py`
- `core/worktree_registry.py`
- per-run ledger records

Success criteria:

- each autonomous task has a stable execution identity,
- worktree collisions are prevented or detected,
- run outcomes are queryable without replaying raw chat traces.

### Phase 5: Multi-agent Integration

Goal:
Connect the new lifecycle and continuity layers to the existing `agent-factory` multi-agent runtime.

Build:

- orchestrator checkpoints,
- evaluator-to-continuity handoff,
- project-pipeline stage restore points,
- session bridge enrichment with ledger ids.

Deliverables:

- continuity hooks in `core/dynamic_orchestrator.py`
- pipeline checkpoints in `core/project_pipeline.py`
- session bridge correlation fields

Success criteria:

- multi-agent runs resume without losing subtask graph position,
- project pipeline can restart from a known stage,
- external CLI session memory is correlated with durable run ids.

### Phase 6: Optional PostgreSQL Observability

Goal:
Improve operations, analytics, and forensic recovery once the file-first model proves useful.

Build:

- append-only ledger backend interface,
- PostgreSQL implementation,
- reconciliation jobs,
- dashboard queries.

Deliverables:

- pluggable ledger backend interface
- optional Postgres adapter
- operational query views

Success criteria:

- DB backend is optional rather than mandatory,
- file-backed mode remains supported,
- operational reporting no longer depends on scanning JSON artifacts only.

## 11. Expected Outcomes

If the port is done in the order above, `agent-factory` should gain:

- better recovery after compaction or interruption,
- safer autonomous long-running execution,
- clearer task/worktree lineage,
- stronger provider portability than Continuous-Claude-v3 alone,
- a cleaner split between memory, continuity, and telemetry.

## 12. Risks

- Over-porting Claude-specific assumptions into the core will reduce portability.
- Introducing a DB too early will complicate operations before the lifecycle model is stable.
- If continuity artifacts are too verbose, they will recreate context bloat rather than solve it.
- If the supervisor owns too much planning logic, it will duplicate `project_pipeline` and `dynamic_orchestrator` responsibilities.

## 13. Recommended Next Step

Start with a narrow MVP:

1. build the hook event bus,
2. add pre-compaction continuity snapshots,
3. add a file-backed run ledger,
4. integrate checkpoints into `dynamic_orchestrator`,
5. only then decide whether PostgreSQL is justified.

This preserves `agent-factory`'s architecture while importing the most valuable ideas from Continuous-Claude-v3.
