import argparse


def _build_scale_plan(traffic):
    try:
        tps = int(traffic)
    except Exception:
        return {"ok": False, "reason": "invalid_traffic", "hint": "Provide integer `traffic` (TPS)."}

    if tps < 0:
        return {"ok": False, "reason": "invalid_traffic", "hint": "Traffic must be non-negative."}

    if tps >= 1000:
        actions = [
            "Enable horizontal scaling for app workers.",
            "Add read replicas for read-heavy database workload.",
            "Introduce queue-based async processing for heavy jobs.",
        ]
        tier = "high"
    elif tps >= 300:
        actions = [
            "Prepare auto-scaling thresholds.",
            "Add cache layer for repeated reads.",
            "Track p95 latency and error budget continuously.",
        ]
        tier = "medium"
    else:
        actions = [
            "Current setup is likely sufficient.",
            "Keep baseline monitoring for CPU, memory, and error rate.",
            "Review capacity monthly or after feature releases.",
        ]
        tier = "low"

    return {"ok": True, "traffic_tps": tps, "tier": tier, "actions": actions}


def scale_report(traffic):
    report = _build_scale_plan(traffic)
    if not report.get("ok"):
        print("[infrastructure_scaler] invalid traffic input.")
        return report

    print(f"[infrastructure_scaler] traffic: {report['traffic_tps']} TPS, tier={report['tier']}")
    for action in report.get("actions", []):
        print(f"- {action}")
    return report


def propose(ctx):
    traffic = (ctx or {}).get("traffic", 200)
    preview = _build_scale_plan(traffic)
    return {"ok": True, "skill": "infrastructure_scaler", "operation": "capacity_plan", "preview": preview}


def apply(ctx):
    if "traffic" not in (ctx or {}):
        return {"ok": False, "reason": "missing_traffic", "hint": "Pass integer `traffic` in context."}
    result = scale_report((ctx or {}).get("traffic"))
    return {"ok": bool(result.get("ok")), "result": result}


def test(ctx):
    sample = apply({"traffic": 1200})
    if not sample.get("ok"):
        return {"ok": False, "reason": "sample_plan_failed", "detail": sample}
    actions = sample.get("result", {}).get("actions", [])
    return {"ok": len(actions) >= 3, "detail": sample}


def main():
    parser = argparse.ArgumentParser(description="Generate a basic infrastructure scaling plan.")
    parser.add_argument("traffic", type=int, help="Expected TPS")
    args = parser.parse_args()
    scale_report(args.traffic)


if __name__ == "__main__":
    main()
