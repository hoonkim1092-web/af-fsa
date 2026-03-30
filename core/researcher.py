import os
import json
import subprocess
import sys
from core.requirement_llm import execute_requirement_prompt
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, get_random_signature,
    print_agent_msg, safe_json_load, resolve_skill_paths, resolve_existing_path,
    to_portable_path,
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
        self._local_pipelines: dict[str, object] = {}  # root -> IngestionPipeline (인스턴스 재사용)

    @property
    def embedder(self):
        """SemanticEmbedder 지연 초기화."""
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

        # --- 시맨틱 유사도 (Phase 1: SemanticEmbedder 통합) ---
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

        # 점수 계산: 토큰(25) + 시맨틱(35) + 파일존재(20) + 메타존재(10) + 테스트(10)
        score = 0
        score += min(len(overlap) * 5, 25)                  # 토큰 오버랩 (25점)
        score += int(semantic_score * 35)                    # 시맨틱 유사도 (35점)
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
        """최상위 후보의 매칭 근거를 1줄 문자열로 생성."""
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

    def _repo_root(self) -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _compact_text(self, value: str, limit: int = 240) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(limit - 3, 0)].rstrip() + "..."

    def _workspace_notes(self, workspace: str) -> list[str]:
        notes: list[str] = []
        target_workspace = os.path.abspath(workspace or os.getcwd())

        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            notes.append(f"existing_todo={to_portable_path(os.path.relpath(todo_path, target_workspace))}")

        board_path = os.path.join(target_workspace, "project_board_state.json")
        if os.path.exists(board_path):
            notes.append(f"existing_project_board={to_portable_path(os.path.relpath(board_path, target_workspace))}")

        work_items_dir = os.path.join(target_workspace, "docs", "work-items")
        if os.path.isdir(work_items_dir):
            item_count = sum(1 for entry in os.listdir(work_items_dir) if os.path.isdir(os.path.join(work_items_dir, entry)) and not entry.startswith("_"))
            if item_count:
                notes.append(f"existing_work_items={item_count}")

        agents_dir = os.path.join(target_workspace, "agents")
        if os.path.isdir(agents_dir):
            agent_count = sum(1 for name in os.listdir(agents_dir) if name.endswith((".yml", ".yaml")))
            if agent_count:
                notes.append(f"existing_agents={agent_count}")

        return notes

    def _is_allowed_reference_path(self, source_path: str) -> bool:
        portable = "/" + to_portable_path(source_path).replace("\\", "/").lower().lstrip("/")
        blocked_tokens = (
            "/.git/",
            "/dist/",
            "/build/",
            "/node_modules/",
            "/.system_generated/",
            "/__pycache__/",
            "/.r1210/",
            "/.r129/",
            "/docs/work-items/_template/",
        )
        return not any(token in portable for token in blocked_tokens)

    def _reference_path(self, source_path: str, root: str) -> str:
        abs_path = os.path.abspath(source_path)
        try:
            rel = os.path.relpath(abs_path, os.path.abspath(root))
            if not rel.startswith(".."):
                return to_portable_path(rel)
        except Exception:
            pass
        return to_portable_path(abs_path)

    def _collect_local_references(self, task_input: str, workspace: str, limit: int = 6) -> list[dict]:
        from core.ingestion_pipeline import IngestionPipeline

        roots: list[str] = []
        for candidate in [workspace, self._repo_root()]:
            if not candidate:
                continue
            candidate_abs = os.path.abspath(candidate)
            if os.path.isdir(candidate_abs) and candidate_abs not in roots:
                roots.append(candidate_abs)

        refs: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for root in roots:
            try:
                pipeline = self._local_pipelines.get(root)
                if pipeline is None:
                    pipeline = IngestionPipeline(project_root=root)
                    self._local_pipelines[root] = pipeline
                pipeline.run(force=True)
                results = pipeline.search(task_input, top_k=max(limit, 8))
            except Exception:
                continue

            for result in results:
                chunk = getattr(result, "chunk", None)
                if not chunk:
                    continue
                source_path = os.path.abspath(str(getattr(chunk, "source_path", "") or ""))
                if not source_path or not os.path.exists(source_path):
                    continue
                if not self._is_allowed_reference_path(source_path):
                    continue

                path_label = self._reference_path(source_path, root)
                heading = self._compact_text(getattr(chunk, "heading", ""), limit=120)
                excerpt = self._compact_text(getattr(chunk, "content", ""), limit=260)
                key = (path_label, heading, excerpt)
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    {
                        "path": path_label,
                        "heading": heading,
                        "excerpt": excerpt,
                        "score": round(float(getattr(result, "score", 0.0) or 0.0), 4),
                    }
                )
                if len(refs) >= limit:
                    return refs
        return refs

    def _collect_web_references(self, task_input: str, limit: int = 4) -> list[dict]:
        if not os.getenv("TAVILY_API_KEY"):
            return []
        try:
            from core.web_search import tavily_search
            results = tavily_search(task_input, max_results=limit, include_answer=False)
        except Exception:
            return []

        refs: list[dict] = []
        for item in results[:limit]:
            url = str(item.get("url") or "").strip()
            title = self._compact_text(item.get("title") or url, limit=120)
            excerpt = self._compact_text(item.get("content") or "", limit=260)
            if not url:
                continue
            refs.append(
                {
                    "url": url,
                    "title": title,
                    "excerpt": excerpt,
                    "score": round(float(item.get("score") or 0.0), 4),
                }
            )
        return refs

    def _collect_notebook_summary(self, task_input: str, local_refs: list[dict], web_refs: list[dict]) -> str:
        try:
            import importlib.util
            if importlib.util.find_spec("notebooklm_tools") is None:
                return ""
        except Exception:
            return ""

        prompt_lines = [
            "Summarize the implementation evidence for the following project request.",
            f"Task: {task_input}",
        ]
        if local_refs:
            prompt_lines.append("Local references:")
            for ref in local_refs[:4]:
                prompt_lines.append(
                    f"- {ref.get('path')}: {ref.get('heading') or ref.get('excerpt') or ''}"
                )
        if web_refs:
            prompt_lines.append("Web references:")
            for ref in web_refs[:3]:
                prompt_lines.append(
                    f"- {ref.get('title') or ref.get('url')}: {ref.get('excerpt') or ''}"
                )
        prompt_lines.append(
            "Return a concise synthesis covering deliverables, implementation constraints, risks, and verification focus."
        )

        try:
            insight = query_notebooklm("\n".join(prompt_lines))
        except Exception:
            return ""
        return self._compact_text(insight, limit=1200)

    def _is_sufficient(self, local_refs: list[dict], task_input: str) -> bool:
        """로컬 근거만으로 충분한지 판정하는 Sufficiency Gate.

        아래 조건을 모두 만족하면 충분:
          - 최소 3개 이상의 참조
          - 평균 score >= 0.25
          - 내용이 있는 참조 최소 1개
          - freshness 키워드 없음
        """
        if len(local_refs) < 3:
            return False
        scores = [float(ref.get("score") or 0.0) for ref in local_refs]
        if scores and (sum(scores) / len(scores)) < 0.25:
            return False
        has_content = any(
            str(ref.get("excerpt") or ref.get("heading") or "").strip()
            for ref in local_refs
        )
        if not has_content:
            return False
        freshness_keywords = (
            "latest", "current", "pricing", "release", "news",
            "2026", "2025", "최신", "현재", "최근",
        )
        text_lower = (task_input or "").lower()
        if any(kw in text_lower for kw in freshness_keywords):
            return False
        return True

    def _collect_llm_prior_knowledge(self, task_input: str, limit: int = 4) -> list[dict]:
        """Tavily 키 없을 때 LLM 학습 지식을 구조화된 근거로 추출.

        반환 항목은 source_type="llm_prior", verified=False, weight=0.4.
        URL을 생성하지 않으며 실제 웹 검색이 아님을 명시.
        """
        prompt = f"""You are a technical knowledge extractor. Based only on your training knowledge (no internet access):

Task context: {task_input}

Extract structured technical knowledge relevant to this task.
Do NOT generate URLs. Do NOT cite articles you cannot verify.
Mark uncertain claims with "likely" or "typically".

Return JSON only:
{{
  "concepts": ["key technical concept"],
  "patterns": ["common implementation pattern"],
  "risks": ["known risk or pitfall"],
  "tech_options": ["relevant library or framework"],
  "constraints": ["typical constraint"],
  "notes": ["anything else relevant, mark uncertainty explicitly"]
}}

Rules:
- 3 to 6 items per field.
- English only, concrete and specific.
- No hallucinated project names, URLs, or paper citations.
""".strip()

        try:
            result = execute_requirement_prompt(prompt)
            if not result.get("ok"):
                return []
            data = safe_json_load(result.get("text") or "{}")
            if not isinstance(data, dict):
                return []
        except Exception:
            return []

        refs: list[dict] = []
        for field_name, label in (
            ("concepts", "Concept"),
            ("patterns", "Pattern"),
            ("risks", "Risk"),
            ("tech_options", "Tech option"),
        ):
            for item in (data.get(field_name) or []):
                text = str(item).strip()
                if not text:
                    continue
                refs.append({
                    "title": f"[LLM prior] {label}: {text[:80]}",
                    "excerpt": text,
                    "url": "",
                    "source_type": "llm_prior",
                    "weight": 0.4,
                    "verified": False,
                    "score": 0.4,
                })
                if len(refs) >= limit:
                    return refs
        return refs

    def _build_evidence_summary(
        self,
        workspace_notes: list[str],
        local_refs: list[dict],
        web_refs: list[dict],
        notebook_summary: str,
        llm_prior_refs: list[dict] | None = None,
    ) -> list[str]:
        summary: list[str] = []
        for note in workspace_notes[:3]:
            summary.append(f"Workspace note: {note}")
        for ref in local_refs[:3]:
            path = str(ref.get("path") or "").strip()
            excerpt = str(ref.get("excerpt") or ref.get("heading") or "").strip()
            if path:
                summary.append(f"Local reference: {path} -> {excerpt}")
        for ref in web_refs[:3]:
            label = str(ref.get("title") or ref.get("url") or "").strip()
            excerpt = str(ref.get("excerpt") or "").strip()
            if label:
                summary.append(f"Web reference: {label} -> {excerpt}")
        for ref in (llm_prior_refs or [])[:2]:
            excerpt = str(ref.get("excerpt") or "").strip()
            if excerpt:
                summary.append(f"LLM prior knowledge (unverified): {excerpt[:160]}")
        if notebook_summary:
            summary.append(f"NotebookLM synthesis: {self._compact_text(notebook_summary, limit=280)}")
        return summary[:10]

    def collect_project_evidence(
        self,
        task_input: str,
        workspace: str | None = None,
        risk_level: str = "normal",
        comparison_mode: bool = False,
    ) -> dict:
        target_workspace = os.path.abspath(workspace or os.getenv("AGENT_PROJECT_ROOT") or os.getcwd())

        workspace_notes = self._workspace_notes(target_workspace)
        local_refs = self._collect_local_references(task_input, target_workspace)

        # -- Sufficiency Gate --
        sufficient = self._is_sufficient(local_refs, task_input)

        # -- 웹 또는 LLM fallback --
        web_refs: list[dict] = []
        llm_prior_refs: list[dict] = []
        if not sufficient:
            if os.getenv("TAVILY_API_KEY"):
                web_refs = self._collect_web_references(task_input)
            else:
                llm_prior_refs = self._collect_llm_prior_knowledge(task_input)

        # -- virtual chunk 인덱싱 (Unified RAG) --
        # _collect_local_references가 캐싱한 pipeline에 직접 올려야 동일 인덱스에서 검색 가능
        if web_refs or llm_prior_refs:
            try:
                from core.document_chunker import make_virtual_chunk
                virtual_chunks = []
                for ref in web_refs:
                    virtual_chunks.append(make_virtual_chunk(
                        content=str(ref.get("excerpt") or ref.get("title") or ""),
                        title=str(ref.get("title") or ref.get("url") or ""),
                        source_type="web",
                        source_url=str(ref.get("url") or ""),
                        weight=0.9,
                        verified=True,
                    ))
                for ref in llm_prior_refs:
                    virtual_chunks.append(make_virtual_chunk(
                        content=str(ref.get("excerpt") or ""),
                        title=str(ref.get("title") or ""),
                        source_type="llm_prior",
                        weight=0.4,
                        verified=False,
                    ))
                if virtual_chunks:
                    # 로컬 검색에 사용된 pipeline 인스턴스를 재사용 (같은 DocumentIndex)
                    _target_pipeline = self._local_pipelines.get(target_workspace)
                    if _target_pipeline is not None:
                        _target_pipeline.ingest_external_chunks(virtual_chunks)
            except Exception:
                pass

        # -- NotebookLM: risk_level==high 또는 comparison_mode일 때만 --
        should_query_notebooklm = (
            risk_level.lower() in ("high", "strict", "elevated") or comparison_mode
        )
        notebook_summary = (
            self._collect_notebook_summary(task_input, local_refs, web_refs)
            if should_query_notebooklm
            else ""
        )

        evidence_summary = self._build_evidence_summary(
            workspace_notes,
            local_refs,
            web_refs,
            notebook_summary,
            llm_prior_refs,
        )
        return {
            "workspace_notes": workspace_notes,
            "local_references": local_refs,
            "web_references": web_refs,
            "llm_prior_references": llm_prior_refs,
            "notebook_summary": notebook_summary,
            "evidence_summary": evidence_summary,
            "sufficiency_gate_passed": sufficient,
        }

    def _merge_project_brief_evidence(self, brief: dict, task_input: str, evidence_bundle: dict | None) -> dict:
        data = dict(brief or {})
        evidence = dict(evidence_bundle or {})
        workspace_notes = [str(x).strip() for x in (evidence.get("workspace_notes") or []) if str(x).strip()]
        evidence_summary = [str(x).strip() for x in (evidence.get("evidence_summary") or []) if str(x).strip()]
        notebook_summary = str(evidence.get("notebook_summary") or "").strip()
        local_references = [item for item in (evidence.get("local_references") or []) if isinstance(item, dict)]
        web_references = [item for item in (evidence.get("web_references") or []) if isinstance(item, dict)]
        llm_prior_references = [item for item in (evidence.get("llm_prior_references") or []) if isinstance(item, dict)]

        data.setdefault("goal", task_input)
        data["constraints"] = [str(x) for x in (data.get("constraints") or []) if str(x).strip()]
        data["required_skills"] = [safe_id(str(x)) for x in (data.get("required_skills") or []) if str(x).strip()]
        data["role_hints"] = [safe_id(str(x)) for x in (data.get("role_hints") or []) if str(x).strip()]
        data["deliverables"] = [str(x).strip() for x in (data.get("deliverables") or []) if str(x).strip()]
        data["risks"] = [str(x).strip() for x in (data.get("risks") or []) if str(x).strip()]
        data["research_notes"] = [str(x).strip() for x in (data.get("research_notes") or []) if str(x).strip()]
        data["tech_stack"] = [str(x).strip() for x in (data.get("tech_stack") or []) if str(x).strip()]
        data["workspace_notes"] = workspace_notes
        data["evidence_summary"] = evidence_summary
        data["local_references"] = local_references
        data["web_references"] = web_references
        data["llm_prior_references"] = llm_prior_references
        data["notebook_summary"] = notebook_summary

        derived_notes: list[str] = []
        if local_references:
            derived_notes.append(f"local_references={len(local_references)}")
        if web_references:
            derived_notes.append(f"web_references={len(web_references)}")
        if llm_prior_references:
            derived_notes.append(f"llm_prior_references={len(llm_prior_references)} (unverified)")
        if notebook_summary:
            derived_notes.append("notebook_summary=available")
        data["research_notes"] = list(dict.fromkeys(data["research_notes"] + derived_notes + evidence_summary[:4]))
        return data

    def _fallback_project_brief(self, task_input: str) -> dict:
        text = (task_input or "").lower()
        required_skills: list[str] = []
        role_hints: list[str] = []
        deliverables: list[str] = []
        risks: list[str] = []

        if any(token in text for token in ("game", "lotto", "poker")):
            required_skills.extend([
                "gameplay_core",
                "state_machine",
                "frontend_game_ui",
                "integration_test_guard",
            ])
            role_hints.extend(["game_logic_dev", "frontend_dev", "qa_engineer"])
            deliverables.extend(["core rules implementation", "player-facing UI", "integration verification"])
            risks.extend(["complex state transitions", "incorrect rule evaluation"])
        if any(token in text for token in ("web", "ui", "page", "screen", "frontend")):
            required_skills.append("frontend_game_ui")
            role_hints.append("frontend_dev")
        if any(token in text for token in ("api", "db", "backend", "server")):
            required_skills.append("backend_service")
            role_hints.append("backend_dev")
            risks.append("data model integrity")
        if not required_skills:
            required_skills.extend(["implementation_plan", "integration_test_guard"])
        if not role_hints:
            role_hints.extend(["general_dev", "qa_engineer"])
        if not deliverables:
            deliverables.append("working implementation output")

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

    def research_project_brief(
        self,
        agent: dict,
        task_input: str,
        workspace: str | None = None,
        evidence_bundle: dict | None = None,
    ) -> dict:
        identity = agent if isinstance(agent, dict) and agent else self._himari_identity()
        sig = get_random_signature(identity)
        print_agent_msg(identity.get("name", "Himari"), f"Project kickoff research started: {task_input}", sig)

        target_workspace = workspace or os.getenv("AGENT_PROJECT_ROOT") or os.getcwd()
        evidence = evidence_bundle if evidence_bundle is not None else self.collect_project_evidence(task_input, workspace=target_workspace)
        workspace_notes = [str(x).strip() for x in (evidence.get("workspace_notes") or []) if str(x).strip()]
        local_refs = [item for item in (evidence.get("local_references") or []) if isinstance(item, dict)]
        web_refs = [item for item in (evidence.get("web_references") or []) if isinstance(item, dict)]
        notebook_summary = str(evidence.get("notebook_summary") or "").strip()
        evidence_summary = [str(x).strip() for x in (evidence.get("evidence_summary") or []) if str(x).strip()]

        prompt = f"""
You are Himari, a project research director.
Task: {task_input}
Workspace notes(JSON): {json.dumps(workspace_notes, ensure_ascii=False)}
Evidence summary(JSON): {json.dumps(evidence_summary, ensure_ascii=False)}
Local references(JSON): {json.dumps(local_refs[:6], ensure_ascii=False)}
Web references(JSON): {json.dumps(web_refs[:4], ensure_ascii=False)}
NotebookLM synthesis: {notebook_summary or '(none)'}

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
- Use the evidence bundle to ground deliverables, risks, and implementation constraints when evidence is available.
- Prefer concrete modules, interfaces, verification targets, and existing project documents over generic placeholders.
        """.strip()
        try:
            result = execute_requirement_prompt(prompt, workspace=target_workspace)
            if not result.get("ok"):
                raise RuntimeError("project_brief_llm_unavailable")
            data = safe_json_load(result.get("text") or "{}")
            if not isinstance(data, dict):
                raise ValueError("project_brief_not_dict")
            data = self._merge_project_brief_evidence(data, task_input, evidence)
            if not data["required_skills"]:
                raise ValueError("required_skills_missing")
            if not data["role_hints"]:
                raise ValueError("role_hints_missing")
            return data
        except Exception:
            return self._merge_project_brief_evidence(self._fallback_project_brief(task_input), task_input, evidence)

    def research(self, agent: dict, reqs: dict, build_targets: list[str] | None = None) -> dict:
        missing = [safe_id(str(s)) for s in (build_targets or reqs.get("missing_skills") or []) if str(s).strip()]
        idx = self._registry_skill_index()
        if not missing:
            return {"suggestions": {}, "all_candidates": [], "evidence_pack": {"targets": {}}}

        # Phase 1: 검색 전략 분류 및 로깅
        plan = self._router.classify(
            reqs.get("goal", ""),
            context={"phase": "research", "role": agent.get("role", "")},
        )
        print(f"[Retrieval] strategy={plan.primary.value}, confidence={plan.confidence:.2f}, "
              f"semantic={'ON' if self.embedder and self.embedder.is_available else 'OFF'}")

        # SemanticEmbedder: 스킬 임베딩 사전 계산
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
        suggestions: dict[str, list[str]] = {}
        try:
            result = execute_requirement_prompt(prompt)
            if not result.get("ok"):
                print("⚠️ [Himari] requirement-stage LLM unavailable — fallback 매칭만 수행합니다.")
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

        # 검색 전략 분류
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
            # Phase 1 확장 필드
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
        print_agent_msg("Himari", f"외부 스킬 소스에서 설치 가능한 후보를 탐색합니다: {needs}", sig)
        installed = registry.resolve_and_install_external(needs, reqs=reqs)
        if installed:
            print(f"💡 [Himari] 외부 소스 설치 성공: {list(installed.keys())}")
        else:
            print("⏭️ [Himari] 외부 소스에서 설치 가능한 후보를 찾지 못했습니다.")
        return installed
