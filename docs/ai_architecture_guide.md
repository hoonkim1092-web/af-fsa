# Agent Factory AI 아키텍처 가이드 (Codex 5.3)

본 문서는 `Agent Factory` 프로젝트 자체를 개발하는 **'뼈대 개발 지능'**과, 릴리즈 후 사용자가 에이전트를 생산하고 구동할 때 사용하는 **'운영/생산 지능'**의 차이와 흐름을 상세히 설명합니다.

---

## 1. 🏗️ 뼈대 개발용 뇌 (Framework Development Brain)
> **대상**: `agent-factory` 프로젝트 아키텍처 및 코어 엔진 개발 (The Skeleton)

이 지능은 공장(Tool) 자체를 안정적이고 확장 가능하게 구축하는 역할을 수행합니다.

### A. 설계 원칙 (Engineering Philosophy)
- **SDD (Spec-Driven Development)**: 코드 작성 전 반드시 `functional_spec.md`와 `technical_plan.md`를 통해 설계를 확정합니다. 환각(Hallucination)을 방지하고 정밀한 아키텍처를 유지하기 위함입니다.
- **Assetization (자산화)**: 모든 기능은 '매각 가능한 자산' 단위로 개발합니다. 재사용 가능한 스킬(`warehouse`)과 코어 모듈(`core/`)이 이 단계에서 설계됩니다.
- **Zero-Integration (침투형 구조)**: 복잡한 API 연동 없이 파일과 이메일만으로 구동되는 시스템의 뼈대를 만듭니다.

### B. 사용 모델 및 역할
- **주 사용 모델**: `gemini-2.0-pro`, `claude-3.5-sonnet` 등 최상위 리즈닝 모델.
- **Antigravity (AI Assistant)**: 프로덕트 디렉터 `Lilith`와 함께 프로젝트의 일정과 기술적 완결성을 지휘합니다. 시스템 아키텍트이자 메인 개발자 역할을 자율적으로 수행합니다.

---

## 2. 🚀 릴리즈 후 사용자용 뇌 (Post-Release User Brain)
> **대상**: 공장을 통해 생산될 에이전트(Logi-Mind 등)의 생성 및 업무 수행

사용자가 공장을 가동하여 실제로 업무에 투입할 에이전트를 조립하고 운영할 때 작동하는 지능망입니다.

### A. 에이전트 생산 지능 (Forging Brain)
에이전트를 조립할 때 `factory_manager.py`를 통해 호출되는 지능입니다.
- **리서치 엔진 (`Himari`)**: `NotebookLM`과 `Gemini Pro`를 결합하여 도메인 지식을 심층 분석합니다.
- **코딩 엔진 (`Codex`)**: `gpt-5-codex-5.3` (또는 `gemini-3.1-pro`)를 사용하여 필요한 스킬을 직접 샌드박스에서 코딩하여 에이전트에게 탑재합니다.

### B. 에이전트 운영 지능 (Operational Brain)
생산된 에이전트가 실제 업무(메일/엑셀 처리)를 수행할 때 사용하는 **3-Stage Funnel** 구조입니다.

| 단계        | 엔진 명칭        | 주요 모델           | 역할                                      |
| :---------- | :--------------- | :------------------ | :---------------------------------------- |
| **Stage 1** | **Rule-based**   | Python Logic        | 단순 판정 및 사전 필터링 (비용 최적화)    |
| **Stage 2** | **Flash Engine** | `Gemini 3.0 Flash`  | 대량 데이터 요약, 스니핑, 텍스트 정규화   |
| **Stage 3** | **Pro Engine**   | `Claude 3.5 Sonnet` | 고도의 비즈니스 추론, 최종 발주/승인 판정 |

---

## 3. 🌐 아키텍처 흐름도 (Overall Architecture)

```mermaid
graph TD
    subgraph Framework_Dev [Agent Factory 뼈대 개발]
        Creator[Antigravity / Master Agent] --> SDD[Spec-Driven Design]
        SDD --> Core[Factory Core Engine]
    end

    subgraph Released_Usage [릴리즈 후 사용자 운영]
        User([User Request]) --> Manager[Factory Manager]
        Manager --> Forger[Forging Brain: Himari/Codex]
        Forger --> Agent[Produced Agent: Logi-Mind]
        Agent --> Operational[Operational Brain: 3-Stage Funnel]
    end

    Core -.-> Manager
```

---

## 4. 🛑 핵심 정리
- **뼈대를 만드는 뇌**는 **'시스템의 규칙과 구조'**를 정의하며, 고도의 아키텍처 설계 능력이 요구됩니다.
- **운영하는 뇌**는 **'정해진 규칙 안에서 업무를 수행'**하며, 속도(Flash)와 정확성(Sonnet)의 밸런스를 중시합니다.
- 모든 지능은 `model_utils.py`의 **Autobahn Engine**을 통해 가용한 최신 모델로 자동 라우팅되도록 구현되어 있습니다.
