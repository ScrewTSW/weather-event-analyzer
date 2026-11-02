import math
from typing import Optional

TILE_SIZE = 678

# WGS84 constants
R_EQ = 6378.137  # Earth equatorial radius (km)
R_POL = 6356.7523  # Earth polar radius (km)
SAT_HEIGHT = 35786.0  # Satellite altitude above equator (km)
E2 = 1 - (R_POL / R_EQ) ** 2  # Eccentricity squared


def _geostationary_angles(lat: float, lon: float, sat_lon: float) -> Optional[tuple[float, float]]:
    lat_r = math.radians(lat)
    lon_diff = math.radians(lon - sat_lon)

    # Geocentric latitude
    c_lat = math.atan((1 - E2) * math.tan(lat_r))
    cos_c_lat = math.cos(c_lat)
    sin_c_lat = math.sin(c_lat)

    # Earth radius at this geocentric latitude
    rl = R_POL / math.sqrt(1 - E2 * cos_c_lat ** 2)

    r1 = SAT_HEIGHT + R_EQ - rl * cos_c_lat * math.cos(lon_diff)
    r2 = -rl * cos_c_lat * math.sin(lon_diff)
    r3 = rl * sin_c_lat

    rn = math.sqrt(r1 ** 2 + r2 ** 2 + r3 ** 2)

    # Visibility check: point must be on the near side of Earth
    sat_dist = SAT_HEIGHT + R_EQ
    if r1 * sat_dist < r1 ** 2 + r2 ** 2 + r3 ** 2 * (R_EQ / R_POL) ** 2:
        return None

    # Scan angles
    x = math.atan2(-r2, r1)
    y = math.asin(r3 / rn)

    return x, y


def _max_scan_angle() -> float:
    return math.asin(R_EQ / (SAT_HEIGHT + R_EQ))


def latlon_to_pixel(lat: float, lon: float, sat_lon: float, zoom: int) -> Optional[tuple[int, int]]:
    angles = _geostationary_angles(lat, lon, sat_lon)
    if angles is None:
        return None

    x_angle, y_angle = angles
    max_angle = _max_scan_angle()

    image_size = TILE_SIZE * (2 ** zoom)
    # Scale: Earth disk spans approximately 2*max_angle
    # fill_fraction controls what fraction of the image half-width the Earth radius uses
    # This value needs calibration against real SLIDER tiles (see Task 10)
    fill_fraction = 0.45
    scale = (image_size * fill_fraction) / max_angle

    center = image_size / 2.0
    col = center + x_angle * scale
    row = center - y_angle * scale  # y increases downward in image

    if not (0 <= col < image_size and 0 <= row < image_size):
        return None

    return int(col), int(row)


def pixel_to_tile(col: int, row: int) -> tuple[int, int]:
    return col // TILE_SIZE, row // TILE_SIZE


def latlon_to_tiles(lat: float, lon: float, bbox_deg: float, zoom: int, sat_lon: float) -> list[tuple[int, int]]:
    half = bbox_deg / 2
    max_tiles = 2 ** zoom

    corners = [
        (lat + half, lon - half),
        (lat + half, lon + half),
        (lat - half, lon - half),
        (lat - half, lon + half),
    ]

    valid_pixels = []
    for clat, clon in corners:
        result = latlon_to_pixel(clat, clon, sat_lon, zoom)
        if result is not None:
            valid_pixels.append(result)

    if not valid_pixels:
        center = latlon_to_pixel(lat, lon, sat_lon, zoom)
        if center is None:
            return []
        col, row = center
        spread = max(1, int(bbox_deg / 5))
        tc, tr = pixel_to_tile(col, row)
        tiles = []
        for tx in range(max(0, tc - spread), min(max_tiles, tc + spread + 1)):
            for ty in range(max(0, tr - spread), min(max_tiles, tr + spread + 1)):
                tiles.append((tx, ty))
        return tiles

    min_tx = min_ty = max_tiles
    max_tx = max_ty = 0
    for col, row in valid_pixels:
        tx, ty = pixel_to_tile(col, row)
        min_tx = min(min_tx, tx)
        min_ty = min(min_ty, ty)
        max_tx = max(max_tx, tx)
        max_ty = max(max_ty, ty)

    tiles = []
    for tx in range(max(0, min_tx), min(max_tiles, max_tx + 1)):
        for ty in range(max(0, min_ty), min(max_tiles, max_ty + 1)):
            tiles.append((tx, ty))
    return tiles


def get_crop_bounds(lat: float, lon: float, bbox_deg: float, zoom: int, sat_lon: float) -> tuple[int, int, int, int]:
    half = bbox_deg / 2
    corners = [
        (lat + half, lon - half),
        (lat + half, lon + half),
        (lat - half, lon - half),
        (lat - half, lon + half),
    ]

    cols, rows = [], []
    for clat, clon in corners:
        result = latlon_to_pixel(clat, clon, sat_lon, zoom)
        if result:
            cols.append(result[0])
            rows.append(result[1])

    if not cols:
        raise ValueError(f"Location ({lat}, {lon}) with bbox {bbox_deg} is not visible from satellite at {sat_lon}")

    tiles = latlon_to_tiles(lat, lon, bbox_deg, zoom, sat_lon)
    if not tiles:
        raise ValueError("No tiles cover this area")

    tile_origin_x = min(tx for tx, ty in tiles) * TILE_SIZE
    tile_origin_y = min(ty for tx, ty in tiles) * TILE_SIZE

    left = min(cols) - tile_origin_x
    right = max(cols) - tile_origin_x
    upper = min(rows) - tile_origin_y
    lower = max(rows) - tile_origin_y

    return max(0, left), max(0, upper), right, lower


def goes_fixed_grid(lat, lon, sat_lon: float, h: float = 35786023.0,
                    req: float = 6378137.0, rpol: float = 6356752.31414):
    """GOES-R ABI fixed-grid scan angles (x, y) in radians for arrays of lat/lon (deg).

    Sweep-angle axis x, as in the ABI L1b/L2 files (GOES-R PUG vol. 3, §4.2.8.1).
    Points on the far side of the Earth come back as NaN.
    """
    import numpy as np
    lat_r, dlon = np.radians(lat), np.radians(np.asarray(lon) - sat_lon)
    phi_c = np.arctan((rpol ** 2 / req ** 2) * np.tan(lat_r))
    e2 = (req ** 2 - rpol ** 2) / req ** 2
    rc = rpol / np.sqrt(1 - e2 * np.cos(phi_c) ** 2)
    H = h + req
    sx = H - rc * np.cos(phi_c) * np.cos(dlon)
    sy = -rc * np.cos(phi_c) * np.sin(dlon)
    sz = rc * np.sin(phi_c)
    visible = H * (H - sx) >= sy ** 2 + (req ** 2 / rpol ** 2) * sz ** 2
    x = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
    y = np.arctan(sz / sx)
    return np.where(visible, x, np.nan), np.where(visible, y, np.nan)


def solar_elevation(lat, lon, when):
    """Solar elevation (deg) for arrays of lat/lon at UTC datetime `when`
    (NOAA general solar position equations, ~0.1° accuracy)."""
    import numpy as np
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (when.timetuple().tm_yday - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    hour_angle = np.radians((hour * 60 + eqtime + 4 * np.asarray(lon)) / 4 - 180)
    lat_r = np.radians(lat)
    sin_el = np.sin(lat_r) * math.sin(decl) + np.cos(lat_r) * math.cos(decl) * np.cos(hour_angle)
    return np.degrees(np.arcsin(np.clip(sin_el, -1, 1)))
