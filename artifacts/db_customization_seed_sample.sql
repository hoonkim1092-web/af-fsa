-- Sample seed for one tenant (ACME) using the customization schema
-- Prerequisite: run artifacts/db_customization_schema.sql first

-- 1) Tenant + channels
with upsert_tenant as (
  insert into tenant (code, name)
  values ('acme', 'ACME Logistics')
  on conflict (code) do update
    set name = excluded.name,
        is_active = true
  returning id
)
insert into ingestion_channel (tenant_id, channel_code, description)
select id, 'excel', 'ACME weekly shipment excel'
from upsert_tenant
on conflict (tenant_id, channel_code) do update
  set description = excluded.description,
      is_active = true;

with target_tenant as (
  select id from tenant where code = 'acme'
)
insert into ingestion_channel (tenant_id, channel_code, description)
select id, 'email', 'ACME shipment status email'
from target_tenant
on conflict (tenant_id, channel_code) do update
  set description = excluded.description,
      is_active = true;

-- 2) Field mapping rules (Excel)
with c as (
  select t.id as tenant_id, ic.id as channel_id
  from tenant t
  join ingestion_channel ic on ic.tenant_id = t.id and ic.channel_code = 'excel'
  where t.code = 'acme'
)
insert into field_mapping_rule (
  tenant_id, channel_id, source_field, target_field, transform_type, transform_config, priority, is_required
)
select tenant_id, channel_id, source_field, target_field, transform_type, transform_config::jsonb, priority, is_required
from c
cross join (
  values
    ('Shipment No', 'shipment_no', 'direct', '{}', 10, true),
    ('Order No',    'order_no',    'direct', '{}', 20, false),
    ('Ship Date',   'ship_date',   'date_parse', '{"input_format":"YYYY-MM-DD"}', 30, true),
    ('Carrier',     'carrier',     'direct', '{}', 40, false),
    ('Status',      'status',      'regex', '{"pattern":"(READY|IN_TRANSIT|DELIVERED|CANCELLED)","group":1}', 50, true)
) as m(source_field, target_field, transform_type, transform_config, priority, is_required)
on conflict (tenant_id, channel_id, source_field, target_field) do update
  set transform_type = excluded.transform_type,
      transform_config = excluded.transform_config,
      priority = excluded.priority,
      is_required = excluded.is_required,
      is_active = true;

-- 3) Field mapping rules (Email)
with c as (
  select t.id as tenant_id, ic.id as channel_id
  from tenant t
  join ingestion_channel ic on ic.tenant_id = t.id and ic.channel_code = 'email'
  where t.code = 'acme'
)
insert into field_mapping_rule (
  tenant_id, channel_id, source_field, target_field, transform_type, transform_config, priority, is_required
)
select tenant_id, channel_id, source_field, target_field, transform_type, transform_config::jsonb, priority, is_required
from c
cross join (
  values
    ('subject', 'shipment_no', 'regex', '{"pattern":"Shipment\\s*#?([A-Z0-9-]+)","group":1}', 10, true),
    ('body',    'order_no',    'regex', '{"pattern":"Order\\s*No\\s*:\\s*([A-Z0-9-]+)","group":1}', 20, false),
    ('body',    'ship_date',   'regex', '{"pattern":"Ship\\s*Date\\s*:\\s*([0-9]{4}-[0-9]{2}-[0-9]{2})","group":1}', 30, true),
    ('body',    'carrier',     'regex', '{"pattern":"Carrier\\s*:\\s*([A-Za-z0-9 ]+)","group":1}', 40, false),
    ('body',    'status',      'regex', '{"pattern":"Status\\s*:\\s*(READY|IN_TRANSIT|DELIVERED|CANCELLED)","group":1}', 50, true)
) as m(source_field, target_field, transform_type, transform_config, priority, is_required)
on conflict (tenant_id, channel_id, source_field, target_field) do update
  set transform_type = excluded.transform_type,
      transform_config = excluded.transform_config,
      priority = excluded.priority,
      is_required = excluded.is_required,
      is_active = true;

-- 4) Validation rules
with t as (
  select id as tenant_id from tenant where code = 'acme'
)
insert into validation_rule (tenant_id, target_field, rule_type, rule_config, severity)
select tenant_id, target_field, rule_type, rule_config::jsonb, severity
from t
cross join (
  values
    ('shipment_no', 'not_null',   '{}', 'error'),
    ('shipment_no', 'regex',      '{"pattern":"^[A-Z0-9-]{4,40}$"}', 'error'),
    ('ship_date',   'not_null',   '{}', 'error'),
    ('ship_date',   'date_format','{"format":"YYYY-MM-DD"}', 'error'),
    ('status',      'not_null',   '{}', 'error'),
    ('status',      'enum',       '{"values":["READY","IN_TRANSIT","DELIVERED","CANCELLED"]}', 'error'),
    ('carrier',     'max_len',    '{"value":50}', 'warning')
) as v(target_field, rule_type, rule_config, severity)
on conflict do nothing;

-- 5) Optional check query
-- select t.code, ic.channel_code, fmr.source_field, fmr.target_field, fmr.transform_type
-- from field_mapping_rule fmr
-- join tenant t on t.id = fmr.tenant_id
-- join ingestion_channel ic on ic.id = fmr.channel_id
-- where t.code = 'acme'
-- order by ic.channel_code, fmr.priority;
