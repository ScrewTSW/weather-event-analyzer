import io
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from core.cache import Cache
from core.projection import TILE_SIZE, latlon_to_tiles, get_crop_bounds

SLIDER_BASE = "https://slider.cira.colostate.edu"
USER_AGENT = "WeatherEventAnalyzer/1.0"
RATE_LIMIT_DELAY = 0.2


def build_tile_url(satellite: str, product: str, timestamp: int, zoom: int, row: int, col: int) -> str:
    ts = str(timestamp)
    yyyy, mm, dd = ts[:4], ts[4:6], ts[6:8]
    return (
        f"{SLIDER_BASE}/data/imagery/{yyyy}/{mm}/{dd}/{satellite}---full_disk/"
        f"{product}/{timestamp}/{zoom:02d}/{row:03d}_{col:03d}.png"
    )


def build_timestamps_url(satellite: str, product: str, date: Optional[str] = None) -> str:
    if date:
        return f"{SLIDER_BASE}/data/json/{satellite}/full_disk/{product}/{date}_by_hour.json"
    return f"{SLIDER_BASE}/data/json/{satellite}/full_disk/{product}/latest_times.json"


def filter_timestamps(timestamps: list[int], start_utc: str, end_utc: str, date: str = None) -> list[int]:
    start_hhmm = int(start_utc.replace(":", ""))
    end_hhmm = int(end_utc.replace(":", ""))
    filtered = []
    for ts in timestamps:
        ts_str = str(ts)
        if date and not ts_str.startswith(date):
            continue
        hhmm = int(ts_str[8:12])
        if start_hhmm <= hhmm <= end_hhmm:
            filtered.append(ts)
    return filtered


class SatelliteClient:
    def __init__(self, cache: Cache):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def fetch_timestamps(self, satellite: str, product: str, date: str) -> list[int]:
        # Try date-specific by_hour endpoint first, fall back to latest_times
        if date:
            url = build_timestamps_url(satellite, product, date=date)
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 404:
                url = build_timestamps_url(satellite, product, date=None)
                resp = self.session.get(url, timeout=15)
        else:
            url = build_timestamps_url(satellite, product, date=None)
            resp = self.session.get(url, timeout=15)

        resp.raise_for_status()
        data = resp.json()

        if "timestamps_int" in data:
            if isinstance(data["timestamps_int"], dict):
                all_ts = []
                for hour_ts in data["timestamps_int"].values():
                    all_ts.extend(hour_ts)
                return sorted(all_ts)
            return sorted(data["timestamps_int"])
        return []

    def download_tile(self, satellite: str, product: str, timestamp: int, zoom: int, row: int, col: int) -> Optional[Path]:
        ts_str = str(timestamp)
        date_str = ts_str[:8]
        cache_key = f"tiles/{satellite}/{date_str}/{timestamp}/{zoom:02d}/{row:03d}_{col:03d}.png"

        cached = self.cache.get(cache_key)
        if cached:
            return cached

        url = build_tile_url(satellite, product, timestamp, zoom, row, col)
        try:
            time.sleep(RATE_LIMIT_DELAY)
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return self.cache.store(cache_key, resp.content, source_url=url)
        except requests.RequestException:
            return None

    def download_and_stitch(
        self,
        satellite: str,
        product: str,
        timestamp: int,
        zoom: int,
        tiles: list[tuple[int, int]],
    ) -> Optional[Image.Image]:
        if not tiles:
            return None

        min_tx = min(tx for tx, ty in tiles)
        min_ty = min(ty for tx, ty in tiles)
        max_tx = max(tx for tx, ty in tiles)
        max_ty = max(ty for tx, ty in tiles)

        width = (max_tx - min_tx + 1) * TILE_SIZE
        height = (max_ty - min_ty + 1) * TILE_SIZE
        composite = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        for tx, ty in tiles:
            path = self.download_tile(satellite, product, timestamp, zoom, tx, ty)
            if path:
                tile_img = Image.open(path).convert("RGBA")
                x = (tx - min_tx) * TILE_SIZE
                y = (ty - min_ty) * TILE_SIZE
                composite.paste(tile_img, (x, y))

        return composite

    def fetch_event(
        self,
        satellite: dict,
        product: str,
        date: str,
        start_utc: str,
        end_utc: str,
        lat: float,
        lon: float,
        zoom: int,
        bbox_deg: float = 5.0,
        on_progress: Optional[callable] = None,
    ) -> list[dict]:
        sat_id = satellite["slider_id"]
        sat_lon = satellite["lon"]

        if on_progress:
            on_progress(f"Fetching timestamps for {sat_id}/{product} on {date}...")

        timestamps = self.fetch_timestamps(sat_id, product, date)
        timestamps = filter_timestamps(timestamps, start_utc, end_utc, date=date)

        if not timestamps:
            # No data for requested date — try without date filter to find nearest available
            all_ts = self.fetch_timestamps(sat_id, product, None)
            if all_ts:
                nearest_date = str(all_ts[-1])[:8]
                if on_progress:
                    on_progress(f"No data for {date}, using nearest available: {nearest_date}")
                timestamps = filter_timestamps(all_ts, start_utc, end_utc, date=nearest_date)
                if not timestamps:
                    # Still nothing in the time range — take latest few frames
                    timestamps = all_ts[-6:]
                    if on_progress:
                        on_progress(f"Time range {start_utc}-{end_utc} not in {nearest_date}, showing latest {len(timestamps)} frames")

        if not timestamps:
            if on_progress:
                on_progress("No timestamps available from this satellite")
            return []

        tiles_needed = latlon_to_tiles(lat, lon, bbox_deg, zoom, sat_lon)
        if on_progress:
            on_progress(f"Found {len(timestamps)} frames, {len(tiles_needed)} tiles each")

        # Compute the geostationary extent for the tile set
        if tiles_needed:
            min_tx = min(tx for tx, ty in tiles_needed)
            min_ty = min(ty for tx, ty in tiles_needed)
            max_tx = max(tx for tx, ty in tiles_needed)
            max_ty = max(ty for tx, ty in tiles_needed)
        else:
            return []

        results = []
        for i, ts in enumerate(timestamps):
            if on_progress:
                on_progress(f"Downloading frame {i+1}/{len(timestamps)}: {ts}")

            composite = self.download_and_stitch(sat_id, product, ts, zoom, tiles_needed)
            if composite:
                results.append({
                    "timestamp": ts,
                    "image": composite,
                    "satellite": sat_id,
                    "product": product,
                    "zoom": zoom,
                    "tile_bounds": (min_tx, min_ty, max_tx, max_ty),
                })

        return results
