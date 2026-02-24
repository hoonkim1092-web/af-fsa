import os
import hashlib
import datetime

AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.system_generated', 'logs', 'audit.log')

def log_audit_event(action: str):
    """
    Immutable Audit Log에 해시 체인 방식으로 로그를 추가합니다.
    형식: [TIMESTAMP] | PREV:[HASH] | [PAYLOAD] | CURR:[HASH]
    """
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    
    prev_hash = "GENESIS_HASH_0000000000000000000"
    
    if os.path.exists(AUDIT_LOG_PATH):
        with open(AUDIT_LOG_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if lines:
                last_line = lines[-1].strip()
                # 마지막 라인에서 CURR:[HASH] 파싱
                parts = last_line.split(" | ")
                if len(parts) >= 4 and parts[-1].startswith("CURR:"):
                    prev_hash = parts[-1].replace("CURR:", "")
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = action
    
    # SHA256( PREV_HASH + TIMESTAMP + PAYLOAD )
    raw_data = f"{prev_hash}{timestamp}{payload}".encode('utf-8')
    curr_hash = hashlib.sha256(raw_data).hexdigest()
    
    log_entry = f"[{timestamp}] | PREV:{prev_hash} | {payload} | CURR:{curr_hash}\n"
    
    with open(AUDIT_LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(log_entry)
        
    return curr_hash
