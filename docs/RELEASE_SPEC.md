# [Agent Factory] 릴리즈용 시스템 요구사항 정의서

> ⚠️ **본 문서는 릴리즈(Release/Output) 전용입니다. 뼈대(Skeleton) 개발에는 적용하지 않습니다.**
>
> 작성일: 2026-02-28 | 상태: 기획 확정(박제)

---

## 1. 마스터 블루프린트 문서 업로드

사용자가 상세 기획서/블루프린트 문서를 **파일로 업로드**하면 시스템이 자동으로 파싱하여 에이전트 생성에 활용한다.

- 지원 포맷: `.md`, `.pdf`, `.docx`
- 파싱 결과: 구조화된 JSON (`goals`, `agents`, `constraints`)
- 필요 모듈: `core/spec_parser.py` [NEW]

## 2. 에이전트 롤/설정 직접 입력 (무한 추가)

사용자가 각 에이전트의 역할, 이름, 성격, 스킬을 직접 설정하고, 개수 제한 없이 추가할 수 있도록 한다.

- 대화형 위저드 또는 Web UI 제공
- 입력 → `agents/{name}.yaml` 자동 생성
- 필요 모듈: `core/agent_configurator.py` [NEW]

## 3. "~~ 만들어줘" 자동 생성

사용자가 문서 업로드나 설정 입력 없이 **"포커게임 만들어줘"** 같은 자연어만으로 프로젝트에 필요한 에이전트 군단을 자동 생성한다.

- 기존 `decompose_roles()` 확장 (역할명 + 스킬 + 성격 + 시스템프롬프트 포함)
- 도메인 분류 → 템플릿 매칭 파이프라인 추가

## 4. 자료수집 AI 엔진 추론 + 서칭

에이전트 생성에 필요한 자료를 수집할 때, 어떤 AI 엔진을 사용할지 시스템이 **자동 추론**하여 최적 모델을 선택한 후 서칭한다.

- 도메인 분석 → 엔진 자동 배정 (예: 게임 도메인 → Web Search → `researcher_gemini`)
- 다중 소스 통합: Web Search + NotebookLM + GitHub
- 수집 결과 → `evidence_pack` 구조화
- 필요 모듈: `core/research_orchestrator.py` [EXTRACT+NEW]

## 5. 에이전트 리스트업 + 사용자 수정

에이전트가 생성되고 스킬이 등록되면, **상세 목록을 사용자에게 표시**하고 문제가 있으면 수정할 수 있게 한다.

### 상세 흐름

```
AI 역할 분해 완료
    ↓
╔══════════════════════════════════════╗
║ #  이름      역할          AI엔진    스킬          ║
║ 1  Dealer   게임 로직     Pro       game_logic    ║
║ 2  Designer UI/UX        Claude    ui_design     ║
║ 3  Tester   QA           GPT-4o    test_runner   ║
╚══════════════════════════════════════╝
    ↓
[Y] 진행 / [번호] 수정 / [A] 추가 / [D 번호] 삭제 / [R] 재생성
    ↓
사용자 확인 후 조립 시작
```

### 필요 기능

- 테이블 형식 리스트업 출력
- 승인 게이트 (사용자 확인 전 조립 불가)
- 대화형 수정 (역할/엔진/스킬 변경)
- 변경 전/후 diff 표시
- 수정 후 의존성 재검증
- 필요 모듈: `core/agent_review.py` [NEW] (~200줄)

### 삽입 지점

```python
# project_orchestrator.py
roles = decompose_roles(description)      # STEP 1: 분해
confirmed = AgentReviewDashboard(roles).run_review_loop()  # STEP 2: 리뷰 [NEW]
forge_roles(confirmed)                     # STEP 3: 조립
```

## 6. 역할별 AI 배정 + 스킬 조달/제작

에이전트별로 역할에 맞는 최적의 AI 엔진을 배정하고, 필요한 스킬을 외부에서 찾아 등록하거나 없으면 새로 제작한다.

- Triad 원칙 적용: Research=Gemini, Coding=Claude, Verify=GPT
- 외부 스킬 조달: GitHub/스킬 마켓 검색 → 자동 설치
- 미존재 스킬: `forge_new_skill()`로 AI가 자동 코딩
- **항상 최신 모델 사용** (`model_utils.resolve_dynamic_model` + Auto-Upgrade)

## 7. 무료 무한 토큰 (다중 API 키 ADD 시스템)

무료 버전 사용 시 토큰 쿼터 문제를 해결하기 위해, API 키를 런타임에 추가할 수 있는 시스템을 구현한다.

### CLI 명령

```
af key add GOOGLE_API_KEY_2=AIza...    # 키 추가
af key list                             # 등록 키 목록 (마스킹)
af key remove 3                         # 키 삭제
```

### 동작 원리

- `.env`에 자동 추가 + Load Balancer 핫리로드
- 429 에러 시 자동 다음 키 전환 (이미 구현됨)
- 키 상태 대시보드 (잔여 쿼터 표시)
- 필요 모듈: `core/key_manager.py` [NEW]

---

## 아키텍처 요약

```
core/
├── spec_parser.py          # 요구사항 #1 [NEW]
├── agent_configurator.py   # 요구사항 #2 [NEW]
├── role_decomposer.py      # 요구사항 #3 [EXTRACT]
├── research_orchestrator.py # 요구사항 #4 [NEW]
├── agent_review.py         # 요구사항 #5 [NEW]
├── skill_procurer.py       # 요구사항 #6 [EXTRACT]
├── key_manager.py          # 요구사항 #7 [NEW]
└── llm_engine.py           # 실행 엔진 [EXISTS]
```

## 구현 우선순위

| 순위 | 요구사항 | 근거 |
|---|---|---|
| 🥇 1 | #3 + #5 | 자동생성 + 리뷰는 UX 핵심, 동시 진행 |
| 🥈 2 | #4 + #7 | 지능 품질 + 무한 토큰 |
| 🥉 3 | #6 | 이미 70% 완성 |
| 4 | #1 + #2 | 파서/위저드는 나중에 |
