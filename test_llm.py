from core.llm_engine import LLMEngine
from core.agent_runner import ModelRouter

mr = ModelRouter()
engine_id = mr.pick('orchestrator')
print('Picked orchestrator model:', engine_id)

llm = LLMEngine(model_name=engine_id)
print('Execution result:', llm._execute_with_retry('Respond with JSON ONLY: {"test": 1}'))
print('Parsed JSON:', llm.generate_json('Respond with JSON ONLY: {"test": 1}'))
