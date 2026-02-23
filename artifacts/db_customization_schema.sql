-- Multi-tenant customization schema for client-specific Excel/Email ingestion
-- Target: PostgreSQL 14+

create extension if not exists "pgcrypto";

create table if not exists tenant (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  name text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists ingestion_channel (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  channel_code text not null, -- excel | email | api | sftp
  description text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (tenant_id, channel_code)
);

create table if not exists raw_ingest_file (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  channel_id bigint not null references ingestion_channel(id),
  source_ref text, -- message-id, file key, etc.
  file_name text,
  file_hash text,
  payload_json jsonb not null, -- parsed email envelope, rows, attachment metadata
  received_at timestamptz not null default now(),
  process_status text not null default 'received', -- received|normalized|validated|loaded|failed
  unique (tenant_id, source_ref)
);

create index if not exists idx_raw_ingest_file_tenant_status
  on raw_ingest_file (tenant_id, process_status, received_at desc);

create table if not exists field_mapping_rule (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  channel_id bigint not null references ingestion_channel(id),
  source_field text not null,     -- e.g. "Shipment No", "tracking_no"
  target_field text not null,     -- normalized field key
  transform_type text not null default 'direct', -- direct|regex|date_parse|lookup|constant
  transform_config jsonb not null default '{}'::jsonb,
  priority int not null default 100,
  is_required boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (tenant_id, channel_id, source_field, target_field)
);

create index if not exists idx_field_mapping_rule_active
  on field_mapping_rule (tenant_id, channel_id, is_active, priority);

create table if not exists validation_rule (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  target_field text not null,
  rule_type text not null, -- not_null|regex|enum|max_len|min|max|date_format
  rule_config jsonb not null default '{}'::jsonb,
  severity text not null default 'error', -- warning|error
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create index if not exists idx_validation_rule_tenant
  on validation_rule (tenant_id, target_field, is_active);

create table if not exists normalized_record (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  raw_ingest_id bigint not null references raw_ingest_file(id),
  business_key text not null, -- e.g. order_no + item_seq
  normalized_json jsonb not null, -- common fields only
  extra_json jsonb not null default '{}'::jsonb, -- tenant-only fields
  validation_ok boolean not null default false,
  validation_errors jsonb not null default '[]'::jsonb,
  normalized_at timestamptz not null default now(),
  unique (tenant_id, raw_ingest_id, business_key)
);

create index if not exists idx_normalized_record_tenant_valid
  on normalized_record (tenant_id, validation_ok, normalized_at desc);

create table if not exists load_job (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  raw_ingest_id bigint not null references raw_ingest_file(id),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  status text not null default 'running', -- running|success|partial|failed
  total_count int not null default 0,
  success_count int not null default 0,
  error_count int not null default 0
);

create index if not exists idx_load_job_tenant_started
  on load_job (tenant_id, started_at desc);

create table if not exists load_error (
  id bigserial primary key,
  load_job_id bigint not null references load_job(id),
  normalized_record_id bigint references normalized_record(id),
  error_code text not null,
  error_message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Example business table using common + custom extension
create table if not exists shipment (
  id bigserial primary key,
  tenant_id uuid not null references tenant(id),
  shipment_no text not null,
  order_no text,
  ship_date date,
  carrier text,
  status text,
  extra_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique (tenant_id, shipment_no)
);

create index if not exists idx_shipment_tenant_ship_date
  on shipment (tenant_id, ship_date desc);

-- Upsert pattern (called after normalized+validated records)
-- insert into shipment (tenant_id, shipment_no, order_no, ship_date, carrier, status, extra_json)
-- values (...)
-- on conflict (tenant_id, shipment_no)
-- do update set
--   order_no = excluded.order_no,
--   ship_date = excluded.ship_date,
--   carrier = excluded.carrier,
--   status = excluded.status,
--   extra_json = excluded.extra_json,
--   updated_at = now();
