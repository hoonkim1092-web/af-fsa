import argparse


def _analyze_query(query):
    q = str(query or "").strip()
    upper = q.upper()
    recommendations = []
    severity = "info"

    if not q:
        return {
            "ok": False,
            "reason": "empty_query",
            "recommendations": ["Provide a SQL statement in `query`."],
            "severity": "error",
        }

    if "SELECT *" in upper:
        severity = "warning"
        recommendations.append("Avoid `SELECT *`; select only required columns.")
    if "WHERE" not in upper and upper.startswith("SELECT"):
        severity = "warning"
        recommendations.append("Missing `WHERE` on SELECT can trigger full table scans.")
    if "ORDER BY" in upper and "LIMIT" not in upper:
        recommendations.append("Consider `LIMIT` with `ORDER BY` for bounded pagination.")

    if not recommendations:
        recommendations.append("No obvious anti-pattern detected with basic static checks.")

    return {"ok": True, "severity": severity, "query": q, "recommendations": recommendations}


def optimize_db(query):
    analysis = _analyze_query(query)
    if not analysis.get("ok"):
        print("[db_optimizer] invalid query input.")
        return analysis

    print(f"[db_optimizer] analyzing query: {analysis['query']}")
    for item in analysis.get("recommendations", []):
        print(f"- {item}")
    return analysis


def propose(ctx):
    query = str((ctx or {}).get("query") or "SELECT id FROM table WHERE id = ?")
    preview = _analyze_query(query)
    return {"ok": True, "skill": "db_optimizer", "operation": "analyze", "preview": preview}


def apply(ctx):
    query = str((ctx or {}).get("query") or "").strip()
    if not query:
        return {"ok": False, "reason": "missing_query", "hint": "Pass `query` in context."}
    result = optimize_db(query)
    return {"ok": bool(result.get("ok")), "result": result}


def test(ctx):
    sample = apply({"query": "SELECT * FROM users"})
    if not sample.get("ok"):
        return {"ok": False, "reason": "sample_analysis_failed", "detail": sample}
    recs = sample.get("result", {}).get("recommendations", [])
    return {"ok": any("SELECT *" in r for r in recs), "detail": sample}


def main():
    parser = argparse.ArgumentParser(description="Run lightweight SQL optimization checks.")
    parser.add_argument("query", help="SQL query to analyze")
    args = parser.parse_args()
    optimize_db(args.query)


if __name__ == "__main__":
    main()
