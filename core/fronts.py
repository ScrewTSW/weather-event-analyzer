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


def _wind_dir_from_uv(u_val, v_val):
    return (np.degrees(np.arctan2(-u_val, -v_val)) + 360) % 360


def _find_ridge_with_wind_shift(field, u, v, lats, lons,
                                 threshold_pct=80, min_points=4,
                                 min_absolute=None, min_wind_shift=30):
    """Find ridge of a 2D field, verified by cross-boundary wind direction shift.

    Only keeps points where wind direction changes >min_wind_shift degrees
    across the ridge, which distinguishes real fronts from terrain gradients.
    """
    nonzero = np.abs(field[field != 0])
    if len(nonzero) == 0:
        return []
    pct_threshold = np.percentile(nonzero, threshold_pct)

    if min_absolute is not None and pct_threshold < min_absolute:
        return []

    threshold = pct_threshold
    ny, nx = field.shape

    ridge_lats = []
    ridge_lons = []
    for i in range(ny):
        row = field[i, :]
        max_val = np.nanmax(row)
        if max_val < threshold:
            continue
        j = int(np.nanargmax(row))

        # Check wind shift across the ridge (compare 1-2 grid cells on each side)
        left = max(0, j - 2)
        right = min(nx - 1, j + 2)
        if left == j or right == j:
            continue
        dir_left = _wind_dir_from_uv(u[i, left], v[i, left])
        dir_right = _wind_dir_from_uv(u[i, right], v[i, right])
        shift = abs(dir_left - dir_right)
        if shift > 180:
            shift = 360 - shift
        if shift < min_wind_shift:
            continue

        ridge_lats.append(float(lats[i]))
        ridge_lons.append(float(lons[j]))

    if len(ridge_lats) < min_points:
        return []

    dlon = np.abs(lons[1] - lons[0]) if len(lons) > 1 else 5.0
    max_gap = dlon * 3
    segments = []
    seg_lats = [ridge_lats[0]]
    seg_lons = [ridge_lons[0]]
    for k in range(1, len(ridge_lats)):
        if abs(ridge_lons[k] - ridge_lons[k - 1]) > max_gap:
            if len(seg_lats) >= min_points:
                segments.append((seg_lats, seg_lons))
            seg_lats = []
            seg_lons = []
        seg_lats.append(ridge_lats[k])
        seg_lons.append(ridge_lons[k])
    if len(seg_lats) >= min_points:
        segments.append((seg_lats, seg_lons))

    return segments


MIN_TFP = 8e-12

def detect_cold_fronts(temp, u, v, lats, lons):
    tfp = compute_tfp(temp, lats, lons)
    segments = _find_ridge_with_wind_shift(
        tfp, u, v, lats, lons,
        threshold_pct=80, min_absolute=MIN_TFP, min_wind_shift=30)
    return [{"type": "cold", "lats": s[0], "lons": s[1],
             "is_merger": False, "label": ""} for s in segments]


def detect_warm_fronts(temp, u, v, lats, lons):
    tfp = compute_tfp(temp, lats, lons)
    neg_tfp = -tfp
    segments = _find_ridge_with_wind_shift(
        neg_tfp, u, v, lats, lons,
        threshold_pct=80, min_absolute=MIN_TFP, min_wind_shift=20)
    return [{"type": "warm", "lats": s[0], "lons": s[1],
             "is_merger": False, "label": ""} for s in segments]


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

    dry_field = np.where(temp_grad_mag < dew_grad_mag * 0.5, dew_grad_mag, 0.0)
    dew_u = dd_dx / (dew_grad_mag + 1e-12)
    dew_v = dd_dy / (dew_grad_mag + 1e-12)
    segments = _find_ridge_with_wind_shift(
        dry_field, dew_u, dew_v, lats, lons,
        threshold_pct=80, min_wind_shift=0, min_points=5)
    return [{"type": "dry", "lats": s[0], "lons": s[1],
             "is_merger": False, "label": ""} for s in segments]


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


def fetch_openmeteo_gridded(lat, lon, date, hour=12, bbox_deg=10, step=2.0):
    import time as _time

    south = max(lat - bbox_deg, -90)
    north = min(lat + bbox_deg, 90)
    west = lon - bbox_deg
    east = lon + bbox_deg

    lats_1d = np.arange(south, north + step / 2, step)
    lons_1d = np.arange(west, east + step / 2, step)
    ny, nx = len(lats_1d), len(lons_1d)

    all_lats = []
    all_lons = []
    for la in lats_1d:
        for lo in lons_1d:
            all_lats.append(la)
            all_lons.append(lo)

    lat_str = ",".join(f"{la:.2f}" for la in all_lats)
    lon_str = ",".join(f"{lo:.2f}" for lo in all_lons)

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat_str}&longitude={lon_str}"
        f"&hourly=temperature_925hPa,dew_point_925hPa,wind_speed_925hPa,wind_direction_925hPa"
        f"&past_days=7&wind_speed_unit=ms"
    )

    session = requests.Session()
    session.headers["User-Agent"] = "WeatherEventAnalyzer/1.0"
    _time.sleep(0.3)
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    target_time = f"{date}T{hour:02d}:00"

    if isinstance(data, list):
        points = data
    else:
        points = [data]

    temp_grid = np.full((ny, nx), np.nan)
    dew_grid = np.full((ny, nx), np.nan)
    u_grid = np.full((ny, nx), np.nan)
    v_grid = np.full((ny, nx), np.nan)

    for idx, pt in enumerate(points):
        row = idx // nx
        col = idx % nx
        if row >= ny or col >= nx:
            break

        hourly = pt.get("hourly", {})
        times = hourly.get("time", [])
        t_idx = None
        for ti, ts in enumerate(times):
            if target_time in ts:
                t_idx = ti
                break
        if t_idx is None:
            continue

        t_vals = hourly.get("temperature_925hPa", [])
        td_vals = hourly.get("dew_point_925hPa", [])
        ws_vals = hourly.get("wind_speed_925hPa", [])
        wd_vals = hourly.get("wind_direction_925hPa", [])

        if t_idx < len(t_vals) and t_vals[t_idx] is not None:
            temp_grid[row, col] = t_vals[t_idx]
        if t_idx < len(td_vals) and td_vals[t_idx] is not None:
            dew_grid[row, col] = td_vals[t_idx]
        if (t_idx < len(ws_vals) and t_idx < len(wd_vals)
                and ws_vals[t_idx] is not None and wd_vals[t_idx] is not None):
            wd_rad = np.radians(wd_vals[t_idx])
            u_grid[row, col] = -ws_vals[t_idx] * np.sin(wd_rad)
            v_grid[row, col] = -ws_vals[t_idx] * np.cos(wd_rad)

    return temp_grid, dew_grid, u_grid, v_grid, lats_1d, lons_1d


class FrontalAnalyzer:
    def analyze(self, temp, dewpoint, u, v, lats, lons):
        all_fronts = []
        all_fronts.extend(detect_cold_fronts(temp, u, v, lats, lons))
        all_fronts.extend(detect_warm_fronts(temp, u, v, lats, lons))
        all_fronts.extend(detect_dry_lines(dewpoint, temp, lats, lons))
        all_fronts = detect_mergers(all_fronts)
        return all_fronts

    def fetch_and_analyze(self, lat, lon, date, run_hour="12", bbox_deg=20):
        try:
            hour = int(run_hour)
        except (ValueError, TypeError):
            hour = 12
        grid_bbox = max(bbox_deg, 10)
        step = max(1.0, grid_bbox / 10)
        try:
            temp, dew, u, v, lats, lons = fetch_openmeteo_gridded(
                lat, lon, date, hour=hour, bbox_deg=grid_bbox, step=step)
            if np.all(np.isnan(temp)):
                return []
            return self.analyze(temp, dew, u, v, lats, lons)
        except Exception:
            return []
