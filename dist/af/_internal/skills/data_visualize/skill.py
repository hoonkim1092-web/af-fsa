import os
import json
import pandas as pd
import matplotlib.pyplot as plt

# 한글 폰트 설정 (Windows 기본 맑은 고딕)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

def propose(ctx):
    return {
        "description": "데이터 파일을 시각화하여 차트 이미지로 저장합니다.",
        "required_keys": ["file_name", "chart_type", "x_col", "y_col"],
        "optional_keys": ["title", "output_name"]
    }

def apply(ctx):
    try:
        data_dir = ctx.get("data_dir", ".")
        artifacts_dir = ctx.get("artifacts_dir", ".")
        
        file_name = ctx.get("file_name")
        file_path = os.path.join(data_dir, file_name)
        
        if not os.path.exists(file_path):
            return {"ok": False, "error": f"File not found: {file_path}"}
            
        # 데이터 로드
        if file_name.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_name.endswith('.json'):
            df = pd.read_json(file_path)
        else:
            return {"ok": False, "error": "Unsupported file format (csv, json only)"}
            
        # 차트 그리기
        chart_type = ctx.get("chart_type", "bar")
        x_col = ctx.get("x_col")
        y_col = ctx.get("y_col")
        title = ctx.get("title", f"{y_col} by {x_col}")
        
        plt.figure(figsize=(10, 6))
        
        if chart_type == "bar":
            plt.bar(df[x_col], df[y_col])
        elif chart_type == "line":
            plt.plot(df[x_col], df[y_col], marker='o')
        elif chart_type == "scatter":
            plt.scatter(df[x_col], df[y_col])
            
        plt.title(title)
        plt.xlabel(x_col)
        plt.ylabel(y_col)
        plt.grid(True, alpha=0.3)
        
        # 저장
        output_name = ctx.get("output_name", "chart_output.png")
        output_path = os.path.join(artifacts_dir, output_name)
        plt.savefig(output_path)
        plt.close()
        
        return {
            "ok": True, 
            "message": f"Chart saved to {output_path}", 
            "output_path": output_path
        }
        
    except Exception as e:
        return {"ok": False, "error": str(e)}

def test(ctx):
    # 테스트용 더미 데이터 생성 및 실행
    data_dir = ctx.get("data_dir", ".")
    artifacts_dir = ctx.get("artifacts_dir", ".")
    
    dummy_csv = os.path.join(data_dir, "test_sales.csv")
    df = pd.DataFrame({
        "Month": ["Jan", "Feb", "Mar", "Apr"],
        "Sales": [100, 150, 120, 180]
    })
    df.to_csv(dummy_csv, index=False)
    
    ctx["file_name"] = "test_sales.csv"
    ctx["chart_type"] = "bar"
    ctx["x_col"] = "Month"
    ctx["y_col"] = "Sales"
    ctx["title"] = "Monthly Sales Test"
    
    result = apply(ctx)
    
    # 청소
    if os.path.exists(dummy_csv):
        os.remove(dummy_csv)
        
    return result
