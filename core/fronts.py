import math

import numpy as np
import requests
from scipy.ndimage import gaussian_filter


def compute_tfp(temp, lats, lons):
    dlat = np.abs(lats[1] - lats[0]) if len(lats) > 1 else 1.0
    dlon = np.abs(lons[1] - lons[0]) if len(lons) > 1 else 1.0
    dy = dlat * 111000.0
    dx = dlon * 111000.0 * np.cos(np.radians(np.mean(lats)))

    smoothed = gaussian_filter(temp, sigma=2)

    dt_dy, dt_dx = np.gradient(smoothed, dy, dx)
    grad_mag = np.sqrt(dt_dx**2 + dt_dy**2)
    grad_mag = np.maximum(grad_mag, 1e-12)

    dgm_dy, dgm_dx = np.gradient(grad_mag, dy, dx)

    unit_dx = dt_dx / grad_mag
    unit_dy = dt_dy / grad_mag

    tfp = -(dgm_dx * unit_dx + dgm_dy * unit_dy)
    return tfp


def _trace_contour_points(field, threshold, lats, lons, min_points=3):
    mask = field > threshold
    if not np.any(mask):
        return []
    rows, cols = np.where(mask)
    if len(rows) < min_points:
        return []
    front_lats = [lats[r] for r in rows]
    front_lons = [lons[c] for c in cols]
    if len(front_lats) > 1:
        order = np.argsort(front_lats)
        front_lats = [front_lats[i] for i in order]
        front_lons = [front_lons[i] for i in order]
    return front_lats, front_lons


def detect_cold_fronts(temp, u, v, lats, lons):
    tfp = compute_tfp(temp, lats, lons)
    threshold = np.percentile(np.abs(tfp[tfp != 0]), 90) if np.any(tfp != 0) else 1e-10
    result = _trace_contour_points(tfp, threshold, lats, lons)
    if not result:
        return []
    return [{"type": "cold", "lats": result[0], "lons": result[1],
             "is_merger": False, "label": ""}]


def detect_warm_fronts(temp, u, v, lats, lons):
    tfp = compute_tfp(temp, lats, lons)
    neg_tfp = -tfp
    threshold = np.percentile(np.abs(neg_tfp[neg_tfp > 0]), 90) if np.any(neg_tfp > 0) else 1e-10
    result = _trace_contour_points(neg_tfp, threshold, lats, lons)
    if not result:
        return []
    return [{"type": "warm", "lats": result[0], "lons": result[1],
             "is_merger": False, "label": ""}]


def detect_dry_lines(dewpoint, temp, lats, lons):
    dlat = np.abs(lats[1] - lats[0]) if len(lats) > 1 else 1.0
    dlon = np.abs(lons[1] - lons[0]) if len(lons) > 1 else 1.0
    dy = dlat * 111000.0
    dx = dlon * 111000.0 * np.cos(np.radians(np.mean(lats)))

    smoothed = gaussian_filter(dewpoint, sigma=2)
    dd_dy, dd_dx = np.gradient(smoothed, dy, dx)
    dew_grad_mag = np.sqrt(dd_dx**2 + dd_dy**2)

    temp_smoothed = gaussian_filter(temp, sigma=2)
    dt_dy, dt_dx = np.gradient(temp_smoothed, dy, dx)
    temp_grad_mag = np.sqrt(dt_dx**2 + dt_dy**2)

    dew_threshold = 10.0 / 100000.0
    is_dry_line = (dew_grad_mag > dew_threshold) & (temp_grad_mag < dew_grad_mag * 0.5)

    rows, cols = np.where(is_dry_line)
    if len(rows) < 3:
        return []
    front_lats = [lats[r] for r in rows]
    front_lons = [lons[c] for c in cols]
    order = np.argsort(front_lats)
    front_lats = [front_lats[i] for i in order]
    front_lons = [front_lons[i] for i in order]
    return [{"type": "dry", "lats": front_lats, "lons": front_lons,
             "is_merger": False, "label": ""}]


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return r * 2 * math.asin(math.sqrt(a))


def detect_mergers(fronts, threshold_km=50):
    for i, f1 in enumerate(fronts):
        for j, f2 in enumerate(fronts):
            if i >= j:
                continue
            if f1["type"] == f2["type"]:
                continue
            for lat1, lon1 in zip(f1["lats"], f1["lons"]):
                for lat2, lon2 in zip(f2["lats"], f2["lons"]):
                    if _haversine_km(lat1, lon1, lat2, lon2) < threshold_km:
                        f1["is_merger"] = True
                        f2["is_merger"] = True
                        label = "TRIPLE POINT" if len({f1["type"], f2["type"]}) > 1 and len(fronts) > 2 else "MERGER"
                        f1["label"] = label
                        f2["label"] = label
                        break
    return fronts


def fetch_gfs_gridded(lat, lon, date, run_hour="12", forecast_hour="000", bbox_deg=10):
    import time as _time
    import xarray as xr
    import io as _io

    south = max(lat - bbox_deg, -90)
    north = min(lat + bbox_deg, 90)
    west = lon - bbox_deg
    east = lon + bbox_deg

    date_compact = date.replace("-", "")
    url = (
        f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
        f"dir=%2Fgfs.{date_compact}%2F{run_hour}%2Fatmos"
        f"&file=gfs.t{run_hour}z.pgrb2.0p25.f{forecast_hour}"
        f"&lev_925_mb=on&lev_850_mb=on"
        f"&var_TMP=on&var_DPT=on&var_UGRD=on&var_VGRD=on"
        f"&subregion=&toplat={north}&leftlon={west}&rightlon={east}&bottomlat={south}"
    )

    session = requests.Session()
    session.headers["User-Agent"] = "WeatherEventAnalyzer/1.0"
    _time.sleep(0.3)
    resp = session.get(url, timeout=60)
    resp.raise_for_status()

    ds = xr.open_dataset(_io.BytesIO(resp.content), engine="cfgrib",
                         backend_kwargs={"indexpath": ""})
    lats_grid = ds.latitude.values
    lons_grid = ds.longitude.values

    if "isobaricInhPa" in ds.dims:
        lvl_idx = list(ds.isobaricInhPa.values).index(925)
        temp = ds["t"].values[lvl_idx] - 273.15
        dewpoint = ds["dpt"].values[lvl_idx] - 273.15 if "dpt" in ds else temp - 5
        u_wind = ds["u"].values[lvl_idx]
        v_wind = ds["v"].values[lvl_idx]
    else:
        temp = ds["t"].values - 273.15
        dewpoint = ds["dpt"].values - 273.15 if "dpt" in ds else temp - 5
        u_wind = ds["u"].values
        v_wind = ds["v"].values

    return temp, dewpoint, u_wind, v_wind, lats_grid, lons_grid


class FrontalAnalyzer:
    def analyze(self, temp, dewpoint, u, v, lats, lons):
        all_fronts = []
        all_fronts.extend(detect_cold_fronts(temp, u, v, lats, lons))
        all_fronts.extend(detect_warm_fronts(temp, u, v, lats, lons))
        all_fronts.extend(detect_dry_lines(dewpoint, temp, lats, lons))
        all_fronts = detect_mergers(all_fronts)
        return all_fronts

    def fetch_and_analyze(self, lat, lon, date, run_hour="12"):
        try:
            temp, dew, u, v, lats, lons = fetch_gfs_gridded(
                lat, lon, date, run_hour=run_hour)
            return self.analyze(temp, dew, u, v, lats, lons)
        except Exception:
            return []
