#!/usr/bin/env python3
"""
Client-customizable ingestion loader.

Pipeline:
1) raw_ingest_file upsert
2) mapping normalization (field_mapping_rule)
3) validation (validation_rule)
4) normalized_record insert
5) shipment upsert (valid rows only)
6) load_job / load_error tracking
"""

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def load_db():
    try:
        import psycopg  # type: ignore

        return ("psycopg", psycopg)
    except Exception:
        import psycopg2  # type: ignore

        return ("psycopg2", psycopg2)


def to_dict_rows(cursor):
    cols = [c[0] for c in cursor.description] if cursor.description else []
    rows = cursor.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def q_all(cur, sql, params=None):
    cur.execute(sql, params or [])
    return to_dict_rows(cur)


def q_one(cur, sql, params=None):
    rows = q_all(cur, sql, params)
    return rows[0] if rows else None


def fmt_to_strptime(fmt):
    return (
        (fmt or "YYYY-MM-DD")
        .replace("YYYY", "%Y")
        .replace("MM", "%m")
        .replace("DD", "%d")
        .replace("hh", "%H")
        .replace("mm", "%M")
        .replace("ss", "%S")
    )


def transform_value(raw_record, rule):
    source_field = rule["source_field"]
    transform_type = rule["transform_type"]
    cfg = rule.get("transform_config") or {}

    src_value = raw_record.get(source_field)

    if transform_type == "direct":
        return src_value

    if transform_type == "constant":
        return cfg.get("value")

    if transform_type == "lookup":
        mapping = cfg.get("map", {})
        default = cfg.get("default")
        return mapping.get(str(src_value), default)

    if transform_type == "regex":
        if src_value is None:
            return None
        pat = cfg.get("pattern")
        grp = int(cfg.get("group", 1))
        if not pat:
            return None
        m = re.search(pat, str(src_value), re.IGNORECASE)
        return m.group(grp).strip() if m else None

    if transform_type == "date_parse":
        if src_value in (None, ""):
            return None
        in_fmt = fmt_to_strptime(cfg.get("input_format", "YYYY-MM-DD"))
        dt = datetime.strptime(str(src_value).strip(), in_fmt)
        return dt.date().isoformat()

    return src_value


def validate_row(normalized, rules):
    issues = []
    by_field = {}
    for r in rules:
        by_field.setdefault(r["target_field"], []).append(r)

    for field, frules in by_field.items():
        value = normalized.get(field)
        for r in frules:
            rule_type = r["rule_type"]
            cfg = r.get("rule_config") or {}
            sev = r.get("severity", "error")
            ok = True
            msg = ""

            if rule_type == "not_null":
                ok = value not in (None, "")
                msg = f"{field} is required"
            elif rule_type == "regex":
                pat = cfg.get("pattern", "")
                ok = value is None or bool(re.match(pat, str(value)))
                msg = f"{field} does not match regex"
            elif rule_type == "enum":
                vals = set(cfg.get("values", []))
                ok = value is None or value in vals
                msg = f"{field} is not in enum"
            elif rule_type == "max_len":
                max_len = int(cfg.get("value", 0))
                ok = value is None or len(str(value)) <= max_len
                msg = f"{field} exceeds max_len={max_len}"
            elif rule_type == "min":
                min_v = float(cfg.get("value"))
                ok = value is None or float(value) >= min_v
                msg = f"{field} is below min={min_v}"
            elif rule_type == "max":
                max_v = float(cfg.get("value"))
                ok = value is None or float(value) <= max_v
                msg = f"{field} exceeds max={max_v}"
            elif rule_type == "date_format":
                if value in (None, ""):
                    ok = True
                else:
                    try:
                        dt_fmt = fmt_to_strptime(cfg.get("format", "YYYY-MM-DD"))
                        datetime.strptime(str(value), dt_fmt)
                        ok = True
                    except Exception:
                        ok = False
                msg = f"{field} has invalid date format"

            if not ok:
                issues.append(
                    {
                        "code": f"VAL_{rule_type.upper()}",
                        "severity": sev,
                        "field": field,
                        "message": msg,
                        "value": value,
                    }
                )
    return issues


def extract_records(payload, channel):
    if channel == "excel":
        rows = payload.get("rows")
        if isinstance(rows, list):
            return rows
    if channel == "email":
        msgs = payload.get("messages")
        if isinstance(msgs, list):
            return msgs
    return [payload]


def compute_business_key(row, source_ref, idx):
    candidate = row.get("shipment_no") or row.get("order_no")
    if candidate:
        return str(candidate)
    h = hashlib.sha256(f"{source_ref}:{idx}:{json.dumps(row, sort_keys=True)}".encode("utf-8")).hexdigest()
    return h[:24]


def main():
    p = argparse.ArgumentParser(description="Tenant-aware loader for db_customization_schema.sql")
    p.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    p.add_argument("--tenant-code", required=True, help="Tenant code, e.g. acme")
    p.add_argument("--channel", required=True, choices=["excel", "email"], help="Ingestion channel")
    p.add_argument("--source-ref", required=True, help="Message-ID or file key")
    p.add_argument("--file-name", default="", help="Original file name")
    p.add_argument("--input-json", required=True, help="Payload JSON path")
    args = p.parse_args()

    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    driver, db = load_db()

    if driver == "psycopg":
        conn = db.connect(args.dsn)
    else:
        conn = db.connect(args.dsn)

    total = 0
    success = 0
    errors = 0

    try:
        with conn:
            with conn.cursor() as cur:
                tenant = q_one(cur, "select id from tenant where code = %s and is_active = true", [args.tenant_code])
                if not tenant:
                    raise RuntimeError(f"tenant not found: {args.tenant_code}")

                channel = q_one(
                    cur,
                    "select id from ingestion_channel where tenant_id = %s and channel_code = %s and is_active = true",
                    [tenant["id"], args.channel],
                )
                if not channel:
                    raise RuntimeError(f"channel not found: {args.channel}")

                file_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
                raw = q_one(
                    cur,
                    """
                    insert into raw_ingest_file (tenant_id, channel_id, source_ref, file_name, file_hash, payload_json, process_status)
                    values (%s, %s, %s, %s, %s, %s::jsonb, 'received')
                    on conflict (tenant_id, source_ref) do update
                      set channel_id = excluded.channel_id,
                          file_name = excluded.file_name,
                          file_hash = excluded.file_hash,
                          payload_json = excluded.payload_json,
                          process_status = 'received',
                          received_at = now()
                    returning id
                    """,
                    [tenant["id"], channel["id"], args.source_ref, args.file_name, file_hash, json.dumps(payload)],
                )
                raw_id = raw["id"]

                load_job = q_one(
                    cur,
                    "insert into load_job (tenant_id, raw_ingest_id, status) values (%s, %s, 'running') returning id",
                    [tenant["id"], raw_id],
                )
                load_job_id = load_job["id"]

                mapping_rules = q_all(
                    cur,
                    """
                    select source_field, target_field, transform_type, transform_config, priority, is_required
                    from field_mapping_rule
                    where tenant_id = %s and channel_id = %s and is_active = true
                    order by priority asc, id asc
                    """,
                    [tenant["id"], channel["id"]],
                )
                validation_rules = q_all(
                    cur,
                    """
                    select target_field, rule_type, rule_config, severity
                    from validation_rule
                    where tenant_id = %s and is_active = true
                    order by id asc
                    """,
                    [tenant["id"]],
                )

                records = extract_records(payload, args.channel)
                for idx, raw_record in enumerate(records, start=1):
                    total += 1
                    normalized = {}
                    extra = {}
                    issues = []

                    used_source_fields = set()
                    for r in mapping_rules:
                        try:
                            v = transform_value(raw_record, r)
                        except Exception as e:
                            v = None
                            issues.append(
                                {
                                    "code": "MAP_TRANSFORM_ERROR",
                                    "severity": "error",
                                    "field": r["target_field"],
                                    "message": str(e),
                                }
                            )

                        if v not in (None, ""):
                            normalized[r["target_field"]] = v

                        used_source_fields.add(r["source_field"])

                        if r.get("is_required") and (v in (None, "")):
                            issues.append(
                                {
                                    "code": "MAP_REQUIRED_MISSING",
                                    "severity": "error",
                                    "field": r["target_field"],
                                    "message": f"required mapping missing for {r['target_field']}",
                                }
                            )

                    for k, v in raw_record.items():
                        if k not in used_source_fields:
                            extra[k] = v

                    issues.extend(validate_row(normalized, validation_rules))
                    hard_errors = [i for i in issues if i.get("severity", "error") == "error"]
                    ok = len(hard_errors) == 0
                    business_key = compute_business_key(normalized, args.source_ref, idx)

                    nr = q_one(
                        cur,
                        """
                        insert into normalized_record
                          (tenant_id, raw_ingest_id, business_key, normalized_json, extra_json, validation_ok, validation_errors)
                        values (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
                        on conflict (tenant_id, raw_ingest_id, business_key) do update
                          set normalized_json = excluded.normalized_json,
                              extra_json = excluded.extra_json,
                              validation_ok = excluded.validation_ok,
                              validation_errors = excluded.validation_errors,
                              normalized_at = now()
                        returning id
                        """,
                        [
                            tenant["id"],
                            raw_id,
                            business_key,
                            json.dumps(normalized),
                            json.dumps(extra),
                            ok,
                            json.dumps(issues),
                        ],
                    )
                    nr_id = nr["id"]

                    if ok:
                        q_one(
                            cur,
                            """
                            insert into shipment (tenant_id, shipment_no, order_no, ship_date, carrier, status, extra_json)
                            values (%s, %s, %s, %s, %s, %s, %s::jsonb)
                            on conflict (tenant_id, shipment_no) do update
                              set order_no = excluded.order_no,
                                  ship_date = excluded.ship_date,
                                  carrier = excluded.carrier,
                                  status = excluded.status,
                                  extra_json = excluded.extra_json,
                                  updated_at = now()
                            returning id
                            """,
                            [
                                tenant["id"],
                                normalized.get("shipment_no", business_key),
                                normalized.get("order_no"),
                                normalized.get("ship_date"),
                                normalized.get("carrier"),
                                normalized.get("status"),
                                json.dumps(extra),
                            ],
                        )
                        success += 1
                    else:
                        errors += 1
                        for issue in hard_errors:
                            q_one(
                                cur,
                                """
                                insert into load_error (load_job_id, normalized_record_id, error_code, error_message, payload)
                                values (%s, %s, %s, %s, %s::jsonb)
                                returning id
                                """,
                                [
                                    load_job_id,
                                    nr_id,
                                    issue.get("code", "UNKNOWN"),
                                    issue.get("message", "validation error"),
                                    json.dumps(issue),
                                ],
                            )

                status = "success"
                if success == 0 and errors > 0:
                    status = "failed"
                elif errors > 0:
                    status = "partial"

                cur.execute(
                    """
                    update load_job
                    set ended_at = now(),
                        status = %s,
                        total_count = %s,
                        success_count = %s,
                        error_count = %s
                    where id = %s
                    """,
                    [status, total, success, errors, load_job_id],
                )

                cur.execute(
                    "update raw_ingest_file set process_status = %s where id = %s",
                    ["loaded" if errors == 0 else "failed", raw_id],
                )

        print(
            json.dumps(
                {
                    "ok": True,
                    "tenant_code": args.tenant_code,
                    "channel": args.channel,
                    "source_ref": args.source_ref,
                    "total": total,
                    "success": success,
                    "errors": errors,
                },
                ensure_ascii=False,
            )
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
