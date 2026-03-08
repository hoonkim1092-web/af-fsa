import os
import json
import subprocess
import sys
from google import genai
from model_utils import normalize_model_name, generate_content_with_self_heal
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, get_random_signature,
    print_agent_msg, safe_json_load, resolve_skill_paths, resolve_existing_path
)
from core.config_paths import AGENTS_DIR, REGISTRY_PATH
from core.research_engine import query_notebooklm

class HimariResearchAgent:
    """Specialized research agent utilizing local and external knowledge (NotebookLM)."""
    def __init__(self, mr):
        self.mr = mr

    def _himari_identity(self) -> dict:
        path = os.path.join(AGENTS_DIR, "himari.yaml")
        data = read_yaml(path) if os.path.exists(path) else {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("name", "Himari")
        data.setdefault("role", "Project Research Director")
        data.setdefault("signature_lines", ["근거를 먼저 고정합니다."])
        return data

    def _approve_notebooklm_insight(self, insight: str) -> bool:
        preview = (insight or "").strip()
        if not preview:
            return False
        print("\n[Himari][디버그] NotebookLM 응답 미리보기")
        print("-" * 50)
        print(preview[:1200])
        print("-" * 50)
        try:
            ans = input("[Himari] 위 응답을 리서치 근거로 반영할까요? (yes/no): ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            return False

    def _registry_skill_index(self) -> dict:
        reg = read_yaml(REGISTRY_PATH)
        items = reg.get("skills", {}) if isinstance(reg, dict) else {}
        idx: dict = {}
        for sid, meta in items.items():
            key = safe_id(str(sid))
            caps = [safe_id(str(c)) for c in (meta.get("capabilities") or [])]
            idx[key] = {
                "id": key,
                "name": meta.get("name") or sid,
                "capabilities": caps,
                "meta": meta,
            }
        return idx

    def _fallback_match(self, need: str, idx: dict) -> list[str]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        picked: list[str] = []
        for sid, item in idx.items():
            corpus = " ".join([sid, safe_id(item.get("name", ""))] + item.get("capabilities", []))
            tokens = set(t for t in corpus.split("_") if t)
            if need_tokens and (need_tokens & tokens):
                picked.append(sid)
        return picked[:3]

    def _score_candidate(self, need: str, item: dict) -> tuple[int, dict]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        caps = [safe_id(str(c)) for c in (item.get("capabilities") or [])]
        corpus = " ".join([safe_id(item.get("id", "")), safe_id(item.get("name", ""))] + caps)
        tokens = set(t for t in corpus.split("_") if t)
        overlap = sorted(list(need_tokens & tokens))

        meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
        path = str(meta.get("path", ""))
        meta_path = str(meta.get("meta_path", ""))
        resolved_py, resolved_meta = resolve_skill_paths(item["id"])
        exists_py = bool(resolve_existing_path(path)) if path else bool(resolved_py)
        exists_meta = bool(resolve_existing_path(meta_path)) if meta_path else bool(resolved_meta)
        last_test_ok = bool(meta.get("last_test_ok", False))

        score = 0
        score += min(len(overlap) * 25, 60)
        if exists_py:
            score += 20
        if exists_meta:
            score += 10
        if last_test_ok:
            score += 10
        score = max(0, min(100, score))
        verify = {
            "exists_skill_py": exists_py,
            "exists_meta_yaml": exists_meta,
            "last_test_ok": last_test_ok,
            "token_overlap": overlap,
        }
        return score, verify

    def _fallback_project_brief(self, task_input: str) -> dict:
        text = (task_input or "").lower()
        required_skills: list[str] = []
        role_hints: list[str] = []
        deliverables: list[str] = []
        risks: list[str] = []

        if any(token in text for token in ("game", "게임", "poker", "포커")):
            required_skills.extend([
                "gameplay_core",
                "state_machine",
                "frontend_game_ui",
                "integration_test_guard",
            ])
            role_hints.extend(["game_logic_dev", "frontend_dev", "qa_engineer"])
            deliverables.extend(["게임 규칙 구현", "플레이 UI", "회귀 테스트"])
            risks.extend(["상태 전이 복잡도", "룰 판정 오류"])
        if any(token in text for token in ("web", "ui", "페이지", "screen", "frontend")):
            required_skills.append("frontend_game_ui")
            role_hints.append("frontend_dev")
        if any(token in text for token in ("api", "db", "backend", "서버")):
            required_skills.append("backend_service")
            role_hints.append("backend_dev")
            risks.append("데이터 모델 정합성")
        if not required_skills:
            required_skills.extend(["implementation_plan", "integration_test_guard"])
        if not role_hints:
            role_hints.extend(["general_dev", "qa_engineer"])
        if not deliverables:
            deliverables.append("작동하는 구현 결과")

        return {
            "goal": task_input,
            "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
            "required_skills": list(dict.fromkeys(required_skills)),
            "role_hints": list(dict.fromkeys(role_hints))[:5],
            "deliverables": deliverables[:6],
            "risks": list(dict.fromkeys(risks))[:6],
            "research_notes": ["LLM unavailable; heuristic brief generated."],
            "tech_stack": [],
        }

    def research_project_brief(self, agent: dict, task_input: str, workspace: str | None = None) -> dict:
        identity = agent if isinstance(agent, dict) and agent else self._himari_identity()
        sig = get_random_signature(identity)
        print_agent_msg(identity.get("name", "Himari"), f"프로젝트 착수 리서치를 시작합니다: {task_input}", sig)

        target_workspace = workspace or os.getenv("AGENT_PROJECT_ROOT") or os.getcwd()
        workspace_notes = []
        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            workspace_notes.append(f"existing_todo={todo_path}")

        _api_key = os.getenv("GOOGLE_API_KEY")
        _client = genai.Client(api_key=_api_key) if _api_key else None
        _model_name = normalize_model_name(self.mr.pick("requirement"))
        prompt = f"""
You are Himari, a project research director.
Task: {task_input}
Workspace notes: {workspace_notes}

Return JSON only:
{{
  "goal": "single sentence goal",
  "constraints": ["constraint"],
  "required_skills": ["snake_case_skill"],
  "role_hints": ["snake_case_role"],
  "deliverables": ["deliverable"],
  "risks": ["risk"],
  "research_notes": ["note"],
  "tech_stack": ["option"]
}}

Rules:
- required_skills: 3 to 8 concrete skills in English snake_case.
- role_hints: 2 to 5 practical implementation roles.
- deliverables and risks should be short Korean phrases.
""".strip()
        try:
            res = generate_content_with_self_heal(_client, _model_name, prompt) if _client else None
            data = safe_json_load(res.text if res else "{}")
            if not isinstance(data, dict):
                raise ValueError("project_brief_not_dict")
            data.setdefault("goal", task_input)
            data["constraints"] = [str(x) for x in (data.get("constraints") or []) if str(x).strip()]
            data["required_skills"] = [safe_id(str(x)) for x in (data.get("required_skills") or []) if str(x).strip()]
            data["role_hints"] = [safe_id(str(x)) for x in (data.get("role_hints") or []) if str(x).strip()]
            data["deliverables"] = [str(x).strip() for x in (data.get("deliverables") or []) if str(x).strip()]
            data["risks"] = [str(x).strip() for x in (data.get("risks") or []) if str(x).strip()]
            data["research_notes"] = [str(x).strip() for x in (data.get("research_notes") or []) if str(x).strip()]
            data["tech_stack"] = [str(x).strip() for x in (data.get("tech_stack") or []) if str(x).strip()]
            if not data["required_skills"]:
                raise ValueError("required_skills_missing")
            if not data["role_hints"]:
                raise ValueError("role_hints_missing")
            return data
        except Exception:
            return self._fallback_project_brief(task_input)

    def research(self, agent: dict, reqs: dict, build_targets: list[str] | None = None) -> dict:
        missing = [safe_id(str(s)) for s in (build_targets or reqs.get("missing_skills") or []) if str(s).strip()]
        idx = self._registry_skill_index()
        if not missing:
            return {"suggestions": {}, "all_candidates": [], "evidence_pack": {"targets": {}}}

        skill_catalog = []
        for sid, item in idx.items():
            skill_catalog.append({
                "id": sid,
                "name": item["name"],
                "capabilities": item["capabilities"],
            })

        # --- NotebookLM Research (V22.0 Hybrid Reasoning) ---
        notebook_insight = ""
        if missing:
            himari_cfg = self._himari_identity()
            sig = get_random_signature(himari_cfg)
            # [MISMATCH-3 FIX] 모든 미싱 스킬에 대해 리서치 (최대 3개)
            research_targets = missing[:3]
            skills_label = ", ".join(research_targets)
            print_agent_msg("Himari", f"비밀 서고(NotebookLM)에서 '{skills_label}' 관련 지식을 탐색합니다...", sig)
            
            from core.research_engine import generate_deep_research_prompt, ResearchMode, classify_research_depth
            query = generate_deep_research_prompt(
                f"다음 스킬들에 대한 설계 지침: {skills_label}. 프로젝트 목표: {reqs.get('goal')}"
            )
            # 미싱 스킬 수를 기반으로 리서치 모드 자율 판정
            target_mode = classify_research_depth(query, missing_skills_count=len(missing))
            insight = query_notebooklm(query, mode=target_mode)
            
            if insight and self._approve_notebooklm_insight(insight):
                notebook_insight = f"\n[NotebookLM Secret Archive Insight]: {insight[:2000]}"
                print("💡 [Himari] 승인된 NotebookLM 근거를 반영합니다.")
            elif insight:
                print("⏭️ [Himari] NotebookLM 근거 반영이 보류되었습니다.")

        # [New SDK] Client 기반 리서치 (Triad: requirement = Gemini Pro)
        _api_key = os.getenv("GOOGLE_API_KEY")
        if not _api_key:
            print("⚠️ [Himari] GOOGLE_API_KEY 없음 — LLM 리서치를 건너뛰고 fallback 매칭만 수행합니다.")
        _client = genai.Client(api_key=_api_key) if _api_key else None
        _model_name = normalize_model_name(self.mr.pick("requirement"))
        prompt = f"""
너는 리서치 에이전트 Himari다.
목표: missing_skills에 대해 설치 가능한 로컬 스킬 후보를 추천한다.

[Architectural Rule]
보스의 토큰 비용 절감 및 코드 무결성을 위해, 복잡한 상태 머신이나 다단계 로직이 포함된 경우 반드시 '원자적 모듈화(Atomic Modularization)'를 제안하라. 
기능을 하나의 거대한 파일이 아닌, 독립된 파일 단위로 쪼개어 설계하도록 유도해야 한다.

AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
MissingSkills: {missing}
LocalSkillCatalog(JSON): {json.dumps(skill_catalog, ensure_ascii=False)}
{notebook_insight}

출력은 JSON만:
{{
  "suggestions": {{
    "missing_skill_id": ["candidate_skill_id_1", "candidate_skill_id_2"]
  }}
}}
"""
        from core.utils import safe_generate
        suggestions: dict[str, list[str]] = {}
        try:
            res = generate_content_with_self_heal(_client, _model_name, prompt) if _client else None
            payload = safe_json_load(res.text if res else "{}")
            raw = payload.get("suggestions", {}) if isinstance(payload, dict) else {}
            if isinstance(raw, dict):
                for need, cands in raw.items():
                    k = safe_id(str(need))
                    values = [safe_id(str(c)) for c in (cands or []) if safe_id(str(c)) in idx]
                    if values:
                        suggestions[k] = list(dict.fromkeys(values))
        except Exception as e:
            print(f"⚠️ [Himari] LLM 리서치 실패: {type(e).__name__}: {e}")
            suggestions = {}

        for need in missing:
            if need not in suggestions:
                fallback = self._fallback_match(need, idx)
                if fallback:
                    suggestions[need] = fallback

        all_candidates = []
        for arr in suggestions.values():
            for sid in arr:
                if sid not in all_candidates:
                    all_candidates.append(sid)

        targets: dict = {}
        for need in missing:
            ranked = []
            for sid in suggestions.get(need, []):
                item = idx.get(sid)
                if not item:
                    continue
                score, verify = self._score_candidate(need, item)
                ranked.append({
                    "candidate_skill_id": sid,
                    "candidate_name": item.get("name", sid),
                    "score": score,
                    "verification": verify,
                    "capabilities": item.get("capabilities", []),
                })
            ranked.sort(key=lambda x: x["score"], reverse=True)
            best = ranked[0] if ranked else None
            targets[need] = {
                "need_skill_id": need,
                "top_candidate": (best or {}).get("candidate_skill_id"),
                "top_score": (best or {}).get("score", 0),
                "verified": bool(best and best["verification"]["exists_skill_py"]),
                "candidates": ranked,
            }

        evidence_pack = {
            "generated_at": now_iso(),
            "agent_role": agent.get("role"),
            "goal": reqs.get("goal"),
            "targets": targets,
            "notebook_insight": notebook_insight
        }
        return {"suggestions": suggestions, "all_candidates": all_candidates, "evidence_pack": evidence_pack}

    def search_external_and_install(self, needs: list[str], reqs: dict, registry) -> dict[str, str]:
        needs = [safe_id(str(n)) for n in (needs or []) if str(n).strip()]
        if not needs:
            return {}
        himari_cfg = self._himari_identity()
        sig = get_random_signature(himari_cfg)
        print_agent_msg("Himari", f"외부 스킬 소스에서 설치 가능한 후보를 탐색합니다: {needs}", sig)
        installed = registry.resolve_and_install_external(needs, reqs=reqs)
        if installed:
            print(f"💡 [Himari] 외부 소스 설치 성공: {list(installed.keys())}")
        else:
            print("⏭️ [Himari] 외부 소스에서 설치 가능한 후보를 찾지 못했습니다.")
        return installed
