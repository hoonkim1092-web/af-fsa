import json
import google.generativeai as genai
from config.schema import factory_config
from core.llm_engine import LLMEngine

class IntentGate:
    """
    V2.0 IntentGate Classifier
    Prior to any agent execution, categorizes the task to determine
    if a structured TODO plan is required (e.g., greenfield, refactoring)
    or if it can proceed immediately (e.g., trivial, question).
    """
    
    INTENT_CATEGORIES = [
        "trivial",      # Typo fixes, simple edits
        "question",     # Codebase queries
        "refactoring",  # Structural changes, moving code
        "greenfield",   # New features, new files
        "debugging"     # Fixing issues, investigating logs
    ]

    def __init__(self):
        self._llm = LLMEngine(model_name="gemini-2.0-flash")
    
    def classify(self, task_input: str, context: str = "") -> dict:
        """
        Classifies the user task.
        Returns a dict: {"intent": "...", "confidence": 0-100, "reasoning": "..."}
        """
        prompt = f"""
You are a strict task classifier. Only output JSON.

Analyze the following user task and classify its intent into exactly ONE of the following categories:
{', '.join(self.INTENT_CATEGORIES)}

Task: {task_input}
Context: {context}

Respond in pure JSON format:
{{
    "intent": "category_name",
    "confidence": 95,
    "reasoning": "Brief explanation of why"
}}
        """
        
        try:
            # We use gemini-2.0-flash for fast, cheap classification
            response = self._llm.generate(
                prompt=prompt
            )
            
            # Clean up potential markdown formatting
            text = response.replace("```json", "").replace("```", "").strip()
            result = json.loads(text)
            
            if result.get("intent") not in self.INTENT_CATEGORIES:
                result["intent"] = "question" # Safe fallback
                
            return result
            
        except Exception as e:
            print(f"[IntentGate] Warning - LLM Classification failed: {e}")
            # Fallback to simple keyword heuristics
            lower_task = task_input.lower()
            if "fix" in lower_task or "error" in lower_task or "bug" in lower_task:
                intent = "debugging"
            elif "refactor" in lower_task or "move" in lower_task or "extract" in lower_task:
                intent = "refactoring"
            elif "create" in lower_task or "new" in lower_task or "add" in lower_task:
                intent = "greenfield"
            else:
                intent = "trivial"
                
            return {
                "intent": intent, 
                "confidence": 50, 
                "reasoning": "Keyword fallback due to LLM failure"
            }
