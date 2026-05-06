"""
Supabase client singleton — with LocalDB fallback when Supabase is not configured.

When SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set, all DB calls
use an in-memory dict so the pipeline runs without any external DB dependency.
"""
from __future__ import annotations
import os
import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
import structlog

log = structlog.get_logger(__name__)

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
_USE_LOCAL = not (_SUPABASE_URL and _SUPABASE_KEY and not _SUPABASE_URL.startswith("http://localhost"))


# ── Local in-memory DB stub ───────────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data

class _Query:
    """Chainable query builder that operates on an in-memory list."""
    def __init__(self, store: list):
        self._store = store
        self._filters: list[tuple[str, Any]] = []
        self._order_col: str | None = None
        self._order_desc: bool = False
        self._limit_n: int | None = None
        self._select_cols: str = "*"
        self._single: bool = False
        self._insert_data: dict | None = None
        self._update_data: dict | None = None

    def select(self, cols="*"):
        self._select_cols = cols
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

    def _match(self, row: dict) -> bool:
        return all(row.get(col) == val for col, val in self._filters)

    def execute(self) -> _Result:
        if self._insert_data is not None:
            row = {**self._insert_data}
            if "created_at" not in row:
                row["created_at"] = datetime.now(timezone.utc).isoformat()
            self._store.append(row)
            return _Result([row])

        if self._update_data is not None:
            updated = []
            for row in self._store:
                if self._match(row):
                    row.update(self._update_data)
                    if self._update_data.get("completed_at") == "now()":
                        row["completed_at"] = datetime.now(timezone.utc).isoformat()
                    updated.append(row)
            return _Result(updated)

        # SELECT
        rows = [r for r in self._store if self._match(r)]
        if self._order_col:
            rows.sort(key=lambda r: r.get(self._order_col, ""), reverse=self._order_desc)
        if self._limit_n:
            rows = rows[:self._limit_n]
        if self._single:
            return _Result(rows[0] if rows else None)
        return _Result(rows)


class _Table:
    def __init__(self, store: list):
        self._store = store

    def select(self, cols="*"):
        return _Query(self._store).select(cols)

    def insert(self, data: dict):
        return _Query(self._store).insert(data)

    def update(self, data: dict):
        return _Query(self._store).update(data)


class LocalDB:
    """Thread-safe in-memory database stub."""
    _lock = threading.Lock()
    _stores: dict[str, list] = {}

    def table(self, name: str) -> _Table:
        with self._lock:
            if name not in self._stores:
                self._stores[name] = []
            return _Table(self._stores[name])


_local_db = LocalDB()


# ── Public accessors ──────────────────────────────────────────────────────────

def get_supabase_admin():
    """Return Supabase admin client, or LocalDB stub if Supabase is not configured."""
    if _USE_LOCAL:
        log.debug("db.using_local_stub")
        return _local_db
    from supabase import create_client
    from backend.config import settings
    client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    log.info("supabase.admin.initialized", url=_SUPABASE_URL)
    return client


def get_supabase_client():
    """Return Supabase anon client, or LocalDB stub if Supabase is not configured."""
    if _USE_LOCAL:
        return _local_db
    from supabase import create_client
    from backend.config import settings
    client = create_client(_SUPABASE_URL, os.getenv("SUPABASE_ANON_KEY", ""))
    return client
