import sys
import os
from unittest.mock import patch

# agent_launcher.py가 있는 경로를 sys.path에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_launcher import AgentFactory

def run_factory_for_midori():
    print("🚀 [System] Saiba Midori Agent Factory 가동 시작...")
    
    # 1. Factory 인스턴스 생성
    factory = AgentFactory()
    
    # 2. Git Commit 여부를 묻는 input()을 'yes'로 모킹(Mocking)하여 자동 진행
    with patch('builtins.input', return_value='yes'):
        # 3. Factory 실행
        # role_spec을 "Saiba Midori"로 지정하면, agents/saiba_midori.yaml을 로드하고
        # 정의된 스킬 중 없는 것을 찾아 Research -> Build 흐름을 탄다.
        factory.run(
            task_input="모든 UI/UX 관련 스킬(generate_image, create_design_system 등)이 누락되어 있다면 구현하고 검증하라.",
            role_spec="Saiba Midori"
        )

if __name__ == "__main__":
    run_factory_for_midori()
