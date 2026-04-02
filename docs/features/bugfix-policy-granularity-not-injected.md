# Bug Fix: policy.yaml task granularity 및 모듈 태스크 수 제한이 LLM 프롬프트에 주입되지 않는 문제

## 상태
- status: identified
- 발견일: 2026-04-02
- 담당 파일: `core/bootstrap_roles.py`

## 현상

사용자가 "~ 만들어줘" 요청 시 `ProjectPlanningDirector.plan()`이 LLM에 전달하는 플래닝 프롬프트에
`policy.yaml`의 `tasks.granularity` 및 `modules.max/min_tasks_per_module` 값이 포함되지 않음.

## 원인

`core/bootstrap_roles.py`의 `_build_policy_rules()` 함수(39~53행)에서
`tasks` 섹션은 `naming_convention`만 렌더링하고 `granularity` 필드는 무시한다.
`modules` 섹션은 아예 읽지 않는다.

```python
# 현재 코드 — granularity, max/min_tasks_per_module 누락
tasks = policy.get("tasks") or {}
if tasks.get("naming_convention"):
    lines.append(f"- required_skills, role ids, ... must be English {tasks['naming_convention']}.")
```

## 영향 범위

- `policy.yaml`에 명시된 `tasks.granularity: "small"` 무효화
- `modules.max_tasks_per_module: 8` / `min_tasks_per_module: 2` 무효화
- LLM이 태스크를 얼마나 잘게 쪼갤지 제어 불가 — LLM 해석에 의존

`constraints` 문구("Every module should have small tasks, not one giant task.")는
프롬프트에 정상 주입되어 일부 효과는 있음.

## 수정 방법

`_build_policy_rules()` 내에 아래 두 블록 추가:

```python
# tasks.granularity 주입
if tasks.get("granularity"):
    lines.append(
        f"- Task granularity must be '{tasks['granularity']}' — "
        "each task should be independently completable in one focused pass."
    )

# modules 태스크 수 제한 주입
modules_policy = policy.get("modules") or {}
min_t = modules_policy.get("min_tasks_per_module")
max_t = modules_policy.get("max_tasks_per_module")
if min_t and max_t:
    lines.append(f"- Each module must have {min_t} to {max_t} tasks.")
elif max_t:
    lines.append(f"- Each module must have at most {max_t} tasks.")
```

## 체크리스트

- [ ] `core/bootstrap_roles.py` `_build_policy_rules()` 수정
- [ ] `policy.yaml` 값 변경 없음 (기존 값 그대로 활용)
- [ ] 단위 테스트: `_build_policy_rules()` 출력에 granularity/task count 문구 포함 확인
- [ ] 배포 빌드 재빌드 및 `dist/af/_internal/policy.yaml` 동기화 확인
