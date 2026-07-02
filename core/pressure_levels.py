import time

import requests

USER_AGENT = "WeatherEventAnalyzer/1.0"

PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
                   400, 300, 250, 200, 150, 100, 70, 50, 30]

SYNOPTIC_HOURS = [0, 6, 12, 18]

VARIABLES = ["temperature", "dew_point", "wind_speed", "wind_direction",
             "geopotential_height"]


def snap_to_synoptic(hour: int) -> int:
    def circular_distance(h1, h2):
        diff = abs(h1 - h2)
        return min(diff, 24 - diff)

    distances = [(circular_distance(hour, h), h) for h in SYNOPTIC_HOURS]
    distances.sort(key=lambda x: (x[0], -x[1]))  # Sort by distance, then by hour descending
    return distances[0][1]


class PressureLevelClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _build_openmeteo_url(self, lat: float, lon: float) -> str:
        params = []
        for var in VARIABLES:
            for level in PRESSURE_LEVELS:
                params.append(f"{var}_{level}hPa")
        hourly = ",".join(params)
        return (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&hourly={hourly}"
            f"&past_days=7&wind_speed_unit=ms"
        )

    def _parse_response(self, data: dict, synoptic_hour: int = None) -> dict:
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

            result = {
                "source": "open-meteo-pressure",
                "synoptic_time": timestamps[idx],
                "pressure_levels": list(PRESSURE_LEVELS),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "timestamps": None,
            }
            for var in VARIABLES:
                key = "dewpoint" if var == "dew_point" else var
                result[key] = [
                    hourly.get(f"{var}_{level}hPa", [None] * (idx + 1))[idx]
                    for level in PRESSURE_LEVELS
                ]
            return result
        else:
            n_times = len(timestamps)
            result = {
                "source": "open-meteo-pressure",
                "synoptic_time": None,
                "pressure_levels": list(PRESSURE_LEVELS),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "timestamps": timestamps,
            }
            for var in VARIABLES:
                key = "dewpoint" if var == "dew_point" else var
                result[key] = [
                    [hourly.get(f"{var}_{level}hPa", [None] * n_times)[t]
                     for level in PRESSURE_LEVELS]
                    for t in range(n_times)
                ]
            return result

    def fetch_snapshot(self, lat: float, lon: float, date: str, hour: int) -> dict:
        synoptic = snap_to_synoptic(hour)
        url = self._build_openmeteo_url(lat, lon)
        time.sleep(0.3)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        hourly = raw.get("hourly", {})
        filtered_times = hourly.get("time", [])
        date_times = [t for t in filtered_times if t.startswith(date)]
        if not date_times:
            date_times = filtered_times
        return self._parse_response(raw, synoptic_hour=synoptic)

    def fetch_hourly(self, lat: float, lon: float, date: str) -> dict:
        url = self._build_openmeteo_url(lat, lon)
        time.sleep(0.3)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        result = self._parse_response(raw, synoptic_hour=None)
        if result["timestamps"]:
            filtered_idx = [i for i, ts in enumerate(result["timestamps"]) if ts.startswith(date)]
            if filtered_idx:
                result["timestamps"] = [result["timestamps"][i] for i in filtered_idx]
                for var in VARIABLES:
                    key = "dewpoint" if var == "dew_point" else var
                    result[key] = [result[key][i] for i in filtered_idx]
        return result
