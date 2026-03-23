-- Enable the pgvector extension to work with embedding vectors
create extension if not exists vector;

-- Create a table to store your documents
create table if not exists cortex_memory (
  id bigserial primary key,
  content text, -- The distinct content (Input/Requirement)
  metadata jsonb, -- Additional info (Output, Explanation, AgentRole)
  embedding vector(768) -- Google Gemini Embedding Dimension
);

-- DHCP (Dynamic Host Configuration Protocol) for your brain
-- Create a function to search for documents
create or replace function match_cortex_memory (
  query_embedding vector(768),
  match_threshold float,
  match_count int,
  filter jsonb default '{}'
)
returns table (
  id bigint,
  content text,
  metadata jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    cortex_memory.id,
    cortex_memory.content,
    cortex_memory.metadata,
    1 - (cortex_memory.embedding <=> query_embedding) as similarity
  from cortex_memory
  where 1 - (cortex_memory.embedding <=> query_embedding) > match_threshold
  and cortex_memory.metadata @> filter
  order by cortex_memory.embedding <=> query_embedding
  limit match_count;
end;
$$;

-- Create an index to query for documents
create index on cortex_memory using ivfflat (embedding vector_cosine_ops)
with (lists = 100);
