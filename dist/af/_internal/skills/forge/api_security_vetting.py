import argparse


def _security_checks(endpoint):
    target = str(endpoint or "").strip()
    if not target:
        return {
            "ok": False,
            "reason": "empty_endpoint",
            "checks": [],
            "risk_level": "error",
        }

    checks = [
        {"name": "auth_token", "status": "review", "message": "Verify auth token validation and expiry."},
        {"name": "rate_limit", "status": "review", "message": "Confirm per-user and per-IP throttling."},
        {"name": "cors", "status": "review", "message": "Restrict origins and methods to known clients."},
        {"name": "input_validation", "status": "review", "message": "Validate and sanitize all user input."},
    ]
    return {"ok": True, "endpoint": target, "checks": checks, "risk_level": "medium"}


def vet_api(endpoint):
    result = _security_checks(endpoint)
    if not result.get("ok"):
        print("[api_security_vetting] invalid endpoint input.")
        return result

    print(f"[api_security_vetting] reviewing endpoint: {result['endpoint']}")
    for item in result.get("checks", []):
        print(f"- {item['name']}: {item['message']}")
    return result


def propose(ctx):
    endpoint = str((ctx or {}).get("endpoint") or "/api/resource")
    preview = _security_checks(endpoint)
    return {"ok": True, "skill": "api_security_vetting", "operation": "review", "preview": preview}


def apply(ctx):
    endpoint = str((ctx or {}).get("endpoint") or "").strip()
    if not endpoint:
        return {"ok": False, "reason": "missing_endpoint", "hint": "Pass `endpoint` in context."}
    result = vet_api(endpoint)
    return {"ok": bool(result.get("ok")), "result": result}


def test(ctx):
    sample = apply({"endpoint": "/api/health"})
    if not sample.get("ok"):
        return {"ok": False, "reason": "sample_review_failed", "detail": sample}
    checks = sample.get("result", {}).get("checks", [])
    return {"ok": len(checks) >= 3, "detail": sample}


def main():
    parser = argparse.ArgumentParser(description="Run lightweight API security checks.")
    parser.add_argument("endpoint", help="API endpoint path or URL")
    args = parser.parse_args()
    vet_api(args.endpoint)


if __name__ == "__main__":
    main()
