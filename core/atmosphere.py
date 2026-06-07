import time
from typing import Optional

import requests

USER_AGENT = "WeatherEventAnalyzer/1.0"

FORECAST_VARS = (
    "temperature_2m,relative_humidity_2m,dewpoint_2m,"
    "cape,convective_inhibition,lifted_index,"
    "surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)

ARCHIVE_VARS = (
    "temperature_2m,relative_humidity_2m,dewpoint_2m,"
    "surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)

GFS_RUN_HOURS = [0, 6, 12, 18]


def build_openmeteo_forecast_url(lat: float, lon: float, past_days: int = 7) -> str:
    return (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly={FORECAST_VARS}"
        f"&past_days={past_days}&wind_speed_unit=ms"
    )


def build_openmeteo_archive_url(lat: float, lon: float, date: str) -> str:
    return (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={date}&end_date={date}"
        f"&hourly={ARCHIVE_VARS}&wind_speed_unit=ms"
    )


def build_gfs_nomads_url(
    date: str, run_hour: str, forecast_hour: str,
    south: float, north: float, west: float, east: float,
) -> str:
    return (
        f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
        f"dir=%2Fgfs.{date}%2F{run_hour}%2Fatmos"
        f"&file=gfs.t{run_hour}z.pgrb2.0p25.f{forecast_hour}"
        f"&lev_2_m_above_ground=on&lev_surface=on"
        f"&lev_entire_atmosphere_%28considered_as_a_single_layer%29=on"
        f"&var_TMP=on&var_DPT=on&var_RH=on&var_CAPE=on&var_CIN=on&var_REFC=on"
        f"&subregion=&toplat={north}&leftlon={west}&rightlon={east}&bottomlat={south}"
    )


def select_gfs_run(event_time_utc: str) -> tuple[str, str]:
    parts = event_time_utc.replace(":", "")
    hour = int(parts[:2])
    minute = int(parts[2:4]) if len(parts) > 2 else 0

    best_run = 0
    for rh in GFS_RUN_HOURS:
        if rh <= hour:
            best_run = rh

    forecast_h = hour - best_run
    return f"{best_run:02d}", f"{forecast_h:03d}"


def parse_openmeteo_response(data: dict, source: str) -> dict:
    hourly = data.get("hourly", {})
    timestamps = hourly.get("time", [])

    var_map = {
        "temperature": "temperature_2m",
        "dewpoint": "dewpoint_2m",
        "humidity": "relative_humidity_2m",
        "cape": "cape",
        "cin": "convective_inhibition",
        "lifted_index": "lifted_index",
        "pressure": "surface_pressure",
        "wind_speed": "wind_speed_10m",
        "wind_direction": "wind_direction_10m",
        "wind_gusts": "wind_gusts_10m",
    }

    variables = {}
    for name, key in var_map.items():
        if key in hourly:
            variables[name] = {
                "data": hourly[key],
                "unit": _get_unit(name),
            }

    return {
        "source": source,
        "gridded": False,
        "timestamps": timestamps,
        "variables": variables,
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
    }


def _get_unit(var_name: str) -> str:
    units = {
        "temperature": "C", "dewpoint": "C", "humidity": "%",
        "cape": "J/kg", "cin": "J/kg", "lifted_index": "",
        "pressure": "hPa", "wind_speed": "m/s",
        "wind_direction": "deg", "wind_gusts": "m/s",
    }
    return units.get(var_name, "")


class OpenMeteoClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_forecast(self, lat: float, lon: float, past_days: int = 7) -> dict:
        url = build_openmeteo_forecast_url(lat, lon, past_days)
        time.sleep(0.3)
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return parse_openmeteo_response(resp.json(), source="open-meteo-forecast")

    def fetch_archive(self, lat: float, lon: float, date: str) -> dict:
        url = build_openmeteo_archive_url(lat, lon, date)
        time.sleep(0.3)
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return parse_openmeteo_response(resp.json(), source="open-meteo-archive")

    def fetch(self, lat: float, lon: float, date: str) -> dict:
        # Try forecast first (has CAPE, 7-day lookback)
        try:
            result = self.fetch_forecast(lat, lon, past_days=7)
            # Filter to requested date
            filtered_ts = []
            filtered_vars = {k: {"data": [], "unit": v["unit"]} for k, v in result["variables"].items()}
            for i, ts in enumerate(result["timestamps"]):
                if ts.startswith(date):
                    filtered_ts.append(ts)
                    for k in filtered_vars:
                        if k in result["variables"]:
                            filtered_vars[k]["data"].append(result["variables"][k]["data"][i])
            if filtered_ts:
                result["timestamps"] = filtered_ts
                result["variables"] = filtered_vars
                return result
        except Exception:
            pass

        # Fall back to archive
        return self.fetch_archive(lat, lon, date)
