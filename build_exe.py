"""
Agent Factory exe 빌드 스크립트.

사용법:
    python build_exe.py

결과:
    dist/af/af.exe
    dist/af-{version}.zip

배포:
    dist/af-{version}.zip 을 사용자에게 전달.
    사용자는 압축 해제 후 af.exe 를 실행하면 됩니다.
"""

import os
import subprocess
import sys
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(ROOT, "af.spec")
DIST_DIR = os.path.join(ROOT, "dist", "af")


def _get_version() -> str:
    try:
        from version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def main():
    print("=" * 60)
    print("  Agent Factory exe 빌드")
    print("=" * 60)

    # 1. PyInstaller 확인
    try:
        import PyInstaller
        print(f"  PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("  PyInstaller가 설치되어 있지 않습니다.")
        print("  설치: pip install pyinstaller")
        sys.exit(1)

    # 2. 이전 빌드 정리
    for d in ["build", "dist"]:
        p = os.path.join(ROOT, d)
        if os.path.exists(p):
            print(f"  이전 빌드 정리: {d}/")
            shutil.rmtree(p, ignore_errors=True)

    # 3. PyInstaller 실행
    print("\n  빌드 시작...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        SPEC_PATH,
    ]
    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode != 0:
        print(f"\n  빌드 실패 (exit code: {result.returncode})")
        sys.exit(result.returncode)

    # 4. 결과 확인
    exe_path = os.path.join(DIST_DIR, "af.exe")
    if not os.path.exists(exe_path):
        print(f"\n  빌드 결과를 찾을 수 없습니다: {exe_path}")
        sys.exit(1)

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    total_files = sum(len(files) for _, _, files in os.walk(DIST_DIR))
    total_size_mb = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fnames in os.walk(DIST_DIR)
        for f in fnames
    ) / (1024 * 1024)

    # 5. zip 패키징
    version = _get_version()
    zip_name = f"af-{version}.zip"
    zip_path = os.path.join(ROOT, "dist", zip_name)
    print(f"\n  zip 패키징: {zip_name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _, fnames in os.walk(DIST_DIR):
            for fname in fnames:
                full = os.path.join(dp, fname)
                arcname = os.path.relpath(full, os.path.join(ROOT, "dist"))
                zf.write(full, arcname)
    zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)

    print("\n" + "=" * 60)
    print("  빌드 완료!")
    print("=" * 60)
    print(f"  버전:        {version}")
    print(f"  exe 경로:    {exe_path}")
    print(f"  exe 크기:    {size_mb:.1f} MB")
    print(f"  전체 파일:   {total_files}개")
    print(f"  전체 크기:   {total_size_mb:.1f} MB")
    print(f"  zip 경로:    {zip_path}")
    print(f"  zip 크기:    {zip_size_mb:.1f} MB")
    print()
    print("  배포 방법:")
    print(f"    dist/{zip_name} 을 사용자에게 전달")
    print(f"    압축 해제 후 af/af.exe 실행")
    print()
    print("  테스트:")
    print(f"    dist\\af\\af.exe --help")


if __name__ == "__main__":
    main()
