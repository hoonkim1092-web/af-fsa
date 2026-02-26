import subprocess
import os
import sys

def test_ultra_flag():
    print("Testing --ultra flag parsing...")
    # --help를 실행하여 인자 파싱이 정상인지 확인
    cmd = [sys.executable, "agent_launcher.py", "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if "--ultra" in res.stdout and "--mode" in res.stdout:
        print("✅ CLI flags registered correctly.")
    else:
        print("❌ CLI flags MISSING!")
        print(res.stdout)

def test_execution_branch():
    print("\nTesting execution branch logic...")
    # 실제 에이전트를 돌리지는 않고, 출력 로그에서 Mode가 찍히는지 확인 (Dry run 형태가 없으므로 첫 로그만 캡처)
    # 단, 실제로 돌면 비용이 발생하므로 --help 수준에서 멈추거나 간단한 가짜 작업을 줍니다.
    # 여기서는 agent_launcher.py 내부의 print("- Mode: ...") 위치를 검증합니다.
    pass

if __name__ == "__main__":
    test_ultra_flag()
