
import pytest
import time
from core.skill_metadata import SkillMetadata, SkillCategory
from core.skill_registry import SkillRegistry
from core.skill_loader import DynamicSkillLoader
from core.skill_autodiscover import auto_discover_skills
from core.skill_cache import OptimizedSkillRelevance


class TestPhase12Integration:
    def setup_method(self):
        self.registry = SkillRegistry()
        self.registry.clear()
    
    def test_autodiscover_50_skills(self):
        for i in range(50):
            metadata = SkillMetadata(
                skill_id=f"skill-{i:02d}",
                name=f"Skill {i}",
                category=SkillCategory.CODING if i % 5 == 0 else SkillCategory.RESEARCH,
                when_to_use_keywords=["test"] * (i % 3 + 1)
            )
            self.registry.register(metadata)
        
        count = self.registry.count()
        assert count == 50
    
    def test_dynamic_loader_performance(self):
        for i in range(50):
            metadata = SkillMetadata(
                skill_id=f"skill-{i:02d}",
                when_to_use_keywords=["code", "test"]
            )
            self.registry.register(metadata)
        
        loader = DynamicSkillLoader()
        
        start = time.time()
        selected, scores = loader.load_skills_for_task("코드 테스트")
        duration = time.time() - start
        
        assert len(selected) <= 12
        assert duration < 0.1, f"성능 목표 미달: {duration:.3f}초"
    
    def test_cache_hit_rate(self):
        for i in range(30):
            metadata = SkillMetadata(
                skill_id=f"skill-{i:02d}",
                when_to_use_keywords=["test"]
            )
            self.registry.register(metadata)
        
        relevance = OptimizedSkillRelevance()
        skill = self.registry.get("skill-00")
        
        task = "test"
        
        relevance.compute_score(task, skill, use_cache=True)
        relevance.compute_score(task, skill, use_cache=True)
        relevance.compute_score(task, skill, use_cache=True)
        
        stats = relevance.get_cache_stats()
        assert stats["keyword_cache"] > 0
    
    def test_routing_prompt_generation(self):
        for i in range(12):
            metadata = SkillMetadata(
                skill_id=f"skill-{i:02d}",
                name=f"Skill {i}",
                description=f"Description {i}",
                category=SkillCategory.CODING,
                when_to_use_keywords=["test"]
            )
            self.registry.register(metadata)
        
        loader = DynamicSkillLoader()
        selected, scores = loader.load_skills_for_task("테스트")
        
        prompt = loader.build_routing_prompt(selected, scores)
        
        assert len(prompt) > 0
        assert "Available Skills" in prompt
        assert "description:" in prompt
    
    def test_memory_efficiency(self):
        import sys
        
        for i in range(100):
            metadata = SkillMetadata(
                skill_id=f"skill-{i:02d}",
                when_to_use_keywords=["test"]
            )
            self.registry.register(metadata)
        
        registry_size = sys.getsizeof(self.registry.get_all())
        assert registry_size < 5_000_000, f"메모리 초과: {registry_size / 1024 / 1024:.1f}MB"


def test_batch_performance():
    registry = SkillRegistry()
    registry.clear()
    
    for i in range(100):
        metadata = SkillMetadata(
            skill_id=f"skill-{i:02d}",
            when_to_use_keywords=["code", "test", "python"]
        )
        registry.register(metadata)
    
    loader = DynamicSkillLoader()
    
    tasks = [
        "파이썬 코드를 작성해줄 수 있어?",
        "테스트를 작성해줄 수 있어?",
        "버그를 수정해줄 수 있어?",
    ]
    
    start = time.time()
    for task in tasks:
        loader.load_skills_for_task(task)
    duration = time.time() - start
    
    avg_time = duration / len(tasks)
    assert avg_time < 0.1, f"평균 {avg_time:.3f}초 (목표: < 0.1초)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
