---
name: af-blueprint-sync
description: "core/*.py 변경 후 Master_Blueprint.md 동기화 규칙. 변경 파일 → §섹션 매핑, §12 이력 업데이트 포맷."
---

<overview>
코드 수정과 Master_Blueprint.md 업데이트는 반드시 같은 커밋에서 이루어져야 한다.
이 스킬은 변경된 파일이 Blueprint 어느 섹션에 해당하는지 매핑하고, 업데이트 포맷을 제공한다.
</overview>

<when-to-use>
- `core/*.py` 파일을 수정한 후 커밋 전
- 새 파일을 생성한 후
- 버전 bump 후
</when-to-use>

<mapping>

## 파일 → Blueprint §섹션 매핑

| 파일 패턴 | §섹션 |
|----------|-------|
| 새 `core/*.py` 생성 | §0 빠른 참조 테이블 |
| `agent_runner.py`, `dynamic_orchestrator.py`, `model_router.py` | §3.1 Runtime Engine |
| `control/*.py` | §3.2 Control Plane |
| `providers/*.py` | §3.3 Provider Layer |
| `hooks/*.py` | §3.4 Hook System |
| `memory_system/*.py` | §3.5 Memory System |
| `ise_*.py` | §4 자가진화 루프 |
| `message_broker.py`, `project_mailbox.py` | §5 에이전트 간 통신 |
| `model_router.py`, `model_utils.py` | §6 모델 라우팅 |
| `security_guard.py`, `destructive_guard.py` | §7 안전장치 |
| `af.spec`, `build_exe.py`, `version.py` | §8 빌드 & 배포 |
| `config_paths.py`, `policy.yaml` | §9 설정 레퍼런스 |
| `skill_*.py`, `external_skill_*.py` | §3.6 Skill System |
| `cross_verification.py`, `consensus_engine.py` | §3.7 Cross Verification |

## §12 변경 이력 포맷

```markdown
### YYYY-MM-DD — 변경 요약 (1줄)

- **변경 파일**: `core/xxx.py`
- **변경 유형**: 신규/수정/삭제
- **영향 섹션**: §N
- **상세**: 변경 내용 1~2줄 설명
```

## 커밋 규칙

1. 코드 수정 + Blueprint 업데이트 = **같은 커밋**
2. `git add core/xxx.py Master_Blueprint.md` 후 커밋
3. `.githooks/pre-commit`이 Blueprint 스테이징 여부를 자동 체크
4. Blueprint 미스테이지 → 커밋 차단

</mapping>
