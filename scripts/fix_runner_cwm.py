"""Fix agent_runner.py CWM integration bugs (#4, #5, #7, #12)."""
import os

RUNNER_PATH = os.path.join(os.path.dirname(__file__), "..", "core", "agent_runner.py")

with open(RUNNER_PATH, "r", encoding="utf-8-sig") as f:
    content = f.read()

# ── FIX #5: safe_generate exception handling ─────────────────────────────────
OLD_SAFE_GENERATE = '''        # 429 Quota 재시도 래퍼 (generate_content용)
        def safe_generate(contents, config):
            max_retries = 3
            for i in range(max_retries):
                try:
                    return gemini_client.models.generate_content(
                        model=gemini_model,
                        contents=contents,
                        config=config,
                    )
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower() or "resource exhausted" in str(e).lower():
                        wait = 5 * (i + 1)
                        print(f"[Quota] API Rate Limit (429). {wait}s wait... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise e
            raise Exception("API Rate Limit Exceeded (Quota)")'''

NEW_SAFE_GENERATE = '''        # 429 Quota 재시도 래퍼 (generate_content용)
        # FIX #5: last_error로 예외 컨텍스트 보존
        def safe_generate(contents, config):
            if not contents:
                raise ValueError("Empty contents list passed to generate_content()")  # FIX #12
            max_retries = 3
            last_error: Exception | None = None
            for i in range(max_retries):
                try:
                    return gemini_client.models.generate_content(
                        model=gemini_model,
                        contents=contents,
                        config=config,
                    )
                except Exception as exc:
                    last_error = exc
                    if "429" in str(exc) or "quota" in str(exc).lower() or "resource exhausted" in str(exc).lower():
                        wait = 5 * (i + 1)
                        print(f"[Quota] API Rate Limit (429). {wait}s wait... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise  # 429 외 에러는 즉시 재발생
            raise last_error or Exception("API Rate Limit Exceeded (Quota)")'''

assert OLD_SAFE_GENERATE in content, "safe_generate block not found"
content = content.replace(OLD_SAFE_GENERATE, NEW_SAFE_GENERATE)

# ── FIX #4: tool rejection → record_tool_result (consecutive user role 방지) ──
# 1. Approval rejected
OLD_APPROVAL = '''                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        _cwm.add_user_message("Tool approval rejected. Try alternative approach.", turn)
                                        continue'''

NEW_APPROVAL = '''                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        # FIX #4: function_response로 기록 (연속 user role 방지)
                                        _cwm.record_tool_call(fname, turn)
                                        _cwm.record_tool_result(fname, "[rejected: approval denied]", turn)
                                        continue'''

assert OLD_APPROVAL in content, "approval rejected block not found"
content = content.replace(OLD_APPROVAL, NEW_APPROVAL)

# 2. Hook blocked
OLD_HOOK_BLOCK = '''                                    _cwm.add_user_message(
                                        f"Tool call blocked by runtime hook: {tool_decision.reason or 'blocked_by_hook'}",
                                        turn,
                                    )
                                    continue'''

NEW_HOOK_BLOCK = '''                                    # FIX #4: function_response로 기록 (연속 user role 방지)
                                    _cwm.record_tool_call(fname, turn)
                                    _cwm.record_tool_result(
                                        fname,
                                        f"[blocked: {tool_decision.reason or 'blocked_by_hook'}]",
                                        turn,
                                    )
                                    continue'''

assert OLD_HOOK_BLOCK in content, "hook block not found"
content = content.replace(OLD_HOOK_BLOCK, NEW_HOOK_BLOCK)

# 3. Tool execution error
OLD_TOOL_ERR = '''                            except Exception as e:
                                print(f"[Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                _cwm.add_user_message(f"Tool execution error: {e}", turn)'''

NEW_TOOL_ERR = '''                            except Exception as e:
                                print(f"[Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                # FIX #4: function_response로 기록 (연속 user role 방지)
                                _cwm.record_tool_result(fname, f"[error: {e}]", turn)'''

assert OLD_TOOL_ERR in content, "tool error block not found"
content = content.replace(OLD_TOOL_ERR, NEW_TOOL_ERR)

# 4. Unknown tool
OLD_UNKNOWN = '''                        else:
                            print(f"[Runner] Unknown tool: {fname}", flush=True)
                            _cwm.add_user_message(f"Unknown tool: {fname}", turn)'''

NEW_UNKNOWN = '''                        else:
                            print(f"[Runner] Unknown tool: {fname}", flush=True)
                            # FIX #4: function_response로 기록 (연속 user role 방지)
                            _cwm.record_tool_result(fname, f"[error: unknown tool '{fname}']", turn)'''

assert OLD_UNKNOWN in content, "unknown tool block not found"
content = content.replace(OLD_UNKNOWN, NEW_UNKNOWN)

# ── FIX #7: get_knowledge tool 등록 ──────────────────────────────────────────
OLD_CWM_INIT = '''            # CWM 초기화: Knowledge 스킬 온디맨드 주입, tool evict 활성화
            _cwm = ContextWindowManager(
                model_name=str(gemini_model),
                system_prompt=sys_prompt,
                all_tools=tool_functions,
                knowledge_skills=_knowledge_for_cwm,
                evict_after_turns=3,
                recent_window=4,
            )'''

NEW_CWM_INIT = '''            # FIX #7: get_knowledge tool 등록 (LLM이 Knowledge 스킬 내용 요청 가능)
            _cwm_placeholder: list = []  # 참조용 (아래에서 _cwm 생성 후 채워짐)

            def get_knowledge(skill_id: str) -> str:
                """Load the full content of a knowledge skill by its ID or name.
                Use this when you need detailed procedures from an available knowledge skill."""
                return _cwm.get_knowledge_content(skill_id)

            _tool_functions_with_knowledge = list(tool_functions) + [get_knowledge]

            # CWM 초기화: Knowledge 스킬 온디맨드 주입, tool evict 활성화
            _cwm = ContextWindowManager(
                model_name=str(gemini_model),
                system_prompt=sys_prompt,
                all_tools=_tool_functions_with_knowledge,
                knowledge_skills=_knowledge_for_cwm,
                evict_after_turns=3,
                recent_window=4,
            )
            # get_knowledge는 항상 active 유지 (tracker에 미리 등록)
            _cwm.tool_tracker.record_use("get_knowledge", turn=0)'''

assert OLD_CWM_INIT in content, "CWM init block not found"
content = content.replace(OLD_CWM_INIT, NEW_CWM_INIT)

# tool_functions → _tool_functions_with_knowledge (루프 내 lookup)
OLD_TOOL_LOOKUP = '''                        # Tool lookup (현재 활성 tool + 전체 fallback)
                        tool_func = next((t for t in tool_functions if t.__name__ == fname), None)'''

NEW_TOOL_LOOKUP = '''                        # Tool lookup: knowledge tool 포함 전체 목록에서 검색
                        tool_func = next((t for t in _tool_functions_with_knowledge if t.__name__ == fname), None)'''

assert OLD_TOOL_LOOKUP in content, "tool lookup not found"
content = content.replace(OLD_TOOL_LOOKUP, NEW_TOOL_LOOKUP)

with open(RUNNER_PATH, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: all fixes applied")
