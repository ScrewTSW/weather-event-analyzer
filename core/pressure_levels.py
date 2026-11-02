from core.models.pressure_levels.base import PressureLevelSnapshot, PressureLevelHourly
from core.api_request import CachedRequest

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
    )


class PressureLevelClient:
    def __init__(self, cache=None):
        self.req = CachedRequest(cache=cache)

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
        cache_key = f"json/pressure/snapshot/{lat}_{lon}_{date}_{synoptic}.json"
        url = self._build_openmeteo_url(lat, lon)
        raw = self.req.fetch_json(url, cache_key=cache_key, ttl=3600)
        return self._parse_response(raw, synoptic_hour=synoptic)

    def fetch_hourly(self, lat: float, lon: float, date: str) -> PressureLevelHourly:
        cache_key = f"json/pressure/hourly/{lat}_{lon}_{date}.json"
        url = self._build_openmeteo_url(lat, lon)
        raw = self.req.fetch_json(url, cache_key=cache_key, ttl=3600)
        result = self._parse_response(raw, synoptic_hour=None)
        if result.timestamps:
            filtered_idx = [i for i, ts in enumerate(result.timestamps) if ts.startswith(date)]
            if filtered_idx:
                result.timestamps = [result.timestamps[i] for i in filtered_idx]
                for var in VARIABLES:
                    key = "dewpoint" if var == "dew_point" else var
                    setattr(result, key, [getattr(result, key)[i] for i in filtered_idx])
        return result
