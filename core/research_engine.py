"""
core/research_engine.py — 사서(Librarian) 모듈 + NotebookLM 통합 엔진
NotebookLM을 활용한 노트북 생성, 소스 주입, 심층 분석 쿼리.
"""
import sys
import os
import subprocess
import json
import time
from typing import Optional

# ── 기본 NotebookLM 아카이브 (Himari 전용 비밀 서고) ──────────────────────
DEFAULT_ARCHIVE_NOTEBOOK_ID = "eaa34a54-a898-46a0-835a-cdb6024887f0"


from enum import Enum

class ResearchMode(Enum):
    FAST = "fast"
    DEEP = "deep"


def _nlm_env() -> dict:
    """NotebookLM CLI 실행을 위한 환경변수를 준비한다."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _nlm_cli(*args, timeout: int = 120) -> subprocess.CompletedProcess:
    """NotebookLM CLI 래퍼. 인증 만료 시 1회 자동 재인증."""
    cmd = [sys.executable, "-m", "notebooklm_tools.cli.main", *args]
    env = _nlm_env()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)

    if p.returncode != 0 and _is_auth_error(p.stderr or ""):
        print("[RESEARCH] 인증 만료 감지 → 자동 재인증 시도...")
        if _reauth_notebooklm():
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)

    return p


def _is_auth_error(stderr_text: str) -> bool:
    s = (stderr_text or "").lower()
    flags = [
        "authentication expired",
        "rpc error 16",
        "clientauthenticationerror",
        "run 'nlm login'",
    ]
    return any(f in s for f in flags)


def _reauth_notebooklm() -> bool:
    env = _nlm_env()
    try:
        print("[RESEARCH] Attempting NotebookLM CLI re-authentication...")
        p = subprocess.run(
            [sys.executable, "-m", "notebooklm_tools.cli.main", "login"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=180,
        )
        return p.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 0. 리서치 복잡도 판정기 (Autonomous Depth Classifier)
# ═══════════════════════════════════════════════════════════════════════════

def classify_research_depth(query: str, missing_skills_count: int = 0) -> ResearchMode:
    """
    쿼리의 복잡도와 상황을 분석하여 Fast 또는 Deep 모드를 결정한다.
    """
    q = query.lower()
    
    # Deep Research 트리거 키워드
    deep_keywords = [
        "architecture", "아키텍처", "설계", "strategy", "전략", "심층", "deep", 
        "analysis", "분석", "비교", "compare", "benchmark", "벤치마크",
        "recipe", "playbook", "레시피", "플레이북", "구조", "structure"
    ]
    
    # Fast Research 트리거 키워드
    fast_keywords = [
        "check", "확인", "정의", "뜻", "what is", "단순", "simple", "quick"
    ]

    # 1. 쿼리 길이 및 키워드 기반 판정
    if any(k in q for k in deep_keywords) or len(query) > 200:
        return ResearchMode.DEEP
    
    # 2. 미싱 스킬 수 기반 판정 (3개 이상이면 심층 분석 필요)
    if missing_skills_count >= 3:
        return ResearchMode.DEEP
        
    if any(k in q for k in fast_keywords):
        return ResearchMode.FAST
        
    # 기본값은 FAST (효율성 우선)
    return ResearchMode.FAST


# ═══════════════════════════════════════════════════════════════════════════
# 1. 기존 사서 기능: NotebookLM 쿼리
# ═══════════════════════════════════════════════════════════════════════════

def query_notebooklm(query: str, notebook_id: Optional[str] = None, mode: Optional[ResearchMode] = None) -> str:
    """
    NotebookLM에 쿼리를 던져 심층 분석 결과를 가져온다.
    notebook_id를 지정하지 않으면 기본 아카이브(비밀 서고)를 사용한다.
    mode가 None이면 classify_research_depth를 통해 자동 결정한다.
    """
    nb_id = notebook_id or DEFAULT_ARCHIVE_NOTEBOOK_ID
    
    # 모드 자율 선택
    target_mode = mode or classify_research_depth(query)
    print(f"[RESEARCH] NotebookLM Query (Mode: {target_mode.value})")

    try:
        # CLI 호출에 모드 파라미터 추가 (CLI 버전 호환성 고려)
        p = _nlm_cli("query", "notebook", nb_id, query, "--mode", target_mode.value)

        if p.returncode != 0:
            # --mode 미지원 시 폴백
            if "unknown argument: --mode" in (p.stderr or "").lower():
                p = _nlm_cli("query", "notebook", nb_id, query)
            else:
                print(f"[RESEARCH Error] NotebookLM query failed: {(p.stderr or '').strip()}")
                return ""

        return p.stdout.strip()
    except Exception as e:
        print(f"[RESEARCH Error] NotebookLM Connection failed: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. 소스 주입(Source Injection) — 사냥개가 수집한 URL을 NotebookLM에 업로드
# ═══════════════════════════════════════════════════════════════════════════

def create_notebook(title: str) -> Optional[str]:
    """
    NotebookLM에 새 노트북을 생성하고 notebook_id를 반환한다.
    실패 시 None을 반환한다.
    """
    try:
        p = _nlm_cli("notebook", "create", title, timeout=60)
        if p.returncode != 0:
            print(f"⚠️ [사서] 노트북 생성 실패: {(p.stderr or '').strip()}")
            return None

        # CLI 출력에서 notebook_id 파싱
        output = p.stdout.strip()
        # JSON 형식이면 파싱 시도
        try:
            data = json.loads(output)
            nb_id = (
                data.get("notebook_id")
                or data.get("id")
                or data.get("notebook", {}).get("id")
            )
            if nb_id:
                print(f"📓 [사서] 새 노트북 생성됨: {title} (ID: {nb_id})")
                return str(nb_id)
        except (json.JSONDecodeError, AttributeError):
            pass

        # 텍스트에서 UUID 패턴 추출 시도
        import re
        uuid_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            output,
            re.IGNORECASE,
        )
        if uuid_match:
            nb_id = uuid_match.group(0)
            print(f"📓 [사서] 새 노트북 생성됨: {title} (ID: {nb_id})")
            return nb_id

        print(f"⚠️ [사서] 노트북은 생성되었으나 ID를 추출할 수 없습니다.\n  출력: {output[:300]}")
        return None
    except Exception as e:
        print(f"⚠️ [사서] 노트북 생성 중 오류: {e}")
        return None


def inject_source_url(notebook_id: str, url: str) -> bool:
    """
    단일 URL을 NotebookLM 노트북에 소스로 추가한다.
    """
    try:
        p = _nlm_cli("source", "add", notebook_id, "--url", url, timeout=60)
        if p.returncode != 0:
            print(f"  ⚠️ 소스 추가 실패 ({url[:60]}): {(p.stderr or '').strip()[:200]}")
            return False
        print(f"  ✅ 소스 추가됨: {url[:80]}")
        return True
    except Exception as e:
        print(f"  ⚠️ 소스 추가 중 오류 ({url[:60]}): {e}")
        return False


def inject_source_text(notebook_id: str, text: str, title: str = "Pasted Text") -> bool:
    """
    텍스트 내용을 NotebookLM 노트북에 소스로 추가한다.
    """
    try:
        p = _nlm_cli(
            "source", "add", notebook_id,
            "--text", text[:45000], "--title", title,
            timeout=60,
        )
        if p.returncode != 0:
            print(f"  ⚠️ 텍스트 소스 추가 실패 ({title}): {(p.stderr or '').strip()[:200]}")
            return False
        print(f"  ✅ 텍스트 소스 추가됨: {title}")
        return True
    except Exception as e:
        print(f"  ⚠️ 텍스트 소스 추가 중 오류 ({title}): {e}")
        return False


def inject_sources(notebook_id: str, urls: list[str], delay: float = 1.0) -> dict:
    """
    여러 URL을 NotebookLM 노트북에 순차적으로 소스로 주입한다.

    Args:
        notebook_id: 대상 노트북 ID
        urls: 업로드할 URL 목록
        delay: 각 요청 사이의 대기 시간 (초)

    Returns:
        dict: {"injected": 성공 수, "failed": 실패 수, "total": 전체 수}
    """
    injected = 0
    failed = 0
    total = len(urls)

    print(f"📥 [사서] {total}개의 소스를 노트북({notebook_id[:8]}...)에 주입합니다...")

    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{total}] 소스 주입 중: {url[:80]}")
        ok = inject_source_url(notebook_id, url)
        if ok:
            injected += 1
        else:
            failed += 1

        if i < total and delay > 0:
            time.sleep(delay)

    print(f"📊 [사서] 소스 주입 완료: 성공 {injected}/{total}, 실패 {failed}")
    return {"injected": injected, "failed": failed, "total": total}


# ═══════════════════════════════════════════════════════════════════════════
# 3. 기존 프롬프트 생성기
# ═══════════════════════════════════════════════════════════════════════════

def generate_deep_research_prompt(role: str) -> str:
    """
    Generates a Domain Deep-Dive template for Himari.
    Ensures research yields specific seasonal context, pricing, tools, and executable recipes.
    """
    return f"""
    Target Role/Domain: {role}
    
    WARNING: Do NOT provide generic encyclopedia answers. 
    Act as a hyper-specialized domain expert. Break down the exact operational realities for '{role}'.
    
    You MUST provide detailed, actionable data addressing these 4 pillars:
    
    1. [Current Context (Season/Time)]: What is critical RIGHT NOW? (e.g., Seasonal ingredients like 방어 in Winter, current market trends, time-sensitive risks).
    2. [Business/FinOps (Cost/Margin)]: What are the exact cost drivers? What is the target cost percentage (e.g., 'Target food cost 35%')? How does this role maximize profit margins and defend ROI?
    3. [Resources/Tools]: What specific, professional-grade tools/equipment/software/prerequisites are absolutely mandatory? (e.g., 야나기바, specific POS, specific machinery).
    4. [Execution (Recipe/Playbook)]: Provide a step-by-step, actionable 'Recipe' or 'Playbook' that this role executes on the floor. Be concrete, not abstract.

    Return the insights structured clearly around these 4 pillars.
    """.strip()
