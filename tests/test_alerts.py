import json
import csv
import io
import pytest
from unittest.mock import patch, MagicMock, Mock
from datetime import datetime
from xml.etree import ElementTree as ET

from core.alerts import (
    GDACSClient,
    NWSClient,
    ESTOFEXClient,
    IBTrACSClient,
    PTWCClient,
    SPCClient,
    MeteoAlarmClient,
    fetch_alerts,
)


class TestGDACSClient:
    @patch('requests.Session.get')
    def test_parse_gdacs_geojson(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "eventid": "1234",
                        "eventtype": "EQ",
                        "episodeid": "5678",
                        "name": "M 7.5 Earthquake",
                        "description": "Strong earthquake in Pacific",
                        "fromdate": "2026-06-01T10:00:00",
                        "todate": "2026-06-01T12:00:00",
                        "url": {
                            "report": "https://www.gdacs.org/report.aspx?eventid=1234"
                        },
                        "alertlevel": "Red",
                        "alertscore": 3.5,
                        "severitydata": {"severity": 7.5, "severitytext": "Magnitude 7.5M, Depth:10km", "severityunit": "M"}
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.5, 37.5]
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = GDACSClient()
        result = client.fetch_current_alerts()

        assert result["source"] == "gdacs"
        assert len(result["alerts"]) == 1
        alert = result["alerts"][0]
        assert alert["event_type"] == "earthquake"
        assert alert["severity"] == "extreme"
        assert alert["title"] == "M 7.5 Earthquake"
        assert alert["geometry"]["type"] == "Point"
        assert alert["valid_from"] == "2026-06-01T10:00:00"

    @patch('requests.Session.get')
    def test_gdacs_historical_alerts(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "eventid": "9999",
                        "eventtype": "TC",
                        "episodeid": "1111",
                        "name": "Cyclone Test",
                        "description": "Test cyclone",
                        "fromdate": "2026-05-30T00:00:00",
                        "todate": "2026-06-02T00:00:00",
                        "url": {"report": "https://example.com"},
                        "alertlevel": "Orange",
                        "severitydata": {"severity": 4, "severitytext": "Category 4", "severityunit": "cat"}
                    },
                    "geometry": None
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = GDACSClient()
        result = client.fetch_historical_alerts("2026-05-31", "2026-06-01")

        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["event_type"] == "cyclone"
        assert result["alerts"][0]["severity"] == "severe"


class TestNWSClient:
    @patch('requests.Session.get')
    def test_parse_nws_geojson(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "alert123",
                        "event": "Tornado Warning",
                        "severity": "Extreme",
                        "headline": "Tornado Warning issued for County X",
                        "description": "Take shelter now",
                        "onset": "2026-06-06T14:00:00-05:00",
                        "expires": "2026-06-06T14:30:00-05:00",
                        "parameters": {
                            "tornadoDetection": ["OBSERVED"],
                            "eventMotionDescription": ["NE at 30 mph"]
                        }
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-95.0, 35.0],
                            [-95.0, 36.0],
                            [-94.0, 36.0],
                            [-94.0, 35.0],
                            [-95.0, 35.0]
                        ]]
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = NWSClient()
        result = client.fetch_current_alerts()

        assert result["source"] == "nws"
        assert len(result["alerts"]) == 1
        alert = result["alerts"][0]
        assert alert["event_type"] == "tornado_warning"
        assert alert["severity"] == "extreme"
        assert alert["geometry"]["type"] == "Polygon"
        assert "tornadoDetection" in alert["properties"]["parameters"]


class TestESTOFEXClient:
    @patch('requests.Session.get')
    def test_parse_estofex_xml(self, mock_get):
        xml_content = """<?xml version="1.0"?>
<estofex>
    <forecast valid_from="2026-06-06T06:00:00Z" valid_to="2026-06-07T06:00:00Z">
        <area risktype="level 2">
            <point lat="50.0" lon="10.0"/>
            <point lat="51.0" lon="10.0"/>
            <point lat="51.0" lon="11.0"/>
            <point lat="50.0" lon="11.0"/>
            <point lat="50.0" lon="10.0"/>
        </area>
        <area risktype="level 3">
            <point lat="45.0" lon="5.0"/>
            <point lat="46.0" lon="5.0"/>
            <point lat="46.0" lon="6.0"/>
            <point lat="45.0" lon="6.0"/>
            <point lat="45.0" lon="5.0"/>
        </area>
    </forecast>
</estofex>"""

        mock_response = MagicMock()
        mock_response.content = xml_content.encode('utf-8')
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = ESTOFEXClient()
        result = client.fetch_current_alerts()

        assert result["source"] == "estofex"
        assert len(result["alerts"]) == 2

        level2 = [a for a in result["alerts"] if a["event_type"] == "convective_level2"][0]
        assert level2["severity"] == "severe"
        assert level2["geometry"]["type"] == "Polygon"
        assert len(level2["geometry"]["coordinates"][0]) == 5

        level3 = [a for a in result["alerts"] if a["event_type"] == "convective_level3"][0]
        assert level3["severity"] == "extreme"


class TestIBTrACSClient:
    @patch('requests.Session.get')
    def test_parse_ibtracs_csv(self, mock_get):
        csv_content = """SID,NAME,ISO_TIME,LAT,LON,WMO_WIND,WMO_PRES,USA_SSHS
2026123S12345,TESTA,2026-06-06T12:00:00,-15.5,145.2,85,950,3
2026456N78901,TESTB,2026-06-06T12:00:00,25.3,-80.1,45,985,0"""

        mock_response = MagicMock()
        mock_response.text = csv_content
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = IBTrACSClient()
        result = client.fetch_current_alerts()

        assert result["source"] == "ibtracs"
        assert len(result["alerts"]) == 2

        testa = result["alerts"][0]
        assert testa["event_type"] == "cyclone"
        assert testa["severity"] == "extreme"
        assert testa["title"] == "Cyclone TESTA (Cat 3)"
        assert testa["geometry"]["type"] == "Point"
        assert testa["properties"]["wind_speed"] == 85
        assert testa["properties"]["category"] == 3


class TestPTWCClient:
    @patch('requests.Session.get')
    def test_parse_ptwc_cap_xml(self, mock_get):
        cap_xml = """<?xml version="1.0"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
    <identifier>TSUNAMI-123</identifier>
    <sent>2026-06-06T12:00:00-00:00</sent>
    <info>
        <event>Tsunami Warning</event>
        <severity>Extreme</severity>
        <urgency>Immediate</urgency>
        <certainty>Observed</certainty>
        <headline>Tsunami Warning for Pacific Coast</headline>
        <description>Major tsunami expected.</description>
        <effective>2026-06-06T12:00:00-00:00</effective>
        <expires>2026-06-06T18:00:00-00:00</expires>
        <area>
            <polygon>-10,150 -10,160 -20,160 -20,150 -10,150</polygon>
        </area>
    </info>
</alert>"""

        mock_response = MagicMock()
        mock_response.content = cap_xml.encode('utf-8')
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = PTWCClient()
        result = client.fetch_current_alerts()

        assert result["source"] == "ptwc"
        assert len(result["alerts"]) == 1
        alert = result["alerts"][0]
        assert alert["event_type"] == "tsunami"
        assert alert["severity"] == "extreme"
        assert alert["geometry"]["type"] == "Polygon"


class TestSPCClient:
    @patch('requests.Session.get')
    def test_parse_spc_tornado_csv(self, mock_get):
        csv_content = """Time,F_Scale,Location,County,State,Lat,Lon,Comments
1400,EF2,Near Town,Test County,OK,35.5,-97.5,Tornado on ground
1430,EF1,City Center,Another County,KS,38.0,-98.0,Brief tornado"""

        mock_response = MagicMock()
        mock_response.text = csv_content
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = SPCClient()
        result = client.fetch_tornado_reports("2026-06-06")

        assert result["source"] == "spc"
        assert len(result["alerts"]) == 2

        ef2 = result["alerts"][0]
        assert ef2["event_type"] == "tornado_report"
        assert ef2["severity"] == "severe"
        assert "EF2" in ef2["title"]
        assert ef2["geometry"]["type"] == "Point"
        assert ef2["properties"]["f_scale"] == "EF2"


class TestMeteoAlarmClient:
    @patch('requests.Session.get')
    def test_parse_meteoalarm_atom_cap(self, mock_get):
        atom_xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
    <entry>
        <id>alert-456</id>
        <updated>2026-06-06T12:00:00Z</updated>
        <title>Severe Thunderstorm Warning</title>
        <cap:event>Thunderstorm</cap:event>
        <cap:severity>Severe</cap:severity>
        <cap:effective>2026-06-06T12:00:00Z</cap:effective>
        <cap:expires>2026-06-06T18:00:00Z</cap:expires>
        <cap:polygon>48.0,11.0 49.0,11.0 49.0,12.0 48.0,12.0 48.0,11.0</cap:polygon>
    </entry>
</feed>"""

        mock_response = MagicMock()
        mock_response.content = atom_xml.encode('utf-8')
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        client = MeteoAlarmClient()
        result = client.fetch_current_alerts("germany")

        assert result["source"] == "meteoalarm"
        assert len(result["alerts"]) == 1
        alert = result["alerts"][0]
        assert alert["event_type"] == "thunderstorm"
        assert alert["severity"] == "severe"


class TestFetchAlertsOrchestrator:
    @patch('core.alerts.GDACSClient')
    @patch('core.alerts.NWSClient')
    @patch('core.alerts.IBTrACSClient')
    def test_orchestrator_us_location(self, mock_ibtracs, mock_nws, mock_gdacs):
        # Mock GDACS
        mock_gdacs_instance = MagicMock()
        mock_gdacs_instance.fetch_current_alerts.return_value = {
            "source": "gdacs",
            "alerts": [{
                "event_type": "earthquake",
                "severity": "extreme",
                "title": "Test EQ",
                "description": "Test",
                "geometry": None,
                "valid_from": "2026-06-06T00:00:00",
                "valid_to": "2026-06-06T23:59:59",
                "source_url": "https://example.com",
                "properties": {}
            }]
        }
        mock_gdacs.return_value = mock_gdacs_instance

        # Mock NWS
        mock_nws_instance = MagicMock()
        mock_nws_instance.fetch_current_alerts.return_value = {
            "source": "nws",
            "alerts": [{
                "event_type": "tornado_warning",
                "severity": "extreme",
                "title": "Tornado Warning",
                "description": "Test",
                "geometry": None,
                "valid_from": "2026-06-06T14:00:00",
                "valid_to": "2026-06-06T14:30:00",
                "source_url": "https://example.com",
                "properties": {}
            }]
        }
        mock_nws.return_value = mock_nws_instance

        # Mock IBTrACS
        mock_ibtracs_instance = MagicMock()
        mock_ibtracs_instance.fetch_current_alerts.return_value = {
            "source": "ibtracs",
            "alerts": []
        }
        mock_ibtracs.return_value = mock_ibtracs_instance

        # Test US location (Oklahoma)
        alerts = fetch_alerts(35.5, -97.5, "2026-06-06T14:00:00")

        # Should have alerts from GDACS and NWS
        assert len(alerts) >= 2
        sources = {a["source"] for a in alerts}
        assert "gdacs" in sources
        assert "nws" in sources

    @patch('core.alerts.GDACSClient')
    @patch('core.alerts.ESTOFEXClient')
    def test_orchestrator_europe_location(self, mock_estofex, mock_gdacs):
        # Mock GDACS
        mock_gdacs_instance = MagicMock()
        mock_gdacs_instance.fetch_current_alerts.return_value = {
            "source": "gdacs",
            "alerts": []
        }
        mock_gdacs.return_value = mock_gdacs_instance

        # Mock ESTOFEX
        mock_estofex_instance = MagicMock()
        mock_estofex_instance.fetch_current_alerts.return_value = {
            "source": "estofex",
            "alerts": [{
                "event_type": "convective_level2",
                "severity": "severe",
                "title": "Convective Outlook Level 2",
                "description": "Test",
                "geometry": None,
                "valid_from": "2026-06-06T00:00:00",
                "valid_to": "2026-06-07T00:00:00",
                "source_url": "https://example.com",
                "properties": {}
            }]
        }
        mock_estofex.return_value = mock_estofex_instance

        # Test Europe location (Germany)
        alerts = fetch_alerts(50.0, 10.0, "2026-06-06T12:00:00")

        sources = {a["source"] for a in alerts}
        assert "estofex" in sources

    def test_orchestrator_severity_sorting(self):
        with patch('core.alerts.GDACSClient') as mock_gdacs:
            mock_gdacs_instance = MagicMock()
            mock_gdacs_instance.fetch_current_alerts.return_value = {
                "source": "gdacs",
                "alerts": [
                    {"severity": "moderate", "event_type": "test1", "title": "A", "description": "",
                     "geometry": None, "valid_from": "", "valid_to": "", "source_url": "", "properties": {}},
                    {"severity": "extreme", "event_type": "test2", "title": "B", "description": "",
                     "geometry": None, "valid_from": "", "valid_to": "", "source_url": "", "properties": {}},
                    {"severity": "severe", "event_type": "test3", "title": "C", "description": "",
                     "geometry": None, "valid_from": "", "valid_to": "", "source_url": "", "properties": {}},
                ]
            }
            mock_gdacs.return_value = mock_gdacs_instance

            alerts = fetch_alerts(0, 0, "2026-06-06T00:00:00", sources=["gdacs"])

            # Should be sorted by severity (extreme, severe, moderate)
            assert alerts[0]["severity"] == "extreme"
            assert alerts[1]["severity"] == "severe"
            assert alerts[2]["severity"] == "moderate"
