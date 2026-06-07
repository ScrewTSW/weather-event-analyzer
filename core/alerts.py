"""
Global Alerts Module for Weather Event Analyzer.

Fetches severe weather warnings from multiple global sources.
Supports both real-time (current alerts) and historical (alerts active at a given date/time).
"""

import csv
import io
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

import requests

USER_AGENT = "WeatherEventAnalyzer/1.0"
RATE_LIMIT_DELAY = 0.3  # 300ms between requests to any single source

logger = logging.getLogger(__name__)


class GDACSClient:
    """Global Disaster Alert and Coordination System client."""

    BASE_URL = "https://www.gdacs.org/gdacsapi/api"
    EVENT_TYPES = "EQ;FL;TC;VO;WF;DR"  # Earthquake, Flood, Cyclone, Volcano, Wildfire, Drought

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self) -> dict:
        """Fetch current active alerts (last 7 days)."""
        try:
            # Python 3.11+ has datetime.UTC
            from datetime import UTC
            to_date = datetime.now(UTC).replace(tzinfo=None)
        except ImportError:
            to_date = datetime.utcnow()
        from_date = to_date - timedelta(days=7)
        return self.fetch_historical_alerts(
            from_date.strftime("%Y-%m-%d"),
            to_date.strftime("%Y-%m-%d")
        )

    def fetch_historical_alerts(self, from_date: str, to_date: str) -> dict:
        """Fetch alerts active between from_date and to_date (ISO format: YYYY-MM-DD)."""
        url = f"{self.BASE_URL}/Events/geteventlist/search"
        params = {
            "eventlist": self.EVENT_TYPES,
            "fromdate": from_date,
            "todate": to_date,
            "alertlevel": "red;orange;green"
        }

        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            alerts = []
            if data.get("type") == "FeatureCollection":
                for feature in data.get("features", []):
                    alert = self._parse_feature(feature)
                    if alert:
                        alerts.append(alert)

            return {"source": "gdacs", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"GDACS fetch failed: {e}")
            return {"source": "gdacs", "alerts": []}

    def _parse_feature(self, feature: dict) -> Optional[dict]:
        """Parse a GDACS GeoJSON feature into standard alert format."""
        props = feature.get("properties", {})

        # Map GDACS event types to standard types
        event_type_map = {
            "EQ": "earthquake",
            "FL": "flood",
            "TC": "cyclone",
            "VO": "volcano",
            "WF": "wildfire",
            "DR": "drought"
        }
        event_type = event_type_map.get(props.get("eventtype", ""), "unknown")

        # Map GDACS severity to standard severity
        severity_map = {
            "Red": "extreme",
            "Orange": "severe",
            "Green": "moderate"
        }
        severity = severity_map.get(props.get("severity", "Green"), "moderate")

        return {
            "event_type": event_type,
            "severity": severity,
            "title": props.get("name", "Unknown Event"),
            "description": props.get("description", ""),
            "geometry": feature.get("geometry"),
            "valid_from": props.get("fromdate", ""),
            "valid_to": props.get("todate", ""),
            "source_url": props.get("url", {}).get("report", ""),
            "properties": {
                "event_id": props.get("eventid"),
                "episode_id": props.get("episodeid"),
                "country": props.get("country", ""),
            }
        }


class NWSClient:
    """National Weather Service (US) Alerts API client."""

    BASE_URL = "https://api.weather.gov/alerts"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self) -> dict:
        """Fetch current active alerts with Extreme or Severe severity."""
        url = f"{self.BASE_URL}/active"
        params = {"severity": "Extreme,Severe"}

        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            alerts = []
            if data.get("type") == "FeatureCollection":
                for feature in data.get("features", []):
                    alert = self._parse_feature(feature)
                    if alert:
                        alerts.append(alert)

            return {"source": "nws", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"NWS fetch failed: {e}")
            return {"source": "nws", "alerts": []}

    def _parse_feature(self, feature: dict) -> Optional[dict]:
        """Parse a NWS alert feature into standard format."""
        props = feature.get("properties", {})

        # Normalize event name to snake_case
        event = props.get("event", "Unknown")
        event_type = event.lower().replace(" ", "_")

        # Map NWS severity to standard severity
        severity_map = {
            "Extreme": "extreme",
            "Severe": "severe",
            "Moderate": "moderate",
            "Minor": "minor"
        }
        severity = severity_map.get(props.get("severity", "Moderate"), "moderate")

        return {
            "event_type": event_type,
            "severity": severity,
            "title": props.get("headline", event),
            "description": props.get("description", ""),
            "geometry": feature.get("geometry"),
            "valid_from": props.get("onset", ""),
            "valid_to": props.get("expires", ""),
            "source_url": f"https://api.weather.gov/alerts/{props.get('id', '')}",
            "properties": {
                "urgency": props.get("urgency"),
                "certainty": props.get("certainty"),
                "parameters": props.get("parameters", {}),
            }
        }


class ESTOFEXClient:
    """ESTOFEX (European Storm Forecast Experiment) client."""

    BASE_URL = "https://www.estofex.org/cgi-bin/polygon/showforecast.cgi"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self) -> dict:
        """Fetch current convective forecast."""
        url = self.BASE_URL
        params = {"xml": "yes"}

        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()

            alerts = self._parse_xml(resp.content)
            return {"source": "estofex", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"ESTOFEX fetch failed: {e}")
            return {"source": "estofex", "alerts": []}

    def _parse_xml(self, xml_content: bytes) -> list[dict]:
        """Parse ESTOFEX XML forecast."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.warning(f"ESTOFEX XML parse error: {e}")
            return []

        alerts = []
        for forecast in root.findall(".//forecast"):
            valid_from = forecast.get("valid_from", "")
            valid_to = forecast.get("valid_to", "")

            for area in forecast.findall(".//area"):
                risk_type = area.get("risktype", "")

                # Parse polygon coordinates
                points = []
                for point in area.findall(".//point"):
                    lat = float(point.get("lat", 0))
                    lon = float(point.get("lon", 0))
                    points.append([lon, lat])  # GeoJSON format: [lon, lat]

                if len(points) < 3:
                    continue

                # Map risk level to severity
                level_map = {
                    "level 1": ("convective_level1", "moderate"),
                    "level 2": ("convective_level2", "severe"),
                    "level 3": ("convective_level3", "extreme"),
                }
                event_type, severity = level_map.get(risk_type, ("convective_unknown", "moderate"))

                geometry = {
                    "type": "Polygon",
                    "coordinates": [points]
                }

                alerts.append({
                    "event_type": event_type,
                    "severity": severity,
                    "title": f"Convective Outlook {risk_type.title()}",
                    "description": f"ESTOFEX convective forecast {risk_type}",
                    "geometry": geometry,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "source_url": self.BASE_URL,
                    "properties": {"risk_type": risk_type}
                })

        return alerts


class IBTrACSClient:
    """IBTrACS (International Best Track Archive for Climate Stewardship) client."""

    ACTIVE_URL = "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.ACTIVE.list.v04r01.csv"
    HISTORICAL_URL = "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.last3years.list.v04r01.csv"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self) -> dict:
        """Fetch currently active cyclones."""
        return self._fetch_from_url(self.ACTIVE_URL)

    def fetch_historical_alerts(self, from_date: str, to_date: str) -> dict:
        """Fetch historical cyclones (last 3 years)."""
        # IBTrACS historical endpoint provides last 3 years
        # We filter by date after fetching
        return self._fetch_from_url(self.HISTORICAL_URL, from_date, to_date)

    def _fetch_from_url(self, url: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """Fetch and parse IBTrACS CSV data."""
        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()

            alerts = self._parse_csv(resp.text, from_date, to_date)
            return {"source": "ibtracs", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"IBTrACS fetch failed: {e}")
            return {"source": "ibtracs", "alerts": []}

    def _parse_csv(self, csv_text: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> list[dict]:
        """Parse IBTrACS CSV data."""
        alerts = []
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            try:
                # Filter by date if specified
                iso_time = row.get("ISO_TIME", "")
                if from_date and to_date and iso_time:
                    if not (from_date <= iso_time <= to_date):
                        continue

                name = row.get("NAME", "UNNAMED")
                lat = float(row.get("LAT", 0))
                lon = float(row.get("LON", 0))
                wind_speed = int(float(row.get("WMO_WIND", 0) or 0))
                pressure = int(float(row.get("WMO_PRES", 0) or 0))
                category = int(float(row.get("USA_SSHS", -1) or -1))

                # Determine severity based on category
                if category >= 3:
                    severity = "extreme"
                elif category >= 1:
                    severity = "severe"
                else:
                    severity = "moderate"

                cat_str = f"Cat {category}" if category >= 0 else "TS"
                title = f"Cyclone {name} ({cat_str})"

                geometry = {
                    "type": "Point",
                    "coordinates": [lon, lat]
                }

                alerts.append({
                    "event_type": "cyclone",
                    "severity": severity,
                    "title": title,
                    "description": f"Tropical cyclone {name}",
                    "geometry": geometry,
                    "valid_from": iso_time,
                    "valid_to": iso_time,  # Point in time
                    "source_url": "https://www.ncei.noaa.gov/products/international-best-track-archive",
                    "properties": {
                        "sid": row.get("SID"),
                        "wind_speed": wind_speed,
                        "pressure": pressure,
                        "category": category,
                    }
                })

            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping IBTrACS row: {e}")
                continue

        return alerts


class PTWCClient:
    """Pacific Tsunami Warning Center client."""

    CAP_URL = "https://www.tsunami.gov/events/xml/PHEBCAP.xml"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self) -> dict:
        """Fetch current tsunami alerts from PTWC CAP feed."""
        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(self.CAP_URL, timeout=30)
            resp.raise_for_status()

            alerts = self._parse_cap_xml(resp.content)
            return {"source": "ptwc", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"PTWC fetch failed: {e}")
            return {"source": "ptwc", "alerts": []}

    def _parse_cap_xml(self, xml_content: bytes) -> list[dict]:
        """Parse CAP 1.2 XML format."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.warning(f"PTWC CAP XML parse error: {e}")
            return []

        # Define CAP namespace
        ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

        alerts = []
        # Check if root is already an alert element or contains alerts
        alert_elems = [root] if root.tag.endswith("alert") else root.findall(".//cap:alert", ns) or root.findall(".//alert")

        for alert_elem in alert_elems:
            # Handle both namespaced and non-namespaced elements
            info = alert_elem.find(".//cap:info", ns)
            if info is None:
                info = alert_elem.find(".//info")
            if info is None:
                info = alert_elem.find("cap:info", ns)
            if info is None:
                info = alert_elem.find("info")
            if info is None:
                continue

            event = self._get_text(info, "event", ns) or "Tsunami"
            severity = self._get_text(info, "severity", ns) or "Moderate"
            headline = self._get_text(info, "headline", ns) or event
            description = self._get_text(info, "description", ns) or ""
            effective = self._get_text(info, "effective", ns) or ""
            expires = self._get_text(info, "expires", ns) or ""

            # Parse area/polygon
            area = info.find(".//cap:area", ns)
            if area is None:
                area = info.find("area")
            geometry = None
            if area is not None:
                polygon_text = self._get_text(area, "polygon", ns)
                if polygon_text:
                    geometry = self._parse_polygon(polygon_text)

            # Map severity
            severity_map = {
                "Extreme": "extreme",
                "Severe": "severe",
                "Moderate": "moderate",
                "Minor": "minor"
            }
            severity = severity_map.get(severity, "moderate")

            alerts.append({
                "event_type": "tsunami",
                "severity": severity,
                "title": headline,
                "description": description,
                "geometry": geometry,
                "valid_from": effective,
                "valid_to": expires,
                "source_url": self.CAP_URL,
                "properties": {
                    "urgency": self._get_text(info, "urgency", ns),
                    "certainty": self._get_text(info, "certainty", ns),
                }
            })

        return alerts

    def _get_text(self, element, tag: str, ns: dict) -> Optional[str]:
        """Get text from an XML element, handling both namespaced and non-namespaced."""
        child = element.find(f".//cap:{tag}", ns)
        if child is None:
            child = element.find(f"cap:{tag}", ns)
        if child is None:
            child = element.find(f".//{tag}")
        if child is None:
            child = element.find(tag)
        return child.text if child is not None else None

    def _parse_polygon(self, polygon_text: str) -> dict:
        """Parse CAP polygon format: 'lat,lon lat,lon lat,lon'."""
        try:
            points = []
            for pair in polygon_text.strip().split():
                lat, lon = pair.split(",")
                points.append([float(lon), float(lat)])  # GeoJSON: [lon, lat]

            return {
                "type": "Polygon",
                "coordinates": [points]
            }
        except (ValueError, IndexError):
            return None


class SPCClient:
    """Storm Prediction Center (US) client."""

    TORNADO_REPORTS_URL = "https://www.spc.noaa.gov/climo/reports/{date}_torn.csv"
    WIND_REPORTS_URL = "https://www.spc.noaa.gov/climo/reports/{date}_wind.csv"
    HAIL_REPORTS_URL = "https://www.spc.noaa.gov/climo/reports/{date}_hail.csv"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_tornado_reports(self, date: str) -> dict:
        """Fetch tornado reports for a specific date (YYYY-MM-DD or 'today')."""
        date_str = self._format_date(date)
        return self._fetch_reports(self.TORNADO_REPORTS_URL.format(date=date_str), "tornado_report")

    def fetch_wind_reports(self, date: str) -> dict:
        """Fetch wind reports for a specific date."""
        date_str = self._format_date(date)
        return self._fetch_reports(self.WIND_REPORTS_URL.format(date=date_str), "wind_report")

    def fetch_hail_reports(self, date: str) -> dict:
        """Fetch hail reports for a specific date."""
        date_str = self._format_date(date)
        return self._fetch_reports(self.HAIL_REPORTS_URL.format(date=date_str), "hail_report")

    def fetch_current_alerts(self) -> dict:
        """Fetch today's tornado reports (most severe)."""
        return self.fetch_tornado_reports("today")

    def _format_date(self, date: str) -> str:
        """Format date string for SPC URLs (YYMMDD or 'today')."""
        if date == "today":
            return "today"
        # Convert YYYY-MM-DD to YYMMDD
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            return dt.strftime("%y%m%d")
        except ValueError:
            return date  # Return as-is if not in expected format

    def _fetch_reports(self, url: str, event_type: str) -> dict:
        """Fetch and parse SPC storm reports CSV."""
        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()

            alerts = self._parse_csv(resp.text, event_type, url)
            return {"source": "spc", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"SPC fetch failed: {e}")
            return {"source": "spc", "alerts": []}

    def _parse_csv(self, csv_text: str, event_type: str, source_url: str) -> list[dict]:
        """Parse SPC storm reports CSV."""
        alerts = []
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            try:
                time_str = row.get("Time", "")
                location = row.get("Location", "Unknown")
                county = row.get("County", "")
                state = row.get("State", "")
                lat = float(row.get("Lat", 0))
                lon = float(row.get("Lon", 0))
                comments = row.get("Comments", "")

                # Event-specific fields
                if event_type == "tornado_report":
                    f_scale = row.get("F_Scale", "")
                    title = f"Tornado Report {f_scale} - {location}, {state}"
                    severity = "extreme" if "EF3" in f_scale or "EF4" in f_scale or "EF5" in f_scale else "severe"
                    properties = {"f_scale": f_scale}
                elif event_type == "wind_report":
                    speed = row.get("Speed", "")
                    title = f"Wind Report {speed} - {location}, {state}"
                    severity = "severe" if speed else "moderate"
                    properties = {"speed": speed}
                else:  # hail_report
                    size = row.get("Size", "")
                    title = f"Hail Report {size} - {location}, {state}"
                    severity = "severe" if size else "moderate"
                    properties = {"size": size}

                geometry = {
                    "type": "Point",
                    "coordinates": [lon, lat]
                }

                alerts.append({
                    "event_type": event_type,
                    "severity": severity,
                    "title": title,
                    "description": comments,
                    "geometry": geometry,
                    "valid_from": time_str,
                    "valid_to": time_str,
                    "source_url": source_url,
                    "properties": {
                        **properties,
                        "county": county,
                        "state": state,
                    }
                })

            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping SPC row: {e}")
                continue

        return alerts


class MeteoAlarmClient:
    """MeteoAlarm (Europe) client."""

    FEED_URL = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_current_alerts(self, country: str = "germany") -> dict:
        """Fetch current alerts for a specific European country."""
        url = self.FEED_URL.format(country=country.lower())

        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()

            alerts = self._parse_atom_cap(resp.content)
            return {"source": "meteoalarm", "alerts": alerts}

        except requests.RequestException as e:
            logger.warning(f"MeteoAlarm fetch failed: {e}")
            return {"source": "meteoalarm", "alerts": []}

    def _parse_atom_cap(self, xml_content: bytes) -> list[dict]:
        """Parse Atom+CAP XML feed."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.warning(f"MeteoAlarm XML parse error: {e}")
            return []

        # Define namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "cap": "urn:oasis:names:tc:emergency:cap:1.2"
        }

        alerts = []
        for entry in root.findall(".//atom:entry", ns) or root.findall("entry"):
            title = self._get_text(entry, "title", ns, "atom")
            event = self._get_text(entry, "event", ns, "cap") or "Unknown"
            severity = self._get_text(entry, "severity", ns, "cap") or "Moderate"
            effective = self._get_text(entry, "effective", ns, "cap") or ""
            expires = self._get_text(entry, "expires", ns, "cap") or ""
            polygon_text = self._get_text(entry, "polygon", ns, "cap")

            # Parse polygon
            geometry = None
            if polygon_text:
                geometry = self._parse_polygon(polygon_text)

            # Map severity
            severity_map = {
                "Extreme": "extreme",
                "Severe": "severe",
                "Moderate": "moderate",
                "Minor": "minor"
            }
            severity = severity_map.get(severity, "moderate")

            # Normalize event type
            event_type = event.lower().replace(" ", "_")

            alerts.append({
                "event_type": event_type,
                "severity": severity,
                "title": title or event,
                "description": "",
                "geometry": geometry,
                "valid_from": effective,
                "valid_to": expires,
                "source_url": self.FEED_URL.format(country=""),
                "properties": {}
            })

        return alerts

    def _get_text(self, element, tag: str, ns: dict, prefix: str = "cap") -> Optional[str]:
        """Get text from an XML element with namespace prefix."""
        child = element.find(f".//{prefix}:{tag}", ns)
        if child is None:
            child = element.find(f"./{prefix}:{tag}", ns)
        if child is None:
            child = element.find(f".//{tag}")
        if child is None:
            child = element.find(f"./{tag}")
        return child.text if child is not None else None

    def _parse_polygon(self, polygon_text: str) -> dict:
        """Parse CAP polygon format: 'lat,lon lat,lon lat,lon'."""
        try:
            points = []
            for pair in polygon_text.strip().split():
                lat, lon = pair.split(",")
                points.append([float(lon), float(lat)])  # GeoJSON: [lon, lat]

            return {
                "type": "Polygon",
                "coordinates": [points]
            }
        except (ValueError, IndexError):
            return None


def is_us_location(lat: float, lon: float) -> bool:
    """Check if coordinates are within CONUS (Continental US) bounds."""
    return 24.0 <= lat <= 49.0 and -125.0 <= lon <= -66.0


def is_europe_location(lat: float, lon: float) -> bool:
    """Check if coordinates are within Europe bounds."""
    return 35.0 <= lat <= 71.0 and -10.0 <= lon <= 40.0


def detect_country(lat: float, lon: float) -> Optional[str]:
    """Detect European country from coordinates (simplified)."""
    # This is a simplified mapping - real implementation would use reverse geocoding
    country_boxes = {
        "germany": (47.0, 55.0, 5.0, 15.0),
        "france": (42.0, 51.0, -5.0, 8.0),
        "spain": (36.0, 44.0, -10.0, 4.0),
        "italy": (36.0, 47.0, 6.0, 19.0),
    }

    for country, (lat_min, lat_max, lon_min, lon_max) in country_boxes.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return country

    return "germany"  # Default fallback


def fetch_alerts(lat: float, lon: float, datetime_utc: str, sources: Optional[list[str]] = None) -> list[dict]:
    """
    Fetch alerts from multiple sources based on location and time.

    Args:
        lat: Latitude
        lon: Longitude
        datetime_utc: ISO 8601 datetime string (e.g., "2026-06-06T14:00:00")
        sources: Optional list of source names to query. If None, auto-detect based on location.

    Returns:
        List of alerts sorted by severity (extreme first).
    """
    all_alerts = []

    # Parse datetime to determine if historical or current
    try:
        dt = datetime.fromisoformat(datetime_utc.replace("Z", ""))
        try:
            from datetime import UTC
            now = datetime.now(UTC).replace(tzinfo=None)
        except ImportError:
            now = datetime.utcnow()
        is_historical = (now - dt).days > 1
    except ValueError:
        is_historical = False

    # Determine which sources to query
    if sources is None:
        sources = ["gdacs", "ibtracs", "ptwc"]  # Always query global sources

        if is_us_location(lat, lon):
            sources.extend(["nws", "spc"])

        if is_europe_location(lat, lon):
            sources.extend(["estofex", "meteoalarm"])

    # Fetch from each source
    for source in sources:
        try:
            result = None
            if source == "gdacs":
                client = GDACSClient()
                if is_historical:
                    # Fetch 7 days around the target date
                    from_date = (dt - timedelta(days=3)).strftime("%Y-%m-%d")
                    to_date = (dt + timedelta(days=3)).strftime("%Y-%m-%d")
                    result = client.fetch_historical_alerts(from_date, to_date)
                else:
                    result = client.fetch_current_alerts()

            elif source == "nws":
                client = NWSClient()
                result = client.fetch_current_alerts()

            elif source == "estofex":
                client = ESTOFEXClient()
                result = client.fetch_current_alerts()

            elif source == "ibtracs":
                client = IBTrACSClient()
                if is_historical:
                    from_date = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
                    to_date = (dt + timedelta(days=7)).strftime("%Y-%m-%d")
                    result = client.fetch_historical_alerts(from_date, to_date)
                else:
                    result = client.fetch_current_alerts()

            elif source == "ptwc":
                client = PTWCClient()
                result = client.fetch_current_alerts()

            elif source == "spc":
                client = SPCClient()
                date_str = dt.strftime("%Y-%m-%d") if is_historical else "today"
                result = client.fetch_tornado_reports(date_str)

            elif source == "meteoalarm":
                client = MeteoAlarmClient()
                country = detect_country(lat, lon)
                result = client.fetch_current_alerts(country)

            # Add source field to each alert
            if result:
                for alert in result["alerts"]:
                    alert["source"] = result["source"]
                all_alerts.extend(result["alerts"])

        except Exception as e:
            logger.warning(f"Failed to fetch from {source}: {e}")
            continue

    # Sort by severity (extreme first)
    severity_order = {"extreme": 0, "severe": 1, "moderate": 2, "minor": 3}
    all_alerts.sort(key=lambda a: severity_order.get(a["severity"], 4))

    return all_alerts
