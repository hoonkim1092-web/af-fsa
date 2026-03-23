"""
core/web_search.py — 사냥개(Hound) 모듈
Tavily API를 활용한 웹 검색 엔진.
에이전트가 외부 인터넷에서 필요한 문서·URL을 자동 수집한다.
"""
import os
import json
import urllib.request
import urllib.parse
from typing import Optional


def _get_tavily_key() -> str:
    """TAVILY_API_KEY 환경변수에서 키를 가져온다."""
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "TAVILY_API_KEY 환경변수가 설정되지 않았습니다.\n"
            "https://tavily.com 에서 무료 가입 후 키를 발급받아 .env에 추가하세요.\n"
            "예: TAVILY_API_KEY=tvly-xxxxx"
        )
    return key


def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "advanced",
    include_answer: bool = True,
    topic: str = "general",
) -> list[dict]:
    """
    Tavily Search API를 호출하여 웹 검색 결과를 반환한다.

    Args:
        query: 검색 쿼리 문자열
        max_results: 최대 결과 수 (기본 5)
        search_depth: "basic" 또는 "advanced" (기본 advanced)
        include_answer: AI 요약 답변 포함 여부
        topic: "general" 또는 "news"

    Returns:
        list[dict]: 각 항목은 {url, title, content, score} 포함.
        빈 리스트는 결과 없음을 의미.
    """
    api_key = _get_tavily_key()

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": include_answer,
        "topic": topic,
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"⚠️ [사냥개] Tavily 검색 실패: {e}")
        return []

    results = body.get("results", [])
    answer = body.get("answer", "")

    parsed: list[dict] = []
    for item in results[:max_results]:
        parsed.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("content", "")[:500],
            "score": item.get("score", 0.0),
        })

    if answer:
        print(f"💡 [사냥개] Tavily 요약: {answer[:200]}...")

    print(f"🐕 [사냥개] {len(parsed)}개의 검색 결과를 수집했습니다.")
    return parsed


def extract_urls(search_results: list[dict]) -> list[str]:
    """검색 결과에서 URL 목록만 추출한다."""
    return [r["url"] for r in search_results if r.get("url")]


if __name__ == "__main__":
    # 빠른 테스트
    import sys
    query = " ".join(sys.argv[1:]) or "AI Agent Architecture 2026"
    results = tavily_search(query, max_results=3)
    for r in results:
        print(f"  [{r['score']:.2f}] {r['title']}")
        print(f"         {r['url']}")
    print(f"\nURLs: {extract_urls(results)}")
