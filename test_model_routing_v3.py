import os
from unittest.mock import patch

# Set API keys for testing
os.environ["OPENAI_API_KEY"] = "fake-key"
os.environ["GOOGLE_API_KEY"] = "fake-key"
os.environ["ANTHROPIC_API_KEY"] = "fake-key"

from core.agent_runner import AgentRunner

class DummyModelInfo:
    def __init__(self, model):
        self.model = model

def test_ai_funnel_routing():
    runner = AgentRunner()
    
    with patch("core.agent_runner.resolve_dynamic_model") as mock_resolve:
        # Mock the lightweight resolution
        mock_resolve.return_value = DummyModelInfo(model="gemini-2.0-flash-lite")
        
        # Test 1: Simple Task -> Should route to lightweight
        print("\n[Test 1] Simple Task (Typo check)")
        agent = {"name": "test_agent", "role": "assistant"}
        with patch.object(runner, "_resolve_system_prompt", return_value="sys"):
            with patch.object(runner.mr, "pick", wraps=runner.mr.pick) as mock_pick:
                runner.run(agent, "오타 수정해줘")
                
                # Check if is_complex was False
                mock_pick.assert_called_with("chat", agent_config=agent, is_complex=False)
                # Check if resolve_dynamic_model("lightweight") was called
                mock_resolve.assert_called_with("lightweight")
                print("✅ Pass: Routed to lightweight model for simple task.")

        # Reset mocks
        mock_resolve.reset_mock()

        # Test 2: Complex Task -> Should route to standard model (not lightweight)
        print("\n[Test 2] Complex Task (Feature implementation)")
        with patch.object(runner, "_resolve_system_prompt", return_value="sys"):
            with patch.object(runner.mr, "pick", wraps=runner.mr.pick) as mock_pick:
                runner.run(agent, "결제 연동 모듈을 처음부터 끝까지 새로 구현해줘.")
                
                # Check if is_complex was True
                mock_pick.assert_called_with("chat", agent_config=agent, is_complex=True)
                # Ensure lightweight was NOT called
                mock_resolve.assert_not_called()
                print("✅ Pass: Maintained complex routing for feature implementation.")

        # Reset mocks
        mock_resolve.reset_mock()

        # Test 3: Role-based Override (Researcher) -> Should force complex even if task is simple
        print("\n[Test 3] Role Override (Researcher with simple task)")
        research_agent = {"name": "researcher", "role": "research"}
        with patch.object(runner, "_resolve_system_prompt", return_value="sys"):
            with patch.object(runner.mr, "pick", wraps=runner.mr.pick) as mock_pick:
                runner.run(research_agent, "요약해") # Short task
                
                # Check if is_complex was forced to True despite the short text
                mock_pick.assert_called_with("chat", agent_config=research_agent, is_complex=True)
                mock_resolve.assert_not_called()
                print("✅ Pass: Maintained complex routing for researcher role despite simple task.")

if __name__ == "__main__":
    test_ai_funnel_routing()
    print("\n🎉 All AI Funnel Routing tests passed!")
