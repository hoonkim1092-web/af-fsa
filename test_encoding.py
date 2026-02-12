import sys
import os

# Reconfigure stdout to utf-8
sys.stdout.reconfigure(encoding='utf-8')

print("Testing Encoding / 인코딩 테스트")
print("안녕하세요. 한글 출력 테스트입니다.")
print("✅ 체크 이모지 테스트")
print("System Encoding:", sys.stdout.encoding)
