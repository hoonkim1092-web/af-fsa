import os
import json
from google import genai
from core.utils import safe_id, now_iso, write_text, write_yaml, strip_code_fences, sha256_text, quick_guard, run_isolated
from core.config_paths import SKILLS_DIR, RUNS_DIR

class SandboxedBuilder:
    """Builds agent skills in a sandboxed environment."""
    def __init__(self, mr):
        self.mr = mr

    def build_skill(
        self,
        agent: dict,
        skill_name: str,
        reqs: dict,
        run_id: str,
        evidence_pack: dict,
    ) -> tuple[bool, str | None, dict]:
        # [Constitution: Planning-First] 승인된 evidence_pack 없이 코드 생성 금지
        if not evidence_pack or not isinstance(evidence_pack, dict):
            print(f"[Planning-First Gate] BLOCKED: evidence_pack empty -- skill '{skill_name}' rejected")
            return False, None, {"id": skill_name, "status": "blocked", "reason": "planning_first_violated"}

        # Constants from core.utils
        from core.utils import MAX_ITERATIONS, TEST_TIMEOUT_SEC

        skill_id = safe_id(skill_name)
        skill_dir = os.path.join(SKILLS_DIR, skill_id)
        os.makedirs(skill_dir, exist_ok=True)
        code_path = os.path.join(skill_dir, "skill.py")
        meta_path = os.path.join(skill_dir, "meta.yaml")

        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)

        evidence_targets = (evidence_pack or {}).get("targets", {}) if isinstance(evidence_pack, dict) else {}
        target_evidence = evidence_targets.get(skill_id)
        if not target_evidence:
            fail_meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "disabled",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "last_test_ok": False,
                "last_test_detail": {"ok": False, "reason": "missing_evidence_pack"},
            }
            write_yaml(meta_path, fail_meta)
            return False, None, fail_meta

        # [New SDK] Client 기반 스킬 빌더 (Triad: builder 단계 = Claude/GPT)
        _api_key = os.getenv("GOOGLE_API_KEY")
        if not _api_key:
            print(f"[Builder] WARN: GOOGLE_API_KEY missing -- cannot build '{skill_name}'")
            fail_meta = {
                "id": skill_id, "name": skill_name, "status": "disabled",
                "version": "0.1.0", "capabilities": [skill_name],
                "created_at": now_iso(), "updated_at": now_iso(),
                "last_test_ok": False,
                "last_test_detail": {"ok": False, "reason": "no_api_key"},
            }
            write_yaml(meta_path, fail_meta)
            return False, None, fail_meta

        _client = genai.Client(api_key=_api_key)
        _model_name = self.mr.pick("builder")
        base_prompt = f"""
당신은 파이썬 스킬 모듈을 작성한다.
Skill: "{skill_name}"
AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
Constraints: {reqs.get("constraints")}
Evidence(JSON): {json.dumps(target_evidence, ensure_ascii=False)}

필수:
- 함수 3개: propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict(반드시 ok 키 포함)
- 데이터 입력: ctx["data_dir"] 아래 파일을 읽는다.
- 산출물 저장: ctx["artifacts_dir"] 아래로 저장해야 하지만, 가능하면 dict로 반환.
금지:
- os/sys/subprocess/shutil/importlib/pathlib/glob/ctypes 등 사용 금지
- eval/exec/__import__/compile/input 금지
출력:
- 마크다운 없이 파이썬 코드만
"""

        last = {"ok": False, "reason": "not_started"}
        feedback_history = []  # [BUG-2 FIX] 실패 피드백 누적

        for i in range(MAX_ITERATIONS):
            # [BUG-2 FIX] 이전 실패 사유를 프롬프트에 추가하여 LLM 자가 교정 유도
            if feedback_history:
                feedback_section = "\n\n[이전 시도 실패 이력 — 반드시 아래 오류를 회피하세요]\n"
                for idx, fb in enumerate(feedback_history, 1):
                    feedback_section += f"시도 {idx}: {fb}\n"
                current_prompt = base_prompt + feedback_section
            else:
                current_prompt = base_prompt

            print(f"[Builder] Building skill '{skill_id}' attempt {i+1}/{MAX_ITERATIONS}...")
            try:
                res = _client.models.generate_content(model=_model_name, contents=current_prompt)
            except Exception as e:
                print(f"[Builder] WARN: LLM call failed: {e}")
                last = {"ok": False, "reason": f"llm_error:{type(e).__name__}", "detail": str(e)[:300]}
                feedback_history.append(f"LLM 호출 오류: {type(e).__name__}")
                continue

            code = strip_code_fences(res.text if res else "")

            ok, vios = quick_guard(code)
            if not ok:
                last = {"ok": False, "reason": "guard_block", "violations": vios}
                feedback_history.append(f"보안 가드 차단 — 금지 패턴 감지: {', '.join(vios[:3])}")
                continue

            write_text(code_path, code)

            t_ok, t_json, t_err = run_isolated(code_path, timeout_sec=TEST_TIMEOUT_SEC)
            last = {"test_ok": t_ok, "test_json": t_json, "stderr": (t_err or "")[:500]}

            if not t_ok:
                err_summary = (t_err or "unknown error")[:200]
                feedback_history.append(f"테스트 실패 — stderr: {err_summary}")

            if t_ok:
                meta = {
                    "id": skill_id,
                    "name": skill_name,
                    "status": "active",
                    "version": "0.1.0",
                    "capabilities": [skill_name],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "code_hash": sha256_text(code),
                    "last_test_ok": True,
                    "last_test_detail": t_json,
                }
                write_yaml(meta_path, meta)
                write_text(os.path.join(run_dir, f"{skill_id}_skill.py"), code)
                write_yaml(os.path.join(run_dir, f"{skill_id}_meta.yaml"), meta)
                return True, code_path, meta

        fail_meta = {
            "id": skill_id,
            "name": skill_name,
            "status": "disabled",
            "version": "0.1.0",
            "capabilities": [skill_name],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "last_test_ok": False,
            "last_test_detail": last,
        }
        write_yaml(meta_path, fail_meta)
        return False, None, fail_meta
