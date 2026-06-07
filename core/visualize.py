"""Visualization module for weather event analysis.

Provides functions for:
- Layer compositing (satellite images + markers + labels)
- Atmospheric data charts
- Timelapse generation (GIF/MP4)
"""

import matplotlib
matplotlib.use("Agg")  # Headless rendering

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from pathlib import Path
import matplotlib.pyplot as plt
import imageio


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


def compose_frame(satellite_image, timestamp, satellite_name, location_str,
                  center_x=None, center_y=None):
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
    # Ensure we're working with RGBA
    if satellite_image.mode != "RGBA":
        frame = satellite_image.convert("RGBA")
    else:
        frame = satellite_image.copy()

    # Layer 2: Add location marker if coordinates provided
    if center_x is not None and center_y is not None:
        frame = add_location_marker(frame, center_x, center_y)

    # Layer 3: Add timestamp label (always)
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
