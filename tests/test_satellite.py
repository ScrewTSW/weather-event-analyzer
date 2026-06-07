import time
import pytest
import tempfile
from unittest.mock import patch, MagicMock
from core.satellite import (
    build_tile_url,
    build_timestamps_url,
    filter_timestamps,
    SatelliteClient,
)
from core.cache import Cache


class TestBuildTileURL:
    def test_himawari_geocolor(self):
        url = build_tile_url(
            satellite="himawari",
            product="geocolor",
            timestamp=20260606100000,
            zoom=1,
            row=0,
            col=0,
        )
        assert url == "https://slider.cira.colostate.edu/data/imagery/2026/06/06/himawari---full_disk/geocolor/20260606100000/01/000_000.png"

    def test_goes19_with_padding(self):
        url = build_tile_url(
            satellite="goes-19",
            product="band_13",
            timestamp=20260606120021,
            zoom=3,
            row=5,
            col=12,
        )
        assert "goes-19---full_disk" in url
        assert "/03/" in url
        assert "005_012.png" in url


class TestBuildTimestampsURL:
    def test_latest_times(self):
        url = build_timestamps_url("himawari", "geocolor")
        assert url == "https://slider.cira.colostate.edu/data/json/himawari/full_disk/geocolor/latest_times.json"

    def test_by_hour(self):
        url = build_timestamps_url("himawari", "geocolor", date="20260606")
        assert url == "https://slider.cira.colostate.edu/data/json/himawari/full_disk/geocolor/20260606_by_hour.json"


class TestFilterTimestamps:
    def test_filter_by_time_range(self):
        timestamps = [
            20260531060000,
            20260531070000,
            20260531080000,
            20260531090000,
            20260531100000,
            20260531110000,
            20260531120000,
            20260531130000,
        ]
        filtered = filter_timestamps(timestamps, start_utc="0700", end_utc="1200")
        assert filtered == [
            20260531070000,
            20260531080000,
            20260531090000,
            20260531100000,
            20260531110000,
            20260531120000,
        ]

    def test_empty_range(self):
        timestamps = [20260531060000, 20260531130000]
        filtered = filter_timestamps(timestamps, start_utc="0700", end_utc="1200")
        assert filtered == []


class TestSatelliteIntegration:
    @pytest.mark.integration
    def test_download_single_himawari_tile(self):
        cache = Cache(tempfile.mkdtemp())
        client = SatelliteClient(cache)
        timestamps = client.fetch_timestamps("himawari", "geocolor", None)
        assert len(timestamps) > 0
        latest = timestamps[-1]
        path = client.download_tile("himawari", "geocolor", latest, 0, 0, 0)
        assert path is not None
        assert path.stat().st_size > 10000  # Should be >10KB for a real image
