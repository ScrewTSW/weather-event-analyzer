"""End-to-end integration tests hitting real APIs.

Run with: python -m pytest tests/test_e2e_integration.py -v -m integration
These tests require network access and may be slow.

Data is fetched ONCE per module run and cached in _DATA for reuse across tests.
"""

import time
import pytest

from core.models import (
    AtmosphereData, VariableTimeSeries,
    PressureLevelSnapshot, PressureLevelHourly,
    Discussion, FrontalFeature, Alert, SatelliteFrame,
)
from core.models.base import BaseDataResult

pytestmark = pytest.mark.integration

# Shared data cache — fetched once, reused across all test classes
_DATA = {}


def _fetch_once(key, fn):
    if key not in _DATA:
        time.sleep(0.5)
        _DATA[key] = fn()
    return _DATA[key]


class TestAtmosphereE2E:
    def test_openmeteo_forecast_returns_typed(self):
        from core.atmosphere import OpenMeteoClient
        client = OpenMeteoClient()
        result = client.fetch_forecast(42.3, -83.0, past_days=1)
        assert isinstance(result, AtmosphereData)
        assert isinstance(result, BaseDataResult)
        assert result.source == "open-meteo-forecast"
        assert len(result.timestamps) > 0
        assert "temperature" in result.variables
        assert isinstance(result.variables["temperature"], VariableTimeSeries)
        assert result.variables["temperature"].unit == "C"
        assert len(result.variables["temperature"].data) == len(result.timestamps)

    def test_openmeteo_forecast_has_cape(self):
        from core.atmosphere import OpenMeteoClient
        client = OpenMeteoClient()
        result = client.fetch_forecast(35.0, -97.0, past_days=1)
        assert "cape" in result.variables
        assert "cin" in result.variables
        assert result.variables["cape"].unit == "J/kg"

    def test_openmeteo_archive_returns_typed(self):
        from core.atmosphere import OpenMeteoClient
        client = OpenMeteoClient()
        result = client.fetch_archive(42.3, -83.0, "2026-06-01")
        assert isinstance(result, AtmosphereData)
        assert result.source == "open-meteo-archive"
        assert "temperature" in result.variables
        assert "cape" not in result.variables


class TestPressureLevelsE2E:
    def test_snapshot_returns_typed(self):
        from core.pressure_levels import PressureLevelClient
        result = _fetch_once("profile_snapshot",
                             lambda: PressureLevelClient().fetch_snapshot(42.3, -83.0, "2026-07-02", 12))
        assert isinstance(result, PressureLevelSnapshot)
        assert isinstance(result, BaseDataResult)
        assert result.source == "open-meteo-pressure"
        assert result.synoptic_time is not None
        assert len(result.pressure_levels) == 19
        assert len(result.temperature) == 19
        assert len(result.dewpoint) == 19
        assert len(result.wind_speed) == 19
        assert len(result.wind_direction) == 19
        assert len(result.geopotential_height) == 19

    def test_hourly_returns_typed(self):
        from core.pressure_levels import PressureLevelClient
        result = _fetch_once("profile_hourly",
                             lambda: PressureLevelClient().fetch_hourly(42.3, -83.0, "2026-07-02"))
        assert isinstance(result, PressureLevelHourly)
        assert isinstance(result, BaseDataResult)
        assert len(result.timestamps) > 0
        assert len(result.temperature) == len(result.timestamps)
        assert len(result.temperature[0]) == 19

    def test_snapshot_values_physical(self):
        from core.pressure_levels import PressureLevelClient
        result = _fetch_once("profile_snapshot",
                             lambda: PressureLevelClient().fetch_snapshot(42.3, -83.0, "2026-07-02", 12))
        temps = [t for t in result.temperature if t is not None]
        assert all(-80 < t < 50 for t in temps), f"Temps out of range: {temps}"
        heights = [h for h in result.geopotential_height if h is not None]
        assert heights[0] < heights[-1], "Heights should increase with altitude"


class TestDiscussionsE2E:
    def test_us_location_returns_discussions(self):
        from core.discussions import MesoscaleDiscussionClient
        client = MesoscaleDiscussionClient()
        results = client.fetch(35.0, -97.0, "2026-07-02")
        assert isinstance(results, list)
        for d in results:
            assert isinstance(d, Discussion)
            assert isinstance(d, BaseDataResult)
            assert d.source != ""
            assert d.title != ""
            assert d.region != ""

    def test_europe_location_returns_discussions(self):
        from core.discussions import MesoscaleDiscussionClient
        client = MesoscaleDiscussionClient()
        results = client.fetch(49.2, 16.6, "2026-07-02")
        assert isinstance(results, list)
        for d in results:
            assert isinstance(d, Discussion)

    def test_spc_md_has_clean_text(self):
        from core.discussions import MesoscaleDiscussionClient
        client = MesoscaleDiscussionClient()
        results = client._fetch_spc_md()
        if results:
            d = results[0]
            assert isinstance(d, Discussion)
            assert "<script" not in d.text.lower()
            assert "window.dataLayer" not in d.text
            assert "&nbsp;" not in d.text


class TestFrontsE2E:
    def test_analyze_returns_typed(self):
        from core.fronts import FrontalAnalyzer
        analyzer = FrontalAnalyzer()
        result = analyzer.fetch_and_analyze(42.3, -83.0, "2026-07-02",
                                            run_hour="12", bbox_deg=10)
        assert isinstance(result, list)
        for f in result:
            assert isinstance(f, FrontalFeature)
            assert f.type in ("cold", "warm", "dry")
            assert len(f.lats) == len(f.lons)
            assert isinstance(f.is_merger, bool)

    def test_no_fronts_in_tropics(self):
        from core.fronts import FrontalAnalyzer
        analyzer = FrontalAnalyzer()
        result = analyzer.fetch_and_analyze(5.0, -60.0, "2026-07-02",
                                            run_hour="12", bbox_deg=10)
        assert len(result) == 0


class TestAlertsE2E:
    def test_fetch_alerts_returns_typed(self):
        from core.alerts import fetch_alerts
        result = fetch_alerts(42.3, -83.0, "2026-07-02T12:00:00")
        assert isinstance(result, list)
        for a in result:
            assert isinstance(a, Alert)
            assert isinstance(a, BaseDataResult)
            assert a.severity in ("extreme", "severe", "moderate", "minor")
            assert a.event_type != ""

    def test_global_sources_always_return(self):
        from core.alerts import fetch_alerts
        result = fetch_alerts(42.3, -83.0, "2026-07-02T12:00:00",
                              sources=["gdacs"])
        assert isinstance(result, list)
        for a in result:
            assert isinstance(a, Alert)


class TestVisualizationE2E:
    """Chart rendering from real fetched data — reuses cached API results."""

    def test_sounding_chart_from_real_data(self):
        import matplotlib.figure
        from core.pressure_levels import PressureLevelClient
        from core.visualize import create_sounding_chart
        profile = _fetch_once("profile_snapshot",
                              lambda: PressureLevelClient().fetch_snapshot(42.3, -83.0, "2026-07-02", 12))
        fig = create_sounding_chart(profile)
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_hodograph_from_real_data(self):
        import matplotlib.figure
        from core.pressure_levels import PressureLevelClient
        from core.visualize import create_hodograph_chart
        profile = _fetch_once("profile_snapshot",
                              lambda: PressureLevelClient().fetch_snapshot(42.3, -83.0, "2026-07-02", 12))
        fig = create_hodograph_chart(profile)
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_atmosphere_charts_from_real_data(self):
        import matplotlib.figure
        from core.atmosphere import OpenMeteoClient
        from core.visualize import create_atmosphere_chart
        data = _fetch_once("atmo_surface",
                           lambda: OpenMeteoClient().fetch(42.3, -83.0, "2026-07-02"))
        for chart_type in ["cape_cin", "temp_dewpoint", "wind"]:
            fig = create_atmosphere_chart(data, chart_type)
            assert isinstance(fig, matplotlib.figure.Figure)
