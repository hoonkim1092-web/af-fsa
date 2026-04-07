#!/usr/bin/env python3
"""
scripts/design_review_watcher.py
==================================
설계 문서 교차 검증 백그라운드 데몬.

.af_review_queue/pending/ 를 폴링하여 리뷰 실행 후 docs/reviews/에 결과 저장.

실행:
  - 자동: design_review_trigger.py가 detached spawn
  - 수동: python scripts/design_review_watcher.py <workspace>
  - 동기: python scripts/design_review_watcher.py <workspace> --sync <rel_path>

idle 60초 후 자동 종료.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── 상수 ──────────────────────────────────────────────────────────────────────

QUEUE_DIR = ".af_review_queue"
PENDING_DIR = os.path.join(QUEUE_DIR, "pending")
FAILED_DIR = os.path.join(QUEUE_DIR, "failed")
NOTIFICATIONS_DIR = os.path.join(QUEUE_DIR, "notifications")
PID_FILE = os.path.join(QUEUE_DIR, ".watcher.pid")

RESULTS_DIR = os.path.join("docs", "reviews")
PROMPTS_DIR = os.path.join("scripts", "prompts")

POLL_INTERVAL = 10   # seconds
QUIET_PERIOD = 8     # seconds — 연속 편집 대기
IDLE_TIMEOUT = 60    # seconds — 무활동 시 자동 종료
REVIEW_TIMEOUT = 600 # seconds — 단일 리뷰 실행 제한 (codex 자율 탐색 고려)

JUDGE_PRIORITY = ["claude", "codex", "gemini"]

# ── 프로바이더 탐지 ───────────────────────────────────────────────────────────

CLI_COMMANDS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
}


def detect_providers() -> list[str]:
    """설치된 CLI 프로바이더 목록 반환."""
    available = []
    for name, cmd in CLI_COMMANDS.items():
        if shutil.which(cmd):
            available.append(name)
    return available


def select_review_pair(providers: list[str]) -> tuple[str, str]:
    """critic과 cross에 서로 다른 프로바이더 배정."""
    if len(providers) >= 2:
        return providers[0], providers[1]
    return providers[0], providers[0]


def select_judge(providers: list[str]) -> str:
    """취합 판정자 선택. claude > codex > gemini 우선순위."""
    for p in JUDGE_PRIORITY:
        if p in providers:
            return p
    return providers[0]


# ── 프롬프트 로드 ─────────────────────────────────────────────────────────────

def _load_prompt(workspace: str, name: str) -> str:
    """scripts/prompts/<name>.txt 로드."""
    path = os.path.join(workspace, PROMPTS_DIR, f"{name}.txt")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _read_document(workspace: str, rel_path: str) -> str:
    """설계 문서 읽기. 50KB 초과 시 앞 20KB + 뒤 10KB + 라인 넘버링."""
    full_path = os.path.join(workspace, rel_path)
    with open(full_path, encoding="utf-8") as f:
        content = f.read()

    if len(content) <= 50_000:
        lines = content.splitlines()
        return "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines))

    lines = content.splitlines()
    # 앞 부분: ~20KB
    front_lines = []
    front_size = 0
    front_end = 0
    for i, line in enumerate(lines):
        front_size += len(line) + 1
        if front_size > 20_000:
            front_end = i
            break
        front_lines.append(f"{i+1}: {line}")
    else:
        front_end = len(lines)

    # 뒷 부분: ~10KB
    back_lines = []
    back_size = 0
    back_start = len(lines)
    for i in range(len(lines) - 1, front_end - 1, -1):
        back_size += len(lines[i]) + 1
        if back_size > 10_000:
            back_start = i + 1
            break
        back_start = i

    back_numbered = [f"{i+1}: {lines[i]}" for i in range(back_start, len(lines))]
    omitted_kb = sum(len(lines[i]) + 1 for i in range(front_end, back_start)) // 1024

    truncation_marker = (
        f"\n--- [TRUNCATED: lines {front_end+1}-{back_start} omitted ({omitted_kb}KB)] ---\n"
    )

    return "\n".join(front_lines) + truncation_marker + "\n".join(back_numbered)


def _read_project_context(workspace: str) -> str:
    """docs/code_review/code-review.md 읽기 (있으면)."""
    path = os.path.join(workspace, "docs", "code_review", "code-review.md")
    if not os.path.exists(path):
        return "(프로젝트 컨텍스트 문서 없음)"
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if len(content) > 15_000:
            return content[:15_000] + "\n... (truncated)"
        return content
    except Exception:
        return "(읽기 실패)"


# ── CLI 실행 ──────────────────────────────────────────────────────────────────

def _resolve_cli(name: str) -> str:
    """CLI 이름을 실제 실행 가능 경로로 해석. Windows .cmd 래퍼 대응."""
    resolved = shutil.which(name)
    return resolved if resolved else name


def _build_exec_command(provider: str) -> list[str]:
    """프로바이더별 실행 명�� 구성 (프롬프트는 stdin으로 전달)."""
    if provider == "codex":
        return [_resolve_cli("codex"), "exec", "-s", "danger-full-access"]
    elif provider == "claude":
        return [_resolve_cli("claude"), "-p", "--output-format", "text"]
    elif provider == "gemini":
        return [_resolve_cli("gemini"), "-p"]
    else:
        return [_resolve_cli("codex"), "exec", "-s", "danger-full-access"]


def _run_provider(provider: str, prompt: str, workspace: str) -> str:
    """프로바이더 CLI 실행. 프롬프트는 stdin으로 전달."""
    cmd = _build_exec_command(provider)
    try:
        result = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=REVIEW_TIMEOUT,
            cwd=workspace,
        )
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if not stdout and stderr:
            return f"(provider error: {stderr[:500]})"
        return stdout or "(empty response)"
    except subprocess.TimeoutExpired:
        return f"(timeout: {REVIEW_TIMEOUT}s)"
    except FileNotFoundError:
        return f"(provider not found: {provider})"
    except Exception as e:
        return f"(execution error: {e})"


# ── 리뷰 실행 ─────────────────────────────────────────────────────────────────

# 자율 탐색 가능 프로바이더: 파일 경로만 전달, 직접 읽기
_AUTONOMOUS_PROVIDERS = {"codex", "gemini"}


def _build_review_prompt(
    template: str,
    provider: str,
    doc_content: str,
    context: str,
    rel_path: str,
    context_path: str,
) -> str:
    """프로바이더 특성에 맞는 프롬프트 구성.

    자율 탐색 프로바이더(codex, gemini): 파일 경로만 전달.
    비자율 프로바이더(claude): 내용 임베딩.
    """
    if provider in _AUTONOMOUS_PROVIDERS:
        return (
            f"{template}\n\n---\n\n"
            f"## 지시사항\n\n"
            f"1. 먼저 `{context_path}` 파일을 읽어 프로젝트 컨텍스트를 파악하라.\n"
            f"2. 그 다음 `{rel_path}` 파일을 읽고 리뷰하라.\n"
            f"3. 변경 파일의 호출자/피호출자도 직접 찾아서 읽어라.\n"
        )
    return f"{template}\n\n---\n\n## Project Context\n\n{context}\n\n---\n\n## Design Document to Review\n\n{doc_content}"


def run_critic(
    provider: str, doc_content: str, context: str, workspace: str,
    *, rel_path: str = "", context_path: str = "",
) -> str:
    """critic 리뷰 실행."""
    template = _load_prompt(workspace, "design_critic")
    prompt = _build_review_prompt(template, provider, doc_content, context, rel_path, context_path)
    return _run_provider(provider, prompt, workspace)


def run_cross(
    provider: str, doc_content: str, context: str, workspace: str,
    *, rel_path: str = "", context_path: str = "",
) -> str:
    """cross 리뷰 실행."""
    template = _load_prompt(workspace, "design_cross_review")
    prompt = _build_review_prompt(template, provider, doc_content, context, rel_path, context_path)
    return _run_provider(provider, prompt, workspace)


def run_aggregation(
    judge: str,
    critic_result: str,
    cross_result: str,
    doc_content: str,
    workspace: str,
) -> str:
    """취합 판정 실행."""
    template = _load_prompt(workspace, "design_aggregation")
    prompt = (
        f"{template}\n\n---\n\n"
        f"## Critic Review\n\n{critic_result}\n\n---\n\n"
        f"## Cross Review\n\n{cross_result}\n\n---\n\n"
        f"## Original Document (for reference)\n\n{doc_content[:10000]}"
    )
    return _run_provider(judge, prompt, workspace)


# ── 결과 저장 ─────────────────────────────────────────────────────────────────

def _write_result(
    workspace: str,
    rel_path: str,
    providers_used: dict[str, str],
    mode: str,
    verdict_content: str,
    trigger_source: str,
) -> str:
    """docs/reviews/ 에 결과 파일 저장. 파일 경로 반환."""
    results_dir = os.path.join(workspace, RESULTS_DIR)
    os.makedirs(results_dir, exist_ok=True)

    stem = Path(rel_path).stem
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d-%H%M%S")
    filename = f"{ts}-{stem}-review.md"
    result_path = os.path.join(results_dir, filename)

    providers_str = ", ".join(f"{role}={name}" for role, name in providers_used.items())
    header = (
        f"# Design Review: {stem}\n\n"
        f"> Source: {rel_path}\n"
        f"> Date: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"> Providers: {providers_str}\n"
        f"> Mode: {mode}\n"
        f"> Trigger: {trigger_source}\n\n"
        f"---\n\n"
    )

    with open(result_path, "w", encoding="utf-8") as f:
        f.write(header + verdict_content)

    return result_path


def _write_notification(workspace: str, rel_path: str, result_path: str) -> None:
    """알림 파일 생성."""
    notif_dir = os.path.join(workspace, NOTIFICATIONS_DIR)
    os.makedirs(notif_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = Path(rel_path).stem
    notif_path = os.path.join(notif_dir, f"{ts}-{stem}.txt")

    rel_result = os.path.relpath(result_path, workspace).replace("\\", "/")
    msg = f"[design-review] Review complete: {rel_path} → {rel_result}"
    with open(notif_path, "w", encoding="utf-8") as f:
        f.write(msg)


# ── 단일 문서 리뷰 처리 ───────────────────────────────────────────────────────

def process_review(workspace: str, rel_path: str, trigger_source: str) -> None:
    """하나의 설계 문서에 대해 리뷰 실행."""
    providers = detect_providers()

    if not providers:
        print(f"[watcher] No providers available, skipping: {rel_path}")
        _write_notification_skip(workspace, rel_path, "프로바이더 없음")
        return

    doc_content = _read_document(workspace, rel_path)
    context = _read_project_context(workspace)
    context_path = os.path.join("docs", "code_review", "code-review.md")

    if len(providers) == 1:
        # ── 단일 프로바이더: critic만 ──
        print(f"[watcher] Single provider ({providers[0]}), critic only: {rel_path}")
        critic_result = run_critic(
            providers[0], doc_content, context, workspace,
            rel_path=rel_path, context_path=context_path,
        )

        result_path = _write_result(
            workspace, rel_path,
            {"critic": providers[0]},
            "single-provider (critic only)",
            critic_result,
            trigger_source,
        )
    else:
        # ── 복수 프로바이더: critic + cross + 취합 ──
        p_a, p_b = select_review_pair(providers)
        judge = select_judge(providers)
        print(f"[watcher] Cross review: critic={p_a}, cross={p_b}, judge={judge}: {rel_path}")

        # 병렬 실행
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_critic = pool.submit(
                run_critic, p_a, doc_content, context, workspace,
                rel_path=rel_path, context_path=context_path,
            )
            future_cross = pool.submit(
                run_cross, p_b, doc_content, context, workspace,
                rel_path=rel_path, context_path=context_path,
            )

            critic_result = future_critic.result()
            cross_result = future_cross.result()

        # 취합 판정
        final = run_aggregation(judge, critic_result, cross_result, doc_content, workspace)

        result_path = _write_result(
            workspace, rel_path,
            {"critic": p_a, "cross": p_b, "judge": judge},
            f"cross-review ({len(providers)} providers)",
            final,
            trigger_source,
        )

    _write_notification(workspace, rel_path, result_path)
    print(f"[watcher] Done: {rel_path} → {result_path}")


def _write_notification_skip(workspace: str, rel_path: str, reason: str) -> None:
    """스킵 알림."""
    notif_dir = os.path.join(workspace, NOTIFICATIONS_DIR)
    os.makedirs(notif_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = Path(rel_path).stem
    notif_path = os.path.join(notif_dir, f"{ts}-{stem}.txt")
    with open(notif_path, "w", encoding="utf-8") as f:
        f.write(f"[design-review] Skipped: {rel_path} — {reason}")


# ── 큐 처리 루프 ──────────────────────────────────────────────────────────────

def _move_to_failed(workspace: str, queue_file: str, error: str) -> None:
    """실패 파일을 failed/ 로 이동."""
    failed_dir = os.path.join(workspace, FAILED_DIR)
    os.makedirs(failed_dir, exist_ok=True)
    try:
        with open(queue_file, encoding="utf-8") as f:
            data = json.load(f)
        data["error"] = error
        data["failed_at"] = time.time()
        dest = os.path.join(failed_dir, os.path.basename(queue_file))
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.remove(queue_file)
    except Exception:
        pass


def process_queue(workspace: str) -> int:
    """pending 큐에서 리뷰 처리. 처리 건수 반환."""
    pending_dir = os.path.join(workspace, PENDING_DIR)
    if not os.path.isdir(pending_dir):
        return 0

    files = sorted(
        [f for f in os.listdir(pending_dir) if f.endswith(".json")],
    )
    if not files:
        return 0

    processed = 0
    for fname in files:
        queue_file = os.path.join(pending_dir, fname)

        # quiet period 체크
        try:
            mtime = os.path.getmtime(queue_file)
            if time.time() - mtime < QUIET_PERIOD:
                continue  # 아직 편집 중일 수 있음
        except OSError:
            continue

        try:
            with open(queue_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            os.remove(queue_file)
            continue

        rel_path = data.get("file_path", "")
        trigger_source = data.get("trigger_source", "unknown")

        # 실제 파일 존재 확인
        if not os.path.exists(os.path.join(workspace, rel_path)):
            os.remove(queue_file)
            continue

        try:
            process_review(workspace, rel_path, trigger_source)
            os.remove(queue_file)
            processed += 1
        except Exception as e:
            print(f"[watcher] Error processing {rel_path}: {e}", file=sys.stderr)
            _move_to_failed(workspace, queue_file, str(e))

    return processed


# ── PID 관리 ──────────────────────────────────────────────────────────────────

def _write_pid(workspace: str) -> None:
    os.makedirs(os.path.join(workspace, QUEUE_DIR), exist_ok=True)
    pid_path = os.path.join(workspace, PID_FILE)
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid(workspace: str) -> None:
    pid_path = os.path.join(workspace, PID_FILE)
    try:
        os.remove(pid_path)
    except OSError:
        pass


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run_daemon(workspace: str) -> None:
    """폴링 데몬. idle timeout 후 자동 종료."""
    _write_pid(workspace)
    print(f"[watcher] Started (pid={os.getpid()}, idle_timeout={IDLE_TIMEOUT}s)")

    last_activity = time.time()

    try:
        while True:
            processed = process_queue(workspace)
            if processed > 0:
                last_activity = time.time()

            # idle timeout 체크
            if time.time() - last_activity > IDLE_TIMEOUT:
                print("[watcher] Idle timeout, shutting down")
                break

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("[watcher] Interrupted")
    finally:
        _remove_pid(workspace)


def run_sync_single(workspace: str, rel_path: str) -> None:
    """단일 문서 동기 리뷰."""
    print(f"[watcher] Sync review: {rel_path}")
    process_review(workspace, rel_path, "manual-sync")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Design review watcher daemon")
    parser.add_argument("workspace", help="프로젝트 워크스페이스 루트")
    parser.add_argument("--sync", metavar="REL_PATH", help="단일 문서 동기 리뷰")
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"[watcher] workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    if args.sync:
        run_sync_single(workspace, args.sync)
    else:
        run_daemon(workspace)


if __name__ == "__main__":
    main()
