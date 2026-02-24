from core.hashline_editor import HashlineEditor
import os

def test_hashline_operations():
    test_file = "test_hashline.txt"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("line 1: init\nline 2: target\nline 3: end\n")

    # 1. Format and Extract Hash
    formatted = HashlineEditor.format_file_with_hashes(test_file)
    print("--- Formatted File ---")
    print(formatted)
    
    # Extract hash for line 2
    target_hash = ""
    for line in formatted.split('\n'):
        if line.startswith("2#"):
            target_hash = line.split('#')[1].split(' | ')[0]
            break
            
    print(f"\nTarget Hash for Line 2: {target_hash}")
    
    # 2. Apply Edit
    success = HashlineEditor.apply_hashline_edit(
        file_path=test_file,
        target_hash=target_hash,
        new_content="line 2: mutated\n",
        operation="replace"
    )
    
    with open(test_file, "r", encoding="utf-8") as f:
        final_content = f.read()
        
    print("\n--- Final Content ---")
    print(final_content)
    
    assert "mutated" in final_content
    assert "target" not in final_content
    os.remove(test_file)
    print("Test Passed: Hashline Replacement Working.")

if __name__ == "__main__":
    test_hashline_operations()
