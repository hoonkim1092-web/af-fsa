import os
import hashlib
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.system_generated', 'logs', 'audit.log')

def verify_audit_log():
    """
    수학적 무결성 검증을 수행합니다. 연쇄 해시 체인이 깨진 부분이 없는지 체크합니다.
    """
    if not os.path.exists(AUDIT_LOG_PATH):
        print("Audit log file does not exist.")
        return False

    with open(AUDIT_LOG_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines:
        print("Audit log is empty.")
        return True

    print(f"Verifying {len(lines)} log entries...")
    expected_prev_hash = "GENESIS_HASH_0000000000000000000"
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
            
        parts = line.split(" | ")
        if len(parts) < 4:
            print(f"[FAIL] Line {i+1}: Invalid format.")
            return False
            
        timestamp_str = parts[0].strip("[]")
        prev_hash_str = parts[1].replace("PREV:", "").strip()
        payload_str = parts[2].strip()
        curr_hash_str = parts[3].replace("CURR:", "").strip()
        
        # Verify PREV_HASH matches previous CURR_HASH
        if prev_hash_str != expected_prev_hash:
            print(f"[FAIL] Line {i+1}: PREV_HASH mismatch. Expected {expected_prev_hash}, got {prev_hash_str}")
            return False
            
        # Verify CURR_HASH mathematically
        raw_data = f"{prev_hash_str}{timestamp_str}{payload_str}".encode('utf-8')
        calculated_hash = hashlib.sha256(raw_data).hexdigest()
        
        if calculated_hash != curr_hash_str:
            print(f"[FAIL] Line {i+1}: Hash calculation mismatch. Data modified!")
            return False
            
        expected_prev_hash = curr_hash_str
        
    print(f"[SUCCESS] All {len(lines)} log entries are mathematically verified. Integrity is 100%.")
    return True

if __name__ == "__main__":
    success = verify_audit_log()
    sys.exit(0 if success else 1)
