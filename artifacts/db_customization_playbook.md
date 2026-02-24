# DB Customization Playbook (Client-specific Excel/Email)

## Goal
- Keep one common schema for core business data.
- Absorb tenant differences with mapping/validation tables.
- Allow limited tenant-only fields via `extra_json`.

## Processing Flow
1. Ingest raw source into `raw_ingest_file.payload_json` without loss.
2. Normalize by `field_mapping_rule` into `normalized_record.normalized_json`.
3. Validate by `validation_rule`; store errors in `validation_errors`.
4. Load valid rows into business table (`shipment`) with upsert.
5. Track each batch in `load_job` and per-row failures in `load_error`.

## What to Customize Per Tenant
- `field_mapping_rule`:
  - Different Excel header names
  - Email body regex extraction
  - Date/number normalization policy
- `validation_rule`:
  - Required fields
  - Format constraints (regex/date)
  - Tenant enum constraints

## What NOT to Customize Per Tenant
- Core table structure (`shipment` common columns)
- Core load pipeline states and status enums
- Audit/history shape (`load_job`, `load_error`)

## Recommended Operational Rules
- Store raw payload first, always.
- Never hardcode tenant-specific `if/else` in loader logic.
- Add all tenant-specific columns to `extra_json` first.
- Promote a custom field to common column only when:
  - Used by at least 3 tenants, and
  - Required by reporting or index-based query.

## Minimal Rollout Plan
1. Apply `artifacts/db_customization_schema.sql`.
2. Register first tenant (`tenant`, `ingestion_channel`).
3. Seed `field_mapping_rule` + `validation_rule` for one sample file.
4. Run one ingest cycle and inspect:
   - `normalized_record.validation_ok`
   - `load_job.success_count/error_count`
   - `load_error` reason distribution
5. Expand tenant by tenant.

## Loader Command (Step 2)
```bash
python artifacts/db_customization_loader.py \
  --dsn "postgresql://USER:PASSWORD@HOST:5432/DBNAME" \
  --tenant-code acme \
  --channel excel \
  --source-ref "acme-file-2026-02-17-01" \
  --file-name "acme_weekly.xlsx" \
  --input-json "sample_payload.json"
```
