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
        # Germany: outside every GOES CONUS/PACUS sector, so no S3 listing either
        source, timestamps = self.client.plan_event(
            lat=50.11, lon=8.68, date="20190214", start_utc="1000", end_utc="1300",
            product="meteosat_ir", bbox_deg=20.0)
        assert mock_get.call_count == 5

        frames = list(self.client.iter_frames(source, timestamps, 50.11, 8.68, bbox_deg=20.0))
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


def _goes_nc(lat=37.69, lon=-97.33, sat_lon=-75.0, half=0.02, step=1e-4,
             background=300.0, spot=210.0, scale=0.04, offset=173.15, chunk=50):
    """Tiny chunked, gzip ABI CMIP NetCDF (HDF5) like the real files: `spot` in a small
    square around (lat, lon), `background` elsewhere, one fill pixel in the corner."""
    import h5py
    from core.projection import goes_fixed_grid
    cx, cy = (float(v) for v in goes_fixed_grid(lat, lon, sat_lon))
    n = int(2 * half / step) + 1
    buf = io.BytesIO()
    with h5py.File(buf, "w") as h:
        for name, start, sign in (("x", cx - half, 1), ("y", cy + half, -1)):
            v = h.create_dataset(name, data=np.arange(n, dtype=np.int16))
            v.attrs["scale_factor"], v.attrs["add_offset"] = np.float32([sign * step]), np.float32([start])
        cmi = np.full((n, n), round((background - offset) / scale), np.int16)
        mid, r = n // 2, max(1, int(3e-4 / step))
        cmi[mid - r:mid + r + 1, mid - r:mid + r + 1] = round((spot - offset) / scale)
        cmi[0, 0] = -1
        v = h.create_dataset("CMI", data=cmi, chunks=(chunk, chunk), compression="gzip")
        v.attrs["scale_factor"], v.attrs["add_offset"] = np.float32([scale]), np.float32([offset])
        v.attrs["_FillValue"] = np.int16([-1])
        g = h.create_dataset("goes_imager_projection", data=0)
        g.attrs.update(longitude_of_projection_origin=[sat_lon], perspective_point_height=[35786023.0],
                       semi_major_axis=[6378137.0], semi_minor_axis=[6356752.31414])
    return buf.getvalue()


def _cmip_key(t, band="C13", sat="G16"):
    return (f"ABI-L2-CMIPC/{t:%Y/%j/%H}/OR_ABI-L2-CMIPC-M6{band}_{sat}_"
            f"s{t:%Y%j%H%M%S}0_e{t:%Y%j%H%M%S}9_c{t:%Y%j%H%M%S}9.nc")


# Reflective bands: reflectance factor = raw * 2e-4 (C02 at half the spacing of C01/C03)
_REFLECTIVE = {"C01": dict(step=1e-4, background=0.10, spot=0.9, scale=2e-4, offset=0.0),
               "C02": dict(step=5e-5, background=0.12, spot=0.9, scale=2e-4, offset=0.0, chunk=100),
               "C03": dict(step=1e-4, background=0.30, spot=0.9, scale=2e-4, offset=0.0)}


class _FakeS3:
    """Session.get stand-in: GIBS answers empty, S3 serves listings and byte ranges."""

    def __init__(self, scans, bands=("C01", "C02", "C03", "C13")):
        self.files = {_cmip_key(t, b): _goes_nc(**_REFLECTIVE.get(b, {})) for t in scans for b in bands}
        self.ranged, self.whole = [], []
        black = io.BytesIO()
        Image.new("RGBA", (512, 512)).save(black, format="PNG")
        self.empty_png = black.getvalue()

    def __call__(self, url, headers=None, **kw):
        if "gibs.earthdata" in url:
            return MagicMock(status_code=200, content=self.empty_png)
        if "list-type=2" in url:
            prefix = url.split("prefix=")[1]
            body = "".join(f"<Contents><Key>{k}</Key><Size>{len(v)}</Size></Contents>"
                           for k, v in self.files.items() if k.startswith(prefix))
            return MagicMock(status_code=200, text=f"<ListBucketResult>{body}</ListBucketResult>")
        data = self.files[url.split(".amazonaws.com/")[1]]
        if headers and "Range" in headers:
            start, end = (int(v) for v in headers["Range"].split("=")[1].split("-"))
            self.ranged.append(url)
            return MagicMock(status_code=206, content=data[start:end + 1])
        self.whole.append(url)
        return MagicMock(status_code=200, content=data)


class TestGOESArchive:
    def setup_method(self):
        self.cache = Cache(tempfile.mkdtemp())
        self.client = SatelliteClient(cache=self.cache)

    def test_fixed_grid_matches_pug_example(self):
        from core.projection import goes_fixed_grid
        x, y = goes_fixed_grid(33.846162, -84.690932, -75.0)  # GOES-R PUG vol. 3, 4.2.8.1
        assert abs(x - -0.024052) < 1e-6 and abs(y - 0.095340) < 1e-6
        assert all(np.isnan(goes_fixed_grid(0.0, 120.0, -75.0)))  # far side of the Earth

    def test_solar_elevation_and_sun_mode(self):
        from datetime import datetime
        from core.projection import solar_elevation
        noon = solar_elevation(37.69, -97.33, datetime(2019, 6, 21, 18, 30))
        assert abs(noon - (90 - 37.69 + 23.44)) < 0.3
        mode = SatelliteClient._sun_mode
        assert mode(37.69, -97.33, 20.0, datetime(2019, 6, 21, 18, 30)) == "day"
        assert mode(37.69, -97.33, 20.0, datetime(2019, 6, 21, 6, 0)) == "night"
        assert mode(37.69, -97.33, 20.0, datetime(2019, 6, 22, 2, 0)) == "twilight"  # ~sunset

    def test_archive_selection_by_date_and_side(self):
        from datetime import date
        from core.models.satellite.source import goes_archives_for
        assert goes_archives_for(date(2016, 12, 31), "goes_east") == []
        assert [a.sat_lon for a in goes_archives_for(date(2017, 6, 1), "goes_east")] == [-89.5]
        assert goes_archives_for(date(2017, 12, 5), "goes_east") == []  # drifting
        assert [a.name for a in goes_archives_for(date(2020, 5, 1), "goes_west_geocolor")] == \
            ["GOES-17", "GOES-16"]
        assert [a.name for a in goes_archives_for(date(2026, 1, 1), "auto")] == ["GOES-19", "GOES-18"]

    def test_true_color_is_sharpened_by_red(self):
        from core.goes_archive import true_color
        red = np.array([[0.2, 0.6]], np.float32)       # 0.5 km detail
        red_1km = np.array([[0.4, 0.4]], np.float32)   # same area, block-averaged
        flat = np.full((1, 2), 0.3, np.float32)
        rgba = true_color(flat, red, flat, red_1km=red_1km)
        assert rgba[0, 0, 2] < rgba[0, 1, 2]           # blue picks up red's edge
        assert (rgba[..., 3] == 255).all()

    @patch('core.api_request.requests.Session.get')
    def test_plan_picks_bands_by_sun_and_product(self, mock_get):
        from datetime import datetime
        scans = [datetime(2019, 6, 21, 18, m, 17) for m in range(1, 60, 5)]
        mock_get.side_effect = _FakeS3(scans, bands=("C01", "C02", "C03", "C13", "C07"))
        logs = []
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20190621", start_utc="1800", end_utc="1830",
            product="goes_east_geocolor", bbox_deg=2.0, on_progress=logs.append)
        assert source.source_type == "goes_s3" and source.archive.name == "GOES-16"
        assert timestamps == ["2019-06-21T18:00:00Z", "2019-06-21T18:10:00Z",
                              "2019-06-21T18:20:00Z", "2019-06-21T18:30:00Z"]
        ts = "2019-06-21T18:10:00Z"
        assert source.modes[ts] == "day" and sorted(source.files[ts]) == ["C01", "C02", "C03"]
        assert source.files[ts]["C02"].key == _cmip_key(scans[2], "C02")  # 18:11 is nearest
        assert source.name_for(ts) == "GOES-16 True Color (NOAA archive)"
        assert any("4 true colour, 0 blended, 0 IR" in line for line in logs)

        ir, _ = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20190621", start_utc="1800", end_utc="1830",
            product="goes_east", bbox_deg=2.0)
        assert set(ir.modes.values()) == {"night"} and list(ir.files[ts]) == ["C13"]

    @patch('core.api_request.requests.Session.get')
    def test_day_frame_reads_only_chunk_ranges_and_caches_render(self, mock_get):
        from datetime import datetime
        scans = [datetime(2019, 6, 21, 18, 1, 17)]
        s3 = mock_get.side_effect = _FakeS3(scans)
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20190621", start_utc="1800", end_utc="1805",
            product="goes_east_geocolor", bbox_deg=1.0)
        frame = next(self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=1.0))
        assert frame.source == "GOES-16 True Color (NOAA archive)"
        assert frame.layer == "GOES-16_ABI_TrueColor" and frame.image.size == (2048, 2048)
        assert s3.ranged and not s3.whole              # never the whole NetCDF
        assert not any("M6C13" in url for url in s3.ranged)  # IR not needed in daylight
        px = np.array(frame.image)
        centre, edge = px[1024, 1024], px[1024, 40]
        assert centre[:3].min() > edge[:3].max()       # bright cloud spot over darker ground
        assert edge[1] > edge[2]                       # veggie band makes the ground greener
        assert self.cache.get(f"goes/GOES-16_ABI_TrueColor/2019-06-21T180000Z/"
                              f"37.69_-97.33_1.0_2048.webp") is not None

        mock_get.side_effect = AssertionError("rendered frame should come from the cache")
        again = next(self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=1.0))
        assert again.timestamp == frame.timestamp

    @patch('core.api_request.requests.Session.get')
    def test_night_frame_is_colour_enhanced_ir(self, mock_get):
        from datetime import datetime
        scans = [datetime(2019, 6, 21, 6, 1, 17)]
        mock_get.side_effect = _FakeS3(scans, bands=("C13",))
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20190621", start_utc="0600", end_utc="0605",
            product="goes_east_geocolor", bbox_deg=1.0)
        frame = next(self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=1.0))
        assert frame.source == "GOES-16 Band 13 IR (NOAA archive)"
        px = np.array(frame.image)
        centre, edge = px[1024, 1024], px[1024, 40]
        assert edge[0] == edge[1] == edge[2]           # 300 K ground: grey
        assert tuple(centre[:3]) != tuple(edge[:3])    # 210 K top: coloured

    @patch('core.api_request.requests.Session.get')
    def test_twilight_frame_blends_true_color_and_ir(self, mock_get):
        from datetime import datetime
        scans = [datetime(2019, 6, 22, 1, 21, 17)]   # Wichita, sun ~5° up
        s3 = mock_get.side_effect = _FakeS3(scans)
        source, timestamps = self.client.plan_event(
            lat=37.69, lon=-97.33, date="20190622", start_utc="0120", end_utc="0125",
            product="goes_east_geocolor", bbox_deg=1.0)
        assert source.modes[timestamps[0]] == "twilight"
        frame = next(self.client.iter_frames(source, timestamps, 37.69, -97.33, bbox_deg=1.0))
        assert frame.source == "GOES-16 True Color + IR (NOAA archive)"
        assert {b for b in ("C01", "C02", "C03", "C13")
                if any(f"M6{b}_" in url for url in s3.ranged)} == {"C01", "C02", "C03", "C13"}

    def test_outside_sector_renders_transparent(self):
        from datetime import datetime
        from core.api_request import CachedRequest
        from core.goes_archive import render_frame
        from core.models.satellite.source import S3Object
        s3 = _FakeS3([datetime(2019, 6, 21, 6, 1, 17)], bands=("C13",))
        key, data = next(iter(s3.files.items()))
        req = CachedRequest()
        with patch.object(req.session, "get", side_effect=s3):
            png = render_frame(req, "noaa-goes16", {"C13": S3Object(key, len(data))},
                               (-60.0, 0.0, -58.0, 2.0), 16, datetime(2019, 6, 21, 6, 1))
        assert png is None  # no chunk under the box -> nothing fetched or rendered


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
