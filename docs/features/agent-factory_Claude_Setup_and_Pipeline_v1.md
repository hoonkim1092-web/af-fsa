# Agent Factory — Claude Code 개발 파이프라인 셋업 v1

**작성일**: 2026-04-03
**상태**: 구현 완료
**브랜치**: `agent-factory_harness_Claude_Setup_and_Pipeline_v1`

---

## 개요

agent-factory(core/ 174파일, 46,000줄) 대규모 프로젝트를 **Claude Code의 스킬, 서브에이전트, hooks**를 활용하여 체계적으로 개발하기 위한 환경 셋업.

단일 대화창에서 수동으로 진행하는 방식 → **자동화된 개발 파이프라인** 전환.

## 목표

1. AF 코드베이스를 이해하는 **전용 스킬** 생성 → 매 대화마다 코드를 처음부터 읽는 낭비 제거
2. 설계/구현/테스트/리뷰를 분담하는 **서브에이전트** 정의
3. 구현 후 테스트·Blueprint 동기화를 자동 실행하는 **hooks** 구성
4. 같은 모델의 "동의 편향" 문제를 해결하는 **교차 검증** 파이프라인

## 배포 범위

| 디렉토리 | 용도 | 빌드(af.spec) 포함 | Git 추적 |
|----------|------|:------------------:|:--------:|
| `.claude/skills/` | AF 개발용 Claude Code 스킬 | ❌ | ✅ |
| `.claude/settings.local.json` | AF 개발용 설정 (hooks, permissions) | ❌ | ✅ |
| `.claude/agents/` | AF 개발용 커스텀 에이전트 (비평가 등) | ❌ | ✅ |
| `.agents/skills/` | AF 개발용 에이전트 스킬 | ❌ | ✅ |
| `.agents/workflows/` | AF 개발용 워크플로우 | ❌ | ✅ |
| `skills/` | AF 런타임 스킬 (제품 일부) | ✅ | ✅ |
| `core/` | AF 소스 코드 | ✅ | ✅ |

> `af.spec`의 `datas`에 `.claude/`, `.agents/`는 포함되지 않으므로 빌드 시 배포되지 않음.
> Git에는 유지하여 개발팀 간 설정 공유 가능.

---

## 단계 1: AF 전용 Claude Code 스킬

현재 `.claude/skills/`에는 LangChain/LangGraph 범용 스킬만 존재.
AF 코드베이스를 이해하는 전용 스킬을 추가한다.

| 스킬 ID | 목적 | 트리거 조건 |
|---------|------|------------|
| `af-architecture` | AF 아키텍처 전체 구조 이해 | `core/*.py` 수정 요청 시 |
| `af-test-runner` | 구현 후 자동 테스트 실행 절차 | 구현 완료 후 |
| `af-blueprint-sync` | Master_Blueprint.md 동기화 규칙 | `core/*.py` 변경 커밋 전 |
| `af-code-review` | AF 코드 리뷰 패턴/체크리스트 | PR 생성 또는 리뷰 요청 시 |
| `af-skill-system` | AF 스킬 서브시스템 전문 지식 | `core/skill_*.py` 수정 시 |

### 각 스킬 상세

**af-architecture**
- Master_Blueprint.md §0~§12 핵심 요약
- 서브시스템 의존 관계 (Runtime → Control → Provider → Hook)
- 핵심 실행 흐름: CLI → ControlPlane → Orchestrator → AgentRunner
- 파일별 역할 빠른 참조

**af-test-runner**
- 3단계 검증: `py_compile` → `pytest tests/ -x` → 모듈 임포트 테스트
- 실패 시 수정 → 재테스트 루프
- `af.spec` hiddenimports 누락 체크

**af-blueprint-sync**
- 변경된 `core/*.py` → 해당 Blueprint §섹션 자동 식별
- §12 변경 이력 업데이트 포맷
- 코드 수정 + Blueprint 업데이트 = 같은 커밋 규칙 강제

**af-code-review**
- `docs/code_review/2026-04-03-code-review.md` 기반 체크 패턴:
  - Non-atomic 파일 쓰기 (C2 유형)
  - 스레드 안전성 (H1, H5 유형)
  - Dead code 감지
  - Shell injection 방어 (C4 유형)

**af-skill-system**
- 스킬 조달 파이프라인 5단계 흐름
- `skill_procurer.py` → `external_skill_sources.py` → `skill_registry.py` 관계
- registry.yaml 구조, lifecycle stage, quality gate

---

## 단계 2: 개발 파이프라인 흐름

단일 대화창 순차 작업 → 자동화 파이프라인:

```
[사용자] "WI-08 구현해줘"
    │
    ▼
[Plan 에이전트] ─────────────────────────────────────
    ├── Feature 문서 읽기
    ├── Master_Blueprint.md 해당 §섹션 확인
    ├── 영향 파일 목록 + 변경 계획 출력
    └── 사용자 승인 대기
    │
    ▼ 승인
[Coder 에이전트] ────────────────────────────────────
    ├── af-architecture 스킬 참조
    ├── 계획에 따라 코드 작성
    └── 완료 신호
    │
    ▼ 자동
[Test] ──────────────────────────────────────────────
    ├── py_compile 전체 core/*.py
    ├── pytest tests/ -x -q
    └── 실패 시 → Coder에 피드백 → 재작업
    │
    ▼ 통과
[Review] ─────────────────────────────────────────────
    ├── diff 기반 코드 리뷰 (af-code-review 스킬)
    ├── code-review.md 패턴 체크
    └── 문제 발견 시 → Coder에 피드백
    │
    ▼ 통과
[Cross-Review] ──────────────────────────────────────
    ├── Codex CLI로 diff 전달 → 피드백 수신
    ├── 피드백 항목별 판정 (ACCEPT/REJECT/HOLD)
    ├── ACCEPT → 코드 반영, HOLD → 사용자 확인
    └── 최종 판정 결과 보고
    │
    ▼ 통과
[Blueprint 동기화] ───────────────────────────────────
    ├── 변경된 core/*.py → §섹션 매핑
    └── Master_Blueprint.md 업데이트
    │
    ▼
[사용자에게 최종 결과 보고]
```

### Claude Code 에이전트 활용 방식

Claude Code의 `Agent` tool (subagent)로 각 단계를 실행:

| 단계 | subagent_type / agent | 사용 스킬 |
|------|----------------------|----------|
| 계획 | `Plan` | af-architecture |
| 구현 | `general-purpose` | af-architecture, af-skill-system |
| 테스트 | `general-purpose` (background) | af-test-runner |
| 리뷰 | `general-purpose` (background) | af-code-review |
| 교차 검증 | `af-cross-review` 에이전트 | Codex CLI 호출 |
| Blueprint | `general-purpose` | af-blueprint-sync |

---

## 단계 3: 교차 검증

### 현재 문제

> Claude에 피드백을 주면 "내가 잘못 판단했다" 동의하는 경우가 대다수

같은 모델이 자기 코드를 자기가 리뷰 → **동의 편향(sycophancy)**

### 해결 방법

**방법 A: 다른 모델로 검증** (권장) → `.claude/agents/af-cross-review.md`
```
[Coder: Claude] → 코드 작성
    ↓
[Codex CLI] → codex review --uncommitted / --commit HEAD
    ↓ 피드백 수신
[Claude] → 각 항목별 코드 근거 확인
    ├── ACCEPT: 실제 버그/개선점 → 코드 반영
    ├── REJECT: 코드 오독/이미 처리됨 → 근거와 함께 기각
    └── HOLD: 추가 컨텍스트 필요 → 사용자에게 판단 요청
```

사용법:
```bash
claude --agent af-cross-review   # Codex 교차 검증 실행
```

**방법 B: 역할 분리 프롬프트** → `.claude/agents/af-critic.md`
```
[Coder: Claude + 기본 프롬프트]
[Reviewer: Claude + af-critic 에이전트]
   - 최소 3개 이상 관찰 보고 의무
   - 문제 없으면 논리적 증명 필수
   - 칭찬/변호/약화 표현 금지
   - af-code-review 체크리스트 10항목 자동 적용
→ 프롬프트가 다르면 같은 모델이라도 관점이 달라짐
```

사용법:
```bash
claude --agent af-critic   # 비평가 모드로 리뷰 실행
```

**방법 C: AF 기존 컴포넌트 활용**

AF 자체에 이미 교차 검증 시스템이 구현되어 있음:
- `core/cross_verification.py` (760줄) — 멀티 LLM 교차 검증
- `core/parallel_critique.py` (371줄) — 병렬 비평
- `core/consensus_engine.py` (364줄) — 합의 엔진

이 컴포넌트를 Claude Code 파이프라인에서 호출하여 활용 가능.

### 적용 전략

- 일반 코드 변경: **방법 B** (빠름, 비용 낮음)
- 핵심 서브시스템 변경 (agent_runner, dynamic_orchestrator 등): **방법 A** (신뢰도 높음)
- 대규모 리팩토링: **방법 C** (AF 자체 교차 검증 엔진 사용)

---

## 단계 4: hooks 자동화

### Claude Code hooks (settings.json)

코드 수정 후 자동 실행되는 작업:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "fp=$TOOL_INPUT_file_path; case \"$fp\" in *.py) python -m py_compile \"$fp\" 2>&1 || echo \"[af-hook] syntax error in $fp\" ;; esac",
            "timeout": 10,
            "statusMessage": "Python syntax check..."
          }
        ]
      }
    ]
  }
}
```

### 기존 Git hooks (.githooks/pre-commit)

이미 존재하는 pre-commit hook:
- `core/*.py` 변경 시 `Master_Blueprint.md`가 스테이징되어 있는지 체크
- 미스테이지 → 커밋 차단

### 자동화 체크리스트

| 이벤트 | 자동 실행 | 방식 |
|--------|----------|------|
| `core/*.py` 수정 | py_compile syntax 체크 | Claude Code hook |
| 구현 완료 | pytest tests/ -x | 수동 또는 서브에이전트 |
| 커밋 전 | Blueprint 스테이징 체크 | Git pre-commit hook (기존) |
| PR 생성 | 코드 리뷰 체크리스트 | af-code-review 스킬 |

---

## 단계 5: 환경 셋팅 절차

### 신규 개발자 셋업 (레포 clone 후)

```bash
# 1. Git hooks 활성화
git config core.hooksPath .githooks

# 2. Claude Code 스킬 확인
ls .claude/skills/af-*/SKILL.md

# 3. 끝. settings.local.json, 스킬 모두 레포에 포함되어 있음.
```

### 현재 settings.local.json 정리

현재 90개+ Bash 허용 규칙이 무질서하게 쌓여있음.
이번 작업에서 용도별로 정리:

| 카테고리 | 내용 |
|----------|------|
| 프로젝트 읽기 | `Read`, `Glob`, `Grep` 허용 범위 |
| 테스트 실행 | `pytest`, `py_compile` 관련 |
| 빌드 | `build_exe.py` 관련 |
| Git | `git status`, `git diff` 등 |

---

## 관련 Feature

- [cross-cli-skill-discovery.md](cross-cli-skill-discovery.md) — Claude Code/Codex CLI 스킬 자동 탐색
- [claude-code-skills-2.0.md](claude-code-skills-2.0.md) — Skills 2.0 통합 (완료)

---

## 체크리스트

- [x] Feature 문서 작성 (본 문서)
- [x] af-architecture 스킬 생성
- [x] af-test-runner 스킬 생성
- [x] af-blueprint-sync 스킬 생성
- [x] af-code-review 스킬 생성
- [x] af-skill-system 스킬 생성
- [x] settings.json hooks 구성
- [x] settings.local.json 허용 규칙 정리 (208개 → 34개 카테고리별 통합)
- [x] 교차 검증 방법 B 프롬프트 설계 (`.claude/agents/af-critic.md`)
- [x] cross-CLI 스킬 탐색 Feature 연결
