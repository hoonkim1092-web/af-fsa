from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from core.config_paths import DATA_DIR, PROJECT_ID
from core.utils import now_iso, safe_id


FEEDBACK_FILENAME = "skill-usage.jsonl"
STAGE_WEIGHTS = {
    "draft": 0.35,
    "candidate": 0.55,
    "canary": 0.75,
    "active": 1.0,
    "archived": 0.10,
}


@dataclass
class SkillFeedbackEvent:
    event_type: str
    skill_id: str
    status: str
    project_id: str = ""
    run_id: str = ""
    agent_role: str = ""
    lifecycle_stage: str = ""
    source: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillFeedbackSummary:
    skill_id: str
    total_events: int = 0
    selection_count: int = 0
    build_passed: int = 0
    build_failed: int = 0
    runtime_succeeded: int = 0
    runtime_failed: int = 0
    current_stage: str = ""
    last_event_at: str = ""
    runtime_success_rate: float = 0.5
    build_success_rate: float = 0.5
    stage_weight: float = 0.5
    usage_signal: float = 0.0
    historical_score: int = 50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillFeedbackLoop:
    """Persist lifecycle feedback as append-only JSONL events."""

    def __init__(self, feedback_path: str | None = None, *, project_id: str = ""):
        raw_project_id = str(project_id or PROJECT_ID or "project").strip()
        self.project_id = safe_id(raw_project_id if raw_project_id else "project")
        self.feedback_path = os.path.abspath(feedback_path or os.path.join(DATA_DIR, FEEDBACK_FILENAME))
        self._events_cache: list[dict[str, Any]] | None = None
        self._events_cache_key: tuple[Any, ...] | None = None

    @classmethod
    def for_workspace(cls, workspace: str | None = None, *, project_id: str = "") -> "SkillFeedbackLoop":
        if workspace:
            normalized_workspace = os.path.abspath(workspace)
            feedback_path = os.path.join(normalized_workspace, "data", FEEDBACK_FILENAME)
            resolved_project_id = project_id or os.path.basename(normalized_workspace)
            return cls(feedback_path, project_id=resolved_project_id)
        return cls(project_id=project_id)

    def record_event(
        self,
        *,
        event_type: str,
        skill_id: str,
        status: str,
        run_id: str = "",
        agent_role: str = "",
        lifecycle_stage: str = "",
        source: str = "",
        payload: dict[str, Any] | None = None,
    ) -> SkillFeedbackEvent:
        event = SkillFeedbackEvent(
            event_type=event_type,
            skill_id=safe_id(skill_id),
            status=str(status or "").strip() or "recorded",
            project_id=self.project_id,
            run_id=str(run_id or "").strip(),
            agent_role=str(agent_role or "").strip(),
            lifecycle_stage=safe_id(lifecycle_stage) if lifecycle_stage else "",
            source=safe_id(source) if source else "",
            payload=payload if isinstance(payload, dict) else {},
        )
        self._append(event.to_dict())
        return event

    def record_selection(
        self,
        *,
        skill_id: str,
        decision_mode: str,
        status: str,
        run_id: str = "",
        agent_role: str = "",
        lifecycle_stage: str = "",
        candidate_skill_id: str = "",
        confidence: float = 0.0,
        score: float = 0.0,
        source: str = "skill_orchestrator",
        payload: dict[str, Any] | None = None,
    ) -> SkillFeedbackEvent:
        merged_payload = {
            "decision_mode": safe_id(decision_mode),
            "candidate_skill_id": safe_id(candidate_skill_id) if candidate_skill_id else "",
            "confidence": float(confidence or 0.0),
            "score": float(score or 0.0),
        }
        if isinstance(payload, dict):
            merged_payload.update(payload)
        return self.record_event(
            event_type="skill_selection",
            skill_id=skill_id,
            status=status,
            run_id=run_id,
            agent_role=agent_role,
            lifecycle_stage=lifecycle_stage,
            source=source,
            payload=merged_payload,
        )

    def record_build(
        self,
        *,
        skill_id: str,
        ok: bool,
        run_id: str = "",
        agent_role: str = "",
        lifecycle_stage: str = "",
        source: str = "builder",
        payload: dict[str, Any] | None = None,
    ) -> SkillFeedbackEvent:
        return self.record_event(
            event_type="skill_build",
            skill_id=skill_id,
            status="passed" if ok else "failed",
            run_id=run_id,
            agent_role=agent_role,
            lifecycle_stage=lifecycle_stage,
            source=source,
            payload=payload,
        )

    def record_runtime_result(
        self,
        *,
        skill_id: str,
        ok: bool,
        run_id: str = "",
        agent_role: str = "",
        lifecycle_stage: str = "",
        source: str = "agent_runner",
        payload: dict[str, Any] | None = None,
    ) -> SkillFeedbackEvent:
        return self.record_event(
            event_type="skill_runtime",
            skill_id=skill_id,
            status="succeeded" if ok else "failed",
            run_id=run_id,
            agent_role=agent_role,
            lifecycle_stage=lifecycle_stage,
            source=source,
            payload=payload,
        )

    def record_promotion(
        self,
        *,
        skill_id: str,
        status: str,
        from_stage: str,
        to_stage: str,
        run_id: str = "",
        source: str = "skill_promotion",
        payload: dict[str, Any] | None = None,
    ) -> SkillFeedbackEvent:
        merged_payload = {
            "from_stage": safe_id(from_stage),
            "to_stage": safe_id(to_stage),
        }
        if isinstance(payload, dict):
            merged_payload.update(payload)
        return self.record_event(
            event_type="skill_promotion",
            skill_id=skill_id,
            status=status,
            run_id=run_id,
            lifecycle_stage=to_stage,
            source=source,
            payload=merged_payload,
        )

    def iter_events(self, *, skill_id: str = "", event_type: str = "") -> list[dict[str, Any]]:
        skill_filter = safe_id(skill_id) if skill_id else ""
        event_filter = safe_id(event_type) if event_type else ""
        events: list[dict[str, Any]] = []
        for payload in self._load_events():
            if skill_filter and payload["skill_id"] != skill_filter:
                continue
            if event_filter and payload["event_type"] != event_filter:
                continue
            events.append(payload)
        return events

    def summarize_skills(self, skill_ids: list[str] | None = None) -> dict[str, SkillFeedbackSummary]:
        requested_ids: list[str] = []
        if skill_ids is not None:
            for raw_skill_id in skill_ids:
                normalized_skill_id = safe_id(str(raw_skill_id))
                if normalized_skill_id and normalized_skill_id not in requested_ids:
                    requested_ids.append(normalized_skill_id)
        requested_filter = set(requested_ids)
        summaries: dict[str, SkillFeedbackSummary] = {
            skill_id: SkillFeedbackSummary(skill_id=skill_id)
            for skill_id in requested_ids
        }
        for event in self._load_events():
            skill_id = str(event.get("skill_id") or "")
            if not skill_id:
                continue
            if requested_filter and skill_id not in requested_filter:
                continue
            summary = summaries.setdefault(skill_id, SkillFeedbackSummary(skill_id=skill_id))
            self._apply_event(summary, event)
        for summary in summaries.values():
            self._finalize_summary(summary)
        return summaries

    def summarize_skill(self, skill_id: str) -> SkillFeedbackSummary:
        normalized_skill_id = safe_id(skill_id)
        if not normalized_skill_id:
            return SkillFeedbackSummary(skill_id="")
        return self.summarize_skills([normalized_skill_id]).get(
            normalized_skill_id,
            SkillFeedbackSummary(skill_id=normalized_skill_id),
        )

    def _apply_event(self, summary: SkillFeedbackSummary, event: dict[str, Any]) -> None:
        summary.total_events += 1
        summary.last_event_at = str(event.get("ts") or summary.last_event_at)
        status = safe_id(str(event.get("status") or ""))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        lifecycle_stage = safe_id(
            str(
                event.get("lifecycle_stage")
                or payload.get("to_stage")
                or payload.get("current_stage")
                or ""
            )
        )
        if lifecycle_stage:
            summary.current_stage = lifecycle_stage

        event_type = str(event.get("event_type") or "")
        if event_type == "skill_selection":
            summary.selection_count += 1
        elif event_type == "skill_build":
            if status == "passed":
                summary.build_passed += 1
            elif status == "failed":
                summary.build_failed += 1
        elif event_type == "skill_runtime":
            if status == "succeeded":
                summary.runtime_succeeded += 1
            elif status == "failed":
                summary.runtime_failed += 1
        elif event_type == "skill_promotion" and not summary.current_stage:
            promoted_stage = safe_id(str(payload.get("to_stage") or ""))
            if promoted_stage:
                summary.current_stage = promoted_stage

    def _finalize_summary(self, summary: SkillFeedbackSummary) -> None:
        runtime_total = summary.runtime_succeeded + summary.runtime_failed
        build_total = summary.build_passed + summary.build_failed
        if runtime_total:
            summary.runtime_success_rate = round(summary.runtime_succeeded / runtime_total, 3)
        if build_total:
            summary.build_success_rate = round(summary.build_passed / build_total, 3)
        summary.stage_weight = float(STAGE_WEIGHTS.get(summary.current_stage, 0.5))
        summary.usage_signal = round(min(summary.runtime_succeeded + summary.build_passed, 5) / 5.0, 3)

        score = 50.0
        if runtime_total:
            score += (summary.runtime_success_rate - 0.5) * 40.0
        if build_total:
            score += (summary.build_success_rate - 0.5) * 20.0
        score += (summary.stage_weight - 0.5) * 30.0
        score += summary.usage_signal * 10.0
        if summary.current_stage == "archived":
            score -= 10.0
        elif summary.current_stage in {"canary", "active"} and runtime_total == 0 and build_total == 0:
            score += 10.0
        summary.historical_score = int(round(max(0.0, min(100.0, score))))

    def _load_events(self) -> list[dict[str, Any]]:
        cache_key = self._cache_key()
        if self._events_cache is not None and self._events_cache_key == cache_key:
            return [dict(event) for event in self._events_cache]
        if not os.path.exists(self.feedback_path):
            self._events_cache = []
            self._events_cache_key = cache_key
            return []
        events: list[dict[str, Any]] = []
        with open(self.feedback_path, "r", encoding="utf-8") as handle:
            for line in handle:
                payload = self._coerce_event(line)
                if payload:
                    events.append(payload)
        self._events_cache = events
        self._events_cache_key = cache_key
        return [dict(event) for event in events]

    def _cache_key(self) -> tuple[Any, ...]:
        if not os.path.exists(self.feedback_path):
            return ("missing", 0)
        stat = os.stat(self.feedback_path)
        return (int(getattr(stat, "st_mtime_ns", 0) or int(stat.st_mtime * 1_000_000_000)), int(stat.st_size))

    def _append(self, payload: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.feedback_path), exist_ok=True)
        with open(self.feedback_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._events_cache = None
        self._events_cache_key = None

    @staticmethod
    def _coerce_event(raw_line: str) -> dict[str, Any]:
        text = str(raw_line or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "event_type": safe_id(str(payload.get("event_type") or "")),
            "skill_id": safe_id(str(payload.get("skill_id") or "")),
            "status": str(payload.get("status") or "").strip(),
            "project_id": safe_id(str(payload.get("project_id") or "")) if payload.get("project_id") else "",
            "run_id": str(payload.get("run_id") or "").strip(),
            "agent_role": str(payload.get("agent_role") or "").strip(),
            "lifecycle_stage": safe_id(str(payload.get("lifecycle_stage") or "")) if payload.get("lifecycle_stage") else "",
            "source": safe_id(str(payload.get("source") or "")) if payload.get("source") else "",
            "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            "ts": str(payload.get("ts") or "").strip(),
        }
