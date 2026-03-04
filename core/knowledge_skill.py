import os
import re
import yaml
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class KnowledgeSkill:
    id: str
    name: str
    description: str
    content: str
    source_path: str
    updated_at: float

def parse_skill_md(path: str) -> Optional[KnowledgeSkill]:
    """마크다운 파일에서 YAML 프론트매터와 본문을 분리하여 파싱합니다."""
    if not os.path.exists(path):
        return None
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
            
        # YAML Frontmatter 추출 (--- ... ---)
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', text, re.DOTALL)
        if match:
            meta_raw = match.group(1)
            content = match.group(2).strip()
            meta = yaml.safe_load(meta_raw) or {}
        else:
            meta = {}
            content = text.strip()
            
        sid = os.path.basename(os.path.dirname(path)) # 디렉토리명을 ID로 사용
        return KnowledgeSkill(
            id=sid,
            name=str(meta.get("name", sid)),
            description=str(meta.get("description", "")),
            content=content,
            source_path=path,
            updated_at=os.path.getmtime(path)
        )
    except Exception as e:
        print(f"[Knowledge] Error parsing {path}: {e}")
        return None

def scan_knowledge_skills(base_dir: str) -> List[KnowledgeSkill]:
    """지정된 디렉토리에서 skill.md 파일을 찾아 KnowledgeSkill 객체 리스트를 반환합니다."""
    skills = []
    if not os.path.exists(base_dir):
        return skills
        
    for root, dirs, files in os.walk(base_dir):
        if "skill.md" in files:
            path = os.path.join(root, "skill.md")
            skill = parse_skill_md(path)
            if skill:
                skills.append(skill)
    return skills

def filter_relevant_knowledge(skills: List[KnowledgeSkill], task_text: str) -> List[KnowledgeSkill]:
    """태스크와 관련된 Knowledge 스킬을 필터링합니다. (단순 키워드 매칭 -> 향후 LLM 기반 고도화 가능)"""
    relevant = []
    task_lower = task_text.lower()
    
    for s in skills:
        if s.name.lower() in task_lower or any(kw.lower() in task_lower for kw in s.description.split()):
            relevant.append(s)
            
    return relevant

def build_knowledge_prompt(skills: List[KnowledgeSkill]) -> str:
    """선택된 지식 스킬들을 시스템 프롬프트용 텍스트로 변환합니다."""
    if not skills:
        return ""
        
    prompt = "\n\n[Knowledge Skills Loaded]\n"
    for s in skills:
        prompt += f"\n### {s.name}\n"
        prompt += f"**Description:** {s.description}\n"
        prompt += f"**Procedure:**\n{s.content}\n"
        prompt += "\n---\n"
    return prompt
