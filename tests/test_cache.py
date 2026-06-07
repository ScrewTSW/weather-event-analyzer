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

    def test_manifest_tracks_entry(self):
        data = b"test data"
        self.cache.store("tiles/test.png", data, source_url="https://example.com/test.png")
        manifest = self.cache.load_manifest()
        assert "tiles/test.png" in manifest
        assert manifest["tiles/test.png"]["source_url"] == "https://example.com/test.png"
        assert manifest["tiles/test.png"]["size"] == len(data)

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
