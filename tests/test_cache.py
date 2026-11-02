import json
import os
import tempfile
from pathlib import Path

from core.cache import Cache


class TestCache:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = Cache(self.tmpdir, max_size_gb=0.001)  # 1 MB limit for tests

    def test_store_and_retrieve(self):
        data = b"fake PNG tile data"
        path = self.cache.store("tiles/himawari/20260606/test.png", data, source_url="https://example.com/tile.png")
        assert path.exists()
        assert path.read_bytes() == data

    def test_db_tracks_entry(self):
        data = b"test data"
        self.cache.store("tiles/test.png", data, source_url="https://example.com/test.png")
        stats = self.cache.stats()
        assert stats["binary"]["count"] >= 1

    def test_store_json_and_retrieve(self):
        data = {"temperature": [20.5, 21.0], "source": "test"}
        self.cache.store_json("json/test/data.json", data, source_url="https://example.com/api", ttl=3600)
        result = self.cache.get_json("json/test/data.json", ttl=3600)
        assert result is not None
        assert result["temperature"] == [20.5, 21.0]

    def test_json_ttl_expiry(self):
        data = {"value": 42}
        self.cache.store_json("json/test/expire.json", data, ttl=1)
        import time
        time.sleep(1.5)
        result = self.cache.get_json("json/test/expire.json", ttl=1)
        assert result is None

    def test_invalidate_pattern(self):
        self.cache.store("json/openmeteo/a.json", b'{}')
        self.cache.store("json/openmeteo/b.json", b'{}')
        self.cache.store("json/spc/c.json", b'{}')
        self.cache.invalidate("json/openmeteo/*")
        assert self.cache.get("json/openmeteo/a.json") is None
        assert self.cache.get("json/spc/c.json") is not None

    def test_get_returns_path_if_cached(self):
        data = b"cached data"
        self.cache.store("tiles/cached.png", data, source_url="https://example.com/cached.png")
        result = self.cache.get("tiles/cached.png")
        assert result is not None
        assert result.read_bytes() == data

    def test_get_returns_none_if_not_cached(self):
        result = self.cache.get("tiles/nonexistent.png")
        assert result is None

    def test_eviction_removes_oldest(self):
        big_data = b"x" * 600_000  # 600 KB
        self.cache.store("file1.bin", big_data, source_url="url1")
        self.cache.store("file2.bin", big_data, source_url="url2")
        # Total is 1.2 MB > 1 MB limit — file1 should be evicted
        self.cache.store("file3.bin", big_data, source_url="url3")
        self.cache.enforce_limit()
        assert self.cache.get("file1.bin") is None
        assert self.cache.get("file3.bin") is not None

    def test_total_size(self):
        self.cache.store("a.bin", b"x" * 1000, source_url="url_a")
        self.cache.store("b.bin", b"y" * 2000, source_url="url_b")
        assert self.cache.total_size() == 3000

    def test_store_enforces_the_size_limit(self):
        big_data = b"x" * 400_000
        for i in range(4):  # 1.6 MB into a 1 MB cache, no explicit enforce_limit()
            self.cache.store(f"f{i}.bin", big_data, source_url="u")
        assert self.cache.total_size() <= self.cache.max_size_bytes
        assert self.cache.get("f0.bin") is None and self.cache.get("f3.bin") is not None

    def test_daily_cleanup_schedules_across_month_end(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        last_day = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)
        with patch("core.cache.datetime") as dt, patch("core.cache.threading.Timer") as timer:
            dt.now.return_value = last_day
            self.cache._schedule_daily_cleanup()
        assert timer.call_args.args[0] == 9 * 3600  # until 2026-12-01 00:00 UTC
