import builtins
import sys as _sys
def _safe_print(*args, **kwargs):
    enc = getattr(_sys.stdout, 'encoding', None) or 'utf-8'
    parts = [str(a).encode(enc, errors='replace').decode(enc, errors='replace') for a in args]
    builtins.print(*parts, **kwargs)
print = _safe_print

"""
HashlineEditor V2 Comprehensive Test
- compile check
- single line edit (V1 compat)
- hash collision detection
- 멀티라인 블록 편집
- dry_run 모드
- diff 미리보기
- 변경 이력
"""
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
from core.hashline_editor import HashlineEditor

PASS = 0
FAIL = 0

def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")

# ── 테스트 파일 생성 ──
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
tmp.write("def hello():\n")
tmp.write("    print('hello')\n")
tmp.write("    print('world')\n")
tmp.write("    return True\n")
tmp.write("\n")
tmp.write("def goodbye():\n")
tmp.write("    return False\n")
tmp.close()
test_file = tmp.name

print("=" * 60)
print("HashlineEditor V2 종합 테스트")
print("=" * 60)

# 1. 컴파일 체크
print("\n[1] 컴파일 체크")
check("hashline_editor.py 컴파일", True)  # import 성공이면 OK

# 2. format_file_with_hashes
print("\n[2] 해시 포맷 읽기")
hashed = HashlineEditor.format_file_with_hashes(test_file)
lines = hashed.split("\n")
check("7줄 출력", len(lines) == 7)
check("라인 해시 포맷 (NUM#HASH)", "#" in lines[0] and "|" in lines[0])

# 3. 해시 충돌 탐지 (⚠DUP)
print("\n[3] 해시 충돌 탐지")
# print('hello')와 print('world') 는 다른 해시이므로 DUP 없어야 함
dup_count = sum(1 for l in lines if "⚠DUP" in l)
check("고유 라인은 DUP 없음", dup_count == 0)

# 충돌 테스트용 파일 생성
dup_file = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
dup_file.write("x = 1\n")
dup_file.write("x = 1\n")  # 동일 라인 → 동일 해시
dup_file.write("y = 2\n")
dup_file.close()
dup_hashed = HashlineEditor.format_file_with_hashes(dup_file.name)
dup_lines = dup_hashed.split("\n")
dup_count = sum(1 for l in dup_lines if "⚠DUP" in l)
check("동일 라인 2개 → ⚠DUP 2개", dup_count == 2)

# 4. find_hash
print("\n[4] find_hash (충돌 해소)")
x_hash = HashlineEditor._compute_line_hash("x = 1")
matches = HashlineEditor.find_hash(dup_file.name, x_hash)
check("동일 해시 2개 매칭", len(matches) == 2)
check("라인 번호 1, 2", matches[0]["line_num"] == 1 and matches[1]["line_num"] == 2)

# 5. 단일 라인 편집 (V1 호환)
print("\n[5] 단일 라인 편집 (V1 호환)")
hello_hash = HashlineEditor._compute_line_hash("    print('hello')")
result = HashlineEditor.apply_hashline_edit(test_file, hello_hash, "    print('HELLO V2')")
check("replace 성공", result["ok"])
check("diff 포함", "diff" in result)

with open(test_file, "r", encoding="utf-8") as f:
    content = f.read()
check("내용 반영됨", "HELLO V2" in content)

# 6. 해시 충돌 + target_line 편집
print("\n[6] 해시 충돌 시 target_line 편집")
result = HashlineEditor.apply_hashline_edit(dup_file.name, x_hash, "x = 99\n", target_line=2)
check("target_line=2 편집 성공", result["ok"])
with open(dup_file.name, "r", encoding="utf-8") as f:
    dup_content = f.readlines()
check("라인2만 변경 (x=99)", "99" in dup_content[1])
check("라인1은 유지 (x=1)", "x = 1" in dup_content[0])

# 7. dry_run 모드
print("\n[7] dry_run 모드")
world_hash = HashlineEditor._compute_line_hash("    print('world')")
result = HashlineEditor.apply_hashline_edit(test_file, world_hash, "    print('DRY')", dry_run=True)
check("dry_run=True 반환", result.get("dry_run") == True)
check("preview 포함", "preview" in result)
check("diff 포함", "diff" in result)
# 파일은 변경되지 않아야 함
with open(test_file, "r", encoding="utf-8") as f:
    check("파일 미변경", "DRY" not in f.read())

# 8. 멀티라인 블록 편집
print("\n[8] 멀티라인 블록 편집")
# 파일 재생성 (깔끔한 상태)
with open(test_file, "w", encoding="utf-8") as f:
    f.write("def hello():\n")
    f.write("    x = 1\n")
    f.write("    y = 2\n")
    f.write("    return x + y\n")
    f.write("\n")
    f.write("def other():\n")
    f.write("    pass\n")

start_h = HashlineEditor._compute_line_hash("def hello():")
end_h = HashlineEditor._compute_line_hash("    return x + y")
new_block = "def hello_v2():\n    return 42"

result = HashlineEditor.apply_block_edit(test_file, start_h, end_h, new_block)
check("블록 편집 성공", result["ok"])
check("4줄 교체됨", result.get("lines_replaced") == 4)
check("2줄 삽입됨", result.get("lines_inserted") == 2)
with open(test_file, "r", encoding="utf-8") as f:
    content = f.read()
check("새 함수 반영", "hello_v2" in content)
check("기존 other() 유지", "def other()" in content)

# 9. 블록 편집 dry_run
print("\n[9] 블록 편집 dry_run")
other_h = HashlineEditor._compute_line_hash("def other():")
pass_h = HashlineEditor._compute_line_hash("    pass")
result = HashlineEditor.apply_block_edit(test_file, other_h, pass_h, "def other():\n    return 999", dry_run=True)
check("블록 dry_run 성공", result.get("dry_run") == True)
check("diff 포함", len(result.get("diff", "")) > 0)

# 10. 변경 이력
print("\n[10] 변경 이력")
history = HashlineEditor.get_edit_history()
check("이력 존재", len(history) > 0)
check("블록 편집 이력 포함", any(h["operation"] == "block_replace" for h in history))
file_history = HashlineEditor.get_edit_history(file_path=test_file)
check("파일별 필터 작동", all(h["file"] == test_file for h in file_history))

# ── 정리 ──
os.unlink(test_file)
os.unlink(dup_file.name)
HashlineEditor.clear_history()

print("\n" + "=" * 60)
print(f"결과: ✅ {PASS} / ❌ {FAIL} (총 {PASS + FAIL})")
print("=" * 60)
if FAIL == 0:
    print("🎉 ALL TESTS PASSED!")
else:
    print(f"⚠️ {FAIL} tests failed!")
    sys.exit(1)
