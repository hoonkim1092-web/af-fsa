---
name: af-skill-system
description: "Agent Factory 스킬 서브시스템 전문 지식. core/skill_*.py 수정 시 트리거. 스킬 조달 파이프라인, registry 구조, lifecycle."
---

<overview>
AF 스킬 시스템은 18개 파일로 구성된 핵심 서브시스템이다.
스킬 생성(forge), 조달(procure), 평가(eval), 승격(promote), 로딩(load)의 전체 라이프사이클을 관리한다.
</overview>

<when-to-use>
- `core/skill_*.py` 파일 수정 시
- `core/external_skill_*.py` 파일 수정 시
- 스킬 조달 로직 변경 시
- registry.yaml 구조 변경 시
</when-to-use>

<system>

## 핵심 파일 관계

```
skill_procurer.py (876줄) ─── 스킬 조달 오케스트레이터
  ├── skill_retrieval_engine.py (345줄) ── 재사용 판단 (ranked_reuse/enhance/shadow_reuse)
  ├── external_skill_sources.py (507줄) ── 외부 소스 탐색 (codex_official, claude_repo 등)
  ├── skill_forge.py (236줄) ── LLM 기반 스킬 코드 생성
  └── skill_eval_harness.py (732줄) ── 품질 평가

skill_registry.py (536줄) ─── 스킬 메타데이터 중앙 레지스트리 (싱글톤)
  ├── skill_metadata.py (129줄) ── SkillMetadata, @skill_metadata 데코레이터
  ├── skill_metadata_adapter.py (449줄) ── YAML/MD → SkillMetadata 변환
  └── skill_loader.py (341줄) ── 12-Cap 동적 로딩

skill_creator.py (1,092줄) ── 스킬 생성/진화/은퇴 CLI
skill_promotion.py (295줄) ── lifecycle stage 전환 (draft→canary→active→retired)
skill_evolution_bus.py (243줄) ── 7단계 캐시 무효화 체인
```

## 스킬 조달 파이프라인 (procure_multiple)

```
스킬 요청
  ↓
[1] 정확 매칭 — resolve_skill_paths() + resolve_knowledge_skill_path()
    탐색 경로: PROJECT_SKILLS_DIR → SKILLS_DIR → get_codex_skill_roots()
  ↓ 없으면
[2] 재사용 판단 — SkillRetrievalEngine.decide_reuse()
    → ranked_reuse: 기존 스킬 그대로 설치
    → enhance: 기존 스킬 + 부족 capability 보강
    → shadow_reuse: 참고만 하고 새로 빌드
  ↓ 해당 없으면
[3] 외부 소스 — ExternalSkillResolver.resolve_and_install()
    우선순위: codex_official → claude_repo → codex_repo → registry → external_cache
  ↓ 없으면
[4] 신규 빌드 — builder.build_skill() → LLM 코드 생성
    → _evaluate_and_promote_built_skill() → 품질 평가
    → registry.register_built()
```

## 외부 스킬 소스 (external_skill_sources.py)

| 소스 클래스 | 소스 ID | 동작 |
|------------|---------|------|
| CodexOfficialSkillSource | codex_official | 로컬 디렉토리 SKILL.md 스캔 |
| RepoCacheSkillSource | claude_repo, codex_repo | Git 레포 clone → 후보 스캔 |
| ManifestSkillSource | registry | install_candidates에서 로드 |
| CacheSweepSkillSource | external_cache | _external_cache/ 전체 스캔 |

## 스킬 로딩 (12-Cap)

`skill_loader.py`의 `AdaptiveSkillLoader`:
- 입력 해시값, 키워드, 카테고리 매칭으로 점수 산출
- 상위 12개만 동적 주입 (MAX_SKILLS_IN_CONTEXT)
- incompatible_with 충돌 자동 해결

## Lifecycle Stage

```
draft → canary → active → retired
         ↑                    │
         └────────────────────┘ (재활성화 가능)
```

각 전환은 SkillPromotionManager가 관리하며, quality gate(SkillEvalHarness) 통과 필요.

</system>
