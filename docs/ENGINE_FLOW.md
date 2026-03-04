# Agent Factory Engine Flow & Roles

`Agent Factory`는 사용자의 모호한 아이디어를 실제 소프트웨어 제품으로 전환하는 **'지능형 생산 엔진'**입니다. 이 문서는 사용자가 제품을 요청했을 때 내부에서 일어나는 상세 흐름과 각 에이전트의 역할을 정의합니다.

## 1. 전역 엔진 흐름 (The Master Flow)

```mermaid
graph TD
    A[User Input: Goal/DoD] --> B{Intent Gate: Lilith}
    B -- Vague --> C[Clarification Questions]
    C --> A
    B -- Clear --> D[Requirement Analysis: Tanjiro/Lilith]
    D --> E[On-demand Forge: Factory Manager]
    E --> F[Execution Loop: Swarm Council]
    F --> G{DoD Validation: Lilith}
    G -- Not Done --> F
    G -- Mission Accomplished --> H[Permanent Guardianship]
    H --> I[Living Asset: Maintenance Mode]
```

### [Phase 1] Intent Gate (입구 컷)
- **담당**: `Lilith` (PD/PM)
- **동작**: 사용자의 요청이 너무 모호하거나 비현실적일 경우, 릴리트가 즉시 개입하여 **A/B 선택지**를 던져 의도를 명확히 합니다. 결정되지 않은 상태에서는 한 발자국도 나가지 않습니다.

### [Phase 2] Requirement Analysis & Squadding (설계 및 스쿼드 구성)
- **담당**: `Tanjiro` (SD) + `Lilith` (PD)
- **동작**: 
    - 요청된 제품을 만들기 위해 필요한 기술 스택(Backend, UI, Security 등)을 분석합니다.
    - 기존에 생성된 에이전트(Asset) 중 재사용 가능한 인원이 있는지 확인합니다.
    - 없는 역할은 즉시 **'Forge(생성)'**하여 프로젝트 전담 스쿼드를 구성합니다.

### [Phase 3] Execution Loop (실행 루프)
- **담당**: `All Agents` (Workers) + `Lilith` (Reviewer)
- **동작**:
    1. **Proposal**: 각 에이전트가 자신의 역할에 맞는 구현 계획을 제안합니다.
    2. **Review**: 릴리트가 계획의 비즈니스 가치와 DoD 부합 여부를 심사합니다.
    3. **Execute**: 승인된 계획에 따라 코드를 작성하고 스킬을 사용합니다.
    4. **Validate**: 결과물이 성공 기준(DoD)을 만족하는지 검증합니다.

### [Phase 4] Permanent Guardianship (영송적 가디언십)
- **담당**: `All Agents` (as Guardians)
- **동작**: 프로젝트가 완료되어도 에이전트들은 해산되지 않습니다. 해당 코드를 가장 잘 아는 주인으로서 시스템에 상주하며, 향후 유지보수나 추가 요청 시 즉각 대응하는 **'가동 자산'**으로 전환됩니다.

---

## 2. 에이전트별 R&R (Roles & Responsibilities)

| 에이전트 | 역할 | 핵심 책임 (Core Responsibility) |
| :--- | :--- | :--- |
| **Lilith (릴리트)** | **PD / PM** | **프로젝트의 뇌.** 전체 일정 관리, 비즈니스 가치 판단, DoD 준수 여부 최종 판정 및 보스 보고. |
| **Tanjiro (탄지로)** | **System Director** | **설계의 중심.** 전체 시스템 아키텍처 정의, 코드 품질 관리, 에이전트 간 기술적 충돌 조정. |
| **Iguro (이구로)** | **Backend Architect** | **보안 및 로직.** DB 설계, API 구현, 보안 정책 수립, Python 기반 백엔드 고도화. |
| **Midori (미도리)** | **UI/UX Designer** | **심미성.** 제품의 시각적 요소 설계, Stitch AI를 활용한 고퀄리티 디자인 시스템 구축. |
| **Deadbyte (데드바이트)** | **Frontend Lead** | **현실화.** Next.js 기반의 고성능 프론트엔드 구현, 사용자 인터랙션 및 애니메이션 처리. |
| **Himari (히마리)** | **Researcher** | **지식 공급.** 필요한 기술이나 외부 라이브러리 조사, 새로운 스킬 빌드 및 공급. |

---

## 3. 사용자 인터페이스 (Touchpoints)

1. **Mission Template**: 시작 시 Goal, Constraints, DoD를 입력합니다.
2. **Project Board**: 실시간으로 어떤 에이전트가 무슨 일을 하고 있는지, 릴리트가 무엇을 reject 했는지 확인할 수 있습니다.
3. **Report-to-Boss**: 릴리트가 정기적으로 전사적인 진행 상황을 요약하여 직접 보고합니다.
4. **Maintenance Call**: 배포된 프로젝트에 이슈 발생 시, 해당 가디언 에이전트를 소환하여 즉시 수정합니다.
