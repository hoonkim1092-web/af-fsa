import sys
import os
import agent_launcher
from agent_launcher import HimariResearchAgent, ModelRouter

# Reconfigure stdout to utf-8
sys.stdout.reconfigure(encoding='utf-8')

class MockModelRouter:
    def pick(self, stage):
        return "models/gemini-2.0-flash"

def test_notebook_query():
    print("Testing Himari NotebookLM Query...")
    mr = MockModelRouter()
    himari = HimariResearchAgent(mr)
    
    # Test 1: Direct Query
    query = "What is the core philosophy of Google Antigravity?"
    print(f"Querying: {query}")
    try:
        result = himari._query_notebooklm(query)
        print("-" * 20)
        print("Result:", result[:200] + "..." if len(result) > 200 else result)
        print("-" * 20)
        
        if not result:
            print("⚠️ No result returned. (Is authentication done?)")
        else:
            print("✅ Query successful.")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_notebook_query()
