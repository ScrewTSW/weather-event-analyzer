# app.py
from pathlib import Path

import gradio as gr
import matplotlib
matplotlib.use("Agg")

from core.cache import Cache, default_cache_dir
from core.location import resolve_location
from core.satellite import SatelliteClient
from core.atmosphere import OpenMeteoClient
from core.alerts import fetch_alerts
from core.visualize import compose_frame, create_atmosphere_chart, create_timelapse_gif, create_timeline_bar


def create_app(cache_max_gb: float = 5.0) -> gr.Blocks:
    cache_dir = default_cache_dir()
    cache = Cache(str(cache_dir), max_size_gb=cache_max_gb)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient()

    state = {
        "composed_frames": [],
        "frame_labels": [],
    }

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product):
        log_lines = []
        def log(msg):
            log_lines.append(msg)

        try:
            if not location_str or not location_str.strip():
                return None, gr.update(), None, None, None, "", "ERROR: Enter a location", []

            from datetime import datetime, timedelta
            try:
                from datetime import UTC
                now = datetime.now(UTC).replace(tzinfo=None)
            except ImportError:
                now = datetime.utcnow()

            if not date or not date.strip():
                date = now.strftime("%Y-%m-%d")
                log(f"Using today: {date}")
            if not start_utc or not start_utc.strip():
                start_utc = (now - timedelta(hours=2)).strftime("%H:%M")
                log(f"Start defaulted to 2h ago: {start_utc} UTC")
            if not end_utc or not end_utc.strip():
                end_utc = now.strftime("%H:%M")
                log(f"End defaulted to now: {end_utc} UTC")

            lat, lon, display, satellite = resolve_location(location_str)
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']} ({satellite['slider_id']})")

            date_compact = date.strip().replace("-", "")
            start_clean = start_utc.replace(":", "")
            end_clean = end_utc.replace(":", "")
            log(f"Time range: {start_clean}-{end_clean} UTC on {date_compact}")

            frames = sat_client.fetch_event(
                satellite=satellite,
                product=product,
                date=date_compact,
                start_utc=start_clean,
                end_utc=end_clean,
                lat=lat, lon=lon,
                zoom=int(zoom),
                bbox_deg=20.0,
                on_progress=log,
            )
            log(f"Downloaded {len(frames)} satellite frames")

            log("Fetching atmosphere data...")
            atmo = atmo_client.fetch(lat, lon, date)
            log(f"Atmosphere data: {atmo['source']} ({len(atmo['timestamps'])} hours)")

            log("Fetching severe weather alerts...")
            start_h = int(start_clean) if start_clean else 0
            end_h = int(end_clean) if end_clean else 2359
            mid_h = str((start_h + end_h) // 2).zfill(4)
            alert_time = f"{date}T{mid_h[:2]}:{mid_h[2:]}:00"
            alerts = fetch_alerts(lat, lon, alert_time)
            log(f"Found {len(alerts)} alerts")

            # Format alerts for dataframe
            alerts_data = []
            for a in alerts:
                props = a.get("properties", {})
                intensity_text = props.get("intensity_text", "")
                if not intensity_text or intensity_text.strip() == "Magnitude 0":
                    intensity_val = props.get("intensity", "")
                    if intensity_val and float(intensity_val) > 0:
                        intensity_unit = props.get("intensity_unit", "")
                        intensity_text = f"{intensity_val} {intensity_unit}".strip()
                    else:
                        intensity_text = a["severity"].title()
                        ev = a.get("event_type", "")
                        if ev == "flood":
                            intensity_text = "Active flooding"
                        elif ev == "drought":
                            intensity_text = props.get("intensity_text", "Drought conditions")
                alert_lat = props.get("lat", "")
                alert_lon = props.get("lon", "")
                geo = a.get("geometry") or {}
                if not alert_lat and geo.get("coordinates"):
                    coords = geo["coordinates"]
                    alert_lon, alert_lat = coords[0], coords[1]
                gps = f"{alert_lat}, {alert_lon}" if alert_lat and alert_lon else ""
                alerts_data.append([
                    a["severity"].upper(),
                    a["event_type"],
                    a["title"][:60],
                    intensity_text[:30],
                    gps,
                    a.get("source", ""),
                    a.get("valid_from", "")[:16],
                    a.get("valid_to", "")[:16],
                ])

            # Compose frames with all layers
            composed = []
            labels = []
            for frame in frames:
                ts_str = str(frame["timestamp"])
                ts_fmt = f"{ts_str[:4]}-{ts_str[4:6]}-{ts_str[6:8]} {ts_str[8:10]}:{ts_str[10:12]} UTC"
                img = compose_frame(
                    satellite_image=frame["image"],
                    timestamp=ts_fmt,
                    satellite_name=satellite["name"],
                    location_str=display,
                    sat_lon=satellite["lon"],
                    center_lat=lat,
                    center_lon=lon,
                    bbox_deg=20.0,
                    tile_bounds=frame.get("tile_bounds"),
                    zoom=frame.get("zoom"),
                )
                composed.append(img)
                labels.append(ts_fmt)

            state["composed_frames"] = composed
            state["frame_labels"] = labels

            # Charts
            cape_fig = create_atmosphere_chart(atmo, "cape_cin")
            temp_fig = create_atmosphere_chart(atmo, "temp_dewpoint")
            wind_fig = create_atmosphere_chart(atmo, "wind")

            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"

            first_img = composed[0] if composed else None
            n_frames = max(1, len(composed))
            slider_max = n_frames - 1 if n_frames > 1 else 1
            first_ts = labels[0] if labels else ""
            timeline = create_timeline_bar(labels, current_idx=0)

            return (first_img, gr.Slider(minimum=0, maximum=slider_max, value=0, step=1, label="Frame"),
                    timeline, first_ts, cape_fig, temp_fig, wind_fig, info, "\n".join(log_lines), alerts_data)

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}")
            log_lines.append(traceback.format_exc())
            return (None, gr.Slider(minimum=0, maximum=1, value=0, step=1, label="Frame"),
                    None, "", None, None, None, "", "\n".join(log_lines), [])

    def update_frame(frame_idx):
        idx = int(frame_idx)
        frames = state["composed_frames"]
        labels = state["frame_labels"]
        if not frames or idx >= len(frames):
            return None, None, ""
        timeline = create_timeline_bar(labels, current_idx=idx)
        return frames[idx], timeline, labels[idx]

    with gr.Blocks(title="Weather Event Analyzer") as app:
        gr.Markdown("# Weather Event Analyzer")

        with gr.Row():
            with gr.Column(scale=1):
                location_input = gr.Textbox(label="Location", placeholder="City name or lat,lon")
                date_input = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-05-31")
                with gr.Row():
                    start_input = gr.Textbox(label="Start UTC", placeholder="07:00", scale=1)
                    end_input = gr.Textbox(label="End UTC", placeholder="12:00", scale=1)
                zoom_slider = gr.Slider(minimum=0, maximum=4, step=1, value=2, label="Zoom (0=wide, 4=close)")
                product_dropdown = gr.Dropdown(
                    choices=["geocolor", "band_13", "band_08"],
                    value="geocolor",
                    label="Satellite Product",
                )
                resolved_info = gr.Textbox(label="Resolved", interactive=False)
                fetch_btn = gr.Button("Fetch Data", variant="primary")

            with gr.Column(scale=2):
                sat_image = gr.Image(label="Satellite Imagery", type="pil", height=500)
                timeline_plot = gr.Plot(label=None, show_label=False)
                with gr.Row():
                    time_slider = gr.Slider(minimum=0, maximum=1, step=1, value=0, label="Frame", scale=4)
                    time_label = gr.Textbox(label="Timestamp", interactive=False, scale=1)

        with gr.Row():
            cape_plot = gr.Plot(label="CAPE / CIN")
            temp_plot = gr.Plot(label="Temperature / Dewpoint")
            wind_plot = gr.Plot(label="Wind Speed")

        alerts_table = gr.Dataframe(
            headers=["Severity", "Event", "Title", "Intensity", "GPS", "Source", "From", "To"],
            label="Active Severe Weather Alerts",
            wrap=True,
        )

        status_log = gr.Textbox(label="Status Log", lines=6, interactive=False)

        fetch_btn.click(
            fn=fetch_data,
            inputs=[location_input, date_input, start_input, end_input, zoom_slider, product_dropdown],
            outputs=[sat_image, time_slider, timeline_plot, time_label, cape_plot, temp_plot, wind_plot, resolved_info, status_log, alerts_table],
        )

        time_slider.change(
            fn=update_frame,
            inputs=[time_slider],
            outputs=[sat_image, timeline_plot, time_label],
        )

    return app
