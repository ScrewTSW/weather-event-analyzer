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
        assert result["source"] == "open-meteo-pressure"
        assert result["synoptic_time"] == "2026-07-02T00:00"
        assert len(result["temperature"]) == len(PRESSURE_LEVELS)
        assert len(result["pressure_levels"]) == len(PRESSURE_LEVELS)
        assert result["timestamps"] is None

    def test_parse_hourly(self):
        client = PressureLevelClient()
        raw = self._make_mock_response()
        result = client._parse_response(raw, synoptic_hour=None)
        assert len(result["timestamps"]) == 2
        assert len(result["temperature"]) == 2
        assert len(result["temperature"][0]) == len(PRESSURE_LEVELS)


class TestPressureLevelClientFetch:
    @patch("core.pressure_levels.requests.Session")
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
        assert result["source"] == "open-meteo-pressure"
        assert result["synoptic_time"] == "2026-07-02T12:00"
