# Knowledge Item: Autonomous Execution Engine (AEE) Architecture

## Overview

Agent Factory의 자율 실행 엔진(AEE)은 에이전트가 사용자 개입 없이 복잡한 태스크를 완수할 수 있도록 설계된 'Plan-Work-Verify-Rework' 루프 시스템입니다.

## Core Components

### 1. Dual-Mode Execution

- **Approval Mode**: 기본 실행 모드로, 도구 실행 전 사용자 승인 프롬프트를 출력합니다.
- **Ultra Mode (Ultrawork)**: 모든 승인 단계를 `auto_approve=True`로 바이패스하며, `UltraLoop`가 실행 권한을 가집니다.

### 2. UltraLoop Orchestrator

- `core/ultra_loop.py`에서 관리됩니다.
- **Cycle Management**: 최대 5회까지 재시도하며, 실패 시 이전 시도의 실패 사유를 컨텍스트에 포함하여 재계획을 유도합니다.
- **Git Safety Net**: 각 사이클 시작 전 `git commit`을 통해 체크포인트를 생성하고, 실패 시 `git rollback`을 통해 무결성을 유지합니다.

### 3. Hash-Anchored Editing (P2 Stability)

- `skills/hash_edit/skill.py` 및 `core/hashline_editor.py`를 통해 제공됩니다.
- 줄 번호 대신 내용 해시를 사용하여 병렬 작업이나 자동화된 루프 내에서 코드 편집의 안정성을 보장합니다.

## Best Practices

- 자율 실행 시 반드시 가상 환경 또는 샌드박스 설정과 Git 저장소가 활성화되어 있어야 합니다.
- `Lilith`와 같은 상위 에이전트가 `UltraLoop`를 제어할 때 품질 감사(Auditor) 역할을 겸수하도록 정책을 구성하십시오.
