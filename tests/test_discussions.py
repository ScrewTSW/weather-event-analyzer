import pytest
from unittest.mock import patch, MagicMock
from core.discussions import MesoscaleDiscussionClient, select_sources
from core.alerts import (
    is_us_location, is_europe_location, is_canada_location,
    is_australia_location, is_japan_location, is_india_location,
    is_south_america_location,
)


class TestRegionDetection:
    def test_canada(self):
        assert is_canada_location(45.0, -75.0)
        assert not is_canada_location(30.0, -90.0)

    def test_australia(self):
        assert is_australia_location(-33.8, 151.2)
        assert not is_australia_location(45.0, 10.0)

    def test_japan(self):
        assert is_japan_location(35.7, 139.7)
        assert not is_japan_location(45.0, -90.0)

    def test_india(self):
        assert is_india_location(28.6, 77.2)
        assert not is_india_location(45.0, -90.0)

    def test_south_america(self):
        assert is_south_america_location(-34.6, -58.4)
        assert not is_south_america_location(45.0, -90.0)


class TestSelectSources:
    def test_us_gets_spc(self):
        sources = select_sources(35.0, -97.0)
        assert "spc_md" in sources

    def test_europe_gets_estofex(self):
        sources = select_sources(48.0, 11.0)
        assert "estofex" in sources

    def test_unknown_region_gets_wmo(self):
        sources = select_sources(0.0, 0.0)
        assert "wmo_swic" in sources


class TestMesoscaleDiscussionClient:
    @patch("core.discussions.requests.Session")
    def test_fetch_spc_md(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<html><body>
        <pre>Mesoscale Discussion 1234
        Areas affected...Southern Plains
        Valid 021600Z - 022200Z
        DISCUSSION...A surface low over western Oklahoma...</pre>
        </body></html>"""
        mock_resp.raise_for_status = MagicMock()
        mock_session_cls.return_value.get.return_value = mock_resp

        client = MesoscaleDiscussionClient()
        results = client._fetch_spc_md()
        assert isinstance(results, list)

    def test_fetch_returns_list(self):
        client = MesoscaleDiscussionClient()
        with patch.object(client, '_fetch_spc_md', return_value=[]):
            with patch.object(client, '_fetch_estofex', return_value=[]):
                results = client.fetch(35.0, -97.0, "2026-07-02")
                assert isinstance(results, list)
