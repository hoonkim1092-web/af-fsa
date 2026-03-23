import os
import json

def propose(ctx):
    return {
        "description": "Generate basic Design System artifacts (variables.css, tokens.json)",
        "required_keys": ["primary_color"],
        "optional_keys": ["font_family"]
    }

def apply(ctx):
    try:
        primary = ctx.get("primary_color", "#3498db")
        font = ctx.get("font_family", "sans-serif")
        artifacts_dir = ctx.get("artifacts_dir", ".")
        
        # 1. Design Tokens (JSON)
        tokens = {
            "color": {
                "primary": primary,
                "secondary": "#2ecc71",
                "background": "#ffffff",
                "text": "#333333"
            },
            "font": {
                "base": font,
                "size": "16px"
            }
        }
        token_path = os.path.join(artifacts_dir, "design_tokens.json")
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)

        # 2. CSS Variables
        css_content = f""":root {{
  --primary-color: {primary};
  --secondary-color: #2ecc71;
  --bg-color: #ffffff;
  --text-color: #333333;
  --font-family: {font};
  --base-size: 16px;
}}
"""
        css_path = os.path.join(artifacts_dir, "variables.css")
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(css_content)

        return {
            "ok": True,
            "message": "Generated Design System artifacts",
            "files": [token_path, css_path]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def test(ctx):
    return apply({
        "primary_color": "#ff0000",
        "font_family": "Roboto",
        "artifacts_dir": ctx.get("artifacts_dir", ".")
    })
