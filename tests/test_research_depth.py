import sys
import os
import unittest

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.research_engine import classify_research_depth, ResearchMode

class TestResearchDepth(unittest.TestCase):
    def test_fast_mode_simple_query(self):
        # 단순 쿼리는 FAST 모드여야 함
        query = "What is AI?"
        self.assertEqual(classify_research_depth(query), ResearchMode.FAST)

    def test_fast_mode_keyword(self):
        # 'check' 키워드가 포함된 경우 FAST 모드
        query = "Check the definition of LLM."
        self.assertEqual(classify_research_depth(query), ResearchMode.FAST)

    def test_deep_mode_architecture_keyword(self):
        # 'architecture' 키워드는 DEEP 모드 트리거
        query = "Explain the AI Agent Architecture for 2026."
        self.assertEqual(classify_research_depth(query), ResearchMode.DEEP)

    def test_deep_mode_long_query(self):
        # 200자 이상의 장문 쿼리는 DEEP 모드
        query = "This is a very long query intended to test the threshold of the research depth classifier. It contains many words and should exceed the two hundred character limit set in the implementation to trigger the deep research mode automatically for better analysis."
        self.assertEqual(classify_research_depth(query), ResearchMode.DEEP)

    def test_deep_mode_missing_skills(self):
        # 미싱 스킬이 3개 이상이면 DEEP 모드
        query = "Simple task"
        self.assertEqual(classify_research_depth(query, missing_skills_count=3), ResearchMode.DEEP)

    def test_fast_mode_one_missing_skill(self):
        # 미싱 스킬이 적으면 쿼리에 따라 결정 (여기서는 단순 쿼리라 FAST)
        query = "Simple task"
        self.assertEqual(classify_research_depth(query, missing_skills_count=1), ResearchMode.FAST)

if __name__ == "__main__":
    unittest.main()
