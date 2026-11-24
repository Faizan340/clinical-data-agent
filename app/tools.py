"""Tools the agents can use: read the schema and run guarded, tenant-scoped SQL."""
import hashlib
import json
from decimal import Decimal

import psycopg
import redis

from app.config import settings
from app.guardrails import check_sql

_redis = None


def _cache():
    global _redis
    if _redis is None:
        try:
            _redis = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
            _redis.ping()
        except redis.RedisError:
            _redis = False  # cache is optional
    return _redis or None


def _connect(tenant_id: int):
    conn = psycopg.connect(settings.agent_db_url, autocommit=False)
    conn.read_only = True
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    return conn


def describe_schema(tenant_id: int = 1) -> str:
    """Columns visible to the agent's DB role (PHI columns are not granted, so they never appear)."""
    with _connect(tenant_id) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position").fetchall()
    tables: dict[str, list[str]] = {}
    for table, column, dtype in rows:
        tables.setdefault(table, []).append(f"{column} {dtype}")
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def run_query(sql: str, tenant_id: int) -> dict:
    """Guardrail-check then execute read-only as the tenant. Results are cached in Redis."""
    safe_sql = check_sql(sql, settings.max_rows)
    key = "cda:q:" + hashlib.sha256(f"{tenant_id}:{safe_sql}".encode()).hexdigest()
    cache = _cache()
    if cache:
        hit = cache.get(key)
        if hit:
            return {**json.loads(hit), "cached": True}

    with _connect(tenant_id) as conn:
        cur = conn.execute(safe_sql)
        columns = [d.name for d in cur.description]
        rows = [[_jsonable(v) for v in row] for row in cur.fetchmany(settings.max_rows)]
    result = {"sql": safe_sql, "columns": columns, "rows": rows, "cached": False}
    if cache:
        cache.setex(key, 600, json.dumps(result))
    return result
