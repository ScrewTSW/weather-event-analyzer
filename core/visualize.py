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
    if center_lat is not None and center_lon is not None and HAS_CARTOPY:
        date_str = None
        if timestamp:
            parts = timestamp.replace(" UTC", "").split(" ")
            if parts:
                date_str = parts[0]
        frame = render_map_view(
            center_lat, center_lon, bbox_deg=bbox_deg,
            date=date_str, output_size=(900, 700))
    else:
        if satellite_image.mode != "RGBA":
            frame = satellite_image.convert("RGBA")
        else:
            frame = satellite_image.copy()
        if center_x is not None and center_y is not None:
            frame = add_location_marker(frame, center_x, center_y)

    # Final layer: timestamp label
    frame = add_timestamp_label(frame, timestamp, satellite_name, location_str)

    return frame


def create_atmosphere_chart(data, chart_type):
    """Create atmospheric data charts.

    Args:
        data: Dict with structure:
            {
                "timestamps": ["2026-05-31T07:00", ...],
                "variables": {
                    "var_name": {"data": [...], "unit": "..."},
                    ...
                }
            }
        chart_type: One of "cape_cin", "temp_dewpoint", "wind"

    Returns:
        matplotlib Figure
    """
    plt.style.use("dark_background")
    timestamps = data["timestamps"]
    variables = data["variables"]

    # Parse timestamps
    try:
        time_objects = [datetime.fromisoformat(ts.replace("Z", "+00:00")) for ts in timestamps]
    except Exception:
        # If parsing fails, use indices
        time_objects = list(range(len(timestamps)))

    fig, ax1 = plt.subplots(figsize=(10, 6))

    if chart_type == "cape_cin":
        # CAPE area fill (red) + CIN line (blue) with dual y-axis
        cape_data = np.array(variables["cape"]["data"])
        cin_data = np.array(variables["cin"]["data"])

        # Replace None with NaN
        cape_data = np.where(cape_data == None, np.nan, cape_data)
        cin_data = np.where(cin_data == None, np.nan, cin_data)

        # Primary axis: CAPE
        ax1.fill_between(time_objects, 0, cape_data, alpha=0.3, color='red', label='CAPE')
        ax1.plot(time_objects, cape_data, color='red', linewidth=2)
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'CAPE ({variables["cape"]["unit"]})', color='red')
        ax1.tick_params(axis='y', labelcolor='red')
        ax1.grid(True, alpha=0.3)

        # Secondary axis: CIN
        ax2 = ax1.twinx()
        ax2.plot(time_objects, cin_data, color='blue', linewidth=2, label='CIN')
        ax2.set_ylabel(f'CIN ({variables["cin"]["unit"]})', color='blue')
        ax2.tick_params(axis='y', labelcolor='blue')

        plt.title('CAPE and CIN Evolution')

    elif chart_type == "temp_dewpoint":
        # Temperature (red) + Dewpoint (green) lines with spread fill
        temp_data = np.array(variables["temperature"]["data"])
        dewpoint_data = np.array(variables["dewpoint"]["data"])

        # Replace None with NaN
        temp_data = np.where(temp_data == None, np.nan, temp_data)
        dewpoint_data = np.where(dewpoint_data == None, np.nan, dewpoint_data)

        ax1.plot(time_objects, temp_data, color='red', linewidth=2, label='Temperature')
        ax1.plot(time_objects, dewpoint_data, color='green', linewidth=2, label='Dewpoint')
        ax1.fill_between(time_objects, temp_data, dewpoint_data, alpha=0.2, color='orange')
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'Temperature ({variables["temperature"]["unit"]})')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        plt.title('Temperature and Dewpoint')

    elif chart_type == "wind":
        # Wind speed (blue) + Gusts (red dashed) lines
        wind_speed_data = np.array(variables["wind_speed"]["data"])
        wind_gusts_data = np.array(variables["wind_gusts"]["data"])

        # Replace None with NaN
        wind_speed_data = np.where(wind_speed_data == None, np.nan, wind_speed_data)
        wind_gusts_data = np.where(wind_gusts_data == None, np.nan, wind_gusts_data)

        ax1.plot(time_objects, wind_speed_data, color='blue', linewidth=2, label='Wind Speed')
        ax1.plot(time_objects, wind_gusts_data, color='red', linewidth=2, linestyle='--', label='Wind Gusts')
        ax1.set_xlabel('Time')
        ax1.set_ylabel(f'Speed ({variables["wind_speed"]["unit"]})')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        plt.title('Wind Speed and Gusts')

    plt.tight_layout()
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
