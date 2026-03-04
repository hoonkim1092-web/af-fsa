"""
core/synergy/tools.py
=====================
에이전트 시너지 도구 팩토리.

단일 책임:
  - get_synergy_context  → 에이전트 시스템 프롬프트 브리핑
  - build_synergy_tools  → 에이전트에게 주입할 callable 도구 목록 생성
"""

from __future__ import annotations

from typing import Callable

from core.synergy.bridge import SynergyBridge


# =============================================================================
# 시스템 프롬프트 브리핑
# =============================================================================

def get_synergy_context(tool_names: list[str] | None = None) -> str:
    """에이전트 시스템 프롬프트에 삽입할 시너지 브리핑 문자열을 반환한다."""
    if tool_names is None:
        names = [
            "synergy_omo_hash_edit",
            "synergy_omo_fsa",
            "synergy_omo_dispatch",
            "synergy_omo_status",
            "synergy_omo_collect",
            "synergy_omo_cancel",
        ]
    else:
        names = [str(n).strip() for n in tool_names if str(n).strip()]

    if not names:
        return (
            "\n\n[Synergy Briefing]\n"
            "OmO synergy tools are not attached in this run."
        )

    rendered = ", ".join([f"`{n}`" for n in names])
    return (
        "\n\n[Synergy & Stability Briefing]\n"
        "코드 수정 시 발생할 수 있는 라인 밀림(Harness Problem) 방지를 위해\n"
        "`get_file_with_hashes`로 파일을 읽고 `apply_edit`을 사용하는 것을 강력 추천합니다.\n"
        f"사용 가능한 시너지 도구: {rendered}\n\n"
        "📌 비동기 병렬 패턴 (권장):\n"
        "   1. synergy_omo_dispatch(task=...)  → job_id 즉시 수령, 본인 작업 계속\n"
        "   2. synergy_omo_status(job_id)      → 상태 확인 (queued/running/succeeded/...)\n"
        "   3. synergy_omo_collect(job_id)     → 종결 상태에서만 결과 수거\n"
        "   4. synergy_omo_cancel(job_id)      → 불필요해진 작업 강제 회수\n\n"
        "⚡ 동기 패턴 (단순/긴급): synergy_omo_fsa — 완료까지 블로킹, 최대 120초\n"
        "Hash-Anchored 편집(`synergy_omo_hash_edit`)은 다중 에이전트 환경에서 가장 안전한 협업 수단입니다."
    )


# =============================================================================
# 에이전트 도구 팩토리
# =============================================================================

def build_synergy_tools(
    policy: dict | None = None,
    is_allowed_fn: Callable | None = None,
) -> list[Callable]:
    """에이전트에게 주입할 시너지 callable 도구 목록을 생성한다.

    Args:
        policy:       스킬 허용 정책 딕셔너리 (없으면 전체 허용)
        is_allowed_fn: (policy, skill_id, fn_name) → bool 형식의 필터 함수

    Returns:
        정책 필터를 통과한 callable 목록 (각 fn._skill_id 속성 포함)
    """
    bridge = SynergyBridge()
    skill_id = "synergy_runner"

    # ── 레거시 도구 ──────────────────────────────────────────────────────────

    def synergy_omo_hash_edit(
        path: str, find: str, replace: str,
        count: int = 1, expected_sha256: str = "",
    ) -> dict:
        """[Hash-Anchored] SHA-256 선택적 가드를 포함한 결정론적 파일 편집."""
        return bridge.run_hash_edit(
            path=path, find=find, replace=replace,
            count=count, expected_sha256=expected_sha256,
        )

    def synergy_omo_fsa(task: str, timeout_sec: int = 120) -> dict:
        """[레거시 동기] OmO FSA에 작업을 위임한다. 완료까지 블로킹됨(최대 120초)."""
        return bridge.run_ultrawork(task=task, timeout_sec=timeout_sec)

    # ── 비동기 병렬 도구 4종 ─────────────────────────────────────────────────

    def synergy_omo_dispatch(task: str, timeout_sec: int | None = None) -> dict:
        """[비동기 발사] OmO에 작업을 백그라운드로 위임한다. job_id를 즉시 반환한다.

        timeout_sec 미지정시 config의 omo_dispatch_timeout_sec 기본값 사용.
        동시 작업 수가 omo_max_concurrent_jobs를 초과하면 에러 반환.

        사용 예:
            result = synergy_omo_dispatch(task="CSS 전체 리팩토링")
            job_id = result["job_id"]  # 이후 본인 작업 계속
        """
        return bridge.dispatch(task=task, timeout_sec=timeout_sec)

    def synergy_omo_status(job_id: str) -> dict:
        """[상태 조회] dispatched job의 현재 상태를 확인한다.

        status: queued | running | succeeded | failed | timeout | cancelled
        is_terminal=True이면 collect 또는 cancel 가능.
        """
        return bridge.check_status(job_id=job_id)

    def synergy_omo_collect(job_id: str) -> dict:
        """[결과 수거] 완료된 job의 stdout/stderr를 가져온다.

        ⚠️ is_terminal=True 상태에서만 결과를 반환한다.
        미종결 시: {"ok": false, "error": "job_not_finished"}
        collect 후 임시 파일은 자동으로 삭제된다.
        """
        return bridge.collect_result(job_id=job_id)

    def synergy_omo_cancel(job_id: str) -> dict:
        """[강제 회수] 실행 중 또는 대기 중인 job을 취소한다.

        2단계 종료: soft terminate → grace period → hard kill(트리 전체)
        이미 종결된 job에 호출해도 안전하다 (no-op).
        """
        return bridge.cancel(job_id=job_id)

    # ── 확장 도구 (선택적) ───────────────────────────────────────────────────

    def synergy_omo_list_jobs() -> dict:
        """[관리] 현재 관리 중인 모든 OmO job의 상태 요약을 반환한다."""
        return bridge.list_jobs()

    all_tools = [
        synergy_omo_hash_edit,
        synergy_omo_fsa,
        synergy_omo_dispatch,
        synergy_omo_status,
        synergy_omo_collect,
        synergy_omo_cancel,
        synergy_omo_list_jobs,
    ]

    result: list[Callable] = []
    for fn in all_tools:
        fn._skill_id = skill_id  # type: ignore[attr-defined]
        if is_allowed_fn is not None and policy is not None:
            if not is_allowed_fn(policy, skill_id, fn.__name__):
                continue
        result.append(fn)

    return result
