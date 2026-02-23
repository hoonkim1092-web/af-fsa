import os
import json
import urllib.request
import urllib.error
import google.generativeai as genai
from dotenv import load_dotenv

# Ensure .env is loaded BEFORE accessing env vars
load_dotenv()

# --- Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Auto-detect Project ID from current directory name
# e.g., "d:\agent-factory" -> "agent-factory"
try:
    PROJECT_ID = os.path.basename(os.getcwd())
except:
    PROJECT_ID = "default"

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

EMBEDDING_MODEL = "models/gemini-embedding-001"

class CortexClient:
    def __init__(self):
        self.sb_url = SUPABASE_URL
        self.sb_key = SUPABASE_KEY
        self.headers = {
            "apikey": self.sb_key,
            "Authorization": f"Bearer {self.sb_key}",
            "Content-Type": "application/json"
        }

    def embed(self, text):
        """Generate embedding using Google Gemini."""
        if not GOOGLE_API_KEY:
             raise ValueError("GOOGLE_API_KEY not set")
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="retrieval_document",
            output_dimensionality=768
        )
        return result['embedding']

    def embed_query(self, text):
        """Generate embedding for query."""
        if not GOOGLE_API_KEY:
             raise ValueError("GOOGLE_API_KEY not set")
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type="retrieval_query",
            output_dimensionality=768
        )
        return result['embedding']

    def save_memory(self, content, metadata):
        """Save a new memory (experience) to Supabase."""
        if not self.sb_url or not self.sb_key:
            return {"ok": False, "error": "Supabase credentials missing"}

        try:
            vector = self.embed(content)
            payload = {
                "content": content,
                "metadata": {**metadata, "project_id": PROJECT_ID},
                "embedding": vector
            }
            
            url = f"{self.sb_url}/rest/v1/cortex_memory"
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode('utf-8'), 
                headers=self.headers, 
                method='POST'
            )
            
            with urllib.request.urlopen(req) as response:
                if response.status in (200, 201):
                    return {"ok": True}
                else:
                    return {"ok": False, "status": response.status}

        except Exception as e:
            return {"ok": False, "error": str(e)}

    def recall(self, query, threshold=0.7, limit=4):
        """Retrieve relevant memories."""
        if not self.sb_url or not self.sb_key:
            return {"ok": False, "error": "Supabase credentials missing"}

        try:
            query_vector = self.embed_query(query)
            payload = {
                "query_embedding": query_vector,
                "match_threshold": threshold,
                "match_count": limit,
                "filter": {"project_id": PROJECT_ID}
            }
            
            url = f"{self.sb_url}/rest/v1/rpc/match_cortex_memory"
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode('utf-8'), 
                headers=self.headers, 
                method='POST'
            )
            
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                return {"ok": True, "results": data}

        except Exception as e:
            return {"ok": False, "error": str(e)}

# --- Interface for Agent ---

def propose(ctx):
    return {
        "name": "cortex",
        "description": "Manage long-term memory and learn from experiences.",
        "actions": [
            {
                "name": "recall",
                "description": "Search for past experiences relevant to the current task.",
                "args": {
                    "query": "string (Required) What to search for?"
                }
            },
            {
                "name": "save",
                "description": "Save a new experience (Input -> Output) for future reference.",
                "args": {
                    "input": "string (Required) The trigger/context (e.g., user request)",
                    "output": "string (Required) The successful response/action",
                    "explanation": "string (Optional) Why was this correct?"
                }
            }
        ]
    }

def apply(ctx):
    """
    ctx: {
        "action": "recall" | "save",
        "args": { ... }
    }
    """
    action = ctx.get("action")
    args = ctx.get("args", {})
    client = CortexClient()

    if action == "recall":
        query = args.get("query")
        if not query:
            return {"ok": False, "error": "Missing query"}
        return client.recall(query)

    elif action == "save":
        inp = args.get("input")
        out = args.get("output")
        expl = args.get("explanation", "")
        
        if not inp or not out:
            return {"ok": False, "error": "Missing input or output"}
            
        metadata = {
            "output": out,
            "explanation": expl,
            "agent": ctx.get("agent_role", "unknown")
        }
        return client.save_memory(inp, metadata)

    else:
        return {"ok": False, "error": f"Unknown action: {action}"}

def test(ctx):
    """
    Self-test function.
    """
    # Dry run check
    if not SUPABASE_URL:
        return {"ok": True, "status": "skipped", "reason": "No Supabase URL"}

    client = CortexClient()
    
    # 1. Embed Test
    try:
        vec = client.embed("Hello Cortex")
        if not vec or len(vec) != 768:
             return {"ok": False, "reason": f"Embedding failed or wrong dimension: {len(vec) if vec else 'None'}"}
    except Exception as e:
        return {"ok": False, "reason": f"Embedding error: {e}"}

    # 2. Connection Test (Simple Recall)
    # We won't save during test to avoid polluting DB, unless we want to.
    # Just try recall.
    res = client.recall("test")
    if not res.get("ok"):
         return {"ok": False, "reason": f"Recall failed: {res.get('error')}"}

    return {"ok": True, "message": "Cortex is online and connected."}

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    # Mock context
    ctx = {
        "agent_role": "tester",
        "action": "recall",
        "args": {"query": "hello"}
    }
    
    print(f"Project ID: {PROJECT_ID}")
    
    print("🧪 Cortex Connection Test...")
    try:
        # 1. Test Self-check
        result = test(ctx)
        print(f"Self-Check Result: {json.dumps(result, indent=2)}")
        
        if result.get("ok"):
            print("\n✅ Cortex is OPERATIONAL.")
        else:
            print("\n❌ Cortex is OFFLINE.")
            
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")
