import os
import subprocess
from dataclasses import dataclass, field

from core.external_skill_candidate_importer import discover_external_candidates
from core.config_paths import EXTERNAL_CACHE_DIR
from core.external_skill_source_ids import (
    DEFAULT_EXTERNAL_SOURCE_PRIORITY,
    normalize_external_source_id,
)
from core.utils import resolve_skill_paths, safe_id


def _split_csv(raw: str | None) -> list[str]:
    items: list[str] = []
    for part in str(raw or "").split(","):
        stripped = part.strip()
        if not stripped:
            continue
        normalized = normalize_external_source_id(stripped, default="")
        if normalized:
            items.append(normalized)
    return items


def _split_raw_csv(raw: str | None) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _normalize_urls(raw_urls) -> list[str]:
    if isinstance(raw_urls, list):
        return [str(item).strip() for item in raw_urls if str(item).strip()]
    if isinstance(raw_urls, str) and raw_urls.strip():
        return [raw_urls.strip()]
    return []


def _source_priority(project_policies: dict, discovered_source_ids: list[str]) -> list[str]:
    policy_raw = project_policies.get("external_skill_source_priority", [])
    policy_priority = []
    if isinstance(policy_raw, list):
        policy_priority = [
            normalize_external_source_id(str(item), default="")
            for item in policy_raw
            if normalize_external_source_id(str(item), default="")
        ]

    env_priority = _split_csv(os.getenv("AGENT_EXTERNAL_SKILL_SOURCE_PRIORITY"))
    desired = env_priority or policy_priority
    if not desired:
        desired = list(DEFAULT_EXTERNAL_SOURCE_PRIORITY[:-1])

    ordered: list[str] = []
    for source_id in desired + discovered_source_ids:
        sid = normalize_external_source_id(source_id, default="")
        if sid and sid not in ordered:
            ordered.append(sid)
    return ordered


def _repo_source_configs(project_policies: dict) -> list[dict]:
    configs: list[dict] = []
    seen_ids: set[str] = set()

    def add_repo_source(source_id: str, urls: list[str]):
        sid = normalize_external_source_id(source_id, default="")
        if not sid:
            return
        normalized_urls = [url for url in urls if str(url).strip()]
        if not normalized_urls and sid in seen_ids:
            return
        if sid in seen_ids:
            for item in configs:
                if item["id"] == sid:
                    item["urls"] = list(dict.fromkeys(item["urls"] + normalized_urls))
                    return
        configs.append({"id": sid, "kind": "repo_cache", "urls": normalized_urls})
        seen_ids.add(sid)

    raw_policy_sources = project_policies.get("external_skill_sources", [])
    if isinstance(raw_policy_sources, list):
        for index, entry in enumerate(raw_policy_sources):
            if isinstance(entry, str) and entry.strip():
                repo_name = safe_id(os.path.basename(entry).replace(".git", "")) or f"external_{index + 1}"
                add_repo_source(repo_name, [entry.strip()])
                continue
            if not isinstance(entry, dict):
                continue
            kind = safe_id(str(entry.get("kind") or "repo_cache"))
            if kind != "repo_cache":
                continue
            source_id = normalize_external_source_id(str(entry.get("id") or ""), default="")
            urls = _normalize_urls(entry.get("urls") or entry.get("url"))
            if not source_id:
                source_id = normalize_external_source_id(
                    os.path.basename(urls[0]).replace(".git", "") if urls else "",
                    default="",
                )
            add_repo_source(source_id or f"external_{index + 1}", urls)

    claude_urls = _split_raw_csv(os.getenv("AGENT_EXTERNAL_SKILL_CLAUDE_REPOS"))
    if claude_urls:
        add_repo_source("claude_repo", claude_urls)

    codex_urls = _split_raw_csv(os.getenv("AGENT_EXTERNAL_SKILL_CODEX_REPOS"))
    if codex_urls:
        add_repo_source("codex_repo", codex_urls)

    external_urls = _split_raw_csv(os.getenv("AGENT_EXTERNAL_SKILL_REPOS"))
    if external_urls:
        add_repo_source("external", external_urls)
    return configs


@dataclass
class ExternalSkillCandidate:
    source_id: str
    skill_id: str
    path: str
    name: str = ""
    capabilities: list[str] = field(default_factory=list)
    source_url: str = ""
    source_repo: str = ""


class ExternalSkillSource:
    def __init__(self, source_id: str):
        self.source_id = normalize_external_source_id(source_id)

    def prepare(self):
        return None

    def iter_candidates(self) -> list[ExternalSkillCandidate]:
        return []


class ManifestSkillSource(ExternalSkillSource):
    def __init__(self, source_id: str, candidates: list[ExternalSkillCandidate]):
        super().__init__(source_id)
        self._candidates = list(candidates)

    def iter_candidates(self) -> list[ExternalSkillCandidate]:
        return list(self._candidates)


class RepoCacheSkillSource(ExternalSkillSource):
    def __init__(self, source_id: str, repo_urls: list[str]):
        super().__init__(source_id)
        self.repo_urls = [str(url).strip() for url in (repo_urls or []) if str(url).strip()]
        self.root_dir = os.path.join(EXTERNAL_CACHE_DIR, self.source_id)
        self._prepare_errors: list[str] = []

    def prepare(self):
        self._prepare_errors = []
        os.makedirs(self.root_dir, exist_ok=True)
        for index, url in enumerate(self.repo_urls):
            repo_name = safe_id(os.path.basename(url).replace(".git", "")) or f"repo_{index + 1}"
            dst = os.path.join(self.root_dir, repo_name)
            try:
                if os.path.exists(dst):
                    result = subprocess.run(
                        ["git", "-C", dst, "pull", "--ff-only"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                else:
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", url, dst],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )
                if int(getattr(result, "returncode", 0) or 0) != 0:
                    detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
                    if detail:
                        detail = detail[:160]
                    else:
                        detail = f"command_failed:{getattr(result, 'returncode', 'unknown')}"
                    self._prepare_errors.append(f"{repo_name}:{detail}")
            except Exception as exc:
                self._prepare_errors.append(f"{repo_name}:{type(exc).__name__}:{str(exc)[:160]}")
                continue

    def _discover_candidates(self, *, scan_python: bool) -> list[ExternalSkillCandidate]:
        root_dir = os.path.abspath(os.path.join(EXTERNAL_CACHE_DIR, os.pardir, os.pardir))
        discovered, _duplicates = discover_external_candidates(
            root_dir=root_dir,
            cache_dir=EXTERNAL_CACHE_DIR,
            source_ids=[self.source_id],
            scan_python=scan_python,
        )
        out: list[ExternalSkillCandidate] = []
        for item in discovered.values():
            if not isinstance(item, dict):
                continue
            if normalize_external_source_id(str(item.get("source_id") or ""), default="") != self.source_id:
                continue
            skill_id = safe_id(str(item.get("id") or ""))
            path = str(item.get("path") or "").strip()
            if not skill_id or not path:
                continue
            out.append(
                ExternalSkillCandidate(
                    source_id=self.source_id,
                    skill_id=skill_id,
                    name=str(item.get("name") or skill_id),
                    path=path,
                    capabilities=[safe_id(str(x)) for x in (item.get("capabilities") or []) if safe_id(str(x))],
                    source_url=str(item.get("source_url") or ""),
                    source_repo=str(item.get("source_repo") or ""),
                )
            )
        return out

    def iter_candidates(self) -> list[ExternalSkillCandidate]:
        out = self._discover_candidates(scan_python=False)
        if not out:
            out = self._discover_candidates(scan_python=True)
        if not out and self._prepare_errors:
            raise RuntimeError("; ".join(self._prepare_errors[:3]))
        return out


class CacheSweepSkillSource(ExternalSkillSource):
    def __init__(self, source_id: str = "external_cache"):
        super().__init__(source_id)
        self.root_dir = EXTERNAL_CACHE_DIR

    def iter_candidates(self) -> list[ExternalSkillCandidate]:
        root_dir = os.path.abspath(os.path.join(EXTERNAL_CACHE_DIR, os.pardir, os.pardir))
        discovered, _duplicates = discover_external_candidates(
            root_dir=root_dir,
            cache_dir=EXTERNAL_CACHE_DIR,
            source_ids=None,
            scan_python=True,
        )
        out: list[ExternalSkillCandidate] = []
        for item in discovered.values():
            if not isinstance(item, dict):
                continue
            skill_id = safe_id(str(item.get("id") or ""))
            path = str(item.get("path") or "").strip()
            if not skill_id or not path:
                continue
            out.append(
                ExternalSkillCandidate(
                    source_id=self.source_id,
                    skill_id=skill_id,
                    name=str(item.get("name") or skill_id),
                    path=path,
                    capabilities=[safe_id(str(x)) for x in (item.get("capabilities") or []) if safe_id(str(x))],
                    source_url=str(item.get("source_url") or ""),
                    source_repo=str(item.get("source_repo") or ""),
                )
            )
        return out


class ExternalSkillResolver:
    def __init__(
        self,
        *,
        project_policies: dict | None,
        install_candidates: list[dict],
        install_fn,
        path_resolver,
        match_fn,
    ):
        self.project_policies = project_policies if isinstance(project_policies, dict) else {}
        self.install_candidates = install_candidates if isinstance(install_candidates, list) else []
        self.install_fn = install_fn
        self.path_resolver = path_resolver
        self.match_fn = match_fn

    def _build_sources(self) -> list[ExternalSkillSource]:
        sources: list[ExternalSkillSource] = []
        grouped_manifest: dict[str, list[ExternalSkillCandidate]] = {}
        for item in self.install_candidates:
            if not isinstance(item, dict):
                continue
            source_id = normalize_external_source_id(str(item.get("source_id") or "registry"), default="registry")
            skill_id = safe_id(str(item.get("id") or ""))
            path = str(item.get("path") or "").strip()
            if not skill_id or not path:
                continue
            grouped_manifest.setdefault(source_id, []).append(
                ExternalSkillCandidate(
                    source_id=source_id,
                    skill_id=skill_id,
                    name=str(item.get("name") or skill_id),
                    path=path,
                    capabilities=[safe_id(str(x)) for x in (item.get("capabilities") or []) if safe_id(str(x))],
                    source_url=str(item.get("source_url") or ""),
                    source_repo=str(item.get("source_repo") or ""),
                )
            )
        for source_id, candidates in grouped_manifest.items():
            sources.append(ManifestSkillSource(source_id, candidates))

        repo_configs = _repo_source_configs(self.project_policies)
        for cfg in repo_configs:
            sources.append(RepoCacheSkillSource(str(cfg.get("id") or "external"), cfg.get("urls") or []))

        repo_source_ids = {
            normalize_external_source_id(str(cfg.get("id") or ""), default="")
            for cfg in repo_configs
        }
        if "external_cache" in _source_priority(self.project_policies, list(repo_source_ids) + ["external_cache"]):
            sources.append(CacheSweepSkillSource())
        return sources

    def _target_preferences(self, need_id: str, evidence_pack: dict | None) -> list[str]:
        if not isinstance(evidence_pack, dict):
            return []
        targets = evidence_pack.get("targets", {})
        if not isinstance(targets, dict):
            return []
        target = targets.get(need_id, {})
        if not isinstance(target, dict):
            return []

        preferred: list[str] = []
        top_candidate = safe_id(str(target.get("top_candidate") or ""))
        if top_candidate:
            preferred.append(top_candidate)
        for item in target.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            candidate_id = safe_id(str(item.get("candidate_skill_id") or item.get("id") or ""))
            if candidate_id and candidate_id not in preferred:
                preferred.append(candidate_id)
        return preferred

    def _candidate_score(
        self,
        need_id: str,
        candidate: ExternalSkillCandidate,
        preferred_ids: list[str],
    ) -> int:
        candidate_id = safe_id(candidate.skill_id)
        if candidate_id in preferred_ids:
            index = preferred_ids.index(candidate_id)
            return max(120 - (index * 10), 80)

        fields = [candidate.skill_id, candidate.name] + list(candidate.capabilities)
        best = 0
        for text in fields:
            best = max(best, int(self.match_fn(need_id, text)))
        return best

    def resolve_and_install(
        self,
        needs: list[str],
        reqs: dict | None = None,
        evidence_pack: dict | None = None,
    ) -> dict:
        del reqs
        results: dict[str, dict] = {}
        installed: dict[str, str] = {}
        normalized_needs = [safe_id(str(need)) for need in (needs or []) if safe_id(str(need))]
        if not normalized_needs:
            return {"installed": installed, "results": results, "source_order": []}

        sources = self._build_sources()
        discovered_ids = list(dict.fromkeys([src.source_id for src in sources if src.source_id]))
        source_order = _source_priority(self.project_policies, discovered_ids)

        candidates_by_source: dict[str, list[ExternalSkillCandidate]] = {sid: [] for sid in source_order}
        source_errors: dict[str, str] = {}
        for source in sources:
            try:
                source.prepare()
                candidates = source.iter_candidates()
            except Exception as exc:
                source_errors[source.source_id] = f"{type(exc).__name__}: {str(exc)[:160]}"
                continue
            if source.source_id not in candidates_by_source:
                candidates_by_source[source.source_id] = []
            candidates_by_source[source.source_id].extend(candidates)

        for need_id in normalized_needs:
            if resolve_skill_paths(need_id)[0]:
                installed[need_id] = need_id
                results[need_id] = {
                    "need_id": need_id,
                    "installed_skill_id": need_id,
                    "installed_from": "local",
                    "attempts": [],
                }
                continue

            preferred_ids = self._target_preferences(need_id, evidence_pack)
            attempts: list[dict] = []
            installed_from = ""
            for source_id in source_order:
                ranked: list[tuple[int, ExternalSkillCandidate, str | None]] = []
                for candidate in candidates_by_source.get(source_id, []):
                    resolved_path = self.path_resolver(candidate.path)
                    if not resolved_path:
                        continue
                    score = self._candidate_score(need_id, candidate, preferred_ids)
                    if score <= 0:
                        continue
                    ranked.append((score, candidate, resolved_path))

                if not ranked:
                    if source_id in source_errors:
                        attempts.append(
                            {
                                "source_id": source_id,
                                "status": "source_error",
                                "reason": source_errors[source_id],
                            }
                        )
                    else:
                        attempts.append({"source_id": source_id, "status": "miss", "reason": "no_candidate"})
                    continue

                ranked.sort(key=lambda item: item[0], reverse=True)
                score, candidate, resolved_path = ranked[0]
                ok, detail = self.install_fn(need_id, resolved_path or "", source_label=source_id)
                attempts.append(
                    {
                        "source_id": source_id,
                        "status": "installed" if ok else "install_failed",
                        "candidate_id": candidate.skill_id,
                        "score": score,
                        "reason": "" if ok else str(detail),
                    }
                )
                if ok:
                    installed[need_id] = need_id
                    installed_from = source_id
                    break

            results[need_id] = {
                "need_id": need_id,
                "installed_skill_id": installed.get(need_id, ""),
                "installed_from": installed_from,
                "attempts": attempts,
            }

        return {
            "installed": installed,
            "results": results,
            "source_order": source_order,
        }
