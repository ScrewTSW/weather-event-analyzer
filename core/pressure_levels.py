import math
from datetime import date as date_cls, datetime, timedelta, timezone

import numpy as np
import requests

from core.models.pressure_levels.base import PressureLevelSnapshot, PressureLevelHourly
from core.models.pressure_levels.raob import RaobPressureHourly
from core.api_request import CachedRequest

PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                   400, 300, 250, 200, 150, 100, 70, 50, 30]

SYNOPTIC_HOURS = [0, 6, 12, 18]

# The forecast API only reaches this far back (past_days) / ahead (default forecast length)
FORECAST_PAST_DAYS = 7
FORECAST_DAYS = 7

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
RAOB_STATIONS_URL = "https://mesonet.agron.iastate.edu/geojson/network/RAOB.geojson"
RAOB_PROFILE_URL = "https://mesonet.agron.iastate.edu/json/raob.py"
RAOB_MAX_KM = 600          # farther than this a sounding no longer represents the location
RAOB_MAX_STATIONS = 3      # nearest sites tried before giving up
RAOB_HOURS = (0, 6, 12, 18, 24)  # 00/12 UTC routine, 06/18 specials, next-day 00 UTC
KNOT_MS = 0.514444

VARIABLES = ["temperature", "dew_point", "wind_speed", "wind_direction",
             "geopotential_height"]

# Open-Meteo surface variable -> DTO field; Open-Meteo corrects them to the returned elevation
SURFACE_VARIABLES = {
    "surface_pressure": "surface_pressure",
    "temperature_2m": "surface_temperature",
    "dew_point_2m": "surface_dewpoint",
    "wind_speed_10m": "surface_wind_speed",
    "wind_direction_10m": "surface_wind_direction",
}


def snap_to_synoptic(hour: int) -> int:
    def circular_distance(h1, h2):
        diff = abs(h1 - h2)
        return min(diff, 24 - diff)

    distances = [(circular_distance(hour, h), h) for h in SYNOPTIC_HOURS]
    distances.sort(key=lambda x: (x[0], -x[1]))  # Sort by distance, then by hour descending
    return distances[0][1]


def extract_snapshot(hourly: PressureLevelHourly, idx: int) -> PressureLevelSnapshot:
    return PressureLevelSnapshot(
        source=hourly.source,
        synoptic_time=hourly.timestamps[idx] if idx < len(hourly.timestamps) else "",
        pressure_levels=hourly.pressure_levels,
        temperature=hourly.temperature[idx],
        dewpoint=hourly.dewpoint[idx],
        wind_speed=hourly.wind_speed[idx],
        wind_direction=hourly.wind_direction[idx],
        geopotential_height=hourly.geopotential_height[idx],
        latitude=hourly.latitude,
        longitude=hourly.longitude,
        elevation=hourly.elevation,
        station=hourly.station,
        **{f: (getattr(hourly, f)[idx] if idx < len(getattr(hourly, f)) else None)
           for f in SURFACE_VARIABLES.values()},
    )


def in_forecast_window(date: str, today: date_cls | None = None) -> bool:
    """True when the forecast API (past_days=7) still covers `date`."""
    today = today or datetime.now(timezone.utc).date()
    d = date_cls.fromisoformat(date)
    return today - timedelta(days=FORECAST_PAST_DAYS) <= d <= today + timedelta(days=FORECAST_DAYS)


def has_profile_data(hourly: PressureLevelHourly) -> bool:
    return any(v is not None for row in hourly.temperature for v in row)


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def _wind_ms(row: dict) -> tuple[float | None, float | None]:
    if row.get("sknt") is None or row.get("drct") is None:
        return None, None
    return round(row["sknt"] * KNOT_MS, 2), row["drct"]


def interpolate_raob(levels: list[dict], targets=PRESSURE_LEVELS) -> dict:
    """Interpolate one IEM RAOB profile onto standard pressure levels (linear in ln p).

    Levels outside the observed range of a variable stay None, so nothing is
    extrapolated below ground or above burst height. Wind is interpolated as u/v.
    The surface row (highest pressure with temperature and dewpoint) is kept as-is.
    """
    def column(key, rows):
        pts = sorted((math.log(r["pres"]), r[key]) for r in rows
                     if r.get("pres") and r.get(key) is not None)
        if len(pts) < 2:
            return [None] * len(targets)
        x, y = np.array([p[0] for p in pts]), np.array([p[1] for p in pts], dtype=float)
        out = []
        for t in targets:
            lt = math.log(t)
            out.append(round(float(np.interp(lt, x, y)), 2) if x[0] <= lt <= x[-1] else None)
        return out

    winds = [dict(pres=r["pres"],
                  u=-r["sknt"] * KNOT_MS * math.sin(math.radians(r["drct"])),
                  v=-r["sknt"] * KNOT_MS * math.cos(math.radians(r["drct"])))
             for r in levels
             if r.get("pres") and r.get("sknt") is not None and r.get("drct") is not None]
    u, v = column("u", winds), column("v", winds)
    speed = [None if a is None else round(math.hypot(a, b), 2) for a, b in zip(u, v)]
    direction = [None if a is None else round(math.degrees(math.atan2(-a, -b)) % 360, 1)
                 for a, b in zip(u, v)]
    sfc = max((r for r in levels if r.get("pres") and r.get("tmpc") is not None
               and r.get("dwpc") is not None), key=lambda r: r["pres"], default={})
    sfc_speed, sfc_dir = _wind_ms(sfc)
    return dict(temperature=column("tmpc", levels), dewpoint=column("dwpc", levels),
                geopotential_height=column("hght", levels),
                wind_speed=speed, wind_direction=direction,
                surface_pressure=sfc.get("pres"), surface_temperature=sfc.get("tmpc"),
                surface_dewpoint=sfc.get("dwpc"),
                surface_wind_speed=sfc_speed, surface_wind_direction=sfc_dir)


class PressureLevelClient:
    def __init__(self, cache=None):
        self.req = CachedRequest(cache=cache)

    @staticmethod
    def _hourly_params() -> str:
        return ",".join([f"{var}_{level}hPa" for var in VARIABLES for level in PRESSURE_LEVELS]
                        + list(SURFACE_VARIABLES))

    def _build_openmeteo_url(self, lat: float, lon: float) -> str:
        hourly = self._hourly_params()
        return (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&hourly={hourly}"
            f"&past_days=7&wind_speed_unit=ms"
        )

    def _parse_response(self, data: dict, synoptic_hour: int = None):
        hourly = data.get("hourly", {})
        timestamps = hourly.get("time", [])

        if synoptic_hour is not None:
            target = f"T{synoptic_hour:02d}:00"
            idx = None
            for i, ts in enumerate(timestamps):
                if target in ts:
                    idx = i
                    break
            if idx is None:
                idx = 0

            var_data = {}
            for var in VARIABLES:
                key = "dewpoint" if var == "dew_point" else var
                var_data[key] = [
                    hourly.get(f"{var}_{level}hPa", [None] * (idx + 1))[idx]
                    for level in PRESSURE_LEVELS
                ]

            for var, key in SURFACE_VARIABLES.items():
                var_data[key] = hourly.get(var, [None] * (idx + 1))[idx]

            return PressureLevelSnapshot(
                source="open-meteo-pressure",
                synoptic_time=timestamps[idx],
                pressure_levels=list(PRESSURE_LEVELS),
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                elevation=data.get("elevation"),
                **var_data
            )
        else:
            n_times = len(timestamps)
            var_data = {}
            for var in VARIABLES:
                key = "dewpoint" if var == "dew_point" else var
                var_data[key] = [
                    [hourly.get(f"{var}_{level}hPa", [None] * n_times)[t]
                     for level in PRESSURE_LEVELS]
                    for t in range(n_times)
                ]
            for var, key in SURFACE_VARIABLES.items():
                var_data[key] = list(hourly.get(var, [None] * n_times))

            return PressureLevelHourly(
                source="open-meteo-pressure",
                pressure_levels=list(PRESSURE_LEVELS),
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                elevation=data.get("elevation"),
                timestamps=timestamps,
                **var_data
            )

    def fetch_snapshot(self, lat: float, lon: float, date: str, hour: int) -> PressureLevelSnapshot:
        synoptic = snap_to_synoptic(hour)
        cache_key = f"json/pressure/snapshot/{lat}_{lon}_{date}_{synoptic}_sfc.json"
        url = self._build_openmeteo_url(lat, lon)
        raw = self.req.fetch_json(url, cache_key=cache_key, ttl=3600)
        return self._parse_response(raw, synoptic_hour=synoptic)

    def fetch_hourly(self, lat: float, lon: float, date: str) -> PressureLevelHourly:
        """Hourly profiles for `date`, from whichever archive still covers it.

        forecast API (±7 days) -> historical forecast API (model runs, ~2021+)
        -> observed radiosondes from the nearest IEM RAOB site (decades, North America).
        """
        if in_forecast_window(date):
            return self._fetch_forecast_hourly(lat, lon, date)
        try:
            result = self.fetch_historical_hourly(lat, lon, date)
            if has_profile_data(result):
                return result
        except requests.RequestException:  # 400 before the archive starts (2016), timeouts
            result = PressureLevelHourly(source="open-meteo-historical",
                                         pressure_levels=list(PRESSURE_LEVELS))
        return self.fetch_raob_hourly(lat, lon, date) or result

    def _fetch_forecast_hourly(self, lat, lon, date):
        cache_key = f"json/pressure/hourly/{lat}_{lon}_{date}_sfc.json"
        url = self._build_openmeteo_url(lat, lon)
        raw = self.req.fetch_json(url, cache_key=cache_key, ttl=3600)
        return self._only_date(self._parse_response(raw, synoptic_hour=None), date)

    def fetch_historical_hourly(self, lat: float, lon: float, date: str) -> PressureLevelHourly:
        url = (f"{HISTORICAL_FORECAST_URL}?latitude={lat}&longitude={lon}"
               f"&hourly={self._hourly_params()}&start_date={date}&end_date={date}&wind_speed_unit=ms")
        # Past model runs never change — cache for the full retention period
        raw = self.req.fetch_json(url, cache_key=f"json/pressure/historical/{lat}_{lon}_{date}_sfc.json",
                                  ttl=7 * 86400)
        result = self._only_date(self._parse_response(raw, synoptic_hour=None), date)
        result.source = "open-meteo-historical"
        return result

    def nearest_raob_stations(self, lat: float, lon: float, date: str) -> list[tuple[float, dict]]:
        """(distance_km, feature) for RAOB sites operating on `date`, nearest first."""
        raw = self.req.fetch_json(RAOB_STATIONS_URL, cache_key="json/pressure/raob/stations.json",
                                  ttl=7 * 86400)
        found = []
        for f in raw.get("features", []):
            props = f.get("properties", {})
            begin, end = props.get("archive_begin"), props.get("archive_end")
            if (begin and begin > date) or (end and end < date):
                continue
            slon, slat = f["geometry"]["coordinates"][:2]
            km = _haversine_km(lat, lon, slat, slon)
            if km <= RAOB_MAX_KM:
                found.append((km, f))
        return sorted(found, key=lambda kf: kf[0])[:RAOB_MAX_STATIONS]

    def fetch_raob_hourly(self, lat: float, lon: float, date: str) -> RaobPressureHourly | None:
        """Observed soundings for `date` from the nearest RAOB site that launched that day."""
        day = datetime.fromisoformat(date)
        for km, feature in self.nearest_raob_stations(lat, lon, date):
            station = feature["id"]
            timestamps, rows = [], []
            for h in RAOB_HOURS:
                t = day + timedelta(hours=h)
                ts = t.strftime("%Y%m%d%H%M")
                raw = self.req.fetch_json(f"{RAOB_PROFILE_URL}?ts={ts}&station={station}",
                                          cache_key=f"json/pressure/raob/{station}_{ts}.json",
                                          ttl=7 * 86400)
                for prof in raw.get("profiles", []):
                    levels = interpolate_raob(prof.get("profile", []))
                    if any(v is not None for v in levels["temperature"]):
                        timestamps.append(t.strftime("%Y-%m-%dT%H:%M"))
                        rows.append(levels)
                        break
            if not rows:
                continue
            slon, slat = feature["geometry"]["coordinates"][:2]
            props = feature.get("properties", {})
            name = (props.get("sname") or "").split(" -- ")[0]
            return RaobPressureHourly(
                source="iem-raob",
                station=f"{station.lstrip('_')} {name}".strip(),
                distance_km=round(km),
                pressure_levels=list(PRESSURE_LEVELS),
                timestamps=timestamps,
                latitude=slat, longitude=slon,
                elevation=props.get("elevation"),
                **{k: [r[k] for r in rows] for k in rows[0]},
            )
        return None

    @staticmethod
    def _only_date(result: PressureLevelHourly, date: str) -> PressureLevelHourly:
        keep = [i for i, ts in enumerate(result.timestamps) if ts.startswith(date)]
        result.timestamps = [result.timestamps[i] for i in keep]
        keys = [("dewpoint" if v == "dew_point" else v) for v in VARIABLES]
        for key in keys + list(SURFACE_VARIABLES.values()):
            values = getattr(result, key)
            setattr(result, key, [values[i] if i < len(values) else None for i in keep])
        return result
