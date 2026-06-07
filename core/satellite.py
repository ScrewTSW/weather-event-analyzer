import io
import time
import math
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from core.cache import Cache

USER_AGENT = "WeatherEventAnalyzer/1.0"
GIBS_WMTS = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best"
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
EUMETSAT_WMS = "https://view.eumetsat.int/geoserver/wms"

SOURCES = {
    "goes_east": {"type": "gibs_wmts", "layer": "GOES-East_ABI_Band13_Clean_Infrared", "matrix": "500m", "max_zoom": 6, "cadence_min": 10},
    "goes_west": {"type": "gibs_wmts", "layer": "GOES-West_ABI_Band13_Clean_Infrared", "matrix": "500m", "max_zoom": 6, "cadence_min": 10},
    "goes_east_geocolor": {"type": "gibs_wms", "layer": "GOES-East_ABI_GeoColor", "cadence_min": 10},
    "goes_west_geocolor": {"type": "gibs_wms", "layer": "GOES-West_ABI_GeoColor", "cadence_min": 10},
    "himawari": {"type": "gibs_wmts", "layer": "Himawari_AHI_Band13_Clean_Infrared", "matrix": "500m", "max_zoom": 6, "cadence_min": 10},
    "himawari_truecolor": {"type": "gibs_wms", "layer": "Himawari_AHI_Band3_Red_Visible_1km", "cadence_min": 10},
    "meteosat_ir": {"type": "eumetsat_wms", "layer": "msg_fes:ir108", "cadence_min": 15},
    "meteosat_vis": {"type": "eumetsat_wms", "layer": "msg_fes:vis006", "cadence_min": 15},
    "modis_terra": {"type": "gibs_wmts", "layer": "MODIS_Terra_CorrectedReflectance_TrueColor", "matrix": "250m", "max_zoom": 8, "cadence_min": 1440},
    "viirs": {"type": "gibs_wmts", "layer": "VIIRS_SNPP_CorrectedReflectance_TrueColor", "matrix": "250m", "max_zoom": 8, "cadence_min": 1440},
}


def select_source(lat: float, lon: float, product: str = "auto") -> tuple[str, dict]:
    if product != "auto" and product in SOURCES:
        return product, SOURCES[product]
    if -125 <= lon <= -30:
        return "goes_east_geocolor", SOURCES["goes_east_geocolor"]
    if -180 <= lon <= -125 or 150 <= lon <= 180:
        return "goes_west_geocolor", SOURCES["goes_west_geocolor"]
    if 90 <= lon < 150:
        return "himawari", SOURCES["himawari"]
    return "meteosat_ir", SOURCES["meteosat_ir"]


def _gibs_wmts_tile_url(layer: str, matrix: str, time_str: str, z: int, row: int, col: int) -> str:
    return f"{GIBS_WMTS}/{layer}/default/{time_str}/{matrix}/{z}/{row}/{col}.png"


def _gibs_wms_url(layer: str, time_str: str, bbox: tuple, width: int = 512, height: int = 512) -> str:
    west, south, east, north = bbox
    return (
        f"{GIBS_WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        f"&LAYERS={layer}&FORMAT=image/jpeg&TRANSPARENT=true"
        f"&WIDTH={width}&HEIGHT={height}&SRS=EPSG:4326"
        f"&BBOX={west},{south},{east},{north}&TIME={time_str}"
    )


def _eumetsat_wms_url(layer: str, bbox: tuple, width: int = 512, height: int = 512) -> str:
    west, south, east, north = bbox
    return (
        f"{EUMETSAT_WMS}?service=WMS&version=1.1.1&request=GetMap"
        f"&layers={layer}&format=image/png&transparent=true"
        f"&width={width}&height={height}&srs=EPSG:4326"
        f"&bbox={west},{south},{east},{north}"
    )


def _generate_timestamps(date: str, start_utc: str, end_utc: str, cadence_min: int) -> list[str]:
    from datetime import datetime, timedelta
    start_h, start_m = int(start_utc[:2]), int(start_utc[2:4]) if len(start_utc) > 2 else 0
    end_h, end_m = int(end_utc[:2]), int(end_utc[2:4]) if len(end_utc) > 2 else 0
    base = datetime.strptime(date, "%Y%m%d")
    start_dt = base.replace(hour=start_h, minute=start_m)
    end_dt = base.replace(hour=end_h, minute=end_m)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    start_dt = start_dt.replace(minute=(start_dt.minute // cadence_min) * cadence_min)
    timestamps = []
    t = start_dt
    while t <= end_dt:
        timestamps.append(t.strftime("%Y-%m-%dT%H:%M:%SZ"))
        t += timedelta(minutes=cadence_min)
    return timestamps


class SatelliteClient:
    def __init__(self, cache: Cache):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _fetch_image(self, url: str, cache_key: str) -> Optional[Image.Image]:
        cached = self.cache.get(cache_key)
        if cached:
            return Image.open(cached).convert("RGBA")
        try:
            time.sleep(0.15)
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 500:
                self.cache.store(cache_key, resp.content, source_url=url)
                return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        except Exception:
            pass
        return None

    def fetch_wms_image(self, source: dict, time_str: str, lat: float, lon: float,
                        bbox_deg: float = 20.0, size: int = 800) -> Optional[Image.Image]:
        half = bbox_deg / 2
        bbox = (lon - half, lat - half, lon + half, lat + half)
        src_type = source["type"]
        layer = source["layer"]

        if src_type == "gibs_wms":
            url = _gibs_wms_url(layer, time_str, bbox, size, size)
        elif src_type == "eumetsat_wms":
            url = _eumetsat_wms_url(layer, bbox, size, size)
        else:
            return None

        cache_key = f"wms/{layer}/{time_str.replace(':', '')}/{lat:.2f}_{lon:.2f}_{bbox_deg}.jpg"
        return self._fetch_image(url, cache_key)

    def fetch_wmts_tiles(self, source: dict, time_str: str, lat: float, lon: float,
                         bbox_deg: float = 20.0, zoom: int = 4) -> Optional[Image.Image]:
        layer = source["layer"]
        matrix = source.get("matrix", "250m")
        max_z = source.get("max_zoom", 6)
        z = min(zoom, max_z)

        half = bbox_deg / 2
        n_rows = 2 ** z
        n_cols = 2 ** (z + 1)
        tile_h = 180.0 / n_rows
        tile_w = 360.0 / n_cols

        min_row = max(0, int((90 - (lat + half)) / tile_h))
        max_row = min(n_rows - 1, int((90 - (lat - half)) / tile_h))
        min_col = max(0, int((lon - half + 180) / tile_w))
        max_col = min(n_cols - 1, int((lon + half + 180) / tile_w))

        width = (max_col - min_col + 1) * 512
        height = (max_row - min_row + 1) * 512
        composite = Image.new("RGBA", (width, height), (20, 20, 40, 255))

        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                url = _gibs_wmts_tile_url(layer, matrix, time_str, z, row, col)
                cache_key = f"wmts/{layer}/{time_str.replace(':', '')}/{z}/{row}_{col}.png"
                tile = self._fetch_image(url, cache_key)
                if tile:
                    x = (col - min_col) * 512
                    y = (row - min_row) * 512
                    composite.paste(tile, (x, y))

        return composite

    def fetch_event(self, lat: float, lon: float, date: str, start_utc: str, end_utc: str,
                    product: str = "auto", zoom: int = 4, bbox_deg: float = 20.0,
                    on_progress=None) -> list[dict]:
        source_name, source = select_source(lat, lon, product)
        cadence = source.get("cadence_min", 15)

        if on_progress:
            on_progress(f"Source: {source_name} ({source['layer']})")

        timestamps = _generate_timestamps(date, start_utc, end_utc, cadence)
        if on_progress:
            on_progress(f"Generated {len(timestamps)} timestamps ({cadence}-min intervals)")

        results = []
        for i, ts in enumerate(timestamps):
            if on_progress:
                on_progress(f"Fetching {i+1}/{len(timestamps)}: {ts}")

            if source["type"] in ("gibs_wms", "eumetsat_wms"):
                img = self.fetch_wms_image(source, ts, lat, lon, bbox_deg, size=800)
            else:
                img = self.fetch_wmts_tiles(source, ts, lat, lon, bbox_deg, zoom)

            if img:
                results.append({
                    "timestamp": ts,
                    "image": img,
                    "source": source_name,
                    "layer": source["layer"],
                })

        return results
