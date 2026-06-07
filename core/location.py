import math
from typing import Optional

import requests

SATELLITES = [
    {"name": "Himawari-9", "slider_id": "himawari", "lon": 140.7, "cadence_min": 10, "ir_band": "band_13", "ts_seconds": "00"},
    {"name": "GK2A", "slider_id": "gk2a", "lon": 128.2, "cadence_min": 10, "ir_band": "band_13", "ts_seconds": "00"},
    {"name": "GOES-19", "slider_id": "goes-19", "lon": -75.2, "cadence_min": 10, "ir_band": "band_13", "ts_seconds": "21"},
    {"name": "GOES-18", "slider_id": "goes-18", "lon": -137.2, "cadence_min": 10, "ir_band": "band_13", "ts_seconds": "23"},
    {"name": "Meteosat-0deg", "slider_id": "meteosat-0deg", "lon": 0.0, "cadence_min": 15, "ir_band": "band_09", "ts_seconds": "00"},
    {"name": "Meteosat-9", "slider_id": "meteosat-9", "lon": 45.5, "cadence_min": 15, "ir_band": "band_09", "ts_seconds": "00"},
    {"name": "Meteosat-12", "slider_id": "meteosat-12", "lon": 0.0, "cadence_min": 10, "ir_band": "band_13", "ts_seconds": "00"},
]

USER_AGENT = "WeatherEventAnalyzer/1.0 (https://github.com/weather-event-analyzer)"


def parse_location_input(text: str) -> Optional[tuple[float, float]]:
    text = text.strip()
    parts = text.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except ValueError:
            pass
    return None


def geocode(query: str) -> tuple[float, float, str]:
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode: {query!r}")
    r = results[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", query)


def angular_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(a)))


def select_satellite(lat: float, lon: float) -> Optional[dict]:
    candidates = []
    for sat in SATELLITES:
        dist = angular_distance(lat, lon, 0, sat["lon"])
        if dist <= 70:
            candidates.append((dist, sat))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def is_conus(lat: float, lon: float) -> bool:
    return 24 <= lat <= 50 and -125 <= lon <= -66


def resolve_location(text: str) -> tuple[float, float, str, dict]:
    parsed = parse_location_input(text)
    if parsed:
        lat, lon = parsed
        display = f"{lat:.4f}{'N' if lat >= 0 else 'S'}, {abs(lon):.4f}{'E' if lon >= 0 else 'W'}"
    else:
        lat, lon, display = geocode(text)
    sat = select_satellite(lat, lon)
    if sat is None:
        raise ValueError(f"No satellite covers location ({lat}, {lon}) -- all >70 angular distance")
    return lat, lon, display, sat
