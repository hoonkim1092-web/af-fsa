# AI Agent Factory

프로젝트 단위로 에이전트를 실행/진화시키는 시스템입니다.

## 사용 방법
`--project`는 필수입니다.

### 단일 실행
```bash
python run_factory_cli.py --project logistics_v1 --role "Backend Architect" --task "주문 API 설계"
```

### 워크플로우 실행
```bash
python run_factory_cli.py --project logistics_v1 --workflow workflows/two_week_webapp_delivery.yaml --agents "Lilith,Himari" --task "2주 데모 플랜 실행"
```

## 구조
- `run_factory_cli.py`: 프로젝트 단위 실행 진입점
- `agent_launcher.py`: 에이전트/스킬/워크플로우 오케스트레이션
- `projects/<project_id>/`: 프로젝트별 `agents/`, `data/`, `artifacts/`, `runs/`, `policies.yaml`, `skill-lock.yaml`, `context_schema.yaml`, `dashboard.json`
- `skills/`: 전역 재사용 스킬 저장소

## 회사/집 PC 동기화 (프로젝트/에이전트별)
Git(권장) 또는 Supabase로 스냅샷을 `push/pull` 하여 회사/집 PC를 이어서 사용할 수 있습니다.
프로젝트 전체 스코프와 에이전트 스코프를 각각 분리해 동기화할 수 있습니다.

### 가장 쉬운 명령 (추천)
루트에서 아래처럼만 실행하면 됩니다.

```bat
start_sync
:: 작업
end_sync
```

내부 동작:
- `start_sync` -> `sync down git all` + `sync down db all`
- `end_sync` -> `sync up db all` + `sync up git all`

단일 프로젝트:
- `start_sync agent-factory`
- `end_sync agent-factory`

단일 프로젝트 + 에이전트:
- `start_sync agent-factory lilith`
- `end_sync agent-factory lilith`

직접 명령이 필요하면:

```bat
sync up git all
sync down git all
sync up db all
sync down db all
```

의미:
- `up` = push
- `down` = pull
- `git` = Git 원격 저장소로 동기화
- `db` = Supabase DB로 동기화
- `all` = `logi-mind-v22` + `agent-factory`

특정 프로젝트/에이전트:
```bat
sync up git agent-factory
sync down db logi-mind-v22
sync up git agent-factory lilith
```

### Git 기반 동기화 (권장)
원격 Git 저장소를 "DB처럼" 사용해서 회사/집 PC를 동기화합니다.

프로젝트 전체 동기화:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode push -Project logi-mind-v22
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode pull -Project logi-mind-v22
```

에이전트별 동기화:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode push -Project logi-mind-v22 -Agent lilith
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode pull -Project logi-mind-v22 -Agent lilith
```

옵션:
- `-Branch main` : 특정 브랜치로 pull/push
- `-Message "sync: logi-mind-v22"` : 커밋 메시지 지정
- `-Projects "logi-mind-v22,agent-factory"` : 여러 프로젝트 일괄 동기화

요청하신 두 프로젝트 일괄 예시:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode push -Projects "logi-mind-v22,agent-factory"
powershell -ExecutionPolicy Bypass -File scripts/project_context_git_sync.ps1 -Mode pull -Projects "logi-mind-v22,agent-factory"
```

1. Supabase SQL Editor에서 `artifacts/context_sync_schema.sql` 실행
2. `.env`에 `SUPABASE_URL`, `SUPABASE_KEY` 설정
3. 회사 PC에서 업로드 (프로젝트 전체):
```bash
python scripts/project_context_sync.py --project logi_mind_v22 --mode push
```
4. 집 PC에서 다운로드 (프로젝트 전체):
```bash
python scripts/project_context_sync.py --project logi_mind_v22 --mode pull
```
5. 에이전트별 업로드/다운로드:
```bash
python scripts/project_context_sync.py --project logi_mind_v22 --agent lilith --mode push
python scripts/project_context_sync.py --project logi_mind_v22 --agent lilith --mode pull
```

여러 프로젝트 일괄(DB) 동기화:
```bash
python scripts/project_context_sync.py --projects "logi-mind-v22,agent-factory" --mode push
python scripts/project_context_sync.py --projects "logi-mind-v22,agent-factory" --mode pull
```

프로젝트 이름 호환:
- `logi-mind-v22`, `logi_mind_v22`, `logi_mind_v22+` 형태를 같은 프로젝트로 매칭해서 동기화합니다.

기본 포함 항목:
- `projects/<project_id>/data/memory/**/*.json` (에이전트 스코프는 해당 에이전트 메모리 우선)
- `projects/<project_id>/runs/*/chat_trace.json` (에이전트 스코프는 최신 100개, 프로젝트 스코프는 전체)
- `projects/<project_id>/runs/*/state.json`
- `dashboard.json`, `policies.yaml`, `settings.yaml`, `skill-lock.yaml`, `workflow.yaml`, `context_schema.yaml`
- 프로젝트 스코프(`--agent` 없음)에서는 `agents/*`, `artifacts/*`, `runs/*`의 주요 텍스트 산출물도 함께 동기화

참고:
- `core_memory`는 기본 저장 위치가 `data/memory/<agent_id>/...`로 분리되어 에이전트별 메모리가 섞이지 않습니다.
