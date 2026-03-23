import os
import json
import subprocess
import sys
from core.requirement_llm import execute_requirement_prompt
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, get_random_signature,
    print_agent_msg, safe_json_load, resolve_skill_paths, resolve_existing_path
)
from core.config_paths import AGENTS_DIR, REGISTRY_PATH
from core.research_engine import query_notebooklm
from core.retrieval_router import RetrievalRouter, RetrievalStrategy
from core.skill_feedback import SkillFeedbackLoop
from core.skill_retrieval_engine import SkillRetrievalEngine

class HimariResearchAgent:
    """Specialized research agent utilizing local and external knowledge (NotebookLM)."""
    def __init__(self, mr):
        self.mr = mr
        self._router = RetrievalRouter()
        self._embedder = None
        self._embedder_checked = False
        self._skill_retrieval_engine = SkillRetrievalEngine()

    @property
    def embedder(self):
        """SemanticEmbedder 吏??珥덇린??"""
        if not self._embedder_checked:
            self._embedder_checked = True
            try:
                from core.semantic_embedder import SemanticEmbedder
                self._embedder = SemanticEmbedder()
            except Exception:
                self._embedder = None
        return self._embedder

    def _himari_identity(self) -> dict:
        path = os.path.join(AGENTS_DIR, "himari.yaml")
        data = read_yaml(path) if os.path.exists(path) else {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("name", "Himari")
        data.setdefault("role", "Project Research Director")
        data.setdefault("signature_lines", ["洹쇨굅瑜?癒쇱? 怨좎젙?⑸땲??"])
        return data

    def _approve_notebooklm_insight(self, insight: str) -> bool:
        preview = (insight or "").strip()
        if not preview:
            return False
        print("\n[Himari][?붾쾭洹? NotebookLM ?묐떟 誘몃━蹂닿린")
        print("-" * 50)
        print(preview[:1200])
        print("-" * 50)
        try:
            ans = input("[Himari] ???묐떟??由ъ꽌移?洹쇨굅濡?諛섏쁺?좉퉴?? (yes/no): ").strip().lower()
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

        # --- ?쒕㎤???좎궗??(Phase 1: SemanticEmbedder ?듯빀) ---
        semantic_score = 0.0
        if self.embedder and self.embedder.is_available:
            try:
                from core.skill_registry import get_global_registry
                registry = get_global_registry()
                skill_meta = registry.get(item["id"])
                if skill_meta:
                    semantic_score = self.embedder.compute_similarity(need, skill_meta)
            except Exception:
                pass

        # ?먯닔 怨꾩궛: ?좏겙(25) + ?쒕㎤??35) + ?뚯씪議댁옱(20) + 硫뷀?議댁옱(10) + ?뚯뒪??10)
        score = 0
        score += min(len(overlap) * 5, 25)                  # ?좏겙 ?ㅻ쾭??(25??
        score += int(semantic_score * 35)                    # ?쒕㎤???좎궗??(35??
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
            "semantic_score": round(semantic_score, 3),
        }
        return score, verify

    def _build_rationale(self, need: str, best: dict) -> str:
        """理쒖긽???꾨낫??留ㅼ묶 洹쇨굅瑜?1以?臾몄옄?대줈 ?앹꽦."""
        parts = []
        v = best.get("verification", {})
        overlap = v.get("token_overlap", [])
        semantic = v.get("semantic_score", 0.0)
        if overlap:
            parts.append(f"token_overlap={len(overlap)}/{','.join(overlap[:3])}")
        if semantic > 0:
            parts.append(f"semantic={semantic:.2f}")
        if v.get("exists_skill_py"):
            parts.append("file_exists")
        if v.get("last_test_ok"):
            parts.append("test_passed")
        return f"score={best.get('score', 0)}: {' + '.join(parts)}" if parts else ""

    def _rank_candidates_for_need(
        self,
        need: str,
        candidate_skill_ids: list[str],
        idx: dict,
        *,
        feedback_loop: SkillFeedbackLoop | None,
        feedback_summaries: dict | None = None,
    ) -> dict:
        ranked = []
        for sid in candidate_skill_ids:
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
                "matching_rationale": self._build_rationale(need, {"score": score, "verification": verify}),
            })
        ranked.sort(key=lambda x: x["score"], reverse=True)
        best = ranked[0] if ranked else {}
        target = {
            "need_skill_id": need,
            "top_candidate": (best or {}).get("candidate_skill_id", ""),
            "top_score": (best or {}).get("score", 0),
            "verified": bool(best and best["verification"].get("exists_skill_py")),
            "candidates": ranked,
            "matching_rationale": str((best or {}).get("matching_rationale") or ""),
            "source_type": "local_registry",
            "feedback_history": [],
        }
        self._skill_retrieval_engine.decide_reuse(
            need,
            target,
            feedback_loop=feedback_loop,
            feedback_summaries=feedback_summaries,
        )
        return target

    def _fallback_project_brief(self, task_input: str) -> dict:
        text = (task_input or "").lower()
        required_skills: list[str] = []
        role_hints: list[str] = []
        deliverables: list[str] = []
        risks: list[str] = []

        if any(token in text for token in ("game", "寃뚯엫", "poker", "?ъ빱")):
            required_skills.extend([
                "gameplay_core",
                "state_machine",
                "frontend_game_ui",
                "integration_test_guard",
            ])
            role_hints.extend(["game_logic_dev", "frontend_dev", "qa_engineer"])
            deliverables.extend(["게임 규칙 구현", "플레이 UI", "통합 테스트"])
            risks.extend(["상태 전이 복잡도", "룰 고정 오류"])
        if any(token in text for token in ("web", "ui", "?섏씠吏", "screen", "frontend")):
            required_skills.append("frontend_game_ui")
            role_hints.append("frontend_dev")
        if any(token in text for token in ("api", "db", "backend", "?쒕쾭")):
            required_skills.append("backend_service")
            role_hints.append("backend_dev")
            risks.append("데이터 모델 정합성")
        if not required_skills:
            required_skills.extend(["implementation_plan", "integration_test_guard"])
        if not role_hints:
            role_hints.extend(["general_dev", "qa_engineer"])
        if not deliverables:
            deliverables.append("?묐룞?섎뒗 援ы쁽 寃곌낵")

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
        print_agent_msg(identity.get("name", "Himari"), f"?꾨줈?앺듃 李⑹닔 由ъ꽌移섎? ?쒖옉?⑸땲?? {task_input}", sig)

        target_workspace = workspace or os.getenv("AGENT_PROJECT_ROOT") or os.getcwd()
        workspace_notes = []
        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            workspace_notes.append(f"existing_todo={todo_path}")

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
            result = execute_requirement_prompt(prompt, workspace=target_workspace)
            if not result.get("ok"):
                raise RuntimeError("project_brief_llm_unavailable")
            data = safe_json_load(result.get("text") or "{}")
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

        # Phase 1: 寃???꾨왂 遺꾨쪟 諛?濡쒓퉭
        plan = self._router.classify(
            reqs.get("goal", ""),
            context={"phase": "research", "role": agent.get("role", "")},
        )
        print(f"[Retrieval] strategy={plan.primary.value}, confidence={plan.confidence:.2f}, "
              f"semantic={'ON' if self.embedder and self.embedder.is_available else 'OFF'}")

        # SemanticEmbedder: ?ㅽ궗 ?꾨쿋???ъ쟾 怨꾩궛
        if self.embedder and self.embedder.is_available:
            try:
                from core.skill_registry import get_global_registry, ensure_skills_loaded
                ensure_skills_loaded()
                registry = get_global_registry()
                all_skills = registry.get_all()
                if all_skills:
                    self.embedder.precompute_skill_embeddings(all_skills)
            except Exception:
                pass

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
            # [MISMATCH-3 FIX] 紐⑤뱺 誘몄떛 ?ㅽ궗?????由ъ꽌移?(理쒕? 3媛?
            research_targets = missing[:3]
            skills_label = ", ".join(research_targets)
            print_agent_msg("Himari", f"鍮꾨? ?쒓퀬(NotebookLM)?먯꽌 '{skills_label}' 愿??吏?앹쓣 ?먯깋?⑸땲??..", sig)
            
            from core.research_engine import generate_deep_research_prompt, ResearchMode, classify_research_depth
            query = generate_deep_research_prompt(
                f"?ㅼ쓬 ?ㅽ궗?ㅼ뿉 ????ㅺ퀎 吏移? {skills_label}. ?꾨줈?앺듃 紐⑺몴: {reqs.get('goal')}"
            )
            # 誘몄떛 ?ㅽ궗 ?섎? 湲곕컲?쇰줈 由ъ꽌移?紐⑤뱶 ?먯쑉 ?먯젙
            target_mode = classify_research_depth(query, missing_skills_count=len(missing))
            insight = query_notebooklm(query, mode=target_mode)
            
            if insight and self._approve_notebooklm_insight(insight):
                notebook_insight = f"\n[NotebookLM Secret Archive Insight]: {insight[:2000]}"
                print("?뮕 [Himari] ?뱀씤??NotebookLM 洹쇨굅瑜?諛섏쁺?⑸땲??")
            elif insight:
                print("??툘 [Himari] NotebookLM 洹쇨굅 諛섏쁺??蹂대쪟?섏뿀?듬땲??")

        # [New SDK] Client 湲곕컲 由ъ꽌移?(Triad: requirement = Gemini Pro)
        prompt = f"""
?덈뒗 由ъ꽌移??먯씠?꾪듃 Himari??
紐⑺몴: missing_skills??????ㅼ튂 媛?ν븳 濡쒖뺄 ?ㅽ궗 ?꾨낫瑜?異붿쿇?쒕떎.

[Architectural Rule]
蹂댁뒪???좏겙 鍮꾩슜 ?덇컧 諛?肄붾뱶 臾닿껐?깆쓣 ?꾪빐, 蹂듭옟???곹깭 癒몄떊?대굹 ?ㅻ떒怨?濡쒖쭅???ы븿??寃쎌슦 諛섎뱶??'?먯옄??紐⑤뱢??Atomic Modularization)'瑜??쒖븞?섎씪. 
湲곕뒫???섎굹??嫄곕????뚯씪???꾨땶, ?낅┰???뚯씪 ?⑥쐞濡?履쇨컻???ㅺ퀎?섎룄濡??좊룄?댁빞 ?쒕떎.

AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
MissingSkills: {missing}
LocalSkillCatalog(JSON): {json.dumps(skill_catalog, ensure_ascii=False)}
{notebook_insight}

異쒕젰? JSON留?
{{
  "suggestions": {{
    "missing_skill_id": ["candidate_skill_id_1", "candidate_skill_id_2"]
  }}
}}
"""
        suggestions: dict[str, list[str]] = {}
        try:
            result = execute_requirement_prompt(prompt)
            if not result.get("ok"):
                print("?좑툘 [Himari] requirement-stage LLM unavailable ??fallback 留ㅼ묶留??섑뻾?⑸땲??")
                raise RuntimeError("research_llm_unavailable")
            payload = safe_json_load(result.get("text") or "{}")
            raw = payload.get("suggestions", {}) if isinstance(payload, dict) else {}
            if isinstance(raw, dict):
                for need, cands in raw.items():
                    k = safe_id(str(need))
                    values = [safe_id(str(c)) for c in (cands or []) if safe_id(str(c)) in idx]
                    if values:
                        suggestions[k] = list(dict.fromkeys(values))
        except Exception as e:
            print(f"?좑툘 [Himari] LLM 由ъ꽌移??ㅽ뙣: {type(e).__name__}: {e}")
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

        feedback_loop = SkillFeedbackLoop.for_workspace(os.getenv("AGENT_PROJECT_ROOT") or os.getcwd())
        feedback_summaries = feedback_loop.summarize_skills(all_candidates) if all_candidates else {}
        feedback_history: list[dict] = []
        feedback_history_skill_ids: set[str] = set()

        targets: dict = {}
        for need in missing:
            target = self._rank_candidates_for_need(
                need,
                suggestions.get(need, []),
                idx,
                feedback_loop=feedback_loop,
                feedback_summaries=feedback_summaries,
            )
            targets[need] = target
            for entry in target.get("feedback_history", []):
                if not isinstance(entry, dict):
                    continue
                feedback_skill_id = safe_id(str(entry.get("skill_id") or ""))
                if feedback_skill_id and feedback_skill_id not in feedback_history_skill_ids:
                    feedback_history_skill_ids.add(feedback_skill_id)
                    feedback_history.append(entry)

        # 寃???꾨왂 遺꾨쪟
        retrieval_plan = self._router.classify(
            reqs.get("goal", ""),
            context={"phase": "research", "role": agent.get("role", "")},
        )

        evidence_pack = {
            "generated_at": now_iso(),
            "agent_role": agent.get("role"),
            "goal": reqs.get("goal"),
            "targets": targets,
            "notebook_insight": notebook_insight,
            # Phase 1 ?뺤옣 ?꾨뱶
            "retrieval_strategy": retrieval_plan.primary.value,
            "retrieval_confidence": round(retrieval_plan.confidence, 3),
            "semantic_available": bool(self.embedder and self.embedder.is_available),
            "feedback_history": feedback_history,
        }
        return {"suggestions": suggestions, "all_candidates": all_candidates, "evidence_pack": evidence_pack}

    def search_external_and_install(self, needs: list[str], reqs: dict, registry) -> dict[str, str]:
        needs = [safe_id(str(n)) for n in (needs or []) if str(n).strip()]
        if not needs:
            return {}
        himari_cfg = self._himari_identity()
        sig = get_random_signature(himari_cfg)
        print_agent_msg("Himari", f"?몃? ?ㅽ궗 ?뚯뒪?먯꽌 ?ㅼ튂 媛?ν븳 ?꾨낫瑜??먯깋?⑸땲?? {needs}", sig)
        installed = registry.resolve_and_install_external(needs, reqs=reqs)
        if installed:
            print(f"?뮕 [Himari] ?몃? ?뚯뒪 ?ㅼ튂 ?깃났: {list(installed.keys())}")
        else:
            print("??툘 [Himari] ?몃? ?뚯뒪?먯꽌 ?ㅼ튂 媛?ν븳 ?꾨낫瑜?李얠? 紐삵뻽?듬땲??")
        return installed
