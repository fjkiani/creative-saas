"""
Supabase client singleton — with SQLite fallback when Supabase is not configured.

When SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set, all DB calls use a
SQLite database at /app/data/creativeos.db (persistent across restarts).

IMPORTANT: We read from pydantic Settings (not raw os.getenv) so that the
decision is made after all env vars have been loaded — not at module import time.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import structlog

log = structlog.get_logger(__name__)

# ── SQLite-backed local DB ────────────────────────────────────────────────────

_SQLITE_PATH = Path("/app/data/creativeos.db")
_SQLITE_LOCK = threading.Lock()
_sqlite_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    campaign_id TEXT,
    workspace_id TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    review_score REAL,
    reviewer_notes TEXT,
    provider_image TEXT NOT NULL DEFAULT 'modal',
    provider_llm TEXT NOT NULL DEFAULT 'openrouter',
    brief TEXT NOT NULL DEFAULT '{}',
    run_report TEXT,
    error_message TEXT,
    video_mode TEXT NOT NULL DEFAULT 'slideshow',
    publish_platforms TEXT NOT NULL DEFAULT '[]',
    scheduled_publish_time TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    brand_config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    workspace_id TEXT,
    asset_type TEXT NOT NULL DEFAULT 'image',
    url TEXT NOT NULL,
    storage_path TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    credits INTEGER NOT NULL DEFAULT 0,
    instagram_access_token TEXT,
    instagram_user_id TEXT,
    tiktok_access_token TEXT,
    tiktok_client_key TEXT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    event_type TEXT NOT NULL,
    credits_used INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitor_analyses (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    input_type TEXT NOT NULL DEFAULT 'screenshot',
    input_data TEXT,
    analysis TEXT,
    counter_brief TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL
);
"""


def _get_conn() -> sqlite3.Connection:
    global _sqlite_conn
    if _sqlite_conn is None:
        _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_conn = sqlite3.connect(str(_SQLITE_PATH), check_same_thread=False)
        _sqlite_conn.row_factory = sqlite3.Row
        _sqlite_conn.execute("PRAGMA journal_mode=WAL")
        _sqlite_conn.executescript(_SCHEMA)
        _sqlite_conn.commit()
        log.info("sqlite.initialized", path=str(_SQLITE_PATH))
    return _sqlite_conn


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    # Deserialize JSON columns
    for col in ("brief", "run_report", "brand_config", "metadata", "payload",
                "publish_platforms", "analysis", "counter_brief"):
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except Exception:
                pass
    return d


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Chainable query builder backed by SQLite."""

    def __init__(self, table: str):
        self._table = table
        self._filters: list[tuple[str, Any]] = []
        self._order_col: str | None = None
        self._order_desc: bool = False
        self._limit_n: int | None = None
        self._single: bool = False
        self._insert_data: dict | None = None
        self._update_data: dict | None = None
        self._gte_filter: tuple | None = None

    def select(self, cols="*"):
        return self

    def insert(self, data: dict):
        self._insert_data = data
        return self

    def update(self, data: dict):
        self._update_data = data
        return self

    def eq(self, col: str, val: Any):
        self._filters.append((col, val))
        return self

    def gte(self, col: str, val: Any):
        self._gte_filter = (col, val)
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    def single(self):
        self._single = True
        return self

    def _serialize(self, data: dict) -> dict:
        """Serialize dict/list values to JSON strings for SQLite storage."""
        out = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                out[k] = json.dumps(v)
            else:
                out[k] = v
        return out

    def execute(self) -> _Result:
        with _SQLITE_LOCK:
            conn = _get_conn()

            if self._insert_data is not None:
                row = {**self._insert_data}
                if "id" not in row or not row["id"]:
                    row["id"] = str(uuid.uuid4())
                if "created_at" not in row:
                    row["created_at"] = datetime.now(timezone.utc).isoformat()
                if "updated_at" not in row and self._table == "workspaces":
                    row["updated_at"] = row["created_at"]
                row = self._serialize(row)
                cols = ", ".join(row.keys())
                placeholders = ", ".join("?" * len(row))
                conn.execute(
                    f"INSERT OR REPLACE INTO {self._table} ({cols}) VALUES ({placeholders})",
                    list(row.values())
                )
                conn.commit()
                return _Result([_row_to_dict(conn.execute(
                    f"SELECT * FROM {self._table} WHERE id=?", [row["id"]]
                ).fetchone())])

            if self._update_data is not None:
                update = {**self._update_data}
                if "completed_at" in update and update["completed_at"] == "now()":
                    update["completed_at"] = datetime.now(timezone.utc).isoformat()
                if self._table == "workspaces":
                    update["updated_at"] = datetime.now(timezone.utc).isoformat()
                update = self._serialize(update)
                set_clause = ", ".join(f"{k}=?" for k in update.keys())
                where_clause, where_vals = self._build_where()
                conn.execute(
                    f"UPDATE {self._table} SET {set_clause} WHERE {where_clause}",
                    list(update.values()) + where_vals
                )
                conn.commit()
                where_clause2, where_vals2 = self._build_where()
                rows = conn.execute(
                    f"SELECT * FROM {self._table} WHERE {where_clause2}", where_vals2
                ).fetchall()
                return _Result([_row_to_dict(r) for r in rows])

            # SELECT
            where_clause, where_vals = self._build_where()
            sql = f"SELECT * FROM {self._table} WHERE {where_clause}"
            if self._order_col:
                direction = "DESC" if self._order_desc else "ASC"
                sql += f" ORDER BY {self._order_col} {direction}"
            if self._limit_n:
                sql += f" LIMIT {self._limit_n}"
            rows = conn.execute(sql, where_vals).fetchall()
            dicts = [_row_to_dict(r) for r in rows]
            if self._single:
                return _Result(dicts[0] if dicts else None)
            return _Result(dicts)

    def _build_where(self) -> tuple[str, list]:
        clauses = []
        vals = []
        for col, val in self._filters:
            clauses.append(f"{col}=?")
            vals.append(val)
        if self._gte_filter:
            col, val = self._gte_filter
            clauses.append(f"{col}>=?")
            vals.append(val)
        if not clauses:
            return "1=1", []
        return " AND ".join(clauses), vals


class _Table:
    def __init__(self, name: str):
        self._name = name

    def select(self, cols="*"):
        return _Query(self._name).select(cols)

    def insert(self, data: dict):
        return _Query(self._name).insert(data)

    def update(self, data: dict):
        return _Query(self._name).update(data)


class SQLiteDB:
    """SQLite-backed persistent database — drop-in replacement for LocalDB."""

    def table(self, name: str) -> _Table:
        return _Table(name)


_local_db = SQLiteDB()

# Module-level cached real Supabase client (None until first call)
_supabase_admin_client = None
_supabase_client_lock = threading.Lock()


# ── Public accessors ──────────────────────────────────────────────────────────

def _is_local() -> bool:
    """
    True when Supabase is not configured — use SQLite instead.
    Reads from pydantic Settings so env vars are fully resolved.
    """
    from backend.config import settings
    return not settings.supabase_configured


def get_supabase_admin():
    """
    Return Supabase admin client (service_role key), or SQLiteDB if
    Supabase is not configured.
    """
    global _supabase_admin_client

    if _is_local():
        log.debug("db.using_sqlite")
        return _local_db

    with _supabase_client_lock:
        if _supabase_admin_client is None:
            from supabase import create_client
            from backend.config import settings
            _supabase_admin_client = create_client(
                settings.supabase_url,
                settings.supabase_service_key_resolved,
            )
            log.info("supabase.admin.initialized", url=settings.supabase_url)
        return _supabase_admin_client


def get_supabase_client():
    """
    Return Supabase anon client, or SQLiteDB if Supabase is not configured.
    """
    if _is_local():
        return _local_db

    from supabase import create_client
    from backend.config import settings
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def using_local_db() -> bool:
    """Expose local-mode flag for health checks."""
    return _is_local()
