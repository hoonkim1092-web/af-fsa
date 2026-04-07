from core.intent import IntentGate


class RequestRouter:
    """Routes a user request to the single-agent or project pipeline."""

    PROJECT_HINTS = [
        "만들어줘",
        "만들기",
        "구축",
        "설계",
        "프로젝트",
        "플랫폼",
        "서비스",
        "시스템",
        "게임",
        "앱",
        "웹사이트",
        "대시보드",
        "처음부터",
        "end to end",
        "from scratch",
        "build ",
        "create ",
        "greenfield",
    ]
    SINGLE_HINTS = [
        "버그",
        "fix",
        "고쳐",
        "색",
        "문구",
        "rename",
        "readme",
        "설명",
        "질문",
    ]
    MULTI_ROLE_HINTS = [
        "frontend",
        "backend",
        "qa",
        "design",
        "ui",
        "ux",
        "api",
        "db",
        "game",
    ]

    def __init__(self, classifier: IntentGate | None = None):
        self.classifier = classifier or IntentGate()

    def _keyword_score(self, task_input: str, keywords: list[str]) -> int:
        text = (task_input or "").lower()
        return sum(1 for kw in keywords if kw in text)

    def route(self, task_input: str, role_spec: str = "", pipeline_mode: str = "auto") -> dict:
        mode = (pipeline_mode or "auto").strip().lower()
        if mode == "project":
            risk_level = self._assess_risk(task_input, "greenfield", 4, "project")
            return {
                "pipeline": "project",
                "intent": "forced",
                "confidence": 100,
                "reasoning": "pipeline_mode=project",
                "risk_level": risk_level,
            }
        if mode == "single":
            return {
                "pipeline": "single",
                "intent": "forced",
                "confidence": 100,
                "reasoning": "pipeline_mode=single",
                "risk_level": "normal",
            }

        task_text = (task_input or "").strip()
        explicit_role = (role_spec or "").strip().lower()
        intent_result = self.classifier.classify(task_text) if task_text else {
            "intent": "trivial",
            "confidence": 0,
            "reasoning": "empty task",
        }

        intent = str(intent_result.get("intent", "trivial")).strip().lower() or "trivial"
        confidence = int(intent_result.get("confidence", 0) or 0)
        reasoning = str(intent_result.get("reasoning", "")).strip()

        project_hint_score = self._keyword_score(task_text, self.PROJECT_HINTS)
        project_score = 0
        project_score += project_hint_score * 2
        project_score += self._keyword_score(task_text, self.MULTI_ROLE_HINTS)
        project_score -= self._keyword_score(task_text, self.SINGLE_HINTS)

        if len(task_text) >= 24:
            project_score += 1
        if any(token in task_text.lower() for token in (" and ", " 및 ", "하면서 ", "포함", "with ")):
            project_score += 1
        if explicit_role and explicit_role not in {"general", "general assistant"}:
            project_score -= 1
        if intent in {"greenfield", "refactoring"} and (project_hint_score > 0 or len(task_text) >= 40):
            project_score += 2

        pipeline = "project" if project_score >= 2 else "single"
        risk_level = self._assess_risk(task_text, intent, project_score, pipeline)
        route_reason = reasoning or "heuristic fallback"
        route_reason = f"{route_reason}; score={project_score}; role={explicit_role or 'auto'}"
        return {
            "pipeline": pipeline,
            "intent": intent,
            "confidence": confidence,
            "reasoning": route_reason,
            "risk_level": risk_level,
        }

    @staticmethod
    def _assess_risk(task_text: str, intent: str, project_score: int, pipeline: str) -> str:
        """태스크 복잡도 기반 risk_level 결정. normal / elevated / strict."""
        if pipeline != "project":
            return "normal"
        text = (task_text or "").lower()
        score = 0
        # greenfield/refactoring은 기본 위험도 상승
        if intent in {"greenfield", "refactoring"}:
            score += 1
        # 긴 요구사항 = 복잡한 프로젝트
        if len(task_text) >= 80:
            score += 1
        # 데이터/보안/인프라 관련 키워드
        if any(kw in text for kw in ("데이터", "통계", "분석", "크롤", "api", "보안", "인증", "배포", "data", "security", "deploy")):
            score += 1
        # 멀티 도메인 (frontend+backend, 데이터+UI 등)
        if project_score >= 4:
            score += 1
        if score >= 3:
            return "strict"
        if score >= 1:
            return "elevated"
        return "normal"
