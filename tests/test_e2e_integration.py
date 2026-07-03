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


class TestSatelliteE2E:
    def test_select_source_us(self):
        from core.satellite import select_source
        name, src = select_source(42.3, -83.0)
        assert "goes" in name
        assert "layer" in src

    def test_select_source_europe(self):
        from core.satellite import select_source
        name, src = select_source(49.2, 16.6)
        assert "meteosat" in name

    def test_select_source_japan(self):
        from core.satellite import select_source
        name, src = select_source(35.7, 139.7)
        assert "himawari" in name

    def test_select_source_manual(self):
        from core.satellite import select_source, SOURCES
        name, src = select_source(42.3, -83.0, product="goes_east")
        assert name == "goes_east"

    def test_fetch_event_returns_typed(self):
        import tempfile
        from core.satellite import SatelliteClient
        from core.cache import Cache
        cache = Cache(tempfile.mkdtemp(), max_size_gb=0.1)
        client = SatelliteClient(cache)
        frames = _fetch_once("satellite_frames",
                             lambda: client.fetch_event(
                                 lat=42.3, lon=-83.0,
                                 date="20260702", start_utc="1500", end_utc="1510",
                                 product="goes_east_geocolor", zoom=2, bbox_deg=20,
                             ))
        assert isinstance(frames, list)
        for f in frames:
            assert isinstance(f, SatelliteFrame)
            assert f.timestamp != ""
            assert f.image is not None
            assert f.source != ""
            assert f.layer != ""


class TestCachingE2E:
    """Verify caching: clean cache, fetch, assert cache grew, re-fetch, assert cache unchanged."""

    def setup_method(self):
        import tempfile
        import shutil
        from core.cache import Cache
        self.cache_dir = tempfile.mkdtemp()
        self.cache = Cache(self.cache_dir, max_size_gb=0.1)
        self.cache.cleanup(max_age_hours=0)

    def _json_count(self):
        return self.cache.stats().get("json", {}).get("count", 0)

    def _json_size(self):
        return self.cache.stats().get("json", {}).get("size_bytes", 0)

    def test_atmosphere_cache_hit(self):
        from core.atmosphere import OpenMeteoClient
        client = OpenMeteoClient(cache=self.cache)

        before_count = self._json_count()
        result1 = client.fetch(42.3, -83.0, "2026-07-02")
        assert isinstance(result1, AtmosphereData)
        api_calls_after_first = client.req.stats.api_calls
        assert api_calls_after_first > 0, "Should have made API calls"
        after_count = self._json_count()
        assert after_count > before_count, "Cache should grow after first fetch"

        result2 = client.fetch(42.3, -83.0, "2026-07-02")
        assert isinstance(result2, AtmosphereData)
        assert client.req.stats.api_calls == api_calls_after_first, "Cache hit should NOT make API calls"
        assert client.req.stats.cache_hits > 0, "Should have cache hits"
        assert self._json_count() == after_count, "Cache should not grow on cache hit"
        assert result2.source == result1.source
        assert len(result2.timestamps) == len(result1.timestamps)

    def test_pressure_levels_cache_hit(self):
        from core.pressure_levels import PressureLevelClient
        client = PressureLevelClient(cache=self.cache)

        before_count = self._json_count()
        result1 = client.fetch_snapshot(42.3, -83.0, "2026-07-02", 12)
        assert isinstance(result1, PressureLevelSnapshot)
        api_calls_after_first = client.req.stats.api_calls
        assert api_calls_after_first > 0, "Should have made API calls"
        after_count = self._json_count()
        assert after_count > before_count

        result2 = client.fetch_snapshot(42.3, -83.0, "2026-07-02", 12)
        assert client.req.stats.api_calls == api_calls_after_first, "Cache hit should NOT make API calls"
        assert client.req.stats.cache_hits > 0, "Should have cache hits"
        assert self._json_count() == after_count
        assert result2.synoptic_time == result1.synoptic_time

    def test_discussions_cache_hit(self):
        from core.discussions import MesoscaleDiscussionClient
        client = MesoscaleDiscussionClient(cache=self.cache)

        before_count = self._json_count()
        result1 = client.fetch(35.0, -97.0, "2026-07-02")
        assert isinstance(result1, list)
        api_calls_after_first = client.req.stats.api_calls
        assert api_calls_after_first > 0, "Should have made API calls"
        after_count = self._json_count()
        assert after_count > before_count

        result2 = client.fetch(35.0, -97.0, "2026-07-02")
        # Discussions use high-level result caching, so cache hits don't increment req.stats
        assert client.req.stats.api_calls == api_calls_after_first, "Cache hit should NOT make API calls"
        assert self._json_count() == after_count
        if result1:
            assert result2[0].title == result1[0].title

    def test_cache_persists_across_instances(self):
        from core.atmosphere import OpenMeteoClient
        from core.cache import Cache
        client = OpenMeteoClient(cache=self.cache)
        client.fetch(42.3, -83.0, "2026-07-02")
        size_before = self._json_size()
        assert size_before > 0

        cache2 = Cache(self.cache_dir, max_size_gb=0.1)
        assert cache2.stats().get("json", {}).get("size_bytes", 0) == size_before

    def test_satellite_cache_hit(self):
        import tempfile
        from core.satellite import SatelliteClient
        from core.cache import Cache
        cache = Cache(tempfile.mkdtemp(), max_size_gb=0.1)
        cache.cleanup(max_age_hours=0)
        client = SatelliteClient(cache)

        before = cache.stats().get("binary", {}).get("count", 0)
        frames1 = client.fetch_event(
            lat=42.3, lon=-83.0, date="20260702",
            start_utc="1500", end_utc="1510",
            product="goes_east_geocolor", zoom=2, bbox_deg=20,
        )
        after = cache.stats().get("binary", {}).get("count", 0)
        assert after > before, "Binary cache should grow after satellite fetch"

        frames2 = client.fetch_event(
            lat=42.3, lon=-83.0, date="20260702",
            start_utc="1500", end_utc="1510",
            product="goes_east_geocolor", zoom=2, bbox_deg=20,
        )
        assert cache.stats().get("binary", {}).get("count", 0) == after, "Cache should not grow on re-fetch"
        assert len(frames2) == len(frames1)

    def test_cached_data_preserves_types(self):
        from core.atmosphere import OpenMeteoClient
        client = OpenMeteoClient(cache=self.cache)
        fresh = client.fetch(42.3, -83.0, "2026-07-02")
        cached = client.fetch(42.3, -83.0, "2026-07-02")
        assert type(fresh) == type(cached)
        for var_name in fresh.variables:
            assert var_name in cached.variables
            assert type(fresh.variables[var_name]) == type(cached.variables[var_name])
