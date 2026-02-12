import os
import json
from datetime import datetime

ISSUE_FILE = "issues.json"

def _load_issues(data_dir):
    path = os.path.join(data_dir, ISSUE_FILE)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_issues(data_dir, issues):
    path = os.path.join(data_dir, ISSUE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

def propose(ctx):
    return {
        "description": "이슈 트래커(JSON) 관리 도구입니다.",
        "commands": ["create", "list", "update"],
        "required_keys": ["command"],
        "optional_keys": ["title", "description", "issue_id", "status", "priority"]
    }

def apply(ctx):
    data_dir = ctx.get("data_dir", ".")
    command = ctx.get("command")
    
    issues = _load_issues(data_dir)
    
    if command == "create":
        new_issue = {
            "id": len(issues) + 1,
            "title": ctx.get("title", "No Title"),
            "description": ctx.get("description", ""),
            "status": "OPEN",
            "priority": ctx.get("priority", "MEDIUM"),
            "created_at": datetime.now().isoformat()
        }
        issues.append(new_issue)
        _save_issues(data_dir, issues)
        return {"ok": True, "message": f"Issue #{new_issue['id']} created.", "issue": new_issue}
        
    elif command == "list":
        status_filter = ctx.get("status")
        result = [i for i in issues if not status_filter or i["status"] == status_filter]
        return {"ok": True, "count": len(result), "issues": result}
        
    elif command == "update":
        issue_id = int(ctx.get("issue_id", -1))
        target = next((i for i in issues if i["id"] == issue_id), None)
        if not target:
            return {"ok": False, "error": f"Issue #{issue_id} not found."}
            
        if "status" in ctx: target["status"] = ctx["status"]
        if "priority" in ctx: target["priority"] = ctx["priority"]
        
        _save_issues(data_dir, issues)
        return {"ok": True, "message": f"Issue #{issue_id} updated.", "issue": target}
        
    else:
        return {"ok": False, "error": f"Unknown command: {command}"}

def test(ctx):
    # 테스트 시나리오: 생성 -> 조회 -> 상태 변경
    data_dir = ctx.get("data_dir", ".")
    
    # 1. 생성
    ctx["command"] = "create"
    ctx["title"] = "Test Issue"
    ctx["priority"] = "HIGH"
    res1 = apply(ctx)
    if not res1["ok"]: return res1
    
    created_id = res1["issue"]["id"]
    
    # 2. 업데이트
    ctx["command"] = "update"
    ctx["issue_id"] = created_id
    ctx["status"] = "DONE"
    res2 = apply(ctx)
    
    # 3. 조회
    ctx["command"] = "list"
    ctx["status"] = "DONE"
    res3 = apply(ctx)
    
    # 테스트 데이터 삭제 (선택)
    # os.remove(os.path.join(data_dir, ISSUE_FILE))
    
    return {"ok": True, "create": res1, "update": res2, "list": res3}
