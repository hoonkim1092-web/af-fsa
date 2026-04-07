# Findings & Decisions

## Requirements
- Read `docs/code_review/code-review.md`.
- Read `docs/features/2026-04-07-llm-powered-document-generation.md`.
- Read and verify the specified core files directly.
- Judge whether the design worsens existing issues, adds new ones, or resolves prior issues correctly.
- Validate concrete hook points, evidence propagation, clarification insertion, UI/FSA behavior, chained refinement risk, impact scope, and prompt/data-shape alignment.
- Report only project-specific findings with severity, file:line references, and code quotes.

## Research Findings
- `docs/code_review/code-review.md` 기준으로 planning 관련 핵심 파일은 `interactive_chat.py`, `researcher.py`, `project_pipeline.py`, `work_item_generator.py`, `bootstrap_roles.py`이며, `interactive_chat.py`에는 이미 자동 compaction 미연동 문제가 기록되어 있다.
- 설계 문서는 현재 병목을 `work_item_generator.py`의 4개 f-string 문서 생성 함수로 규정하고, Evidence 원본 전파와 Clarification 단계 삽입을 `project_pipeline.py` 중심으로 해결하려 한다.
- 설계 문서는 새 파일 `core/clarification.py`를 도입하고, `interactive_chat.py`가 Clarification 질문/응답 UI를 담당한다고 가정한다.
- 설계 문서는 `generate_work_items(..., evidence=...)` 형태의 시그니처 변경과 chained refinement 기반의 4회 추가 LLM 호출을 제안한다.
- 실제 `core/work_item_generator.py`는 Evidence를 별도 인자로 받지 않고, `project_brief` 안의 `evidence_summary`, `research_notes`, `notebook_summary`, `local_references`, `web_references`만 사용한다.
- 실제 `core/project_pipeline.py`의 `prepare()`는 `research_evidence`를 수집/저장하지만 `research_project_brief()` 호출 이후 `generate_work_items()`에 전달하지 않는다. 중간에 사용자 상호작용을 반환하거나 재개할 수 있는 인터럽트 구조도 없다.
- 실제 `core/interactive_chat.py`의 project 모드는 `factory.run(...)` 결과를 받아 `_format_project_result()`로 문자열만 렌더링한다. `PreparedProject` 또는 추가 질의 요청 객체를 해석하는 코드 경로는 아직 없다.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Focus on actual call/data flow rather than design intent alone | User asked for validation against current implementation |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Skill script default path mismatched local environment | Used actual skill installation path under `.codex` |

## Resources
- `docs/code_review/code-review.md`
- `docs/features/2026-04-07-llm-powered-document-generation.md`
- `core/work_item_generator.py`
- `core/project_pipeline.py`
- `core/interactive_chat.py`
- `core/researcher.py`
- `core/requirement_llm.py`
- `core/bootstrap_roles.py`

## Visual/Browser Findings
- None.
