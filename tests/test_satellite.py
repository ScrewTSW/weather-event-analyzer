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
        # Archive probe ok; frame 1 ok, frame 2 times out on every attempt, frame 3 ok
        mock_get.side_effect = [_png_response(), _png_response(), timeout, timeout, _png_response()]
        logs = []

        frames = self.client.fetch_event(
            lat=37.69, lon=-97.33, date="20261102", start_utc="1500", end_utc="1520",
            product="goes_east_geocolor", bbox_deg=20.0, on_progress=logs.append,
        )

        assert [f.timestamp for f in frames] == ["2026-11-02T15:00:00Z", "2026-11-02T15:20:00Z"]
        assert any("FAILED 2/3" in line for line in logs)

    @patch('core.api_request.requests.Session.get')
    def test_iter_frames_yields_before_fetching_the_rest(self, mock_get):
        mock_get.side_effect = [_png_response() for _ in range(4)]
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20261102", start_utc="1500", end_utc="1520",
            product="goes_east_geocolor", bbox_deg=20.0)
        assert mock_get.call_count == 1  # one archive probe

        frames = self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=20.0)
        first = next(frames)

        assert first.timestamp == "2026-11-02T15:00:00Z"
        assert mock_get.call_count == 2  # the app can show frame 1 while 2..n download
        assert [f.timestamp for f in frames] == ["2026-11-02T15:10:00Z", "2026-11-02T15:20:00Z"]


    @patch('core.api_request.requests.Session.get')
    def test_window_outside_archive_is_skipped_after_probes(self, mock_get):
        black = io.BytesIO()
        Image.new("RGBA", (512, 512)).save(black, format="PNG")  # GIBS: transparent outside its archive
        mock_get.side_effect = [MagicMock(status_code=200, content=black.getvalue()) for _ in range(5)]
        logs = []
        _, timestamps = self.client.plan_event(
            lat=33.21, lon=-87.57, date="20110427", start_utc="2100", end_utc="0000",
            product="goes_east_geocolor", bbox_deg=20.0, on_progress=logs.append)

        assert timestamps == []
        # first/middle/last geostationary probe + Terra + Aqua probe, no 2048 px fetches
        assert mock_get.call_count == 5
        assert any("outside the" in line for line in logs)

    @patch('core.api_request.requests.Session.get')
    def test_window_outside_archive_falls_back_to_daily_polar_passes(self, mock_get):
        black = io.BytesIO()
        Image.new("RGBA", (512, 512)).save(black, format="PNG")
        empty = MagicMock(status_code=200, content=black.getvalue())
        mock_get.side_effect = [empty, empty, empty, _png_response(), _png_response()]
        source, timestamps = self.client.plan_event(
            lat=33.21, lon=-87.57, date="20110427", start_utc="2100", end_utc="0000",
            product="goes_east_geocolor", bbox_deg=20.0)

        # ~10:30 / ~13:30 local solar time at 87.57° W; VIIRS only starts in 2015
        assert timestamps == ["2011-04-27T16:20:00Z", "2011-04-27T19:20:00Z"]
        assert source.layer_for(timestamps[0]) == "MODIS_Terra_CorrectedReflectance_TrueColor"
        assert source.layer_for(timestamps[1]) == "MODIS_Aqua_CorrectedReflectance_TrueColor"
        assert "TIME=2011-04-27T19:20:00Z" in mock_get.call_args_list[4].args[0]
        assert "MODIS_Aqua" in mock_get.call_args_list[4].args[0]

    @patch('core.api_request.requests.Session.get')
    def test_daily_polar_prefers_viirs_once_it_exists(self, mock_get):
        black = io.BytesIO()
        Image.new("RGBA", (512, 512)).save(black, format="PNG")
        empty = MagicMock(status_code=200, content=black.getvalue())
        mock_get.side_effect = [empty, empty, empty, _png_response(), _png_response(),
                                _png_response(), _png_response()]
        source, timestamps = self.client.plan_event(
            lat=36.16, lon=-86.78, date="20190214", start_utc="1800", end_utc="2100",
            product="goes_east_geocolor", bbox_deg=20.0)

        frames = list(self.client.iter_frames(source, timestamps, 36.16, -86.78, bbox_deg=20.0))
        assert [f.layer for f in frames] == ["MODIS_Terra_CorrectedReflectance_TrueColor",
                                             "VIIRS_SNPP_CorrectedReflectance_TrueColor"]
        assert frames[1].source == "VIIRS SNPP (daily pass)"

    @patch('core.api_request.requests.Session.get')
    def test_no_polar_probe_before_any_polar_archive(self, mock_get):
        black = io.BytesIO()
        Image.new("RGBA", (512, 512)).save(black, format="PNG")
        mock_get.side_effect = [MagicMock(status_code=200, content=black.getvalue()) for _ in range(3)]
        _, timestamps = self.client.plan_event(
            lat=35.47, lon=-97.52, date="19820502", start_utc="2100", end_utc="0000",
            product="goes_east_geocolor", bbox_deg=20.0)

        assert timestamps == []
        assert mock_get.call_count == 3


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
