"""Visualization module for weather event analysis.

Provides functions for:
- Layer compositing (satellite images + markers + labels)
- Atmospheric data charts
- Timelapse generation (GIF/MP4)
"""

import matplotlib
matplotlib.use("Agg")  # Headless rendering

import io
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from pathlib import Path
import matplotlib.pyplot as plt
import imageio

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

import metpy.calc as mpcalc
from metpy.plots import SkewT, Hodograph
from metpy.units import units


def _get_font(size=12):
    """Try to load a font, fall back to default if not found."""
    font_paths = [
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue

    # Fall back to default font
    return ImageFont.load_default()


def add_timestamp_label(img, timestamp, satellite, location):
    """Add a semi-transparent label box with metadata to the image.

    Args:
        img: PIL Image (RGBA mode)
        timestamp: Timestamp string (e.g., "2026-05-31 09:00 UTC")
        satellite: Satellite name (e.g., "Himawari-9")
        location: Location string (e.g., "45.75N, 126.65E")

    Returns:
        PIL Image with label composited
    """
    # Create a copy to avoid modifying the original
    result = img.copy()
    draw = ImageDraw.Draw(result, "RGBA")

    # Scale font to image size (target: ~3% of image height for large, ~2.5% for small)
    min_dim = min(img.width, img.height)
    font_size_large = max(10, min(24, int(min_dim * 0.035)))
    font_size_small = max(8, min(18, int(min_dim * 0.028)))
    font_large = _get_font(font_size_large)
    font_small = _get_font(font_size_small)

    lines = [
        (satellite, font_large),
        (timestamp, font_small),
        (location, font_small),
    ]

    padding = max(4, int(min_dim * 0.01))
    line_spacing = max(2, int(min_dim * 0.005))
    max_width = 0
    total_height = padding * 2

    for text, font in lines:
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        max_width = max(max_width, width)
        total_height += height + line_spacing

    total_height -= line_spacing  # Remove extra spacing after last line

    # Draw semi-transparent background box
    box_coords = [
        (5, 5),
        (5 + max_width + padding * 2, 5 + total_height)
    ]
    draw.rectangle(box_coords, fill=(0, 0, 0, 180))

    # Draw text lines
    y_offset = 5 + padding
    for text, font in lines:
        draw.text((5 + padding, y_offset), text, fill=(255, 255, 255, 255), font=font)
        bbox = draw.textbbox((0, 0), text, font=font)
        y_offset += (bbox[3] - bbox[1]) + line_spacing

    return result


def add_location_marker(img, center_x, center_y, color=(0, 255, 255, 255), size=None):
    """Add a crosshair marker at the specified location.

    Args:
        img: PIL Image (RGBA mode)
        center_x: X coordinate of center
        center_y: Y coordinate of center
        color: RGBA color tuple (default: cyan)
        size: Size of the crosshair in pixels

    Returns:
        PIL Image with marker composited
    """
    result = img.copy()
    draw = ImageDraw.Draw(result, "RGBA")

    if size is None:
        size = max(8, min(30, min(img.width, img.height) // 20))
    half_size = size // 2

    # Horizontal line
    draw.line(
        [(center_x - half_size, center_y), (center_x + half_size, center_y)],
        fill=color,
        width=2
    )

    # Vertical line
    draw.line(
        [(center_x, center_y - half_size), (center_x, center_y + half_size)],
        fill=color,
        width=2
    )

    # Draw circle around center
    circle_radius = half_size // 2
    draw.ellipse(
        [
            (center_x - circle_radius, center_y - circle_radius),
            (center_x + circle_radius, center_y + circle_radius)
        ],
        outline=color,
        width=2
    )

    return result


GIBS_BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best"
GIBS_TILE_SIZE = 512


def fetch_gibs_tile(session, layer, date, zoom, row, col, cache=None):
    """Download a single NASA GIBS tile."""
    cache_key = f"gibs/{layer}/{date}/{zoom}/{row}_{col}.jpg"
    if cache:
        cached = cache.get(cache_key)
        if cached:
            return Image.open(cached).convert("RGBA")

    url = f"{GIBS_BASE}/{layer}/default/{date}/250m/{zoom}/{row}/{col}.jpg"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 1000:
            if cache:
                cache.store(cache_key, resp.content, source_url=url)
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception:
        pass
    return None


def render_map_view(center_lat, center_lon, bbox_deg=20.0, date=None,
                    output_size=(900, 700), cache=None):
    """Render a flat map view with NASA GIBS satellite imagery + coastlines.

    Uses MODIS/VIIRS pre-projected tiles (EPSG:4326) so no reprojection needed.
    """
    if not HAS_CARTOPY:
        return Image.new("RGBA", output_size, (30, 30, 30, 255))

    import requests
    session = requests.Session()
    session.headers["User-Agent"] = "WeatherEventAnalyzer/1.0"

    if date is None:
        from datetime import datetime
        date = datetime.utcnow().strftime("%Y-%m-%d")

    half = bbox_deg / 2
    map_crs = ccrs.PlateCarree()

    fig = plt.figure(figsize=(output_size[0] / 100, output_size[1] / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1], projection=map_crs)
    ax.set_extent([center_lon - half, center_lon + half,
                   center_lat - half, center_lat + half], crs=map_crs)

    # Base layers as fallback background
    ax.add_feature(cfeature.OCEAN, facecolor='#1a2a3a', zorder=0)
    ax.add_feature(cfeature.LAND, facecolor='#2a3a2a', zorder=0)

    # Fetch GIBS MODIS/VIIRS tiles
    gibs_zoom = 4  # ~2km/pixel, good for 20-degree bbox
    if bbox_deg <= 10:
        gibs_zoom = 5
    if bbox_deg <= 5:
        gibs_zoom = 6

    n_tiles_y = 2 ** gibs_zoom
    n_tiles_x = 2 ** (gibs_zoom + 1)
    tile_height = 180.0 / n_tiles_y
    tile_width = 360.0 / n_tiles_x

    min_row = max(0, int((90 - (center_lat + half)) / tile_height))
    max_row = min(n_tiles_y - 1, int((90 - (center_lat - half)) / tile_height))
    min_col = max(0, int((center_lon - half + 180) / tile_width))
    max_col = min(n_tiles_x - 1, int((center_lon + half + 180) / tile_width))

    for layer in ["MODIS_Terra_CorrectedReflectance_TrueColor", "VIIRS_SNPP_CorrectedReflectance_TrueColor"]:
        tiles_ok = 0
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                tile_img = fetch_gibs_tile(session, layer, date, gibs_zoom, row, col, cache)
                if tile_img:
                    tile_lat_top = 90 - row * tile_height
                    tile_lat_bot = tile_lat_top - tile_height
                    tile_lon_left = -180 + col * tile_width
                    tile_lon_right = tile_lon_left + tile_width
                    ax.imshow(np.array(tile_img), origin='upper', transform=map_crs,
                              extent=[tile_lon_left, tile_lon_right, tile_lat_bot, tile_lat_top],
                              interpolation='bilinear', zorder=1)
                    tiles_ok += 1
        if tiles_ok > 0:
            break  # Got tiles from this layer, don't try fallback

    ax.coastlines(resolution='50m', color='cyan', linewidth=0.8, alpha=0.8, zorder=5)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor='cyan', alpha=0.5, zorder=5)
    ax.add_feature(cfeature.LAKES, facecolor='none', edgecolor='cyan', linewidth=0.3, alpha=0.4, zorder=5)

    ax.plot(center_lon, center_lat, '+', color='yellow', markersize=14,
            markeredgewidth=2, transform=map_crs, zorder=10)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0,
                facecolor='#1a1a2e', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


def add_geo_overlay(img, center_lat, center_lon, bbox_deg=20.0, fronts=None):
    """Overlay coastlines, borders, and target marker on a pre-projected satellite image."""
    if not HAS_CARTOPY:
        return add_location_marker(img, img.width // 2, img.height // 2)

    half = bbox_deg / 2
    west = center_lon - half
    east = center_lon + half
    south = max(center_lat - half, -65.0)
    north = min(center_lat + half, 65.0)
    map_crs = ccrs.PlateCarree()

    w, h = img.size
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1], projection=map_crs)
    ax.set_extent([west, east, south, north], crs=map_crs)
    ax.set_facecolor('#1a1a2e')

    ax.imshow(np.array(img.convert("RGB")), origin='upper', transform=map_crs,
              extent=[west, east, south, north], zorder=1, aspect='auto')

    ax.coastlines(resolution='50m', color='cyan', linewidth=0.8, alpha=0.8, zorder=5)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor='cyan', alpha=0.5, zorder=5)
    ax.add_feature(cfeature.LAKES, facecolor='none', edgecolor='cyan', linewidth=0.5, alpha=0.6, zorder=5)
    ax.add_feature(cfeature.STATES, linewidth=0.3, edgecolor='cyan', alpha=0.3, zorder=5)
    ax.plot(center_lon, center_lat, '+', color='yellow', markersize=12,
            markeredgewidth=2, transform=map_crs, zorder=10)

    if fronts:
        for front in fronts:
            if len(front.lats) < 2:
                continue
            if front.is_merger:
                ax.plot(front.lons, front.lats, color='magenta', linewidth=4,
                        transform=map_crs, zorder=8)
                mid = len(front.lats) // 2
                ax.plot(front.lons[mid], front.lats[mid], '*', color='magenta',
                        markersize=15, transform=map_crs, zorder=9)
                ax.text(front.lons[mid], front.lats[mid], f' {front.label}',
                        color='magenta', fontsize=8, fontweight='bold',
                        transform=map_crs, zorder=9)
            elif front.type == "cold":
                ax.plot(front.lons, front.lats, color='blue', linewidth=2.5,
                        transform=map_crs, zorder=7)
                for k in range(0, len(front.lats), max(1, len(front.lats) // 8)):
                    ax.plot(front.lons[k], front.lats[k], '^', color='blue',
                            markersize=6, transform=map_crs, zorder=8)
            elif front.type == "warm":
                ax.plot(front.lons, front.lats, color='red', linewidth=2.5,
                        transform=map_crs, zorder=7)
                for k in range(0, len(front.lats), max(1, len(front.lats) // 8)):
                    ax.plot(front.lons[k], front.lats[k], 'o', color='red',
                            markersize=5, transform=map_crs, zorder=8)
            elif front.type == "dry":
                ax.plot(front.lons, front.lats, color='orange', linewidth=2,
                        linestyle='--', transform=map_crs, zorder=7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, pad_inches=0,
                facecolor='#1a1a2e', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


def compose_frame(satellite_image, timestamp, satellite_name, location_str,
                  center_x=None, center_y=None, sat_lon=None, center_lat=None,
                  center_lon=None, bbox_deg=20.0, tile_bounds=None, zoom=None):
    """Compose all layers into a single frame.

    This is the central compositing pipeline for timelapse generation.
    Layers are composited in order:
    1. Base satellite image
    2. Location marker (if center_x/center_y provided)
    3. Timestamp label

    Args:
        satellite_image: PIL Image (RGBA mode) - base satellite image
        timestamp: Timestamp string
        satellite_name: Satellite name
        location_str: Location string
        center_x: Optional X coordinate for location marker
        center_y: Optional Y coordinate for location marker

    Returns:
        PIL Image (RGBA) - fully composited frame
    """
    if satellite_image.mode != "RGBA":
        frame = satellite_image.convert("RGBA")
    else:
        frame = satellite_image.copy()
    if center_x is not None and center_y is not None:
        frame = add_location_marker(frame, center_x, center_y)

    # Final layer: timestamp label
    frame = add_timestamp_label(frame, timestamp, satellite_name, location_str)

    return frame


def create_atmosphere_chart(data, chart_type, profile_hourly=None):
    """Create atmospheric data charts.

    Args:
        data: AtmosphereData with structure:
            {
                "timestamps": ["2026-05-31T07:00", ...],
                "variables": {
                    "var_name": VariableTimeSeries(data=[...], unit="..."),
                    ...
                }
            }
        chart_type: One of "cape_cin", "temp_dewpoint", "wind"
        profile_hourly: Optional dict with keys pressure_levels, timestamps,
                       wind_speed, wind_direction, geopotential_height
                       for adding SRH helicity lines to cape_cin chart

    Returns:
        matplotlib Figure
    """
    plt.style.use("dark_background")
    timestamps = data.timestamps
    variables = data.variables

    # Parse timestamps
    try:
        time_objects = [datetime.fromisoformat(ts.replace("Z", "+00:00")) for ts in timestamps]
    except Exception:
        # If parsing fails, use indices
        time_objects = list(range(len(timestamps)))

    fig, ax1 = plt.subplots(figsize=(10, 6))

    if chart_type == "cape_cin":
        if "cape" not in variables or "cin" not in variables:
            # Compute CAPE/CIN from pressure-level profiles if available
            if profile_hourly is not None and profile_hourly.timestamps:
                cape_vals = []
                cin_vals = []
                p_lvl = np.array(profile_hourly.pressure_levels, dtype=float) * units.hPa
                for t_idx in range(len(profile_hourly.timestamps)):
                    try:
                        t_prof = np.array(profile_hourly.temperature[t_idx], dtype=float) * units.degC
                        td_prof = np.array(profile_hourly.dewpoint[t_idx], dtype=float) * units.degC
                        sb_cape, sb_cin = mpcalc.surface_based_cape_cin(p_lvl, t_prof, td_prof)
                        cape_vals.append(float(sb_cape.magnitude))
                        cin_vals.append(float(sb_cin.magnitude))
                    except Exception:
                        cape_vals.append(np.nan)
                        cin_vals.append(np.nan)
                cape_data = np.array(cape_vals)
                cin_data = np.array(cin_vals)
                time_objects = []
                for ts in profile_hourly.timestamps:
                    try:
                        time_objects.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                    except Exception:
                        time_objects.append(ts)
                from core.models.atmosphere.base import VariableTimeSeries
                variables["cape"] = VariableTimeSeries(data=cape_vals, unit="J/kg")
                variables["cin"] = VariableTimeSeries(data=cin_vals, unit="J/kg")
            else:
                ax1.text(0.5, 0.5, 'CAPE/CIN not available\n(archive data, no profile)',
                         transform=ax1.transAxes, ha='center', va='center',
                         fontsize=14, color='gray')
                ax1.set_title('CAPE and CIN Evolution')
                plt.tight_layout()
                return fig
        else:
            cape_data = np.array(variables["cape"].data)
            cin_data = np.array(variables["cin"].data)

        # Replace None with NaN
        cape_data = np.where(cape_data == None, np.nan, cape_data)
        cin_data = np.where(cin_data == None, np.nan, cin_data)

        # Primary axis: CAPE
        ax1.fill_between(time_objects, 0, cape_data, alpha=0.3, color='red', label='CAPE')
        ax1.plot(time_objects, cape_data, color='red', linewidth=2)
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'CAPE ({variables["cape"].unit})', color='red')
        ax1.tick_params(axis='y', labelcolor='red')
        ax1.grid(True, alpha=0.3)

        # Secondary axis: CIN
        ax2 = ax1.twinx()
        ax2.plot(time_objects, cin_data, color='cyan', linewidth=2, label='CIN')
        ax2.set_ylabel(f'CIN ({variables["cin"].unit})', color='cyan')
        ax2.tick_params(axis='y', labelcolor='cyan')

        if profile_hourly is not None:
            try:
                srh_3km = []
                srh_1km = []
                p_levels = np.array(profile_hourly.pressure_levels, dtype=float) * units.hPa
                for t_idx in range(len(profile_hourly.timestamps)):
                    ws = np.array(profile_hourly.wind_speed[t_idx], dtype=float) * units('m/s')
                    wd = np.array(profile_hourly.wind_direction[t_idx], dtype=float) * units.degrees
                    ht = np.array(profile_hourly.geopotential_height[t_idx], dtype=float) * units.meter
                    u_t, v_t = mpcalc.wind_components(ws, wd)
                    try:
                        pos3, neg3, total3 = mpcalc.storm_relative_helicity(
                            ht, u_t, v_t, depth=3000 * units.meter)
                        srh_3km.append(float(total3.magnitude))
                    except Exception:
                        srh_3km.append(np.nan)
                    try:
                        pos1, neg1, total1 = mpcalc.storm_relative_helicity(
                            ht, u_t, v_t, depth=1000 * units.meter)
                        srh_1km.append(float(total1.magnitude))
                    except Exception:
                        srh_1km.append(np.nan)

                srh_times = []
                for ts in profile_hourly.timestamps:
                    try:
                        srh_times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                    except Exception:
                        srh_times.append(ts)

                ax3 = ax1.twinx()
                ax3.spines['right'].set_position(('axes', 1.15))
                ax3.plot(srh_times, srh_3km, color='magenta', linewidth=2,
                         label='SRH 0-3km', solid_capstyle='round')
                ax3.plot(srh_times, srh_1km, color='magenta', linewidth=2,
                         linestyle='--', label='SRH 0-1km')
                ax3.set_ylabel('SRH (m²/s²)', color='magenta')
                ax3.tick_params(axis='y', labelcolor='magenta')
            except Exception:
                pass

        plt.title('CAPE and CIN Evolution')

    elif chart_type == "temp_dewpoint":
        # Temperature (red) + Dewpoint (green) lines with spread fill
        temp_data = np.array(variables["temperature"].data)
        dewpoint_data = np.array(variables["dewpoint"].data)

        # Replace None with NaN
        temp_data = np.where(temp_data == None, np.nan, temp_data)
        dewpoint_data = np.where(dewpoint_data == None, np.nan, dewpoint_data)

        ax1.plot(time_objects, temp_data, color='red', linewidth=2, label='Temperature')
        ax1.plot(time_objects, dewpoint_data, color='green', linewidth=2, label='Dewpoint')
        ax1.fill_between(time_objects, temp_data, dewpoint_data, alpha=0.2, color='orange')
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'Temperature ({variables["temperature"].unit})')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        plt.title('Temperature and Dewpoint')

    elif chart_type == "wind":
        # Wind speed (blue) + Gusts (red dashed) lines
        wind_speed_data = np.array(variables["wind_speed"].data)
        wind_gusts_data = np.array(variables["wind_gusts"].data)

        # Replace None with NaN
        wind_speed_data = np.where(wind_speed_data == None, np.nan, wind_speed_data)
        wind_gusts_data = np.where(wind_gusts_data == None, np.nan, wind_gusts_data)

        ax1.plot(time_objects, wind_speed_data, color='cyan', linewidth=2, label='Wind Speed')
        ax1.plot(time_objects, wind_gusts_data, color='red', linewidth=2, linestyle='--', label='Wind Gusts')
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'Speed ({variables["wind_speed"].unit})')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        plt.title('Wind Speed and Gusts')

    plt.tight_layout()
    return fig


def create_sounding_chart(profile_data):
    """Create a Skew-T Log-P sounding chart from atmospheric profile data.

    Args:
        profile_data: Dict with keys:
            - pressure_levels: List of pressure levels (hPa)
            - temperature: List of temperatures (°C)
            - dewpoint: List of dewpoint temperatures (°C)
            - wind_speed: List of wind speeds (m/s)
            - wind_direction: List of wind directions (degrees)
            - synoptic_time: Timestamp string
            - latitude: Location latitude
            - longitude: Location longitude

    Returns:
        matplotlib Figure
    """
    plt.style.use("dark_background")

    from scipy.interpolate import PchipInterpolator

    p_raw = np.array(profile_data.pressure_levels, dtype=float)
    t_raw = np.array(profile_data.temperature, dtype=float)
    td_raw = np.array(profile_data.dewpoint, dtype=float)
    ws_raw = np.array(profile_data.wind_speed, dtype=float)
    wd_raw = np.array(profile_data.wind_direction, dtype=float)

    # Interpolate in log-pressure space; PCHIP preserves monotonicity
    # and avoids overshooting artifacts in dewpoint dry layers
    log_p = np.log(p_raw)
    log_p_fine = np.linspace(log_p[0], log_p[-1], 200)
    p_fine = np.exp(log_p_fine)

    valid_t = np.isfinite(t_raw) & np.isfinite(td_raw)
    if valid_t.sum() >= 4:
        lp_v = log_p[valid_t]
        t_v = t_raw[valid_t]
        td_v = td_raw[valid_t]
        # PCHIP needs strictly increasing x — reverse if decreasing
        if lp_v[0] > lp_v[-1]:
            lp_v = lp_v[::-1]
            t_v = t_v[::-1]
            td_v = td_v[::-1]
        t_interp = PchipInterpolator(lp_v, t_v)
        td_interp = PchipInterpolator(lp_v, td_v)
        t_fine = t_interp(log_p_fine)
        td_fine = td_interp(log_p_fine)
    else:
        p_fine = p_raw
        t_fine = t_raw
        td_fine = td_raw

    pressure = p_raw * units.hPa
    temperature = t_raw * units.degC
    dewpoint = td_raw * units.degC
    wind_speed = ws_raw * units('m/s')
    wind_dir = wd_raw * units.degrees

    p_smooth = p_fine * units.hPa
    t_smooth = t_fine * units.degC
    td_smooth = td_fine * units.degC

    u, v = mpcalc.wind_components(wind_speed, wind_dir)

    fig = plt.figure(figsize=(9, 9), dpi=150)
    skew = SkewT(fig, rotation=45)

    skew.plot(p_smooth, t_smooth, 'r', linewidth=2, label='Temperature')
    skew.plot(p_smooth, td_smooth, 'g', linewidth=2, label='Dewpoint')

    u_kt = u.to('knots')
    v_kt = v.to('knots')
    skew.plot_barbs(pressure, u_kt, v_kt, xloc=0.95, length=7, linewidth=0.8)

    try:
        parcel_prof = mpcalc.parcel_profile(pressure, temperature[0], dewpoint[0]).to('degC')
        skew.plot(pressure, parcel_prof, color='yellow', linewidth=1.5, linestyle='--', label='Parcel')
        skew.shade_cape(pressure, temperature, parcel_prof, facecolor='red', alpha=0.4, linewidth=0)
        skew.shade_cin(pressure, temperature, parcel_prof, facecolor='deepskyblue', alpha=0.4, linewidth=0)
    except Exception:
        parcel_prof = None

    # Use yaxis transform so labels always pin to the right edge
    ytrans = skew.ax.get_yaxis_transform()

    # LCL, LFC, EL markers
    annotations = []
    try:
        lcl_p, lcl_t = mpcalc.lcl(pressure[0], temperature[0], dewpoint[0])
        skew.ax.axhline(lcl_p.magnitude, color='lime', linewidth=1, linestyle='-', alpha=0.7)
        skew.ax.text(1.02, lcl_p.magnitude,
                     f'LCL {lcl_p.magnitude:.0f} hPa', color='lime', fontsize=8,
                     va='center', ha='left', fontweight='bold',
                     transform=ytrans, clip_on=False)
    except Exception:
        lcl_p = None

    try:
        if parcel_prof is not None:
            lfc_p, lfc_t = mpcalc.lfc(pressure, temperature, dewpoint)
            if not np.isnan(lfc_p.magnitude):
                skew.ax.axhline(lfc_p.magnitude, color='orange', linewidth=1, linestyle='-', alpha=0.7)
                skew.ax.text(1.02, lfc_p.magnitude,
                             f'LFC {lfc_p.magnitude:.0f} hPa', color='orange', fontsize=8,
                             va='center', ha='left', fontweight='bold',
                             transform=ytrans, clip_on=False)
    except Exception:
        pass

    try:
        if parcel_prof is not None:
            el_p, el_t = mpcalc.el(pressure, temperature, dewpoint)
            if not np.isnan(el_p.magnitude):
                skew.ax.axhline(el_p.magnitude, color='deepskyblue', linewidth=1, linestyle='-', alpha=0.7)
                skew.ax.text(1.02, el_p.magnitude,
                             f'EL {el_p.magnitude:.0f} hPa', color='deepskyblue', fontsize=8,
                             va='center', ha='left', fontweight='bold',
                             transform=ytrans, clip_on=False)
    except Exception:
        pass

    # CAPE values: surface-based, mixed-layer, most-unstable
    cape_lines = []
    try:
        sb_cape, sb_cin = mpcalc.surface_based_cape_cin(pressure, temperature, dewpoint)
        cape_lines.append(f'SBCAPE: {sb_cape.magnitude:.0f} J/kg')
        cape_lines.append(f'CIN: {sb_cin.magnitude:.0f} J/kg')
    except Exception:
        pass
    try:
        ml_cape, ml_cin = mpcalc.mixed_layer_cape_cin(pressure, temperature, dewpoint)
        cape_lines.append(f'MLCAPE: {ml_cape.magnitude:.0f} J/kg')
    except Exception:
        pass
    try:
        mu_cape, mu_cin = mpcalc.most_unstable_cape_cin(pressure, temperature, dewpoint)
        cape_lines.append(f'MUCAPE: {mu_cape.magnitude:.0f} J/kg')
    except Exception:
        pass

    if cape_lines:
        cape_text = '\n'.join(cape_lines)
        skew.ax.text(0.02, 0.02, cape_text, transform=skew.ax.transAxes,
                     fontsize=9, color='white', verticalalignment='bottom',
                     fontfamily='monospace',
                     bbox=dict(boxstyle='round,pad=0.4', facecolor='black', alpha=0.7))

    # SFC label with I-beam and height
    sfc_hgt = profile_data.geopotential_height[0] if profile_data.geopotential_height else 0
    sfc_p = pressure[0].magnitude
    skew.ax.axhline(sfc_p, color='white', linewidth=1.5, linestyle='-', alpha=0.5)
    skew.ax.text(1.02, sfc_p,
                 f'SFC {sfc_hgt:.0f}m', color='white', fontsize=8,
                 va='center', ha='left', fontweight='bold',
                 transform=ytrans, clip_on=False)

    skew.plot_dry_adiabats(alpha=0.8, linewidth=0.8, colors='#CC6644')
    skew.plot_moist_adiabats(alpha=0.7, linewidth=0.8, colors='#44AA66')
    skew.plot_mixing_lines(alpha=0.85, linewidth=1.2, linestyle='--', colors='#008080')

    # Label mixing ratio values (g/kg) at bottom of each line
    mixing_ratios = [1, 2, 4, 7, 10, 16, 24, 32]
    bottom_p = 1050 * units.hPa
    for mr in mixing_ratios:
        try:
            td = mpcalc.dewpoint(mpcalc.vapor_pressure(
                bottom_p, mr / 1000.0)).to('degC').magnitude
            if -40 <= td <= 50:
                skew.ax.text(td, 1080, f'{mr}', color='#008080', fontsize=6,
                             ha='center', va='top', fontweight='bold',
                             clip_on=False)
        except Exception:
            pass

    # Highlight the 0°C and -20°C isotherms (diagonal lines on Skew-T)
    skew.ax.axvline(0, color='#00FFFF', linewidth=2, linestyle=':', alpha=0.8, zorder=5)
    skew.ax.axvline(-20, color='#AA00FF', linewidth=2, linestyle=':', alpha=0.8, zorder=5)

    skew.ax.set_ylim(1050, 100)
    skew.ax.set_xlim(-40, 50)

    synoptic = profile_data.synoptic_time or ""
    lat = profile_data.latitude or ""
    lon = profile_data.longitude or ""
    skew.ax.set_title(f"Sounding — {synoptic}  ({lat}°, {lon}°)", fontsize=12)
    skew.ax.legend(loc='upper left', fontsize=8)

    plt.tight_layout()
    return fig


def create_hodograph_chart(profile_data):
    """Create a hodograph with Bunkers storm motion vectors and height-colored segments.

    Args:
        profile_data: Dict with keys:
            - pressure_levels: List of pressure levels (hPa)
            - wind_speed: List of wind speeds (m/s)
            - wind_direction: List of wind directions (degrees)
            - geopotential_height: List of geopotential heights (meters)
            - synoptic_time: Timestamp string
            - latitude: Location latitude
            - longitude: Location longitude

    Returns:
        matplotlib Figure
    """
    plt.style.use("dark_background")

    pressure = np.array(profile_data.pressure_levels, dtype=float) * units.hPa
    wind_speed = np.array(profile_data.wind_speed, dtype=float) * units('m/s')
    wind_dir = np.array(profile_data.wind_direction, dtype=float) * units.degrees
    heights = np.array(profile_data.geopotential_height, dtype=float) * units.meter

    u, v = mpcalc.wind_components(wind_speed, wind_dir)
    MS_TO_KT = 1.94384
    u_kt = u.magnitude * MS_TO_KT
    v_kt = v.magnitude * MS_TO_KT

    max_wind_kt = max(float(wind_speed.max().magnitude) * MS_TO_KT, 40)
    ring_max = int(np.ceil(max_wind_kt / 20) * 20) + 20

    fig = plt.figure(figsize=(9, 7), dpi=150)
    ax = fig.add_axes([0.08, 0.08, 0.68, 0.84])
    h = Hodograph(ax, component_range=ring_max)
    h.add_grid(increment=20)

    height_m = heights.magnitude
    bands = [
        (0, 500, '#AA00FF', 3.5, '0-0.5 km'),
        (500, 1000, 'red', 3.0, '0.5-1 km'),
        (1000, 3000, 'orange', 2.0, '1-3 km'),
        (3000, 6000, 'yellow', 1.5, '3-6 km'),
        (6000, 9000, 'green', 1.5, '6-9 km'),
        (9000, 99999, 'deepskyblue', 1.0, '9+ km'),
    ]

    from scipy.interpolate import interp1d
    u_mag = u_kt
    v_mag = v_kt
    valid = np.isfinite(u_mag) & np.isfinite(v_mag) & np.isfinite(height_m)
    if valid.sum() >= 4:
        u_interp = interp1d(height_m[valid], u_mag[valid], kind='cubic',
                            bounds_error=False, fill_value='extrapolate')
        v_interp = interp1d(height_m[valid], v_mag[valid], kind='cubic',
                            bounds_error=False, fill_value='extrapolate')
        h_fine = np.arange(height_m[valid].min(), min(height_m[valid].max(), 12000), 50)

        for low, high, color, lw, label in bands:
            mask = (h_fine >= low) & (h_fine < high)
            seg_h = h_fine[mask]
            if len(seg_h) < 2:
                continue
            seg_u = u_interp(seg_h)
            seg_v = v_interp(seg_h)
            ax.plot(seg_u, seg_v, color=color, linewidth=lw, label=label, solid_capstyle='round')

        for label_h in [500, 1000, 3000, 6000, 9000]:
            if height_m[valid].min() <= label_h <= height_m[valid].max():
                lu = float(u_interp(label_h))
                lv = float(v_interp(label_h))
                ax.annotate(f'{label_h/1000:.0f}km', (lu, lv), fontsize=7,
                            color='white', fontweight='bold',
                            xytext=(5, 5), textcoords='offset points')
    else:
        ax.plot(u_mag, v_mag, color='white', linewidth=1.5)

    try:
        rm, lm, mean = mpcalc.bunkers_storm_motion(pressure, u, v, heights)
        ax.plot(rm[0].magnitude * MS_TO_KT, rm[1].magnitude * MS_TO_KT, 'o',
                color='magenta', markersize=10, zorder=10, label='RM')
        ax.plot(lm[0].magnitude * MS_TO_KT, lm[1].magnitude * MS_TO_KT, 'o',
                color='cyan', markersize=10, markerfacecolor='none',
                markeredgewidth=2, zorder=10, label='LM')
        ax.plot(mean[0].magnitude * MS_TO_KT, mean[1].magnitude * MS_TO_KT, '+',
                color='white', markersize=12, markeredgewidth=2, zorder=10, label='MW')
    except Exception:
        pass

    ax.set_xlabel('u (kt)')
    ax.set_ylabel('v (kt)')
    synoptic = profile_data.synoptic_time or ""
    ax.set_title(f"Hodograph — {synoptic}", fontsize=12)
    ax.legend(loc='upper right', fontsize=6, ncol=2)

    # Wind barb column on the right — interpolated for density
    ax_barbs = fig.add_axes([0.77, 0.08, 0.10, 0.84])
    u_kt_arr = u.to('knots').magnitude
    v_kt_arr = v.to('knots').magnitude
    p_arr = pressure.magnitude
    log_p_arr = np.log(p_arr)

    if valid.sum() >= 4:
        log_p_fine_b = np.linspace(log_p_arr[0], log_p_arr[-1], len(p_arr) * 2)
        # Ensure raw data points are included so surface isn't lost to interpolation
        log_p_fine_b = np.unique(np.sort(np.concatenate([log_p_fine_b, log_p_arr])))
        p_fine_b = np.exp(log_p_fine_b)
        u_interp_b = interp1d(log_p_arr[valid], u_kt_arr[valid], kind='cubic',
                               bounds_error=False, fill_value='extrapolate')
        v_interp_b = interp1d(log_p_arr[valid], v_kt_arr[valid], kind='cubic',
                               bounds_error=False, fill_value='extrapolate')
        h_interp_b = interp1d(log_p_arr[valid], height_m[valid], kind='linear',
                               bounds_error=False, fill_value='extrapolate')
        u_fine_b = u_interp_b(log_p_fine_b)
        v_fine_b = v_interp_b(log_p_fine_b)
        h_fine_b = h_interp_b(log_p_fine_b)
    else:
        p_fine_b = p_arr
        u_fine_b = u_kt_arr
        v_fine_b = v_kt_arr
        h_fine_b = height_m

    for i in range(len(p_fine_b)):
        hgt = h_fine_b[i]
        barb_color = 'white'
        for low, high, color, _, _ in bands:
            if low <= hgt < high:
                barb_color = color
                break
        ax_barbs.barbs(0, p_fine_b[i], u_fine_b[i], v_fine_b[i],
                       color=barb_color, length=6, linewidth=0.7)
    ax_barbs.set_yscale('log')
    ax_barbs.set_ylim(1050, 100)
    ax_barbs.set_xlim(-1, 1)
    ax_barbs.set_xticks([])
    ax_barbs.set_ylabel('hPa', fontsize=8)
    ax_barbs.yaxis.set_label_position('right')
    ax_barbs.yaxis.tick_right()
    import matplotlib.ticker as mticker
    ax_barbs.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax_barbs.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax_barbs.set_yticks([1000, 925, 850, 700, 500, 300, 200, 100])
    ax_barbs.set_yticklabels(['1000', '925', '850', '700', '500', '300', '200', '100'],
                              fontsize=6)
    ax_barbs.set_facecolor('black')
    ax_barbs.set_title('Wind', fontsize=9)

    return fig


def create_timeline_bar(timestamps, current_idx=0):
    """Render a notched timeline bar with timestamps above each notch.

    Args:
        timestamps: list of timestamp strings (e.g., "2026-06-06 10:00 UTC")
        current_idx: index of the currently selected frame

    Returns:
        matplotlib Figure showing the timeline
    """
    plt.style.use("dark_background")
    n = len(timestamps)
    if n == 0:
        fig, ax = plt.subplots(figsize=(9, 0.6))
        ax.set_visible(False)
        return fig

    fig, ax = plt.subplots(figsize=(9, 0.8))
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(-0.3, 1.0)

    # Draw the horizontal line
    ax.plot([-0.3, n - 0.7], [0, 0], color='#888888', linewidth=2, zorder=1)

    for i, ts in enumerate(timestamps):
        # Extract short label: "HH:MM" from "YYYY-MM-DD HH:MM UTC"
        parts = ts.replace(" UTC", "").split(" ")
        time_part = parts[-1] if len(parts) > 1 else ts
        date_part = parts[0].split("-")
        short_date = f"{date_part[1]}/{date_part[2]}" if len(date_part) == 3 else ""

        is_current = (i == current_idx)
        notch_color = '#FFD700' if is_current else '#AAAAAA'
        notch_height = 0.35 if is_current else 0.2
        notch_width = 2.5 if is_current else 1.5

        # Notch
        ax.plot([i, i], [-notch_height, notch_height], color=notch_color,
                linewidth=notch_width, zorder=2)

        # Current frame marker
        if is_current:
            ax.plot(i, -0.25, marker='^', color='#FFD700', markersize=8, zorder=3)

        # Labels — show all if <=12 frames, else every Nth
        show_label = n <= 12 or i % max(1, n // 10) == 0 or is_current
        if show_label:
            fontsize = 7 if n > 8 else 8
            ax.text(i, 0.45, time_part, ha='center', va='bottom',
                    fontsize=fontsize, color=notch_color, fontweight='bold' if is_current else 'normal')
            if short_date and (n <= 6 or is_current):
                ax.text(i, 0.75, short_date, ha='center', va='bottom',
                        fontsize=6, color='#666666')

    ax.set_axis_off()
    fig.subplots_adjust(left=0.02, right=0.98, top=0.95, bottom=0.05)
    return fig


def create_timelapse_gif(frames, output_path, fps=4):
    """Create animated GIF from a list of PIL Images.

    Args:
        frames: List of PIL Images (RGBA mode)
        output_path: Path to save the GIF
        fps: Frames per second
    """
    # Calculate duration per frame in milliseconds
    duration = int(1000 / fps)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Save as animated GIF
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=False
    )


def create_timelapse_mp4(frames, output_path, fps=4):
    """Create MP4 video from a list of PIL Images.

    Args:
        frames: List of PIL Images (RGBA mode)
        output_path: Path to save the MP4
        fps: Frames per second
    """
    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Convert RGBA frames to RGB numpy arrays
    rgb_frames = []
    for frame in frames:
        if frame.mode == 'RGBA':
            # Convert RGBA to RGB (composite on white background)
            rgb = Image.new('RGB', frame.size, (255, 255, 255))
            rgb.paste(frame, mask=frame.split()[3])  # Use alpha channel as mask
            rgb_frames.append(np.array(rgb))
        else:
            rgb_frames.append(np.array(frame.convert('RGB')))

    # Write MP4 using imageio
    # Use mimwrite for simpler API
    imageio.mimwrite(output_path, rgb_frames, fps=fps, codec='libx264')
