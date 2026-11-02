import io
import math
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

from PIL import Image

from core.cache import Cache
from core.api_request import CachedRequest
from core.models.satellite.base import SatelliteFrame
from core.models.satellite.source import SatelliteSource, SOURCES, select_source

GIBS_WMTS = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best"
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
EUMETSAT_WMS = "https://view.eumetsat.int/geoserver/wms"


def _gibs_wmts_tile_url(layer: str, matrix: str, time_str: str, z: int, row: int, col: int) -> str:
    return f"{GIBS_WMTS}/{layer}/default/{time_str}/{matrix}/{z}/{row}/{col}.png"


def _gibs_wms_url(layer: str, time_str: str, bbox: tuple, width: int = 512, height: int = 512) -> str:
    return (
        f"{GIBS_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        f"&LAYERS={layer}&FORMAT=image/png&TRANSPARENT=true"
        f"&CRS=EPSG:4326&BBOX={bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
        f"&WIDTH={width}&HEIGHT={height}&SRS=EPSG:4326"
        f"&TIME={time_str}"
    )


def _eumetsat_wms_url(layer: str, bbox: tuple, width: int = 512, height: int = 512, time_str: str = None) -> str:
    url = (
        f"{EUMETSAT_WMS}?service=WMS&version=1.1.1&request=GetMap"
        f"&layers={layer}&format=image/png&transparent=true"
        f"&bbox={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
        f"&width={width}&height={height}&srs=EPSG:4326"
    )
    if time_str:
        url += f"&time={time_str}"
    return url


class SatelliteClient:
    OUTPUT_SIZE = 2048

    def __init__(self, cache: Cache):
        self.cache = cache
        self.req = CachedRequest(cache=cache)

    def _fetch_image(self, url: str, cache_key: str) -> Optional[Image.Image]:
        data = self.req.fetch_binary(url, cache_key=cache_key)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGBA")
        return None

    def _normalize(self, img: Image.Image) -> Image.Image:
        if img.width == self.OUTPUT_SIZE and img.height == self.OUTPUT_SIZE:
            return img
        return img.resize((self.OUTPUT_SIZE, self.OUTPUT_SIZE), Image.LANCZOS)

    @staticmethod
    def _has_content(img: Image.Image, threshold: float = 8.0) -> bool:
        return np.array(img).mean() > threshold

    def _build_url(self, source: SatelliteSource, ts: str, lat: float, lon: float,
                   bbox_deg: float, size: int) -> tuple[str, str]:
        half = bbox_deg / 2
        north = min(lat + half, 65.0)
        south = max(lat - half, -65.0)
        bbox = (lon - half, south, lon + half, north)

        if source.source_type == "gibs_wms":
            url = _gibs_wms_url(source.layer, ts, bbox, size, size)
            cache_key = f"wms/{source.layer}/{ts.replace(':', '')}/" \
                        f"{lat:.2f}_{lon:.2f}_{bbox_deg}_{size}.jpg"
        elif source.source_type == "eumetsat_wms":
            url = _eumetsat_wms_url(source.layer, bbox, size, size, time_str=ts)
            cache_key = f"wms/{source.layer}/{ts.replace(':', '')}/" \
                        f"{lat:.2f}_{lon:.2f}_{bbox_deg}_{size}.jpg"
        else:
            return None, None
        return url, cache_key

    def _fetch_wms(self, source: SatelliteSource, ts: str, lat: float, lon: float,
                   bbox_deg: float, size: int) -> Optional[Image.Image]:
        url, cache_key = self._build_url(source, ts, lat, lon, bbox_deg, size)
        if not url:
            return None
        return self._fetch_image(url, cache_key)

    def _fetch_wmts(self, source: SatelliteSource, ts: str, lat: float, lon: float,
                    bbox_deg: float, zoom: int) -> Optional[Image.Image]:
        half = bbox_deg / 2
        lat_min = max(lat - half, -85)
        lat_max = min(lat + half, 85)
        lon_min = lon - half
        lon_max = lon + half

        n = 2 ** zoom
        min_row = max(0, int((90 - lat_max) / (180 / n)))
        max_row = min(n - 1, int((90 - lat_min) / (180 / n)))
        min_col = max(0, int((lon_min + 180) / (360 / (2 * n))))
        max_col = min(2 * n - 1, int((lon_max + 180) / (360 / (2 * n))))

        width = (max_col - min_col + 1) * 512
        height = (max_row - min_row + 1) * 512
        composite = Image.new("RGBA", (width, height), (20, 20, 40, 255))

        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                url = _gibs_wmts_tile_url(source.layer, source.matrix, ts, zoom, row, col)
                cache_key = f"wmts/{source.layer}/{ts.replace(':', '')}/{zoom}/{row}_{col}.png"
                tile = self._fetch_image(url, cache_key)
                if tile:
                    x = (col - min_col) * 512
                    y = (row - min_row) * 512
                    composite.paste(tile, (x, y))

        return composite

    def _probe_frame(self, source: SatelliteSource, ts: str, lat: float, lon: float,
                     bbox_deg: float) -> Optional[Image.Image]:
        """Fetch a small uncached image just to check if data exists."""
        half = bbox_deg / 2
        bbox = (lon - half, max(lat - half, -65.0), lon + half, min(lat + half, 65.0))
        if source.source_type == "gibs_wms":
            url = _gibs_wms_url(source.layer, ts, bbox, 128, 128)
        elif source.source_type == "eumetsat_wms":
            url = _eumetsat_wms_url(source.layer, bbox, 128, 128, time_str=ts)
        else:
            url = _gibs_wmts_tile_url(source.layer, source.matrix, ts, 2, 0, 0)
        try:
            resp = self.req.fetch_raw(url)
            if resp.status_code == 200 and len(resp.content) > 500:
                return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        except Exception:
            pass
        return None

    def _fetch_frame(self, source: SatelliteSource, ts: str, lat: float, lon: float,
                     bbox_deg: float, zoom: int, size: int) -> Optional[Image.Image]:
        if source.source_type in ("gibs_wms", "eumetsat_wms"):
            return self._fetch_wms(source, ts, lat, lon, bbox_deg, size)
        else:
            return self._fetch_wmts(source, ts, lat, lon, bbox_deg, zoom)

    def probe_latest(self, source: SatelliteSource, lat: float, lon: float,
                     bbox_deg: float = 20.0, on_progress=None) -> str | None:
        """Walk backward from estimated latest, verify actual pixel content."""
        estimated = source.estimate_latest()
        cadence = timedelta(minutes=source.cadence_min)

        for offset in range(18):  # Up to 3 hours back
            probe_time = estimated - (cadence * offset)
            ts = probe_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            img = self._probe_frame(source, ts, lat, lon, bbox_deg)
            if img and self._has_content(img):
                if on_progress:
                    on_progress(f"Latest valid frame: {ts}")
                return ts
            if on_progress:
                on_progress(f"Probing {ts}... empty")
        return None

    def fetch_event(self, lat: float, lon: float, date: str = None,
                    start_utc: str = None, end_utc: str = None,
                    product: str = "auto", zoom: int = 4, bbox_deg: float = 20.0,
                    on_progress=None) -> list[SatelliteFrame]:
        source, timestamps = self.plan_event(lat, lon, date, start_utc, end_utc,
                                             product, bbox_deg, on_progress)
        return list(self.iter_frames(source, timestamps, lat, lon, zoom, bbox_deg, on_progress))

    def plan_event(self, lat: float, lon: float, date: str = None,
                   start_utc: str = None, end_utc: str = None,
                   product: str = "auto", bbox_deg: float = 20.0,
                   on_progress=None) -> tuple[SatelliteSource, list[str]]:
        """Pick the source and the frame timestamps (probing for the latest in live mode)."""
        source = select_source(lat, lon, product)

        if on_progress:
            on_progress(f"Source: {source.name} ({source.layer})")

        if date and start_utc and end_utc:
            start_h = int(start_utc.replace(":", "")[:2])
            start_m = int(start_utc.replace(":", "")[2:4]) if len(start_utc.replace(":", "")) > 2 else 0
            end_h = int(end_utc.replace(":", "")[:2])
            end_m = int(end_utc.replace(":", "")[2:4]) if len(end_utc.replace(":", "")) > 2 else 0
            base = datetime.strptime(date, "%Y%m%d")
            start_dt = base.replace(hour=start_h, minute=start_m)
            end_dt = base.replace(hour=end_h, minute=end_m)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            timestamps = source.generate_timestamps(start_dt, end_dt)
        else:
            latest = self.probe_latest(source, lat, lon, bbox_deg, on_progress)
            if not latest:
                if on_progress:
                    on_progress("No valid frames found")
                return source, []
            end_dt = datetime.fromisoformat(latest.replace("Z", ""))
            start_dt = end_dt - timedelta(hours=2)
            timestamps = source.generate_timestamps(start_dt, end_dt)

        if on_progress:
            on_progress(f"Generated {len(timestamps)} timestamps ({source.cadence_min}-min intervals)")
        return source, timestamps

    def iter_frames(self, source: SatelliteSource, timestamps: list[str],
                    lat: float, lon: float, zoom: int = 4, bbox_deg: float = 20.0,
                    on_progress=None):
        """Yield each SatelliteFrame as soon as it is fetched; empty/failed frames are skipped."""
        if not timestamps:
            return
        produced = 0
        failed = 0
        stats_before = self.req.stats.cache_hits
        for i, ts in enumerate(timestamps):
            hits_before = self.req.stats.cache_hits
            try:
                img = self._fetch_frame(source, ts, lat, lon, bbox_deg, zoom, self.OUTPUT_SIZE)
            except requests.RequestException as e:
                if on_progress:
                    on_progress(f"FAILED {i+1}/{len(timestamps)}: {ts} ({type(e).__name__})")
                failed += 1
                continue
            was_cached = self.req.stats.cache_hits > hits_before

            if img and not self._has_content(img):
                if on_progress:
                    on_progress(f"SKIP {i+1}/{len(timestamps)}: {ts} (empty)")
                continue

            tag = "CACHE" if was_cached else "PULLED"
            if on_progress:
                on_progress(f"{tag} {i+1}/{len(timestamps)}: {ts}")

            if img:
                half = bbox_deg / 2
                frame_bbox = (
                    max(lat - half, -65.0),
                    min(lat + half, 65.0),
                    lon - half,
                    lon + half,
                )
                produced += 1
                yield SatelliteFrame(
                    source=source.name,
                    timestamp=ts,
                    image=self._normalize(img),
                    layer=source.layer,
                    bbox=frame_bbox,
                )

        cache_hits = self.req.stats.cache_hits - stats_before
        api_calls = len(timestamps) - cache_hits - failed
        if on_progress:
            on_progress(f"Got {produced} frames ({cache_hits} CACHE, {api_calls} PULLED, {failed} FAILED)")
