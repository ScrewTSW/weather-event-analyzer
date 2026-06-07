import pytest
from unittest.mock import patch, MagicMock
from core.atmosphere import (
    build_openmeteo_forecast_url,
    build_openmeteo_archive_url,
    build_gfs_nomads_url,
    parse_openmeteo_response,
    select_gfs_run,
    OpenMeteoClient,
)


class TestBuildURLs:
    def test_forecast_url(self):
        url = build_openmeteo_forecast_url(45.75, 126.65, past_days=7)
        assert "api.open-meteo.com/v1/forecast" in url
        assert "latitude=45.75" in url
        assert "cape" in url
        assert "convective_inhibition" in url
        assert "lifted_index" in url
        assert "wind_speed_unit=ms" in url
        assert "past_days=7" in url

    def test_archive_url(self):
        url = build_openmeteo_archive_url(45.75, 126.65, "2025-05-31")
        assert "archive-api.open-meteo.com/v1/archive" in url
        assert "cape" not in url  # CAPE excluded from archive (always null)

    def test_gfs_nomads_url(self):
        url = build_gfs_nomads_url("20260531", "06", "003", 42.75, 48.75, 123.65, 129.65)
        assert "nomads.ncep.noaa.gov" in url
        assert "gfs.20260531" in url
        assert "gfs.t06z" in url
        assert "f003" in url
        assert "var_CAPE=on" in url


class TestSelectGFSRun:
    def test_morning_event(self):
        run_hour, forecast_hour = select_gfs_run("09:30")
        assert run_hour == "06"
        assert forecast_hour == "003"

    def test_afternoon_event(self):
        run_hour, forecast_hour = select_gfs_run("15:00")
        assert run_hour == "12"
        assert forecast_hour == "003"

    def test_midnight_event(self):
        run_hour, forecast_hour = select_gfs_run("02:00")
        assert run_hour == "00"
        assert forecast_hour == "002"


class TestParseOpenMeteoResponse:
    def test_parse_forecast(self):
        mock_json = {
            "latitude": 45.73,
            "longitude": 126.62,
            "hourly": {
                "time": ["2026-05-31T00:00", "2026-05-31T01:00"],
                "temperature_2m": [20.5, 19.3],
                "relative_humidity_2m": [65, 70],
                "dewpoint_2m": [13.2, 13.8],
                "cape": [450.0, 380.0],
                "convective_inhibition": [12.0, 8.0],
                "lifted_index": [-3.1, -2.5],
                "surface_pressure": [1013.2, 1013.5],
                "wind_speed_10m": [5.2, 4.8],
                "wind_direction_10m": [225, 230],
                "wind_gusts_10m": [12.1, 10.5],
            },
        }
        result = parse_openmeteo_response(mock_json, source="open-meteo-forecast")
        assert result["source"] == "open-meteo-forecast"
        assert result["gridded"] is False
        assert len(result["timestamps"]) == 2
        assert result["variables"]["temperature"]["data"] == [20.5, 19.3]
        assert result["variables"]["cape"]["data"] == [450.0, 380.0]


class TestAtmosphereIntegration:
    @pytest.mark.integration
    def test_fetch_forecast_has_cape(self):
        client = OpenMeteoClient()
        result = client.fetch_forecast(45.75, 126.65, past_days=1)
        assert result["source"] == "open-meteo-forecast"
        assert "cape" in result["variables"]
        cape_data = result["variables"]["cape"]["data"]
        assert any(v is not None for v in cape_data)
