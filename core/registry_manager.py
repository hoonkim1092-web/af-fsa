import os
import shutil
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, resolve_skill_paths,
    resolve_existing_path, to_portable_path, is_portable_rel_path,
    read_skill_lock, lock_skill_state, append_dashboard_run, skill_markdown_filenames
)
from core.external_skill_source_ids import normalize_external_source_id
from core.install_candidate_utils import normalize_install_candidate_collection
from core.config_paths import (
    REGISTRY_PATH, SKILLS_DIR, WORKFLOW_PATH
)
from core.external_skill_sources import ExternalSkillResolver
from core.policy import resolve_quality_gate_policy

def read_project_policies():
    from core.config_paths import POLICIES_PATH
    return read_yaml(POLICIES_PATH)

def ensure_registry_files():
    if not os.path.exists(REGISTRY_PATH):
        write_yaml(REGISTRY_PATH, {"skills": {}, "install_candidates": {}})

class RegistryManager:
    """Manages the global skill registry and external skill installations."""
    def __init__(self):
        self._read_only = False
        try:
            ensure_registry_files()
            self._normalize_registry_paths()
        except PermissionError:
            self._read_only = True

    def _read_registry(self) -> dict:
        reg = read_yaml(REGISTRY_PATH)
        if not isinstance(reg, dict):
            reg = {}
        reg.setdefault("skills", {})
        reg.setdefault("install_candidates", {})
        return reg

    def _write_registry(self, reg: dict):
        if self._read_only:
            raise PermissionError(REGISTRY_PATH)
        reg = reg if isinstance(reg, dict) else {}
        reg.setdefault("skills", {})
        reg.setdefault("install_candidates", {})
        write_yaml(REGISTRY_PATH, reg)

    def _tokenize(self, text: str) -> set[str]:
        return {t for t in safe_id(text).split("_") if t}

    def _score_need_match(self, need: str, candidate_text: str) -> int:
        need_tokens = self._tokenize(need)
        cand_tokens = self._tokenize(candidate_text)
        if not need_tokens or not cand_tokens:
            return 0
        overlap = len(need_tokens & cand_tokens)
        if overlap == 0:
            return 0
        base = overlap * 20
        if safe_id(need) == safe_id(candidate_text):
            base += 40
        return min(100, base)

    def _resolve_path(self, path_text: str) -> str | None:
        return resolve_existing_path(path_text)

    def _normalize_install_candidates(self, raw_candidates) -> dict:
        normalized: dict = {}
        for cid, item in normalize_install_candidate_collection(raw_candidates, default_source="registry").items():
            if not isinstance(item, dict):
                continue
            n_item = dict(item)
            path = str(n_item.get("path") or "").strip()
            if path:
                portable_path = to_portable_path(path)
                if not is_portable_rel_path(portable_path):
                    continue
                n_item["path"] = portable_path
            else:
                n_item.pop("path", None)
            n_item["id"] = safe_id(str(n_item.get("id") or cid))
            n_item["source_id"] = normalize_external_source_id(
                str(n_item.get("source_id") or "registry"),
                default="registry",
            )
            source_repo = str(n_item.get("source_repo") or "").strip()
            if source_repo:
                n_item["source_repo"] = source_repo
            else:
                n_item.pop("source_repo", None)
            source_url = str(n_item.get("source_url") or "").strip()
            if source_url:
                n_item["source_url"] = source_url
            else:
                n_item.pop("source_url", None)
            capabilities: list[str] = []
            for raw_value in (n_item.get("capabilities") or []):
                capability = safe_id(str(raw_value))
                if capability and capability not in capabilities:
                    capabilities.append(capability)
            if capabilities:
                n_item["capabilities"] = capabilities
            else:
                n_item.pop("capabilities", None)
            normalized[cid] = n_item
        return normalized

    def _normalize_registry_paths(self):
        reg = self._read_registry()
        skills = reg.get("skills", {}) if isinstance(reg, dict) else {}
        changed = False
        for sid, item in list((skills or {}).items()):
            if not isinstance(item, dict):
                continue
            key = safe_id(str(sid))
            resolved_py, resolved_meta = resolve_skill_paths(key)
            if resolved_py:
                item["path"] = to_portable_path(resolved_py)
                changed = True
            elif item.get("path"):
                p = self._resolve_path(str(item.get("path")))
                if p:
                    item["path"] = to_portable_path(p)
                    changed = True
            if resolved_meta:
                item["meta_path"] = to_portable_path(resolved_meta)
                changed = True
            elif item.get("meta_path"):
                mp = self._resolve_path(str(item.get("meta_path")))
                if mp:
                    item["meta_path"] = to_portable_path(mp)
                    changed = True
        if changed:
            reg["skills"] = skills
            self._write_registry(reg)

        raw_cands = reg.get("install_candidates", {})
        normalized = self._normalize_install_candidates(raw_cands)
        if raw_cands != normalized:
            reg["install_candidates"] = normalized
            changed = True
        if changed:
            self._write_registry(reg)

    def _iter_install_candidates(self) -> list[dict]:
        reg = self._read_registry()
        raw = self._normalize_install_candidates(reg.get("install_candidates", {}))
        out: list[dict] = []
        for cid, item in raw.items():
            if not isinstance(item, dict):
                continue
            sid = safe_id(str(item.get("id") or cid))
            out.append({
                "id": sid,
                "name": str(item.get("name") or sid),
                "path": str(item.get("path") or "").strip(),
                "source_url": str(item.get("source_url") or ""),
                "source_repo": str(item.get("source_repo") or ""),
                "source_id": normalize_external_source_id(
                    str(item.get("source_id") or "registry"),
                    default="registry",
                ),
                "capabilities": [safe_id(str(x)) for x in (item.get("capabilities") or []) if str(x).strip()],
            })
        return out

    def _install_skill_file(self, need_id: str, source_path: str, source_label: str = "external") -> tuple[bool, str]:
        sid = safe_id(need_id)
        src = self._resolve_path(source_path)
        if not src: return False, "source_not_found"

        skill_py: str | None = None
        skill_md: str | None = None
        if os.path.isdir(src):
            cand = os.path.join(src, "skill.py")
            if os.path.exists(cand):
                skill_py = cand
            else:
                for filename in skill_markdown_filenames():
                    md_cand = os.path.join(src, filename)
                    if os.path.exists(md_cand):
                        skill_md = md_cand
                        break
        elif os.path.isfile(src) and src.endswith(".py"):
            skill_py = src
        elif os.path.isfile(src) and src.lower().endswith(".md"):
            skill_md = src

        if not skill_py and not skill_md:
            return False, "no_supported_skill_file"

        target_dir = os.path.join(SKILLS_DIR, sid)
        os.makedirs(target_dir, exist_ok=True)
        target_py = os.path.join(target_dir, "skill.py")
        target_md = os.path.join(target_dir, "skill.md")
        target_meta = os.path.join(target_dir, "meta.yaml")
        if os.path.isdir(src):
            shutil.copytree(src, target_dir, dirs_exist_ok=True)
        elif skill_py:
            shutil.copy2(skill_py, target_py)
        elif skill_md:
            shutil.copy2(skill_md, target_md)

        if skill_md and not os.path.exists(target_md):
            shutil.copy2(skill_md, target_md)

        skill_type = "action" if skill_py else "knowledge"
        active_path = target_py if skill_py else target_md

        meta = {
            "id": sid, "name": sid, "version": "1.0.0", "capabilities": [sid],
            "status": "active", "updated_at": now_iso(), "source": source_label, "source_path": src,
            "type": skill_type,
        }
        write_yaml(target_meta, meta)

        reg = self._read_registry()
        reg["skills"][sid] = {
            "id": sid, "name": sid, "status": "active", "version": "1.0.0",
            "capabilities": [sid], "type": skill_type, "path": to_portable_path(active_path),
            "meta_path": to_portable_path(target_meta), "updated_at": now_iso(), "last_test_ok": True,
        }
        self._write_registry(reg)
        lock_skill_state(sid, {"version": "1.0.0", "status": "active"})
        return True, sid

    def resolve_and_install_external_detailed(
        self,
        needs: list[str],
        reqs: dict | None = None,
        evidence_pack: dict | None = None,
    ) -> dict:
        resolver = ExternalSkillResolver(
            project_policies=read_project_policies(),
            install_candidates=self._iter_install_candidates(),
            install_fn=self._install_skill_file,
            path_resolver=self._resolve_path,
            match_fn=self._score_need_match,
        )
        return resolver.resolve_and_install(needs, reqs=reqs, evidence_pack=evidence_pack)

    def resolve_and_install_external(
        self,
        needs: list[str],
        reqs: dict | None = None,
        evidence_pack: dict | None = None,
    ) -> dict[str, str]:
        result = self.resolve_and_install_external_detailed(needs, reqs=reqs, evidence_pack=evidence_pack)
        installed = result.get("installed", {})
        return installed if isinstance(installed, dict) else {}

    def _quality_gate_policy(self) -> dict:
        qg = resolve_quality_gate_policy(read_project_policies())
        return {
            "default_stage_on_build": str(qg.get("default_stage_on_build", "candidate")),
            "auto_promote_sequence": [safe_id(str(s)) for s in (qg.get("auto_promote_sequence") or ["canary", "active"])],
            "installable_statuses": [safe_id(str(s)) for s in (qg.get("installable_statuses") or ["active"])],
        }

    def apply_quality_gate(self, meta: dict) -> dict:
        qg = self._quality_gate_policy()
        stage = safe_id(qg.get("default_stage_on_build", "candidate"))
        patched = dict(meta or {})
        patched["status"] = stage
        patched["quality_stage"] = stage
        patched["quality_updated_at"] = now_iso()
        return patched

    def is_installable(self, skill_id: str) -> bool:
        lock = read_skill_lock()
        item = (lock.get("skills", {}) or {}).get(safe_id(skill_id), {})
        status = safe_id(str(item.get("status", "")))
        qg = self._quality_gate_policy()
        return status in qg.get("installable_statuses", ["active"])

    def ensure_lock_for_existing_skill(self, skill_id: str):
        sid = safe_id(skill_id)
        lock = read_skill_lock()
        if sid in (lock.get("skills", {}) or {}): return
        reg = self._read_registry()
        item = (reg.get("skills", {}) or {}).get(sid, {})
        lock_skill_state(sid, {"version": item.get("version", "1.0.0"), "status": item.get("status", "active")})

    def register_built(self, meta: dict, skill_dir: str):
        gated = self.apply_quality_gate(meta)
        reg = self._read_registry()
        reg["skills"][gated["id"]] = {
            "id": gated["id"], "name": gated.get("name"), "status": gated.get("status"),
            "version": gated.get("version"), "capabilities": gated.get("capabilities", []),
            "path": to_portable_path(os.path.join(skill_dir, "skill.py")),
            "meta_path": to_portable_path(os.path.join(skill_dir, "meta.yaml")),
            "updated_at": now_iso(), "last_test_ok": bool(gated.get("last_test_ok", False)),
        }
        self._write_registry(reg)
        lock_skill_state(gated["id"], gated)

    def workflow_apply(self, metas: list[dict]):
        wf = read_yaml(WORKFLOW_PATH)
        wf.setdefault("capability_to_skill", {})
        mapping = wf["capability_to_skill"]
        for meta in metas:
            sid = meta["id"]
            for cap in meta.get("capabilities", []):
                k = safe_id(str(cap))
                mapping.setdefault(k, [])
                if sid not in mapping[k]: mapping[k].append(sid)
        wf["updated_at"] = now_iso()
        write_yaml(WORKFLOW_PATH, wf)




