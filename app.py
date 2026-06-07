# app.py
import tempfile
from pathlib import Path
from typing import Optional

import gradio as gr
import matplotlib
matplotlib.use("Agg")

from core.cache import Cache
from core.location import resolve_location
from core.satellite import SatelliteClient
from core.atmosphere import OpenMeteoClient
from core.visualize import compose_frame, create_atmosphere_chart, create_timelapse_gif


def create_app(cache_max_gb: float = 5.0) -> gr.Blocks:
    cache_dir = Path("./cache")
    cache = Cache(str(cache_dir), max_size_gb=cache_max_gb)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient()

    # Shared state
    state = {"frames": [], "atmo_data": None, "lat": None, "lon": None, "satellite": None}

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product):
        log_lines = []
        def log(msg):
            log_lines.append(msg)

        try:
            lat, lon, display, satellite = resolve_location(location_str)
            state["lat"] = lat
            state["lon"] = lon
            state["satellite"] = satellite
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']} ({satellite['slider_id']})")

            # Fetch satellite imagery
            date_compact = date.replace("-", "")
            frames = sat_client.fetch_event(
                satellite=satellite,
                product=product,
                date=date_compact,
                start_utc=start_utc.replace(":", ""),
                end_utc=end_utc.replace(":", ""),
                lat=lat, lon=lon,
                zoom=zoom,
                on_progress=log,
            )
            state["frames"] = frames
            log(f"Downloaded {len(frames)} satellite frames")

            # Fetch atmosphere data
            log("Fetching atmosphere data...")
            atmo = atmo_client.fetch(lat, lon, date)
            state["atmo_data"] = atmo
            log(f"Atmosphere data: {atmo['source']} ({len(atmo['timestamps'])} hours)")

            # Build gallery images using compose_frame for proper layer compositing
            gallery = []
            for frame in frames:
                ts_str = str(frame["timestamp"])
                ts_formatted = f"{ts_str[:4]}-{ts_str[4:6]}-{ts_str[6:8]} {ts_str[8:10]}:{ts_str[10:12]} UTC"
                # Use compose_frame to render all layers (satellite + marker + label)
                composed = compose_frame(
                    satellite_image=frame["image"],
                    timestamp=ts_formatted,
                    satellite_name=satellite["name"],
                    location_str=display,
                    center_x=frame["image"].width // 2,
                    center_y=frame["image"].height // 2,
                )
                gallery.append(composed)

            # Build charts
            cape_fig = create_atmosphere_chart(atmo, "cape_cin")
            temp_fig = create_atmosphere_chart(atmo, "temp_dewpoint")
            wind_fig = create_atmosphere_chart(atmo, "wind")

            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"

            return gallery, cape_fig, temp_fig, wind_fig, info, "\n".join(log_lines)

        except Exception as e:
            log_lines.append(f"ERROR: {e}")
            return [], None, None, None, "", "\n".join(log_lines)

    with gr.Blocks(title="Weather Event Analyzer", theme=gr.themes.Soft()) as app:
        gr.Markdown("# Weather Event Analyzer")

        with gr.Row():
            with gr.Column(scale=1):
                location_input = gr.Textbox(label="Location", placeholder="City name or lat,lon (e.g. 45.75, 126.65)")
                date_input = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-05-31")
                with gr.Row():
                    start_input = gr.Textbox(label="Start UTC", placeholder="07:00", scale=1)
                    end_input = gr.Textbox(label="End UTC", placeholder="12:00", scale=1)
                zoom_slider = gr.Slider(minimum=0, maximum=4, step=1, value=2, label="Zoom Level")
                product_dropdown = gr.Dropdown(
                    choices=["geocolor", "band_13", "band_08"],
                    value="geocolor",
                    label="Satellite Product",
                )
                resolved_info = gr.Textbox(label="Resolved", interactive=False)
                fetch_btn = gr.Button("Fetch Data", variant="primary")

            with gr.Column(scale=2):
                gallery = gr.Gallery(label="Satellite Imagery", columns=3, height=400)

        with gr.Row():
            cape_plot = gr.Plot(label="CAPE / CIN")
            temp_plot = gr.Plot(label="Temperature / Dewpoint")
            wind_plot = gr.Plot(label="Wind Speed")

        status_log = gr.Textbox(label="Status Log", lines=8, interactive=False)

        fetch_btn.click(
            fn=fetch_data,
            inputs=[location_input, date_input, start_input, end_input, zoom_slider, product_dropdown],
            outputs=[gallery, cape_plot, temp_plot, wind_plot, resolved_info, status_log],
        )

    return app
