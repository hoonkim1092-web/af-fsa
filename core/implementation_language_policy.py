from __future__ import annotations

import os

from core.documentation_policy import get_document_language_code


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def enforce_os_language_for_human_text() -> bool:
    if "AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT" in os.environ:
        return _env_flag("AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT", True)
    try:
        from config.schema import factory_config

        policy = getattr(factory_config, "policy", None)
        language_policy = getattr(policy, "language_policy", None)
        return bool(
            getattr(
                language_policy,
                "enforce_os_language_for_comments_messages_strings",
                True,
            )
        )
    except Exception:
        return True


def implementation_language_profile() -> dict[str, str]:
    language_code = get_document_language_code()
    primary = language_code.split("-", 1)[0].lower()

    if primary == "ko":
        return {
            "language_code": language_code,
            "language_name": "한국어",
            "contract": (
                "[Implementation Language Contract]\n"
                f"사람이 읽는 새 주석, docstring, 메시지, 로그, 오류 문구, 테스트 문자열, 문자열 리터럴은 한국어로 작성한다 (OS: `{language_code}`).\n"
                "식별자, 파일 경로, 명령어, 플래그, 환경 변수 이름, API 필드명, 외부 API 계약 문자열은 번역하지 않는다."
            ),
        }

    return {
        "language_code": language_code,
        "language_name": "English",
        "contract": (
            "[Implementation Language Contract]\n"
            f"Write new human-readable comments, docstrings, messages, logs, errors, test strings, and string literals in the OS language (OS: `{language_code}`).\n"
            "Do not translate identifiers, file paths, commands, flags, environment variable names, API field names, or external API contract strings."
        ),
    }


def implementation_language_contract_text() -> str:
    return str(implementation_language_profile()["contract"])


def inject_implementation_language_contract(system_prompt: str) -> str:
    base = str(system_prompt or "").strip()
    marker = "[Implementation Language Contract]"
    if marker in base or not enforce_os_language_for_human_text():
        return base

    contract = implementation_language_contract_text()
    return f"{base}\n\n{contract}".strip()