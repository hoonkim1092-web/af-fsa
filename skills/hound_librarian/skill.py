"""
skills/hound_librarian/skill.py — 사냥개-사서(Hound-Librarian) 파이프라인 스킬

3단계 자동화 리서치:
  Step 1 (🐕 사냥개): Tavily 웹 검색으로 URL 수집
  Step 2 (📥 소스 주입): NotebookLM에 수집된 소스 자동 업로드
  Step 3 (📚 사서): NotebookLM 쿼리로 심층 인사이트 추출
"""
import os
import sys
import json
from datetime import datetime

# 경로 설정: agent-factory 루트를 Python Path에 추가
_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_SKILL_DIR))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


def propose(ctx):
    """스킬 메타 정보를 반환한다."""
    return {
        "description": "3단계 자동화 리서치 파이프라인 (웹 검색 → 소스 주입 → 심층 분석)",
        "required_keys": ["topic"],
        "optional_keys": ["max_results", "search_depth", "analysis_questions", "output_format"],
    }


def apply(ctx):
    """
    사냥개-사서 파이프라인을 실행한다.

    ctx 필수 키:
        topic (str): 리서치 주제

    ctx 선택 키:
        max_results (int): 검색 결과 수 (기본 5)
        search_depth (str): "basic" 또는 "advanced" (기본 "advanced")
        analysis_questions (list[str]): 추가 분석 질문 목록
        output_format (str): "markdown" 또는 "json" (기본 "markdown")
        artifacts_dir (str): 보고서 저장 경로
    """
    try:
        from core.web_search import tavily_search, extract_urls
        from core.research_engine import (
            create_notebook,
            inject_sources,
            query_notebooklm,
            generate_deep_research_prompt,
        )
    except ImportError as e:
        return {"ok": False, "error": f"필수 모듈 임포트 실패: {e}"}

    topic = ctx.get("topic")
    if not topic:
        return {"ok": False, "error": "topic이 지정되지 않았습니다."}

    max_results = int(ctx.get("max_results", 5))
    search_depth = ctx.get("search_depth", "advanced")
    analysis_questions = ctx.get("analysis_questions", [])
    artifacts_dir = ctx.get("artifacts_dir", ".")
    os.makedirs(artifacts_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"hound_librarian_{timestamp}.md"
    report_path = os.path.join(artifacts_dir, report_filename)

    results = {
        "ok": False,
        "topic": topic,
        "phase_1_search": {},
        "phase_2_injection": {},
        "phase_3_analysis": {},
        "report_path": report_path,
    }

    # ═══════════════════════════════════════════════════════════════════
    # Phase 1: 🐕 사냥개 (Hound) — 웹 검색
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"🐕 [Phase 1: 사냥개] 웹 검색 시작: '{topic}'")
    print(f"{'='*60}")

    try:
        search_results = tavily_search(
            query=topic,
            max_results=max_results,
            search_depth=search_depth,
        )
    except EnvironmentError as e:
        return {"ok": False, "error": str(e)}

    urls = extract_urls(search_results)
    results["phase_1_search"] = {
        "query": topic,
        "results_count": len(search_results),
        "urls": urls,
        "results": search_results,
    }

    if not urls:
        print("⚠️ [사냥개] 검색 결과가 없습니다. 기본 아카이브로 폴백합니다.")
        # 검색 결과 없어도 기본 아카이브 쿼리는 시도
        notebook_id = None
        results["phase_2_injection"] = {"skipped": True, "reason": "검색 결과 없음"}
    else:
        # ═══════════════════════════════════════════════════════════════
        # Phase 2: 📥 소스 주입 (Source Injection) — NotebookLM에 업로드
        # ═══════════════════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"📥 [Phase 2: 소스 주입] NotebookLM에 {len(urls)}개 소스 업로드")
        print(f"{'='*60}")

        notebook_title = f"Research: {topic} ({timestamp[:8]})"
        notebook_id = create_notebook(notebook_title)

        if not notebook_id:
            print("⚠️ [소스 주입] 노트북 생성 실패. 기본 아카이브로 폴백합니다.")
            notebook_id = None
            results["phase_2_injection"] = {"skipped": True, "reason": "노트북 생성 실패"}
        else:
            injection_result = inject_sources(notebook_id, urls, delay=1.5)
            results["phase_2_injection"] = {
                "notebook_id": notebook_id,
                "notebook_title": notebook_title,
                **injection_result,
            }

    # ═══════════════════════════════════════════════════════════════════
    # Phase 3: 📚 사서 (Librarian) — 심층 분석
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"📚 [Phase 3: 사서] 심층 분석 쿼리 실행")
    print(f"{'='*60}")

    # 메인 분석 프롬프트
    main_prompt = generate_deep_research_prompt(topic)

    # notebook_id가 있으면 새 노트북에 쿼리, 없으면 기본 아카이브
    main_insight = query_notebooklm(main_prompt, notebook_id=notebook_id)

    # 추가 분석 질문 처리
    additional_insights = []
    for q in (analysis_questions or []):
        print(f"  📖 추가 질문: {q[:80]}...")
        insight = query_notebooklm(q, notebook_id=notebook_id)
        additional_insights.append({"question": q, "answer": insight})

    results["phase_3_analysis"] = {
        "notebook_id": notebook_id,
        "main_insight": main_insight[:3000] if main_insight else "(분석 결과 없음)",
        "additional_insights": additional_insights,
    }

    # ═══════════════════════════════════════════════════════════════════
    # 보고서 생성
    # ═══════════════════════════════════════════════════════════════════
    sources_section = "\n".join(
        f"- [{r.get('title', 'Untitled')}]({r.get('url', '')})" for r in search_results
    )
    additional_section = ""
    if additional_insights:
        additional_section = "\n## 추가 분석\n\n"
        for item in additional_insights:
            additional_section += f"### Q: {item['question']}\n\n{item['answer']}\n\n"

    report = f"""# 🔬 사냥개-사서 리서치 보고서: {topic}

**생성일시**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**검색 엔진**: Tavily ({search_depth})
**분석 엔진**: NotebookLM
**노트북 ID**: {notebook_id or '기본 아카이브'}

---

## Phase 1: 수집된 소스 ({len(search_results)}건)

{sources_section}

## Phase 2: 소스 주입 현황

- 주입 성공: {results['phase_2_injection'].get('injected', 'N/A')}건
- 주입 실패: {results['phase_2_injection'].get('failed', 'N/A')}건

## Phase 3: 심층 분석

{main_insight or '(분석 결과를 가져오지 못했습니다.)'}

{additional_section}

---
*Generated by Hound-Librarian Pipeline (Agent Factory)*
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    results["ok"] = True
    results["content_preview"] = report[:2000]

    print(f"\n{'='*60}")
    print(f"✅ [완료] 보고서 저장됨: {report_path}")
    print(f"{'='*60}\n")

    return results


def test(ctx):
    """
    스킬 기능 테스트.
    TAVILY_API_KEY가 없으면 Phase 1만 건너뛰고 구조 검증만 한다.
    """
    tmp_ctx = dict(ctx or {})
    tmp_ctx["topic"] = "AI Agent Architecture Patterns"
    tmp_ctx["max_results"] = 2

    has_tavily = bool(os.getenv("TAVILY_API_KEY", "").strip())

    if not has_tavily:
        print("⚠️ [테스트] TAVILY_API_KEY 미설정 — 구조 검증만 수행합니다.")
        # 모듈 임포트 검증
        try:
            from core.web_search import tavily_search, extract_urls
            from core.research_engine import (
                create_notebook,
                inject_sources,
                query_notebooklm,
                generate_deep_research_prompt,
            )
            return {
                "ok": True,
                "message": "모듈 임포트 성공. TAVILY_API_KEY 설정 후 전체 테스트 가능.",
            }
        except ImportError as e:
            return {"ok": False, "error": f"모듈 임포트 실패: {e}"}

    # 전체 파이프라인 테스트
    res = apply(tmp_ctx)
    # 테스트 후 생성된 파일 정리
    report_path = res.get("report_path")
    if report_path and os.path.exists(report_path):
        try:
            os.remove(report_path)
        except Exception:
            pass

    if res.get("ok"):
        return {"ok": True, "message": "사냥개-사서 파이프라인 E2E 테스트 성공."}
    return res
