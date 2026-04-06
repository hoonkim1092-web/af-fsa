# Design Review: cross-cli-skill-discovery

> Source: docs/features/cross-cli-skill-discovery.md
> Date: 2026-04-06 12:27
> Providers: critic=claude, cross=codex, judge=claude
> Mode: cross-review (3 providers)
> Trigger: manual

---

코드 검증 완료. 이제 최종 리뷰를 작성합니다.

---

## Final Design Review

### Verdict: WARN

Cross Review가 실패(`provider not found: codex`)하여, Critic Review만을 기반으로 판정합니다. 각 finding은 코드를 직접 확인하여 독립적으로 검증했습니다.

### Aggregated Findings (6 total)

#### 1. [ACCEPT] [Critical] `ClaudeOfficialSkillSource`가 `_build_sources()`에 등록되지 않음

- **Critic**: 클래스 정의(line 327)와 helper 함수(`_official_claude_skill_roots`, line 162)는 존재하지만, `_build_sources()` (line 418-457)에서 인스턴스화하지 않아 `ExternalSkillResolver` 파이프라인에서 Claude 스킬이 후보로 나타나지 않음
- **Cross**: (unavailable)
- **Judgment**: **코드로 확인됨.** `_build_sources()`는 `CodexOfficialSkillSource`(line 444-445), `ManifestSkillSource`(line 441), `RepoCacheSkillSource`(line 449), `CacheSweepSkillSource`(line 456)만 추가하며, `ClaudeOfficialSkillSource`는 어디에서도 인스턴스화되지 않는다. 설계 문서의 핵심 목적(Claude Code 스킬 탐색)이 구현 경로에서 누락된 상태.
- **Action Required**: 설계 문서 §수정 대상 파일에 `_build_sources()` 배선 코드를 명시적으로 추가. `CodexOfficialSkillSource` 추가 패턴(line 443-445)을 따라 `ClaudeOfficialSkillSource` 등록 코드를 포함할 것.

#### 2. [ACCEPT] [High] `_extract_skill_id`가 3개 모듈에서 서로 다른 로직

- **Critic**: `external_skill_sources.py:142`(frontmatter 우선), `skill_eval_harness.py:687`(디렉토리명만), `skill_preflight.py:445`(디렉토리명 + `_`→`-` 변환) — 동일 스킬이 서브시스템마다 다른 ID로 인식 가능
- **Cross**: (unavailable)
- **Judgment**: **코드로 확인됨.**
  - `external_skill_sources.py:142`: `frontmatter name → safe_id()`, fallback `dirname → safe_id()`
  - `skill_eval_harness.py:687`: `safe_id(os.path.basename(os.path.dirname(skill_path)))` — 디렉토리명만
  - `skill_preflight.py:445`: `parent.replace("_", "-")` — `safe_id()`도 사용하지 않음
  
  세 함수의 용도가 다를 수 있지만(eval harness는 SKILL.md 경로, preflight는 skill.py 경로), 설계 문서가 "frontmatter name 우선으로 통일"을 표방하면서 다른 모듈은 언급하지 않는 것은 혼란의 소지가 있음.
- **Action Required**: 설계 문서에 "v3 `_extract_skill_id` 통일은 `external_skill_sources.py`에만 적용. `skill_eval_harness`, `skill_preflight`의 동명 함수는 용도가 다르며 별도 관리"를 명시하거나, 통일 계획을 v3.1 대상으로 기록.

#### 3. [ACCEPT] [High] `_official_claude_skill_roots()` 초기화 시점 디렉토리 필터링

- **Critic**: line 174-178에서 `os.path.isdir(root)` 필터가 함수 호출 시점에 적용되어, `ClaudeOfficialSkillSource.__init__`에서 `root_dirs=None`으로 호출하면 그 시점에 없는 디렉토리가 영구 제외됨
- **Cross**: (unavailable)
- **Judgment**: **코드로 확인됨.** `ClaudeOfficialSkillSource.__init__`(line 330-336)에서 `root_dirs`가 `None`이면 `_official_claude_skill_roots()`를 호출하고, 이 함수(line 174-178)는 `os.path.isdir(root)` 필터를 적용. 반면 `iter_candidates()`(line 342)도 `os.path.isdir(root_dir)` 체크가 있으므로, `_official_claude_skill_roots()`에서 필터를 제거해도 안전. `_official_codex_skill_roots()`(line 159)도 동일 패턴이므로 같은 문제가 있지만, Codex는 `_build_sources()`에서 매번 새로 호출되므로 영향이 다름.
- **Action Required**: 설계 문서에 이 한계를 명시하고, `_official_claude_skill_roots()`에서 `os.path.isdir` 필터 제거를 구현 시 반영하도록 기술.

#### 4. [ACCEPT] [Medium] mtime 기반 변경 감지가 기존 스킬 내용 수정을 놓침

- **Critic**: `should_rescan_external()`(line 118-128)은 루트 디렉토리 mtime만 확인. 하위 파일 수정(예: SKILL.md 편집)은 루트 mtime에 반영되지 않음. "대부분의 변경 감지 가능"이라는 설계 문서 주장이 부정확.
- **Cross**: (unavailable)
- **Judgment**: **코드로 확인됨.** `os.path.getmtime(root)`는 직접 자식 항목의 추가/삭제만 감지. 설계 문서 line 170의 "대부분의 변경 감지 가능"은 과대 표현. 다만, 주요 유스케이스인 "새 스킬 설치 감지"는 커버되므로 기능적으로는 충분할 수 있음.
- **Action Required**: 설계 문서 line 170을 수정: "이 체크는 스킬 디렉토리의 추가/삭제만 감지. 기존 스킬 내부 파일 수정은 감지하지 않음. 내용 수정 감지가 필요하면 v3.1에서 서브디렉토리 재귀 mtime 또는 해시 기반 방식 검토."

#### 5. [ACCEPT] [Medium] `resolve_knowledge_skill_path`의 `prefer_project_skills=True` 기본값과 설계 문서 충돌

- **Critic**: 설계 문서가 "personal > project"를 AF 전체 규칙처럼 기술하지만, `resolve_knowledge_skill_path()`(line 306-320)는 `prefer_project_skills=True`가 기본값이라 project가 personal보다 먼저 옴. 직접적 충돌은 아니지만(AF 내부 스킬 vs 외부 스킬 소스), 혼란 유발.
- **Cross**: (unavailable)
- **Judgment**: **코드로 확인됨.** `prefer_project`가 `True`이면 `[PROJECT_SKILLS_DIR, SKILLS_DIR]` 순서(line 313). 설계 문서의 "personal > project" 규칙은 외부 소스 간 우선순위인데, 이 구분이 명확하지 않음.
- **Action Required**: 설계 문서 §스캔 우선순위 규칙에 "이 우선순위는 **외부 스킬 소스** 간 순서. AF 내부 스킬(`PROJECT_SKILLS_DIR`, `SKILLS_DIR`)은 `prefer_project_skills` 설정에 따르며 항상 외부 소스보다 우선"을 명시.

#### 6. [REJECT] [Low] `should_rescan_external()` thread safety

- **Source**: Critic
- **Original Finding**: `self._last_scan_time`을 `self._lock` 없이 읽어 멀티스레드에서 stale 값 가능
- **Rejection Reason**: Critic 본인이 "CPython GIL로 인해 float 읽기는 실질적으로 안전"이라고 인정. AF는 단일 프로세스 CLI 도구이며, 설계 문서에서 다룰 수준의 이슈가 아님.

### Summary Table

| # | Title | Severity | Verdict | Source |
|---|-------|----------|---------|--------|
| 1 | `ClaudeOfficialSkillSource` `_build_sources()` 미등록 | Critical | ACCEPT | Critic (코드 검증됨) |
| 2 | `_extract_skill_id` 3중 구현 불일치 | High | ACCEPT | Critic (코드 검증됨) |
| 3 | `_official_claude_skill_roots()` 초기화 시점 필터링 | High | ACCEPT | Critic (코드 검증됨) |
| 4 | mtime 변경 감지 한계 미기술 | Medium | ACCEPT | Critic (코드 검증됨) |
| 5 | personal > project 우선순위 범위 모호 | Medium | ACCEPT | Critic (코드 검증됨) |
| 6 | `should_rescan_external()` thread safety | Low | REJECT | Critic |

### Recommendations

1. **[필수] `_build_sources()`에 `ClaudeOfficialSkillSource` 배선 코드를 설계 문서에 추가** — 이것이 없으면 v3의 핵심 목적인 Claude Code 스킬 통합이 `ExternalSkillResolver` 경로에서 작동하지 않음
2. **[필수] `_extract_skill_id` 다중 구현에 대한 입장을 설계 문서에 명시** — 통일 또는 의도적 분리 중 하나를 선택하고 기록
3. **[권장] `_official_claude_skill_roots()`의 `os.path.isdir` 필터 제거를 구현 계획에 포함** — `iter_candidates()`의 기존 체크로 충분
4. **[권장] mtime 감지 한계를 설계 문서에 정직하게 기술** — "대부분의 변경 감지" → "추가/삭제만 감지"
5. **[권장] 우선순위 규칙의 적용 범위를 명확히 구분** — 외부 소스 간 vs AF 내부 스킬

---

**Note**: Cross Review 미수행으로 인해 단일 리뷰어 기반 판정입니다. Critical finding #1이 코드로 확실히 검증되었으므로 WARN을 유지하지만, 구현 전 #1 해결이 강력히 권장됩니다.