import io
import tempfile
from unittest.mock import patch, MagicMock

import numpy as np
import requests
from PIL import Image

from core.cache import Cache
from core.satellite import SatelliteClient


def _png_response():
    rng = np.random.default_rng(0)
    arr = rng.integers(50, 255, size=(64, 64, 4), dtype=np.uint8)
    arr[..., 3] = 255
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    resp = MagicMock()
    resp.status_code = 200
    resp.content = buf.getvalue()
    return resp


class TestFetchEventResilience:
    def setup_method(self):
        self.cache = Cache(tempfile.mkdtemp())
        self.client = SatelliteClient(cache=self.cache)

    @patch('core.api_request.requests.Session.get')
    def test_timed_out_frame_is_skipped_not_fatal(self, mock_get):
        timeout = requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")
        # Frame 1 ok, frame 2 times out on every attempt, frame 3 ok
        mock_get.side_effect = [_png_response(), timeout, timeout, _png_response()]
        logs = []

        frames = self.client.fetch_event(
            lat=37.69, lon=-97.33, date="20261102", start_utc="1500", end_utc="1520",
            product="goes_east_geocolor", bbox_deg=20.0, on_progress=logs.append,
        )

        assert [f.timestamp for f in frames] == ["2026-11-02T15:00:00Z", "2026-11-02T15:20:00Z"]
        assert any("FAILED 2/3" in line for line in logs)

    @patch('core.api_request.requests.Session.get')
    def test_iter_frames_yields_before_fetching_the_rest(self, mock_get):
        mock_get.side_effect = [_png_response(), _png_response(), _png_response()]
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20261102", start_utc="1500", end_utc="1520",
            product="goes_east_geocolor", bbox_deg=20.0)

        frames = self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=20.0)
        first = next(frames)

        assert first.timestamp == "2026-11-02T15:00:00Z"
        assert mock_get.call_count == 1  # the app can show frame 1 while 2..n download
        assert [f.timestamp for f in frames] == ["2026-11-02T15:10:00Z", "2026-11-02T15:20:00Z"]


class TestFetchBinaryRetry:
    def setup_method(self):
        self.cache = Cache(tempfile.mkdtemp())

    @patch('core.api_request.requests.Session.get')
    def test_retries_once_after_timeout(self, mock_get):
        from core.api_request import CachedRequest
        ok = _png_response()
        mock_get.side_effect = [requests.exceptions.ReadTimeout("stall"), ok]
        req = CachedRequest(cache=self.cache)

        data = req.fetch_binary("https://gibs.earthdata.nasa.gov/x", cache_key="wms/test/a_2048.jpg")

        assert data == ok.content
        assert mock_get.call_count == 2
