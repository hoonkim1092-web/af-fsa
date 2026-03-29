# Agent Factory 수정 계획서 (v2) — 사용자 지정 순서

> 🐍 **"신용하지 않아. 코드는 거짓말을 하지 않으니까."**

---

## 수정 순서 총괄

| Step | 주제 | 위험도 | 핵심 작업 |
|:----:|:-----|:------:|:---------|
| **1** | security_guard 통합 | 🔴 | 중복 코드 ~210줄 삭제 + 네트워크 차단 보완 |
| **2** | generate_json() 수정 | 🟣 | JSON 파싱 안전성 강화 |
| **3** | orchestrator resume 버그 | 🔴 | `[/]` 무시, interrupted 누락, placeholder 무한루프 |
| **4** | minesweeper ?? | ?? | Context Isolation ?? ???? ?? |
| **5** | wildcard import / safe_id 정리 | 🟠 | `import *` 제거, safe_id 3중 정의 통합 |
| **6** | agent_runner.py 분리 + 설정 정리 | 🟠 | ModelRouter 분리, config side-effect, 프로젝트 위생 |

---

## Step 1: security_guard 통합

> `agent_runner.py`에서 보안 코드 중복 제거, `security_guard.py`로 단일화

### 1-A. 중복 삭제 대상 (agent_runner.py)

| 삭제 대상 | 라인 | 줄 수 |
|:---------|:----:|:----:|
| `BANNED_IMPORT_TOPS` / `BANNED_CALLS` | L110~118 | 9 |
| `quick_guard()` | L120~148 | 29 |
| `build_child_env()` | L150~156 | 7 |
| `run_isolated()` + 주석 | L157~309 | 153 |
| **합계** | | **~198줄** |

```diff
# core/agent_runner.py — 삭제할 블록

-# =============================================================================
-# 2) Quick Guard (AST) ...
-# =============================================================================
-BANNED_IMPORT_TOPS = { ... }
-BANNED_CALLS = { ... }
-def quick_guard(code: str) -> tuple[bool, list[str]]: ...
-def build_child_env() -> dict: ...

-# =============================================================================
-# 3) Isolated Run (Lite) ...
-# =============================================================================
-def run_isolated(...) -> tuple[bool, dict, str]: ...
```

### 1-B. security_guard.py 네트워크 차단 보완

```diff
# core/security_guard.py — run_isolated() 내부 f-string

+    _orig_connect = _socket.socket.connect
     def _blocked_connect(self, address):
         _audit(f"BLOCKED socket.connect address={{address}}")
         raise PermissionError(f"Network access blocked: {{address}}")
     _socket.socket.connect = _blocked_connect
+
+    _orig_create_connection = _socket.create_connection
+    def _blocked_create_connection(address, *args, **kwargs):
+        _audit(f"BLOCKED socket.create_connection address={{address}}")
+        raise PermissionError(f"Network access blocked: {{address}}")
+    _socket.create_connection = _blocked_create_connection
```

### 검증

```bash
python -c "from core.agent_runner import AgentRunner; print('OK')"
python -m pytest tests/test_runner_contracts.py tests/test_destructive_guard.py -v
```

---

## Step 2: generate_json() 수정

> `llm_engine.py`의 JSON 파싱에서 비-JSON 코드블록 오파싱 방지

### 수정 대상: `core/llm_engine.py` L184~202

```diff
     def generate_json(self, prompt: str) -> dict:
         text = self._execute_with_retry(prompt)
         if not text:
             return {}
         try:
             if "```json" in text:
                 json_block = text.split("```json")[1].split("```")[0].strip()
-            elif "```" in text:
-                json_block = text.split("```")[1].split("```")[0].strip()
             else:
-                json_block = text.strip()
+                json_block = text.strip()
+                import re
+                m = re.search(r'\{[\s\S]*\}', json_block)
+                if m:
+                    json_block = m.group(0)
             return json.loads(json_block)
         except json.JSONDecodeError as e:
             print(f"[LLMEngine Error] JSON parse failed: {e}")
             return {}
```

### 검증

```bash
python -c "
import json, re
# 파싱 로직 단위 테스트
cases = [
    ('{\"ok\": true}', True),
    ('Here is result: {\"ok\": true} done', True),
    ('\`\`\`python\nprint(1)\n\`\`\`', False),
]
for text, expect_ok in cases:
    if '\`\`\`json' in text:
        block = text.split('\`\`\`json')[1].split('\`\`\`')[0].strip()
    else:
        block = text.strip()
        m = re.search(r'\{[\s\S]*\}', block)
        if m: block = m.group(0)
    try:
        r = json.loads(block)
        assert expect_ok, f'Should have failed: {text}'
    except: 
        assert not expect_ok, f'Should have passed: {text}'
print('All JSON parse tests passed')
"
```

---

## Step 3: orchestrator resume 버그 수정

> `dynamic_orchestrator.py`의 3건의 resume 관련 버그 수정

### 버그 3-A: `_open_todo_items()`가 `- [/]` (진행 중) 항목 무시

**현상**: `- [/] 작업 중...` 형태의 진행 중 항목이 `- ` 일반 항목으로 포착되어 **이미 실행 중인 작업이 다시 배정됨**.

```diff
# core/dynamic_orchestrator.py — _open_todo_items()

     for raw in handle:
         line = str(raw or "").strip()
         if line.startswith("- [ ] "):
             items.append(line[6:].strip())
-        elif line.startswith("- ") and not line.startswith("- [x] "):
+        elif line.startswith("- [/] "):
+            pass  # 진행 중 항목은 건너뛰기
+        elif line.startswith("- ") and not line.startswith("- [x] ") and not line.startswith("- [/] "):
             items.append(line[2:].strip())
```

### 버그 3-B: `state_board` 초기화에 `interrupted_subtasks` 누락

**현상**: `_completed_subtask_keys()`가 `interrupted_subtasks` 버킷을 조회하지만, `__init__`에서 해당 키가 초기화되지 않음.

```diff
# core/dynamic_orchestrator.py — __init__()

         self.state_board: Dict[str, Any] = {
             "completed_subtasks": [],
             "failed_subtasks": [],
+            "interrupted_subtasks": [],
             "agents_status": {},
             "current_status": "",
         }
```

### 버그 3-C: placeholder 태스크의 무한 재시도

**현상**: LLM 응답이 비어있을 때 `__placeholder__` 태스크를 반환하여 cycle이 소진됨.

```diff
# core/dynamic_orchestrator.py — _lilith_decide_next()

             if not filtered_tasks:
                 fallback_tasks = self._fallback_next_tasks(available_roles, target_workspace)
                 if fallback_tasks:
                     return fallback_tasks
                 if not data:
                     print_agent_msg("Lilith", "LLM response empty. Retrying next cycle...", "")
-                    return [{"assigned_role": "__placeholder__", "subtask_instruction": "retry"}]
+                    return []  # 빈 배열 → _orchestration_loop에서 idle 대기
                 return []
```

### 검증

```bash
python -m pytest tests/test_dynamic_orchestrator_workspace_scope.py -v
python -c "
from core.dynamic_orchestrator import DynamicOrchestrator
from core.agent_runner import ModelRouter
do = DynamicOrchestrator(ModelRouter())
assert 'interrupted_subtasks' in do.state_board
print('orchestrator init OK')
"
```

---

## Step 4: minesweeper 제거

> 팩토리 코어 엔진에서 특정 산출물 이름 하드코딩 제거

### 수정 대상: `core/llm_engine.py` L84~89

```diff
 def _flash_auto_upgrade_enabled() -> bool:
-    project_id = str(os.getenv("AGENT_PROJECT_ID", "") or "").strip().lower()
-    if project_id:
-        return project_id == "minesweeper"
-    project_root = str(os.getenv("AGENT_PROJECT_ROOT", "") or "").strip().replace("\\", "/").rstrip("/")
-    return project_root.endswith("/projects/minesweeper")
+    """환경변수 AGENT_FLASH_AUTO_UPGRADE=1로 제어."""
+    env_val = str(os.getenv("AGENT_FLASH_AUTO_UPGRADE", "") or "").strip().lower()
+    return env_val in ("1", "true", "yes", "on")
```

### 추가: `run_factory_cli.py` Context Isolation

```diff
-    parser = argparse.ArgumentParser(description="Logi-Mind Agent Factory CLI")
+    parser = argparse.ArgumentParser(description="Agent Factory CLI")
```

### 검증

```bash
python -m pytest tests/test_llm_engine_auto_upgrade_scope.py -v
```

---

## Step 5: wildcard import / safe_id 정리

### 5-A. `tool_runtime.py`의 중복 `safe_id` 제거

```diff
# core/tool_runtime.py
+from core.utils import safe_id
-def safe_id(text: str) -> str: ...
```

### 5-B. `agent_runner.py`의 `import *` → 명시적 import

```diff
-from core.config_paths import *
-from core.utils import *
-from core.utils import _safe_write_json
+from core.config_paths import (
+    BASE_DIR, PROJECT_ROOT, PROJECT_ID,
+    SKILLS_DIR, PROJECT_SKILLS_DIR, RUNS_DIR, DATA_DIR, ARTIFACTS_DIR,
+    GOOGLE_API_KEY, OPENAI_API_KEY,
+)
+from core.utils import (
+    safe_id, now_iso, read_yaml, write_yaml, read_core_memory,
+    get_random_signature, print_agent_msg, is_codex_model, is_claude_model,
+    run_skill_safely, validate_context_with_schema, resolve_skill_paths,
+    _safe_write_json,
+)
```

### 5-C. Deprecated 메서드 삭제

```diff
-    def _make_tool_wrapper(self, func, ctx: dict):
-        # Deprecated: Extracted to core.tool_runtime.ToolRuntimeWrapper
-        return func
-    def _build_tool_functions(self, modules: list, ctx: dict, policy: dict) -> list:
-        # Deprecated: Extracted to core.tool_runtime.ToolRuntimeWrapper
-        return []
```

### 검증

```bash
python -m pytest tests/test_runner_contracts.py -v
```

---

## Step 6: agent_runner.py 분리 + 설정 정리

### 6-A. ModelRouter → `core/model_router.py` 분리 [NEW]

`agent_runner.py`의 `ModelRouter` 클래스(~43줄)를 새 파일로 이동.

### 6-B. `config_paths.py` Side Effect → `ensure_directories()` 함수화

### 6-C. Mojibake 수정 + 하드코딩 모델 동적 전환

### 6-D. 프로젝트 위생
- 루트 테스트 8개 → `tests/` 이동
- `.gitignore` 업데이트 (임시/디버그 파일 추가)

### 검증

```bash
python -m pytest tests/ -v --tb=short
python -c "from core.model_router import ModelRouter; print('OK')"
```

---

## 수정 규모 요약

| Step | 수정 파일 수 | 순 변화 |
|:----:|:----------:|:------:|
| 1 | 2 | **-198줄** |
| 2 | 1 | -2줄 |
| 3 | 1 | +4줄 |
| 4 | 2 | -3줄 |
| 5 | 2 | -12줄 |
| 6 | 4 (1 NEW) | -5줄 |
| **합계** | **12** | **~-216줄** |
