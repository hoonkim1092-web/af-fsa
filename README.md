# Agent Factory

멀티 에이전트 오케스트레이션 플랫폼. 프로젝트 단위로 특화된 에이전트를 생성·실행·진화시킵니다.

---

## 설치

### 사전 요구사항

- Python 3.11 이상
- Git

### 1. 저장소 클론

```bash
git clone <repo_url>
cd agent-factory
```

### 2. 패키지 설치

```bash
pip install -e .
```

설치하면 `af` 명령어가 등록됩니다.

```bash
af --help
```

### 3. 환경 변수 설정

`.env` 파일을 만들거나 환경 변수를 직접 설정합니다.

```bash
# 필수 (하나 이상)
GOOGLE_API_KEY=...          # Gemini 모델 사용 시
OPENAI_API_KEY=...          # GPT 모델 사용 시
ANTHROPIC_API_KEY=...       # Claude 모델 사용 시

# 선택
LANGSMITH_API_KEY=...       # LangSmith 트레이싱
LANGSMITH_PROJECT=agent-factory
AGENT_CHAT_PROVIDER=claude_cli   # CLI 제공자 (claude_cli / gemini_cli / codex_cli)
```

---

## 사용법

### 기본 실행

```bash
# 태스크 실행 (--project 필수)
af -p my_project -t "포커 게임 웹앱 만들어줘"

# 역할 지정
af -p my_project -r "Backend Dev" -t "REST API 설계"

# 태스크 없이 실행하면 입력 프롬프트 표시
af -p my_project
```

### 실행 모드

```bash
# approval 모드 (기본): 실행 전 계획 검토 및 승인
af -p my_project -t "대시보드 구현"

# FSA 모드: 완전 자동 실행
af -p my_project -t "대시보드 구현" --fsa
```

### 스킬 빌드 포함

```bash
# 필요한 스킬이 없으면 자동 빌드
af -p my_project -t "데이터 분석" --build
```

### 모델 지정

```bash
af -p my_project -t "기획서 작성" -m "claude-opus-4-6"
af -p my_project -t "코드 리뷰" -m "gemini-2.0-flash"
```

### 파이프라인 모드

```bash
# auto (기본): 태스크 복잡도에 따라 자동 선택
af -p my_project -t "간단한 수정" --pipeline single

# project: 다단계 프로젝트 파이프라인 (기획 → 승인 → 실행)
af -p my_project -t "전체 앱 구현" --pipeline project
```

### CLI 제공자 지정

```bash
# Claude CLI 사용
af -p my_project -t "코드 작성" --provider claude_cli

# Gemini CLI 사용
af -p my_project -t "코드 작성" --provider gemini_cli

# 명령어 경로 직접 지정
af -p my_project -t "..." --provider claude_cli --provider-command /usr/local/bin/claude
```

### 워크플로우 실행

```bash
af -p my_project -w workflows/my_workflow.yaml
af -p my_project -w workflows/my_workflow.yaml -a "Lilith,Himari" -t "2주 플랜"
```

### 스킬 관련 서브커맨드

```bash
af skill-eval      # 스킬 평가
af skill-promote   # 스킬 승격
af skill-create    # 스킬 생성
af preflight       # 사전 검증
```

---

## 옵션 전체 목록

| 옵션 | 단축 | 설명 |
|------|------|------|
| `--project` | `-p` | 프로젝트 ID (필수) |
| `--task` | `-t` | 실행할 태스크 |
| `--role` | `-r` | 에이전트 역할 (기본: General Assistant) |
| `--mode` | | `approval` 또는 `fsa` |
| `--fsa` | | FSA 모드 단축키 |
| `--build` | | 누락 스킬 자동 빌드 |
| `--model` | `-m` | 모델 이름 지정 |
| `--provider` | | CLI 제공자 지정 |
| `--provider-command` | | CLI 명령어 경로 |
| `--pipeline` | | `auto` / `single` / `project` |
| `--workflow` | `-w` | 워크플로우 YAML 경로 |
| `--agents` | `-a` | 워크플로우 역할 목록 (쉼표 구분) |
| `--projects-root` | | 프로젝트 루트 경로 재정의 |
| `--no-cli-auto-install` | | CLI 자동 설치 비활성화 |

---

## 프로젝트 구조

```
agent-factory/
├── core/               # 핵심 모듈 (orchestrator, runner, memory 등)
├── skills/             # 전역 재사용 스킬
├── agents/             # 전역 에이전트 템플릿
├── projects/           # 프로젝트별 데이터 (실행 후 자동 생성)
│   └── <project_id>/
│       ├── agents/     # 프로젝트 에이전트 YAML
│       ├── data/       # 메모리, 메일박스
│       ├── artifacts/  # 산출물
│       ├── runs/       # 실행 로그
│       └── planning/   # 기획 문서
├── run_factory_cli.py  # CLI 진입점
└── pyproject.toml      # 패키지 설정
```

---

## 테스트

```bash
python -m pytest tests/ -v
```
