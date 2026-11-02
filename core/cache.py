import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

APP_NAME = "weather-event-analyzer"


def default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / APP_NAME


class Cache:
    def __init__(self, cache_dir: str, max_size_gb: float = 5.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        self.db_path = self.cache_dir / "cache.db"
        self._init_db()
        self._migrate_manifest()
        self.cleanup()
        self._schedule_daily_cleanup()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    file_path TEXT,
                    source_url TEXT,
                    size INTEGER,
                    acquired REAL,
                    last_validated REAL,
                    etag TEXT,
                    last_modified TEXT,
                    content_type TEXT DEFAULT 'binary',
                    ttl_seconds INTEGER DEFAULT 3600
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path), timeout=5)

    def _migrate_manifest(self):
        manifest_path = self.cache_dir / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            with self._conn() as conn:
                for key, entry in manifest.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO cache_entries "
                        "(key, file_path, source_url, size, acquired, content_type) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (key, key, entry.get("source_url", ""),
                         entry.get("size", 0), entry.get("acquired", 0), "binary")
                    )
            manifest_path.unlink()
        except Exception:
            pass

    # --- Binary file storage (existing behavior) ---

    def store(self, key: str, data: bytes, source_url: str = "") -> Path:
        path = self.cache_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(key, file_path, source_url, size, acquired, content_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, key, source_url, len(data), time.time(), "binary")
            )
        return path

    def get(self, key: str) -> Optional[Path]:
        path = self.cache_dir / key
        if path.exists():
            return path
        with self._conn() as conn:
            conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
        return None

    # --- JSON storage with TTL ---

    def store_json(self, key: str, data: Any, source_url: str = "",
                   ttl: int = 3600, etag: str = "", last_modified: str = "") -> Path:
        json_bytes = json.dumps(data, default=str).encode("utf-8")
        path = self.cache_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(key, file_path, source_url, size, acquired, last_validated, "
                "etag, last_modified, content_type, ttl_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, key, source_url, len(json_bytes), time.time(),
                 time.time(), etag, last_modified, "json", ttl)
            )
        return path

    def get_json(self, key: str, ttl: int = 3600) -> Any | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT file_path, acquired, ttl_seconds FROM cache_entries WHERE key = ?",
                (key,)
            ).fetchone()
        if row is None:
            return None
        file_path, acquired, stored_ttl = row
        effective_ttl = min(ttl, stored_ttl) if stored_ttl else ttl
        if (time.time() - acquired) > effective_ttl:
            self._delete_entry(key)
            return None
        path = self.cache_dir / file_path
        if not path.exists():
            self._delete_entry(key)
            return None
        try:
            return json.loads(path.read_bytes())
        except (json.JSONDecodeError, OSError):
            self._delete_entry(key)
            return None

    # --- Freshness checking ---

    def is_fresh(self, key: str, url: str, session) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT etag, last_modified, acquired FROM cache_entries WHERE key = ?",
                (key,)
            ).fetchone()
        if row is None:
            return False
        etag, last_mod, acquired = row
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_mod:
            headers["If-Modified-Since"] = last_mod
        if headers:
            try:
                resp = session.head(url, headers=headers, timeout=5)
                if resp.status_code == 304:
                    with self._conn() as conn:
                        conn.execute(
                            "UPDATE cache_entries SET last_validated = ? WHERE key = ?",
                            (time.time(), key)
                        )
                    return True
                return False
            except Exception:
                pass
        # Fallback: if acquired less than 5 minutes ago, treat as fresh
        return (time.time() - acquired) < 300

    # --- Cleanup and maintenance ---

    def cleanup(self, max_age_hours: int = 168):
        cutoff = time.time() - (max_age_hours * 3600)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, file_path FROM cache_entries WHERE acquired < ?",
                (cutoff,)
            ).fetchall()
            for key, file_path in rows:
                path = self.cache_dir / file_path
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
            conn.execute("DELETE FROM cache_entries WHERE acquired < ?", (cutoff,))

    def invalidate(self, pattern: str):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, file_path FROM cache_entries WHERE key GLOB ?",
                (pattern,)
            ).fetchall()
            for key, file_path in rows:
                path = self.cache_dir / file_path
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
            conn.execute("DELETE FROM cache_entries WHERE key GLOB ?", (pattern,))

    def stats(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT content_type, COUNT(*), SUM(size) FROM cache_entries GROUP BY content_type"
            ).fetchall()
        return {row[0]: {"count": row[1], "size_bytes": row[2] or 0} for row in rows}

    def total_size(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT SUM(size) FROM cache_entries").fetchone()
        return row[0] or 0

    def enforce_limit(self):
        if self.total_size() <= self.max_size_bytes:
            return
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, file_path, size FROM cache_entries ORDER BY acquired ASC"
            ).fetchall()
            removed = 0
            for key, file_path, size in rows:
                if self.total_size() - removed <= self.max_size_bytes:
                    break
                path = self.cache_dir / file_path
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
                conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                removed += size or 0

    def _delete_entry(self, key: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT file_path FROM cache_entries WHERE key = ?", (key,)
            ).fetchone()
            if row:
                path = self.cache_dir / row[0]
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
            conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))

    def _schedule_daily_cleanup(self):
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if midnight <= now:
            midnight = midnight.replace(day=now.day + 1)
        delay = (midnight - now).total_seconds()

        def _run():
            self.cleanup()
            self._schedule_daily_cleanup()

        t = threading.Timer(delay, _run)
        t.daemon = True
        t.start()
