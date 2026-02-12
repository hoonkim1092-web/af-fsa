import os
import json

def propose(ctx):
    return {
        "description": "Generate a Mermaid.js user flow diagram.",
        "required_keys": ["flow_name", "steps"],
        "optional_keys": []
    }

def apply(ctx):
    try:
        name = ctx.get("flow_name", "User Journey")
        steps = ctx.get("steps", ["Start", "Action", "End"])
        artifacts_dir = ctx.get("artifacts_dir", ".")
        
        filename = f"user_flow_{name.replace(' ', '_').lower()}.mmd"
        filepath = os.path.join(artifacts_dir, filename)
        
        # Build Mermaid graph
        mermaid = f"graph TD\n    title[{name}]\n"
        for i, step in enumerate(steps):
            node_id = f"S{i}"
            mermaid += f"    {node_id}[{step}]\n"
            if i < len(steps) - 1:
                next_id = f"S{i+1}"
                mermaid += f"    {node_id} --> {next_id}\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(mermaid)

        return {
            "ok": True,
            "message": f"Generated User Flow Diagram: {filepath}",
            "file_path": filepath
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def test(ctx):
    return apply({
        "flow_name": "Login Process",
        "steps": ["User visits login page", "Enters credentials", "Validates input", "Redirects to Dashboard"],
        "artifacts_dir": ctx.get("artifacts_dir", ".")
    })
