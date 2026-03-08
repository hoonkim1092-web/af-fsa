from core.request_router import RequestRouter


class _Classifier:
    def __init__(self, intent: str, confidence: int = 90):
        self.intent = intent
        self.confidence = confidence

    def classify(self, task_input: str, context: str = "") -> dict:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reasoning": f"forced:{self.intent}",
        }


def test_router_selects_project_pipeline_for_complex_build_request():
    router = RequestRouter(_Classifier("greenfield"))

    result = router.route("포커 게임 만들어줘", role_spec="General", pipeline_mode="auto")

    assert result["pipeline"] == "project"
    assert result["intent"] == "greenfield"


def test_router_keeps_single_pipeline_for_simple_fix_request():
    router = RequestRouter(_Classifier("debugging"))

    result = router.route("버튼 색만 고쳐줘", role_spec="Deadbyte", pipeline_mode="auto")

    assert result["pipeline"] == "single"
    assert result["intent"] == "debugging"
