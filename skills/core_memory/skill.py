import os
import json
import glob
from datetime import datetime

def _get_memory_path(ctx):
    """
    메모리 저장 경로를 반환합니다.
    ctx['data_dir'] 하위의 'memory' 폴더를 사용합니다.
    """
    data_dir = ctx.get("data_dir", ".")
    memory_dir = os.path.join(data_dir, "memory")
    return memory_dir

def store(ctx, key, value, category="general"):
    """
    정보를 장기 기억장치에 저장합니다.
    """
    memory_dir = _get_memory_path(ctx)
    category_dir = os.path.join(memory_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    
    # 안전한 파일명 생성
    safe_key = "".join([c for c in key if c.isalnum() or c in (' ', '_', '-')]).strip()
    if not safe_key:
        safe_key = "unnamed_memory"
    
    file_path = os.path.join(category_dir, f"{safe_key}.json")
    
    record = {
        "key": key,
        "value": value,
        "category": category,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print(f"✅ [기억 저장] '{key}' 내용을 '{category}' 서랍에 잘 넣어두었습니다.")
        return {"ok": True, "path": file_path}
    except Exception as e:
        print(f"⚠️ [저장 실패] 앗, 기억을 저장하는 중에 문제가 생겼어요: {e}")
        return {"ok": False, "error": str(e)}

def retrieve(ctx, key, category="general"):
    """
    키를 기반으로 기억을 불러옵니다.
    """
    memory_dir = _get_memory_path(ctx)
    # 안전한 파일명 생성 (동일 로직)
    safe_key = "".join([c for c in key if c.isalnum() or c in (' ', '_', '-')]).strip()
    if not safe_key:
         safe_key = "unnamed_memory"

    file_path = os.path.join(memory_dir, category, f"{safe_key}.json")
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"📖 [기억 인출] '{key}'에 대한 기억을 찾아냈어요!")
            return {"ok": True, "data": data}
        except Exception as e:
             return {"ok": False, "error": str(e)}
    else:
        print(f"🤔 [기억 없음] '{key}'에 대한 기억은 제 머릿속(파일)에 없는 것 같아요.")
        return {"ok": False, "error": "not_found"}

def search(ctx, query, category="general"):
    """
    특정 카테고리 내에서 키워드로 기억을 검색합니다.
    """
    memory_dir = _get_memory_path(ctx)
    category_dir = os.path.join(memory_dir, category)
    
    if not os.path.exists(category_dir):
        return {"ok": True, "results": []}
        
    results = []
    query_lower = query.lower()
    
    files = glob.glob(os.path.join(category_dir, "*.json"))
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                match = False
                if query_lower in str(data.get("key", "")).lower():
                    match = True
                elif query_lower in str(data.get("value", "")).lower():
                    match = True
                
                if match:
                    results.append(data)
        except:
            continue
            
    print(f"🔍 [검색 완료] '{query}' 검색 결과 {len(results)}건을 찾았습니다.")
    return {"ok": True, "results": results}

def test(ctx):
    """
    스킬 테스트 함수
    """
    # 1. Store
    res_store = store(ctx, "test_key_123", "test_value_456", "test_cat")
    if not res_store.get("ok"):
        return {"ok": False, "reason": "store_failed", "detail": res_store}

    # 2. Retrieve
    res_retr = retrieve(ctx, "test_key_123", "test_cat")
    if not res_retr.get("ok") or res_retr["data"]["value"] != "test_value_456":
         return {"ok": False, "reason": "retrieve_mismatch", "detail": res_retr}

    # 3. Search
    res_search = search(ctx, "value_456", "test_cat")
    if not res_search.get("ok") or len(res_search["results"]) == 0:
        return {"ok": False, "reason": "search_failed", "detail": res_search}

    return {"ok": True}
