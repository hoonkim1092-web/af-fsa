"""Quick E2E test for the Hound-Librarian pipeline."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env 로드
from pathlib import Path
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

print("=" * 60)
print("Phase 1: Tavily Web Search Test")
print("=" * 60)

from core.web_search import tavily_search, extract_urls

results = tavily_search("AI Agent Architecture 2026", max_results=3)
for r in results:
    print(f"  [{r['score']:.2f}] {r['title'][:60]}")
    print(f"         {r['url'][:80]}")

urls = extract_urls(results)
print(f"\nURLs collected: {len(urls)}")

if not urls:
    print("No URLs found. Check TAVILY_API_KEY.")
    sys.exit(1)

print("\n" + "=" * 60)
print("Phase 2: NotebookLM Notebook Create + Source Injection")
print("=" * 60)

from core.research_engine import create_notebook, inject_sources

notebook_id = create_notebook("Test: AI Agent Architecture 2026")
if notebook_id:
    result = inject_sources(notebook_id, urls[:2], delay=2.0)
    print(f"Injection result: {result}")
else:
    print("Notebook creation failed (may need `nlm login`). Skipping Phase 2.")

print("\n" + "=" * 60)
print("Phase 3: NotebookLM Query (Deep Analysis)")
print("=" * 60)

from core.research_engine import query_notebooklm

if notebook_id:
    insight = query_notebooklm("Summarize the key findings about AI Agent Architecture.", notebook_id)
    print(f"Insight preview: {(insight or '(empty)')[:500]}")
else:
    print("Skipped (no notebook).")

print("\n" + "=" * 60)
print("ALL PHASES COMPLETE")
print("=" * 60)
