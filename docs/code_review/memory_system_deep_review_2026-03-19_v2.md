# Memory System Deep Review (2026-03-19)

## Scope and verification

I reviewed the current next-generation memory-system code path and verified it with targeted tests and one runtime probe.

Reviewed modules:
- `core/memory_system/facade.py`
- `core/memory_system/router.py`
- `core/memory_system/episode_extractor.py`
- `core/memory_system/episode_matcher.py`
- `core/memory_system/knowledge_forger.py`
- `core/memory_system/knowledge_injection.py`
- `core/memory_system/graph_builder.py`
- `core/memory_system/graph_query.py`
- `core/memory_system/cross_project.py`
- `core/memory_system/adapters/core_memory.py`
- `core/memory_system/adapters/knowledge_graph.py`
- `core/hooks/memory_consolidation.py`
- `core/ast_memory_hub.py`
- `core/agent_runner.py`
- `core/hooks/event_bus.py`
- `core/hooks/skill_self_evolution.py`

Verified test runs:
- `python -m pytest -q tests/test_phase10_memory_foundation.py tests/test_phase13_knowledge_graph.py tests/test_phase16_integration.py` -> `70 passed`
- `python -m pytest -q tests/test_stage4_7_knowledge_pipeline.py tests/test_phase12_episodic_memory.py tests/test_phase14_decay_cross_project.py` -> `49 passed`

Important validation note:
- `tests/test_stage4_7_knowledge_pipeline.py` initially failed during collection because `core.memory_system.episode_matcher` no longer exported `_keyword_similarity`. That regression is fixed in this review pass.
- A direct runtime probe confirmed that `UnifiedMemoryFacade.record_episode()` combined with `KnowledgeGraphAdapter` returns the stored episode back as `memory_type=graph`, not `memory_type=episodic`.

## What I fixed now

1. Restored backward compatibility for `_keyword_similarity` in `core/memory_system/episode_matcher.py` so the Stage 4-7 pipeline suite is runnable again.
2. Fixed nested same-skill call matching in `core/memory_system/episode_extractor.py`.
   - Before the fix, nested `skill_call_start(A) -> skill_call_start(A) -> skill_call_end(A) -> skill_call_end(A)` sequences were matched FIFO and could attach the inner result to the outer call.
   - The extractor now prefers the most recent pending call and uses depth-aware matching.
3. Fixed `CrossProjectRecall.find_similar_solutions()` in `core/memory_system/cross_project.py`.
   - Before the fix, the method called `search_semantic()` and therefore still respected the current project filter.
   - It now searches all backends first and then filters to graph records.
4. Added regression coverage for:
   - nested same-skill episode extraction
   - cross-project solution recall

## Confirmed findings

### Critical 1. The next-generation memory pipeline is not wired into runtime

Evidence:
- `core/agent_runner.py:803-815` registers guardrail hooks, context fork, and skill self-evolution.
- No runtime registration for `KnowledgeInjectionHook` or `MemoryConsolidationHook` was found.
- `core/hooks/event_bus.py:34-36` only auto-registers `LangSmithTracingHook`.

Impact:
- Phase 12-16 exists as modules and tests, but the production runner does not actually execute the memory capture or memory injection pipeline.
- That means the current system cannot yet claim persistent closed-loop learning in real runs.

Required fix steps:
1. Add a dedicated memory bootstrap function that constructs `UnifiedMemoryFacade` and registers concrete adapters.
2. Register `KnowledgeInjectionHook` early in pre-execute.
3. Register `MemoryConsolidationHook` late in post-execute.
4. Inject the same facade and graph adapter into both hooks.
5. Call `MemoryConsolidationHook.flush()` during shutdown.

### Critical 2. Injected knowledge currently has no verified runtime consumer

Evidence:
- `core/memory_system/knowledge_injection.py:64-68` writes `_knowledge_context` into `agent_state`.
- Repository search found no runtime consumer of `_knowledge_context` outside tests.

Impact:
- Even if the hook is registered later, the recalled graph knowledge still will not influence prompts unless prompt assembly explicitly consumes that field.

Required fix steps:
1. Thread `_knowledge_context` into the final prompt/system-context assembly path.
2. Add a trace field showing which lessons were injected for each run.
3. Add an end-to-end test proving that injected lessons appear in the model input.

### High 3. Episodic writes are polluted by `KnowledgeGraphAdapter`

Evidence:
- `core/memory_system/facade.py:251-271` writes episodes to every adapter.
- `core/memory_system/adapters/knowledge_graph.py:115-127` converts every incoming `MemoryRecord` into a `KnowledgeNode`.
- `core/memory_system/adapters/knowledge_graph.py:202-216` always converts stored nodes back to `memory_type=GRAPH`.
- Runtime probe confirmed that an episode written through a facade backed only by `KnowledgeGraphAdapter` comes back as `graph`.

Impact:
- Episodes are no longer recoverable as episodes from that backend.
- Graph memory becomes polluted with non-graph records.
- Retrieval quality will decay over time because the graph store is mixing facts, solutions, and raw episodic traces.

Required fix steps:
1. Stop broadcasting episodic writes to the knowledge-graph backend.
2. Add adapter capability metadata, or route writes by memory type instead of writing to every backend.
3. Add a regression test: `record_episode()` must not surface graph records unless graph forging explicitly created them.

### High 4. `CoreMemoryAdapter` cannot round-trip episodic records safely

Evidence:
- `core/memory_system/adapters/core_memory.py:54-68` writes only `key`, `value`, `category`, timestamps, and generic metadata.
- `core/memory_system/adapters/core_memory.py:95-108` reconstructs records from `read_core_memory()` but drops original timestamps, memory type, scope, project_id, causal links, and most structured metadata.

Impact:
- If this becomes the only durable backend for episodes, `EpisodeMatcher._load_episode()` cannot reliably rebuild `EpisodeRecord` objects.
- Causal replay and failure->success pairing become fragile.

Required fix steps:
1. Persist the full normalized `MemoryRecord.to_dict()` payload for next-gen memory writes.
2. Read that same payload back with `MemoryRecord.from_dict()`.
3. Keep legacy `core/memory.py` briefing reads separate from structured memory persistence.

### High 5. Semantic relevance ranking is only partially real today

Evidence:
- `core/memory_system/decay.py:78-117` gives semantic similarity the largest weight.
- `core/memory_system/facade.py:189-194` ranks records, but it does not pass backend similarity scores into `rank_by_relevance()`.
- `core/memory_system/adapters/cortex_vector.py:141-150` returns matched records but does not surface any similarity score into the ranking path.

Impact:
- The ranking pipeline mostly behaves like recency/frequency/confidence scoring.
- The system is not yet doing strong hybrid retrieval even when a vector backend exists.

Required fix steps:
1. Extend the adapter contract so search returns records plus similarity scores, or stores a `_semantic_score` in metadata.
2. Feed those scores into `MemoryDecayManager.rank_by_relevance()`.
3. Add recall-quality tests where old but semantically closer records beat newer but irrelevant ones.

### High 6. Router "graph" mode is still filtered search, not graph traversal

Evidence:
- `core/memory_system/router.py:129-143` routes graph queries to `search_semantic(..., memory_type=GRAPH)`.
- `core/memory_system/graph_query.py` contains BFS/DFS traversal helpers, but the router does not call them.
- `core/memory_system/adapters/knowledge_graph.py:135-145` search is plain substring matching.

Impact:
- The system classifies graph questions correctly more often than before, but it does not yet perform causal traversal.
- This is below the bar for a truly next-generation memory system.

Required fix steps:
1. Split graph retrieval into two phases: candidate problem-node lookup, then graph traversal.
2. Use `GraphQuery.get_full_triple()` or BFS/DFS once a problem node is identified.
3. Return structured graph recall instead of flat substring matches.

### High 7. `SkillSelfEvolutionHook` targets a non-existent memory facade API

Evidence:
- `core/hooks/skill_self_evolution.py:152-170` calls `UnifiedMemoryFacade.get_instance()`.
- `core/memory_system/facade.py` does not define `get_instance()`.
- The same block calls `facade.write(key=..., value=..., adapter_hint=...)`, but the current `UnifiedMemoryFacade.write()` signature is `write(content, *, memory_type=..., target_backend=...)`.

Impact:
- Skill evolution events are logged but not persisted into the next-generation memory system.
- Because the exception is swallowed, the failure is easy to miss in production.

Required fix steps:
1. Choose one memory API surface and delete the obsolete call shape.
2. If singleton access is required, implement and bootstrap it explicitly.
3. Add a regression test covering `on_skill_evolved()`.

### Medium 8. Stage 4-7 coverage had a real blind spot

Evidence:
- The focused memory suites passed before I checked `tests/test_stage4_7_knowledge_pipeline.py`.
- That suite had a collection-time import failure due to the missing `_keyword_similarity` symbol.

Impact:
- A broken knowledge-pipeline test file could sit unnoticed while the broader memory suite still looked healthy.

Required fix steps:
1. Keep Stage 4-7 in the default memory regression suite.
2. Add a single aggregated CI target for all memory phases.
3. Fail CI on collection errors, not only assertion failures.

### Medium 9. Knowledge graph has no canonicalization or entity-resolution layer

Evidence:
- `KnowledgeForger` always adds new nodes and edges.
- `KnowledgeGraphAdapter.add_node()` and `add_edge()` do not attempt merge, dedup, or canonical linking.

Impact:
- Repeated retries for the same class of problem will produce duplicate problem/cause/solution nodes.
- Injection quality and graph traversal quality will degrade as the graph grows.

Required fix steps:
1. Introduce node fingerprints for problem/cause/solution triples.
2. Merge near-duplicates before persistence.
3. Track evidence counts and source episodes per canonical node.

## Explicit next steps to get ahead of Claude/OpenAI-style memory systems

### Step 1. Productionize the loop
1. Bootstrap one real `UnifiedMemoryFacade` instance per run.
2. Register the next-gen hooks in the actual runner.
3. Prove that captured episodes and injected lessons flow through live runs.

### Step 2. Fix data integrity
1. Separate episodic storage from graph storage.
2. Preserve full structured records in durable backends.
3. Add regression tests for nested tools, retries, and causal links.

### Step 3. Upgrade retrieval quality
1. Pass semantic scores through the adapter boundary.
2. Replace graph-mode substring search with graph traversal.
3. Add canonicalization and merge logic for problem/cause/solution nodes.

### Step 4. Add memory-specific evaluation
1. Measure retrieval precision, not just whether code executes.
2. Track whether recalled memory changed the model decision.
3. Log memory hits, misses, stale recalls, and false positives.

### Step 5. Add memory governance
1. Add provenance for every promoted lesson.
2. Add conflict handling when two lessons disagree.
3. Add decay and promotion rules based on successful reuse, not only recency.

## Bottom line

The implementation is no longer "empty scaffolding". The internal modules are real, and the current code already supports meaningful unit-level behavior.

But it is not yet ahead of frontier memory systems in production terms, because the most important loop is still incomplete:
- capture is not wired into the live runner
- injection is not wired into the prompt path
- graph retrieval is not true graph retrieval
- episodic persistence is not cleanly separated from graph persistence

After the fixes applied in this review, the local test surface is healthier. The next milestone is not another isolated module. It is full runtime wiring plus retrieval evaluation.
