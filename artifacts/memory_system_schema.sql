-- ============================================================
-- Next-Generation Memory System — Supabase Schema (Phase 10)
-- 비파괴적: CREATE IF NOT EXISTS, 기존 테이블 미수정
-- 롤백: 새 테이블 DROP으로 이전 상태 복원
-- ============================================================

-- 1) pgvector 확장 (이미 cortex_memory에서 사용 중일 수 있음)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2) 통합 메모리 레코드
CREATE TABLE IF NOT EXISTS memory_records (
    record_id    TEXT PRIMARY KEY,
    memory_type  TEXT NOT NULL DEFAULT 'semantic',   -- episodic/semantic/procedural/working/graph
    scope        TEXT NOT NULL DEFAULT 'local',      -- local/global/session
    project_id   TEXT,
    content      TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}',
    embedding    vector(768),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    accessed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    access_count INTEGER NOT NULL DEFAULT 0,
    ttl_hours    REAL,                               -- NULL = 영구
    source_backend TEXT NOT NULL DEFAULT '',
    causal_links TEXT[] NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_mr_project    ON memory_records (project_id);
CREATE INDEX IF NOT EXISTS idx_mr_type       ON memory_records (memory_type);
CREATE INDEX IF NOT EXISTS idx_mr_scope      ON memory_records (scope);
CREATE INDEX IF NOT EXISTS idx_mr_updated    ON memory_records (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mr_hash       ON memory_records (content_hash);

-- IVFFlat 인덱스 (embedding 시맨틱 검색용)
-- 참고: 데이터 100건 이상 삽입 후 생성 권장
-- CREATE INDEX IF NOT EXISTS idx_mr_embedding ON memory_records
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- 3) 실행 에피소드
CREATE TABLE IF NOT EXISTS episodes (
    episode_id   TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    project_id   TEXT,
    agent_name   TEXT NOT NULL DEFAULT '',
    task_input   TEXT NOT NULL DEFAULT '',
    actions      JSONB NOT NULL DEFAULT '[]',
    outcome      TEXT NOT NULL DEFAULT '',            -- success/failure/partial
    error_info   TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    causal_links TEXT[] NOT NULL DEFAULT '{}',
    metadata     JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ep_project  ON episodes (project_id);
CREATE INDEX IF NOT EXISTS idx_ep_outcome  ON episodes (outcome);
CREATE INDEX IF NOT EXISTS idx_ep_created  ON episodes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ep_run      ON episodes (run_id);

-- 4) 지식 그래프 — 노드
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    node_id      TEXT PRIMARY KEY,
    node_type    TEXT NOT NULL DEFAULT 'fact',        -- problem/cause/solution/fact/pattern
    label        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    project_id   TEXT,                               -- NULL = 글로벌 지식
    confidence   REAL NOT NULL DEFAULT 1.0,
    embedding    vector(768),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_kn_project  ON knowledge_nodes (project_id);
CREATE INDEX IF NOT EXISTS idx_kn_type     ON knowledge_nodes (node_type);

-- 5) 지식 그래프 — 엣지
CREATE TABLE IF NOT EXISTS knowledge_edges (
    edge_id      TEXT PRIMARY KEY,
    edge_type    TEXT NOT NULL DEFAULT 'related_to',  -- caused_by/solved_by/related_to/depends_on/evolved_from
    source_id    TEXT NOT NULL REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
    target_id    TEXT NOT NULL REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
    weight       REAL NOT NULL DEFAULT 1.0,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ke_source ON knowledge_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_ke_target ON knowledge_edges (target_id);
CREATE INDEX IF NOT EXISTS idx_ke_type   ON knowledge_edges (edge_type);

-- 6) RPC: 시맨틱 검색 — memory_records
CREATE OR REPLACE FUNCTION match_memory_records(
    query_embedding vector(768),
    match_threshold  real DEFAULT 0.5,
    match_count      int  DEFAULT 10,
    filter_project   text DEFAULT NULL,
    filter_type      text DEFAULT NULL
)
RETURNS TABLE (
    record_id    text,
    content      text,
    metadata     jsonb,
    similarity   real
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        mr.record_id,
        mr.content,
        mr.metadata,
        1 - (mr.embedding <=> query_embedding) AS similarity
    FROM memory_records mr
    WHERE mr.embedding IS NOT NULL
      AND 1 - (mr.embedding <=> query_embedding) > match_threshold
      AND (filter_project IS NULL OR mr.project_id = filter_project)
      AND (filter_type IS NULL OR mr.memory_type = filter_type)
    ORDER BY mr.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 7) RPC: 시맨틱 검색 — knowledge_nodes
CREATE OR REPLACE FUNCTION match_knowledge_nodes(
    query_embedding vector(768),
    match_threshold  real DEFAULT 0.5,
    match_count      int  DEFAULT 10,
    filter_project   text DEFAULT NULL,
    filter_type      text DEFAULT NULL
)
RETURNS TABLE (
    node_id      text,
    label        text,
    description  text,
    node_type    text,
    confidence   real,
    similarity   real
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        kn.node_id,
        kn.label,
        kn.description,
        kn.node_type,
        kn.confidence,
        1 - (kn.embedding <=> query_embedding) AS similarity
    FROM knowledge_nodes kn
    WHERE kn.embedding IS NOT NULL
      AND 1 - (kn.embedding <=> query_embedding) > match_threshold
      AND (filter_project IS NULL OR kn.project_id = filter_project OR kn.project_id IS NULL)
      AND (filter_type IS NULL OR kn.node_type = filter_type)
    ORDER BY kn.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
