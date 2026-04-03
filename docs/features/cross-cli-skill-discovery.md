# Cross-CLI 스킬 탐색 (Claude Code + Codex 통합)

**작성일**: 2026-04-03
**최종 수정**: 2026-04-03
**상태**: 설계 v3 (Codex 피드백 15건 반영), 구현 대기

---

## 목적

Agent Factory가 스킬을 새로 생성(forge)하기 전에, 사용자의 로컬 머신에 이미 설치된
**Claude Code / Codex CLI의 스킬 저장소**를 자동으로 탐색하여 재사용한다.

- 동일한 SKILL.md 포맷을 공유하므로 knowledge skill은 호환 가능 (단, AF 지원 frontmatter subset 한정 — 아래 호환성 매트릭스 참조)
- 이미 존재하는 스킬을 LLM 토큰 써서 다시 forge 하는 낭비를 제거

---

## v2 → v3 변경 사항

v3는 Codex 교차 검증(15건)을 반영한 재설계.

| v2 설계 | 문제 | v3 변경 |
|---------|------|---------|
| 6단계 파이프라인 (CLI fetch 포함) | `codex skills install` / `claude skills install` 명령이 존재하지 않음 (**Critical**) | **5단계로 축소** — CLI fetch 단계 제거 |
| 스캔 순서: project > personal | Claude 공식 precedence 역전 (personal > project) | **personal 우선** |
| `safe_id(entry)` 비교 | frontmatter name과 디렉토리명 불일치 시 false positive/negative | **frontmatter name 우선, 디렉토리명 fallback** |
| 새 디렉토리만 감지 | 삭제/수정/내용 변경 미감지 | **mtime 기반 변경 감지** |
| CLI 존재 시 repo source 무력화 가정 | private/pinned repo는 CLI와 별개 | **repo source 항상 활성** |
| CLI auto-fetch 기본 on | 보안/과금 side effect | **단계 자체 제거** |
| Windows 경로 하드코딩 | 공식 문서 근거 부족 | **env opt-in으로 전환** |
| dedup 기준 없음 | 같은 skill ID가 여러 source에 존재 시 비결정적 | **명시적 우선순위 규칙** |
| plugin namespace 미지원 | Claude plugin skill 누락 | **v3.1에서 별도 대응** (범위 제한) |

---

## 현재 문제 3가지

### 문제 1: Claude Code 스킬 경로 미탐색

`get_codex_skill_roots()` (`core/utils.py:253`)에 Claude Code 경로가 없음:

```
✅ ~/.codex/skills/
✅ ~/.agents/skills/
❌ ~/.claude/skills/            ← Claude Code 사용자 스킬 (personal)
❌ PROJECT_ROOT/.claude/skills/ ← Claude Code 프로젝트 스킬
```

### 문제 2: 유지보수 시 신규 외부 스킬 미감지

```python
# core/skill_registry.py:274
def ensure_skills_loaded() -> None:
    registry = get_global_registry()
    if registry.count() == 0:          # ← 이미 로드된 적 있으면 건너뜀!
        registry.auto_load_from_directories()
```

**시나리오**: 어제 codex에서 새 스킬 설치 → 오늘 AF 실행 → `count() > 0`이므로 건너뜀 → 새 스킬 미감지

### 문제 3: 스킬 중복 해소 규칙 없음

같은 skill ID가 `~/.claude/skills/`, `~/.codex/skills/`, repo cache에 동시에 존재할 때
어떤 source가 이기는지 정의 없음 → 결과 비결정적.

---

## 설계 (v3)

### 전체 스킬 조달 파이프라인

```
스킬 필요
   ↓
[단계 1] 정확 매칭 — 로컬에 이미 있는지 확인 (기존)
   ↓ 없으면

[단계 2] 재사용 판단 — SkillRetrievalEngine.decide_reuse() (기존)
   ↓ 해당 없으면

[단계 3] 로컬 디스크 스캔 (경로 확장 + 우선순위 수정)
   ├── 1순위 (personal): ~/.claude/skills/, ~/.codex/skills/, ~/.agents/skills/
   ├── 2순위 (project):  PROJECT_ROOT/.claude/skills/, .codex/skills/, .agents/skills/
   ├── 3순위 (runtime):  $CODEX_HOME/skills/
   ├── 4순위 (env):      AGENT_CODEX_SKILL_DIRS, AGENT_CLAUDE_SKILL_DIRS
   └── 5순위 (system):   /etc/codex/skills/ (Linux), env opt-in (Windows)
   ↓ 없으면

[단계 4] Git 레포 폴백 (기존 외부 소스 — 항상 활성)
   ├── claude_repo (AGENT_EXTERNAL_SKILL_CLAUDE_REPOS)
   ├── codex_repo (AGENT_EXTERNAL_SKILL_CODEX_REPOS)
   ├── registry (매니페스트)
   └── external_cache
   ↓ 없으면

[단계 5] LLM forge (기존)
```

**v2 대비 변경**: CLI auto-fetch (구 단계 4) 제거. `codex skills install` / `claude skills install` 명령이 존재하지 않으므로.

### 스캔 우선순위 규칙

Claude Code 공식 문서는 personal > project 순서를 명시. AF도 이를 따른다:

```
같은 skill ID "data_parser"가 두 곳에 존재:
  ~/.claude/skills/data_parser/      ← personal (1순위) → 이것이 선택됨
  PROJECT_ROOT/.claude/skills/data_parser/  ← project (2순위)
```

**dedup 규칙**: 같은 skill ID가 여러 source에 존재하면 **먼저 발견된 것이 승리** (우선순위 순서대로 스캔하므로).

### 스킬 ID 매칭 로직 수정

```python
# v2 (버그): 디렉토리명만으로 비교
skill_id = safe_id(entry)  # 디렉토리 이름

# v3 (수정): frontmatter name 우선, 디렉토리명 fallback
def _extract_skill_id(skill_dir: str) -> str:
    """SKILL.md의 frontmatter name을 우선 사용, 없으면 디렉토리명."""
    for md_name in ("SKILL.md", "skill.md"):
        md_path = os.path.join(skill_dir, md_name)
        if os.path.exists(md_path):
            name = _parse_frontmatter_name(md_path)
            if name:
                return safe_id(name)
    return safe_id(os.path.basename(skill_dir))
```

### 유지보수 시 스킬 감지 수정

v2의 `_check_external_skill_changes()`는 새 디렉토리만 감지했음. v3는 **mtime 기반**으로 변경 전체를 감지:

```python
# core/skill_registry.py

def ensure_skills_loaded() -> None:
    registry = get_global_registry()
    if not registry._external_scanned:            # ← public 메서드로 변경 예정
        registry.auto_load_from_directories()
    elif registry.should_rescan_external():        # ← mtime 비교
        registry.auto_load_from_directories(force=True)


class SkillRegistry:
    def __init__(self):
        self._external_scanned = False
        self._last_scan_time: float = 0.0         # time.time() at last scan

    def should_rescan_external(self) -> bool:
        """외부 스킬 디렉토리의 mtime이 마지막 스캔 이후 변경되었는지 확인."""
        for root in get_external_skill_roots():
            if not os.path.isdir(root):
                continue
            if os.path.getmtime(root) > self._last_scan_time:
                return True  # 디렉토리 자체가 변경됨 (파일 추가/삭제)
        return False

    @property
    def external_scanned(self) -> bool:
        """외부 스킬 스캔 완료 여부 (public API)."""
        return self._external_scanned
```

**v2 대비 변경**:
- `count() == 0` → `_external_scanned` 플래그 (Codex 피드백 #14: public API로 노출)
- `_check_external_skill_changes()` (디렉토리 basename 비교) → `should_rescan_external()` (mtime 비교)
- mtime은 파일 추가/삭제 시 디렉토리 자체의 mtime이 갱신되므로, 이 체크만으로 대부분의 변경 감지 가능

---

## SKILL.md 호환성 매트릭스

v2에서 "knowledge skill은 100% 호환"이라 표현했으나, 실제로는 AF가 지원하는 frontmatter subset이 있다.

| frontmatter 필드 | Claude Code | Codex | AF 지원 | 비고 |
|-----------------|:-----------:|:-----:|:-------:|------|
| `name` | ✅ | ✅ | ✅ | skill ID로 사용 |
| `description` | ✅ | ✅ | ✅ | |
| `user-invocable` | ✅ | ❌ | ❌ | AF에서는 무시 |
| `disable-model-invocation` | ✅ | ❌ | ❌ | AF에서는 무시 |
| `allowed-tools` | ✅ | ❌ | ❌ | AF에서는 무시 |
| `context: fork` | ✅ | ❌ | ❌ | AF에서는 무시 |
| `hooks` | ✅ | ❌ | ❌ | AF에서는 무시 |
| `paths` | ✅ | ❌ | ❌ | AF에서는 무시 |
| body (markdown) | ✅ | ✅ | ✅ | knowledge content |

**결론**: body(markdown) 기반 knowledge skill은 호환. Claude 확장 frontmatter는 AF에서 무시(에러 아닌 skip).

---

## 수정 대상 파일

### 1. `core/utils.py` — `get_codex_skill_roots()` → `get_external_skill_roots()`

Claude Code 경로 추가 + personal 우선 순서 + 함수명 리네임:

```python
def get_external_skill_roots(extra_roots: list[str] | None = None) -> list[str]:
    roots, seen = [], set()
    home_dir = os.path.expanduser("~")
    defaults = [
        # 1순위: personal (Claude 공식 precedence: personal > project)
        os.path.join(home_dir, ".claude", "skills"),
        os.path.join(home_dir, ".codex", "skills"),
        os.path.join(home_dir, ".agents", "skills"),
        # 2순위: project
        os.path.join(PROJECT_ROOT, ".claude", "skills"),
        os.path.join(PROJECT_ROOT, ".codex", "skills"),
        os.path.join(PROJECT_ROOT, ".agents", "skills"),
    ]
    # 3순위: Codex 런타임 홈
    codex_home = os.getenv("CODEX_HOME", "").strip()
    if codex_home:
        defaults.append(os.path.join(codex_home, "skills"))
    # 4순위: 환경변수 (사용자 지정 경로)
    # 5순위: 시스템 경로 (Linux만 기본, Windows는 env opt-in)
    if os.name != "nt":
        defaults.append("/etc/codex/skills")

    env_paths = (
        _split_env_paths(os.getenv("AGENT_CODEX_SKILL_DIRS"))
        + _split_env_paths(os.getenv("AGENT_CLAUDE_SKILL_DIRS"))
    )
    # ... 기존 dedup 로직 (extra_roots + env_paths + defaults) ...

# 하위 호환
get_codex_skill_roots = get_external_skill_roots
```

**v2 대비 변경**:
- personal 경로가 project보다 먼저 (Codex 피드백 #6)
- Windows `%APPDATA%\Claude\skills`, `%ProgramData%\codex\skills` 제거 → 공식 문서 근거 없음 (Codex 피드백 #11). 필요하면 `AGENT_CLAUDE_SKILL_DIRS`로 추가.

### 2. `core/external_skill_source_ids.py` — 소스 ID/우선순위 수정

```python
EXTERNAL_SOURCE_ID_ALIASES = {
    # 기존
    "codex_official": "codex_official",
    "official_codex": "codex_official",
    "official_codex_skills": "codex_official",
    "codex_skills": "codex_official",
    "claude": "claude_repo",
    "claude_repo": "claude_repo",
    "codex": "codex_repo",
    "codex_repo": "codex_repo",
    # 신규
    "claude_official": "claude_official",
    "claude_code": "claude_official",
    "claude_skills": "claude_official",
}

DEFAULT_EXTERNAL_SOURCE_PRIORITY = [
    "codex_official",
    "claude_official",     # 신규: Claude Code 로컬 스킬
    "claude_repo",         # Git clone (항상 활성 — CLI 존재와 무관)
    "codex_repo",          # Git clone (항상 활성)
    "registry",
    "external_cache",
]
```

**v2 대비 변경**: repo source를 CLI 존재 여부로 비활성화하지 않음 (Codex 피드백 #7).

### 3. `core/external_skill_sources.py` — `ClaudeOfficialSkillSource` 추가

`CodexOfficialSkillSource`와 동일 패턴이지만, skill ID 추출에 frontmatter name 우선 사용:

```python
class ClaudeOfficialSkillSource(ExternalSkillSource):
    def __init__(self, root_dirs: list[str], source_id: str = "claude_official"):
        super().__init__(source_id)
        self.root_dirs = [os.path.abspath(str(r)) for r in (root_dirs or []) if str(r).strip()]

    def iter_candidates(self) -> list[ExternalSkillCandidate]:
        out: list[ExternalSkillCandidate] = []
        seen: set[str] = set()
        for root_dir in self.root_dirs:
            if not os.path.isdir(root_dir):
                continue
            for entry in sorted(os.listdir(root_dir)):
                skill_dir = os.path.join(root_dir, entry)
                if not os.path.isdir(skill_dir):
                    continue
                if not any(os.path.exists(os.path.join(skill_dir, n)) for n in ("SKILL.md", "skill.md")):
                    continue
                skill_id = _extract_skill_id(skill_dir)  # frontmatter name 우선
                if not skill_id or skill_id in seen:
                    continue
                seen.add(skill_id)
                out.append(ExternalSkillCandidate(
                    source_id=self.source_id,
                    skill_id=skill_id,
                    name=skill_id,
                    path=skill_dir,
                    capabilities=[skill_id],
                    source_repo=os.path.basename(root_dir) or self.source_id,
                ))
        return out
```

**v2 대비 변경**: `safe_id(entry)` → `_extract_skill_id(skill_dir)` (Codex 피드백 #4).

### 4. `core/skill_registry.py` — 유지보수 시 스킬 감지 수정

위 "유지보수 시 스킬 감지 수정" 섹션 참조:
- `count() == 0` → `external_scanned` property
- `_check_external_skill_changes()` → `should_rescan_external()` (mtime 기반)

### 5. `core/external_skill_sources.py` — `CodexOfficialSkillSource`도 frontmatter name 사용

기존 `CodexOfficialSkillSource.iter_candidates()`의 `safe_id(entry)` → `_extract_skill_id(skill_dir)` 동일 적용.

---

## 외부 소스 우선순위 정리

v3에서는 CLI 존재 여부와 관계없이 **모든 소스가 항상 활성**:

```
로컬 스캔 (personal > project > runtime > env > system)
   ↓ 없으면
외부 소스 (codex_official → claude_official → claude_repo → codex_repo → registry → cache)
   ↓ 없으면
LLM forge
```

- `claude_repo`, `codex_repo` Git clone은 **항상 폴백으로 동작** (Codex 피드백 #7)
- private/pinned repo는 CLI installer의 catalog과 별개이므로, CLI가 있어도 비활성화하면 안 됨

### 같은 skill ID 중복 해소

```
"data_parser"가 3곳에 존재:
  ~/.claude/skills/data_parser/   (claude_official, personal)  ← 1순위 승리
  ~/.codex/skills/data_parser/    (codex_official)
  repo cache의 data_parser/       (codex_repo)

→ claude_official의 것이 선택됨 (스캔 순서 = 우선순위)
```

**규칙**: 같은 ID는 **먼저 발견된 source의 것을 사용**. 스캔 순서가 곧 우선순위.

---

## v3.1 (향후) — 범위 밖으로 미룬 항목

| 항목 | 이유 |
|------|------|
| Claude plugin namespace (`plugin-name:skill-name`) | plugin 생태계 안정화 후 별도 설계 필요 (Codex 피드백 #13) |
| monorepo nested skill (`packages/*/. claude/skills/`) | `--add-dir` 경로 탐색은 Claude Code가 자체 처리. AF가 개입할 범위 아님 (Codex 피드백 #5) |
| `SKILL.md` / `skill.md` 동시 허용 parity claim | AF 전용 확장으로 문서화, "동일 동작" 주장 제거 (Codex 피드백 #10) |

---

## 영향 범위

| 파일 | 변경 유형 | 영향 |
|------|----------|------|
| `core/utils.py` | 수정 | 경로 추가 + 순서 변경 + 리네임 (alias 유지) |
| `core/external_skill_source_ids.py` | 수정 | `claude_official` 소스 ID + 우선순위 추가 |
| `core/external_skill_sources.py` | 수정 | `ClaudeOfficialSkillSource` 추가 + `_extract_skill_id()` |
| `core/skill_registry.py` | 수정 | `should_rescan_external()` + `external_scanned` property |
| `af.spec` | 수정 | 신규 모듈 hiddenimports 불필요 (기존 파일만 수정) |

### Blast Radius

- `get_codex_skill_roots()` alias 유지 → **하위 호환 100%**
- repo source 항상 활성 → **기존 동작 영향 없음**
- `ensure_skills_loaded()` 수정 → `count() == 0` → `external_scanned` property로 변경
- skill ID 매칭 변경 → frontmatter name 우선 → 기존 디렉토리명 매칭은 fallback으로 유지

### v2 대비 제거된 파일

| 파일 | 이유 |
|------|------|
| `core/cli_skill_fetcher.py` (신규 예정이었음) | CLI install 명령 미존재로 **불필요** |

---

## 체크리스트

- [x] Feature 문서 v1 작성
- [x] 유지보수 시 스킬 미감지 버그 문서화
- [x] CLI fetch + Git clone 중복 관계 정리
- [x] Codex 교차 검증 (15건 피드백)
- [x] v3 재설계 (Codex 피드백 반영)
- [x] `core/utils.py` — `get_external_skill_roots()` 리네임 + Claude 경로 추가 + personal 우선 순서
- [x] `core/external_skill_source_ids.py` — `claude_official` 소스 ID + 우선순위 추가
- [x] `core/external_skill_sources.py` — `ClaudeOfficialSkillSource` + `_extract_skill_id()` 구현
- [x] `core/external_skill_sources.py` — `CodexOfficialSkillSource`도 `_extract_skill_id()` 적용
- [x] `core/skill_registry.py` — `ensure_skills_loaded()` + `should_rescan_external()` 수정
- [x] 테스트: 유지보수 시 신규 외부 스킬 감지 확인
- [x] 테스트: Claude Code 스킬 디렉토리 탐색 확인
- [x] 테스트: personal > project 우선순위 확인
- [x] 테스트: 같은 skill ID 중복 해소 확인
- [x] 테스트: frontmatter name vs 디렉토리명 매칭 확인
- [ ] Master_Blueprint.md 업데이트
