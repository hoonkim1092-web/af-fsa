import os
import json

def propose(ctx):
    return {
        "description": "Create a placeholder SVG image with specified dimensions and text.",
        "required_keys": ["width", "height", "text"],
        "optional_keys": ["color", "filename"]
    }

def apply(ctx):
    try:
        width = int(ctx.get("width", 800))
        height = int(ctx.get("height", 600))
        text = ctx.get("text", "Placeholder")
        color = ctx.get("color", "#e0e0e0")
        filename = ctx.get("filename", f"image_{width}x{height}.svg")
        
        artifacts_dir = ctx.get("artifacts_dir", ".")
        filepath = os.path.join(artifacts_dir, filename)

        # Basic SVG Template
        svg_content = f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="{color}"/>
  <text x="50%" y="50%" font-family="Arial" font-size="24" fill="black" dominant-baseline="middle" text-anchor="middle">
    {text}
  </text>
</svg>"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_content)

        return {
            "ok": True,
            "message": f"Generated SVG image at {filepath}",
            "file_path": filepath
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def test(ctx):
    return apply({
        "width": 100, 
        "height": 100, 
        "text": "TEST", 
        "color": "#ffcccc",
        "artifacts_dir": ctx.get("artifacts_dir", ".")
    })
