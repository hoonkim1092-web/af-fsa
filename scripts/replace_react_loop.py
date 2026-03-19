"""Replace the Gemini ReAct loop in agent_runner.py with CWM-based implementation."""
import os

RUNNER_PATH = os.path.join(os.path.dirname(__file__), "..", "core", "agent_runner.py")

with open(RUNNER_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Lines 1041-1183 (1-indexed) → 1040-1182 (0-indexed)
START = 1040
END = 1183

new_block = r'''        try:
            # [Gemini SDK + CWM] ContextWindowManager 기반 직접 턴 관리
            from google import genai
            from google.genai import types as genai_types
            from core.context_window_manager import ContextWindowManager

            gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

            # CWM 초기화: Knowledge 스킬 온디맨드 주입, tool evict 활성화
            _cwm = ContextWindowManager(
                model_name=str(gemini_model),
                system_prompt=sys_prompt,
                all_tools=tool_functions,
                knowledge_skills=_knowledge_for_cwm,
                evict_after_turns=3,
                recent_window=4,
            )
        except Exception as e:
            import traceback
            print(f"[Runner] SDK/CWM Init Error: {str(e)}")
            traceback.print_exc()
            result = {"ok": False, "reason": "sdk_init_failed", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "sdk_init", "message": str(e)})
            _flush_trace(result)
            return result

        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})

        # 429 Quota 재시도 래퍼 (generate_content용)
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
            raise Exception("API Rate Limit Exceeded (Quota)")

        try:
            # 초기 사용자 메시지 등록
            _cwm.add_user_message(f"Task: {task_input}", turn=0)

            # CWM 기반 ReAct Loop (직접 턴 관리)
            for turn in range(10):
                # 매 턴 컨텍스트 최적화
                gen_config = _cwm.get_generate_config(turn, genai_types=genai_types)
                response = safe_generate(
                    contents=gen_config["contents"],
                    config=genai_types.GenerateContentConfig(
                        system_instruction=gen_config["system_instruction"],
                        tools=gen_config["tools"],
                    ),
                )

                if not response.parts:
                    if not response.candidates:
                        pass
                    break

                # 모델 응답 히스토리에 기록
                _cwm.record_model_response(response, turn)

                has_action = False
                for part in response.parts:
                    # 1. Output Text
                    if hasattr(part, "text") and part.text:
                        print(f"{part.text}", flush=True)
                        _append_trace("assistant", {"text": str(part.text)})

                    # 2. Function Call
                    if hasattr(part, "function_call") and part.function_call:
                        has_action = True
                        fc = part.function_call
                        fname = fc.name
                        fargs = dict(fc.args)
                        print(f"[Tool] {fname}({fargs})", flush=True)
                        _append_trace("tool_call", {"name": str(fname), "args": fargs})

                        # Tool lookup (현재 활성 tool + 전체 fallback)
                        tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                        if tool_func:
                            try:
                                skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                                if self._requires_tool_approval(policy, skill_id, fname):
                                    if not self._ask_tool_approval(fname, skill_id):
                                        print(f"[Policy] Approval rejected: {fname}", flush=True)
                                        approval_rejects += 1
                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        _cwm.add_user_message("Tool approval rejected. Try alternative approach.", turn)
                                        continue
                                tool_decision = bus.run_pre_tool_call(agent_state, fname, fargs)
                                if not tool_decision.allowed:
                                    approval_rejects += 1
                                    _append_trace(
                                        "tool_reject",
                                        {
                                            "name": str(fname),
                                            "skill_id": str(skill_id),
                                            "reason": str(tool_decision.reason or "blocked_by_hook"),
                                        },
                                    )
                                    _cwm.add_user_message(
                                        f"Tool call blocked by runtime hook: {tool_decision.reason or 'blocked_by_hook'}",
                                        turn,
                                    )
                                    continue

                                # Execute
                                if skill_id:
                                    used_skill_ids_runtime.add(skill_id)
                                res_obj = tool_func(**dict(tool_decision.tool_args or fargs))
                                res_obj = bus.run_post_tool_call(agent_state, fname, res_obj)

                                print(f"  -> Result: {str(res_obj)[:100]}...", flush=True)
                                _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})

                                # CWM에 tool 호출 + 결과 기록
                                _cwm.record_tool_call(fname, turn)
                                _cwm.record_tool_result(fname, res_obj, turn)
                            except Exception as e:
                                print(f"[Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                _cwm.add_user_message(f"Tool execution error: {e}", turn)
                        else:
                            print(f"[Runner] Unknown tool: {fname}", flush=True)
                            _cwm.add_user_message(f"Unknown tool: {fname}", turn)

                if not has_action:
                    break

            # CWM 통계 로깅
            _cwm_stats = _cwm.get_stats()
            _safe_print(f"[CWM] history={_cwm_stats['history']['total_tokens']}tok "
                        f"compressed={_cwm_stats['history']['compressed_entries']} "
                        f"saved={_cwm_stats['history']['saved_tokens']}tok")
            print("Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            # Phase 3: post-execute hook
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result

        except Exception as e:
            import traceback
            print(f"[Runner] Execution error: {e}")
            traceback.print_exc()
            print("Execution failed. Check logs for details.")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            # Phase 3: post-execute hook
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result
'''

new_lines = lines[:START] + [new_block + "\n"] + lines[END:]

with open(RUNNER_PATH, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print(f"OK: replaced lines {START+1}-{END} with CWM-based code")
print(f"Old: {END - START} lines -> New: {new_block.count(chr(10))+1} lines")
