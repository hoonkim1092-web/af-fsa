"""
af setup — 첫 실행 시 API 키 등록 마법사.

실행 조건:
  1. `af setup` 명령 직접 실행
  2. .env 없이 첫 실행 시 자동 안내 (AGENT_SKIP_SETUP_HINT 없으면)
"""
from __future__ import annotations

import os
import sys


# 지원 API 키 목록 (이름, 환경변수, 안내 URL, 필수여부)
_KEYS = [
    {
        "name": "Tavily (웹 검색)",
        "env": "TAVILY_API_KEY",
        "url": "https://app.tavily.com",
        "required": False,
        "desc": "프로젝트 리서치 품질을 높이는 웹 검색 API",
    },
    {
        "name": "Google (Gemini CLI)",
        "env": "GOOGLE_API_KEY",
        "url": "https://aistudio.google.com/apikey",
        "required": False,
        "desc": "Gemini CLI 제공자 사용 시 필요",
    },
    {
        "name": "Anthropic (Claude API)",
        "env": "ANTHROPIC_API_KEY",
        "url": "https://console.anthropic.com",
        "required": False,
        "desc": "Claude API 직접 호출 시 필요 (CLI 모드에서는 불필요)",
    },
    {
        "name": "OpenAI (Codex CLI)",
        "env": "OPENAI_API_KEY",
        "url": "https://platform.openai.com/api-keys",
        "required": False,
        "desc": "Codex CLI 제공자 사용 시 필요",
    },
]


def _get_env_path() -> str:
    """exe 옆 또는 소스 루트의 .env 경로."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), ".env")
    factory_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(factory_dir, ".env")


def _load_existing(env_path: str) -> dict[str, str]:
    """기존 .env 파싱."""
    result: dict[str, str] = {}
    if not os.path.isfile(env_path):
        return result
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _save_env(env_path: str, data: dict[str, str]) -> None:
    """data를 .env에 저장 (기존 주석/빈줄 유지 + 신규 키 추가)."""
    lines: list[str] = []
    existing_keys: set[str] = set()

    # 기존 파일 읽기
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if "=" in raw and not raw.startswith("#"):
                    k = raw.split("=", 1)[0].strip()
                    if k in data:
                        lines.append(f"{k}={data[k]}")
                        existing_keys.add(k)
                        continue
                lines.append(raw)

    # 신규 키 추가
    for k, v in data.items():
        if k not in existing_keys and v:
            lines.append(f"{k}={v}")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_setup(interactive: bool = True) -> None:
    """API 키 등록 마법사 실행."""
    env_path = _get_env_path()
    existing = _load_existing(env_path)

    print()
    print("=" * 60)
    print("  Agent Factory — API 키 설정")
    print("=" * 60)
    print(f"  저장 위치: {env_path}")
    print()

    updated: dict[str, str] = {}
    changed = False

    for key_info in _KEYS:
        env_key = key_info["env"]
        current = existing.get(env_key) or os.getenv(env_key, "")
        masked = f"{current[:8]}..." if len(current) > 8 else ("(미설정)" if not current else current)

        print(f"▶ {key_info['name']}")
        print(f"  {key_info['desc']}")
        print(f"  발급: {key_info['url']}")
        print(f"  현재: {masked}")

        if not interactive:
            print()
            continue

        prompt = "  새 값 입력 (엔터=유지, s=건너뛰기): "
        try:
            val = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n설정 중단.")
            break

        if val.lower() == "s" or val == "":
            updated[env_key] = current
        else:
            updated[env_key] = val
            changed = True
            print(f"  ✓ {env_key} 저장됨")
        print()

    if changed:
        _save_env(env_path, updated)
        print(f"✓ .env 저장 완료: {env_path}")
        print("  변경사항은 af 재시작 후 적용됩니다.")
    else:
        print("변경사항 없음.")

    print()


def check_and_hint() -> None:
    """
    첫 실행 시 API 키 미설정 항목이 있으면 힌트 출력.
    AGENT_SKIP_SETUP_HINT=1 이면 건너뜀.
    """
    if os.getenv("AGENT_SKIP_SETUP_HINT"):
        return

    env_path = _get_env_path()
    existing = _load_existing(env_path)

    missing = [
        k["name"]
        for k in _KEYS
        if not (existing.get(k["env"]) or os.getenv(k["env"], ""))
    ]

    if not missing:
        return

    # TAVILY 없을 때만 명시적으로 경고 (문서 품질에 직결)
    if any("Tavily" in m for m in missing):
        print()
        print("┌─────────────────────────────────────────────────────┐")
        print("│  ⚠  웹 검색 API 미설정 — 문서 품질이 낮아질 수 있습니다  │")
        print("│  af setup  명령으로 TAVILY_API_KEY를 등록하세요        │")
        print("│  무료 발급: https://app.tavily.com                   │")
        print("└─────────────────────────────────────────────────────┘")
        print()
