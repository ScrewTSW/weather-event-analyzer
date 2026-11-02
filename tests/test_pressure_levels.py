import pytest
from unittest.mock import patch, MagicMock
from core.pressure_levels import snap_to_synoptic, PressureLevelClient

PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                   400, 300, 250, 200, 150, 100, 70, 50, 30]


class TestSnapToSynoptic:
    def test_snap_to_00(self):
        assert snap_to_synoptic(2) == 0

    def test_snap_to_06(self):
        assert snap_to_synoptic(7) == 6

    def test_snap_to_12(self):
        assert snap_to_synoptic(10) == 12

    def test_snap_to_18(self):
        assert snap_to_synoptic(16) == 18

    def test_snap_boundary_3(self):
        assert snap_to_synoptic(3) == 6

    def test_snap_to_00_from_22(self):
        assert snap_to_synoptic(22) == 0


class TestPressureLevelClientURL:
    def test_build_url_contains_all_levels(self):
        client = PressureLevelClient()
        url = client._build_openmeteo_url(45.0, -90.0)
        for level in PRESSURE_LEVELS:
            assert f"temperature_{level}hPa" in url
            assert f"dew_point_{level}hPa" in url
            assert f"wind_speed_{level}hPa" in url
            assert f"wind_direction_{level}hPa" in url
            assert f"geopotential_height_{level}hPa" in url

    def test_build_url_has_wind_speed_unit(self):
        client = PressureLevelClient()
        url = client._build_openmeteo_url(45.0, -90.0)
        assert "wind_speed_unit=ms" in url


class TestPressureLevelClientParse:
    def _make_mock_response(self):
        hourly = {"time": ["2026-07-02T00:00", "2026-07-02T01:00"]}
        for level in PRESSURE_LEVELS:
            hourly[f"temperature_{level}hPa"] = [20.0, 21.0]
            hourly[f"dew_point_{level}hPa"] = [15.0, 16.0]
            hourly[f"wind_speed_{level}hPa"] = [5.0, 6.0]
            hourly[f"wind_direction_{level}hPa"] = [180, 190]
            hourly[f"geopotential_height_{level}hPa"] = [100.0, 110.0]
        return {"latitude": 45.0, "longitude": -90.0, "hourly": hourly}

    def test_parse_snapshot(self):
        client = PressureLevelClient()
        raw = self._make_mock_response()
        result = client._parse_response(raw, synoptic_hour=0)
        assert result.source == "open-meteo-pressure"
        assert result.synoptic_time == "2026-07-02T00:00"
        assert len(result.temperature) == len(PRESSURE_LEVELS)
        assert len(result.pressure_levels) == len(PRESSURE_LEVELS)

    def test_parse_hourly(self):
        client = PressureLevelClient()
        raw = self._make_mock_response()
        result = client._parse_response(raw, synoptic_hour=None)
        assert len(result.timestamps) == 2
        assert len(result.temperature) == 2
        assert len(result.temperature[0]) == len(PRESSURE_LEVELS)

    def test_build_url_requests_surface_level(self):
        url = PressureLevelClient()._build_openmeteo_url(45.0, -90.0)
        for var in ("surface_pressure", "temperature_2m", "dew_point_2m",
                    "wind_speed_10m", "wind_direction_10m"):
            assert var in url

    def test_parse_surface_level(self):
        from core.pressure_levels import extract_snapshot
        raw = self._make_mock_response()
        raw["hourly"].update(surface_pressure=[959.1, 958.8], temperature_2m=[19.8, 20.6],
                             dew_point_2m=[17.2, 18.0], wind_speed_10m=[7.92, 5.46],
                             wind_direction_10m=[176, 189])
        client = PressureLevelClient()
        snap = client._parse_response(raw, synoptic_hour=1)
        assert snap.surface_pressure == 958.8 and snap.surface_temperature == 20.6
        hourly = client._parse_response(raw)
        assert hourly.surface_dewpoint == [17.2, 18.0]
        one = extract_snapshot(hourly, 1)
        assert (one.surface_pressure, one.surface_wind_speed, one.surface_wind_direction) == (958.8, 5.46, 189)
        day = client._only_date(client._parse_response(raw), "2026-07-02")
        assert day.surface_temperature == [19.8, 20.6]


class TestPressureLevelClientFetch:
    @patch("core.api_request.requests.Session")
    def test_fetch_snapshot_calls_api(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        hourly = {"time": ["2026-07-02T12:00"]}
        for level in PRESSURE_LEVELS:
            hourly[f"temperature_{level}hPa"] = [20.0]
            hourly[f"dew_point_{level}hPa"] = [15.0]
            hourly[f"wind_speed_{level}hPa"] = [5.0]
            hourly[f"wind_direction_{level}hPa"] = [180]
            hourly[f"geopotential_height_{level}hPa"] = [100.0]
        mock_resp.json.return_value = {"latitude": 45.0, "longitude": -90.0, "hourly": hourly}
        mock_resp.raise_for_status = MagicMock()
        mock_session_cls.return_value.get.return_value = mock_resp

        client = PressureLevelClient()
        result = client.fetch_snapshot(45.0, -90.0, "2026-07-02", 12)
        assert result.source == "open-meteo-pressure"
        assert result.synoptic_time == "2026-07-02T12:00"


class TestArchiveFallback:
    """fetch_hourly: forecast (±7 d) -> historical forecast (2016+) -> IEM RAOB."""

    def _client(self):
        import tempfile
        from core.cache import Cache
        return PressureLevelClient(cache=Cache(tempfile.mkdtemp()))

    def test_forecast_window(self):
        from datetime import date
        from core.pressure_levels import in_forecast_window
        today = date(2026, 11, 2)
        assert in_forecast_window("2026-10-26", today) and in_forecast_window("2026-11-09", today)
        assert not in_forecast_window("2026-10-25", today)
        assert not in_forecast_window("2011-04-27", today)

    def test_interpolate_raob_log_p_and_no_extrapolation(self):
        from core.pressure_levels import interpolate_raob
        levels = [
            {"pres": 1000.0, "hght": 100.0, "tmpc": None, "dwpc": None, "drct": None, "sknt": None},
            {"pres": 900.0, "hght": 1000.0, "tmpc": 20.0, "dwpc": 10.0, "drct": 180.0, "sknt": 20.0},
            {"pres": 800.0, "hght": 2000.0, "tmpc": 10.0, "dwpc": 0.0, "drct": 270.0, "sknt": 20.0},
        ]
        out = interpolate_raob(levels, targets=[1000, 900, 850, 800, 700])
        assert out["temperature"][0] is None          # below the first valid temperature
        assert out["temperature"][1] == 20.0 and out["temperature"][3] == 10.0
        assert 14.0 < out["temperature"][2] < 16.0     # ln-p interpolation
        assert out["temperature"][4] is None           # above burst height
        assert out["geopotential_height"][0] == 100.0  # heights exist below ground
        assert out["wind_speed"][1] == round(20 * 0.514444, 2)
        assert out["wind_direction"][1] == 180.0

    def test_interpolate_raob_keeps_surface_row(self):
        from core.pressure_levels import interpolate_raob
        levels = [  # KOAX 2015-09-08 12Z: 1000 hPa is below ground, 971 hPa is the surface
            {"pres": 1000.0, "hght": 98.0, "tmpc": None, "dwpc": None, "drct": None, "sknt": None},
            {"pres": 971.0, "hght": 350.0, "tmpc": 18.0, "dwpc": 16.3, "drct": 70.0, "sknt": 10.0},
            {"pres": 950.0, "hght": 536.0, "tmpc": 18.4, "dwpc": 13.7, "drct": None, "sknt": None},
            {"pres": 850.0, "hght": 1500.0, "tmpc": 20.0, "dwpc": 5.0, "drct": 200.0, "sknt": 30.0},
        ]
        out = interpolate_raob(levels)
        assert out["surface_pressure"] == 971.0
        assert out["surface_temperature"] == 18.0 and out["surface_dewpoint"] == 16.3
        assert out["surface_wind_speed"] == round(10 * 0.514444, 2)
        assert out["surface_wind_direction"] == 70.0

    def test_old_date_falls_back_to_nearest_raob(self):
        client = self._client()
        stations = {"features": [
            {"id": "_FAR", "geometry": {"coordinates": [-80.0, 40.0]},
             "properties": {"archive_begin": "1950-01-01", "archive_end": None, "sname": "Far"}},
            {"id": "KBMX", "geometry": {"coordinates": [-86.7, 33.17]},
             "properties": {"archive_begin": "1950-01-01", "archive_end": None,
                            "sname": "Birmingham", "elevation": 178.0}},
            {"id": "_OLD", "geometry": {"coordinates": [-87.5, 33.2]},
             "properties": {"archive_begin": "1950-01-01", "archive_end": "1990-01-01", "sname": "Closed"}},
        ]}
        profile = {"profiles": [{"profile": [
            {"pres": 1000.0, "hght": 100.0, "tmpc": 25.0, "dwpc": 20.0, "drct": 180.0, "sknt": 10.0},
            {"pres": 500.0, "hght": 5800.0, "tmpc": -10.0, "dwpc": -20.0, "drct": 240.0, "sknt": 50.0}]}]}
        empty = {"profiles": []}

        def fake_json(url, **kw):
            if "historical-forecast" in url:
                import requests
                raise requests.HTTPError("400")  # before 2016
            if "RAOB.geojson" in url:
                return stations
            return profile if ("1200" in url or "1800" in url) else empty

        with patch.object(client.req, "fetch_json", side_effect=fake_json):
            r = client.fetch_hourly(33.21, -87.57, "2011-04-27")
        assert r.source == "iem-raob" and r.station == "KBMX Birmingham"
        assert r.timestamps == ["2011-04-27T12:00", "2011-04-27T18:00"]
        assert r.temperature[0][0] == 25.0 and len(r.temperature[0]) == len(r.pressure_levels)

        def timeout(url, **kw):
            import requests
            if "historical-forecast" in url:
                raise requests.exceptions.ReadTimeout("read timeout=30")
            return fake_json(url, **kw)

        with patch.object(client.req, "fetch_json", side_effect=timeout):
            assert client.fetch_hourly(33.21, -87.57, "2021-06-11").source == "iem-raob"
