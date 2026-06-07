# app.py
from pathlib import Path

import gradio as gr
import matplotlib
matplotlib.use("Agg")

from core.cache import Cache, default_cache_dir
from core.location import resolve_location
from core.satellite import SatelliteClient, SOURCES
from core.atmosphere import OpenMeteoClient
from core.alerts import fetch_alerts
from core.visualize import create_atmosphere_chart, add_timestamp_label, add_geo_overlay


def _timeline_html(labels, current_idx=0):
    """Build the unified timeline HTML with notches, timestamps, and embedded slider."""
    if not labels:
        return "<div style='height:60px;background:#1a1a2e;border-radius:8px'></div>"
    n = len(labels)
    notches = []
    for i, ts in enumerate(labels):
        parts = ts.replace(" UTC", "").split(" ")
        time_part = parts[-1] if len(parts) > 1 else ts
        date_parts = parts[0].split("-") if parts else []
        short_date = f"{date_parts[1]}/{date_parts[2]}" if len(date_parts) == 3 else ""
        is_cur = (i == current_idx)
        color = "#FFD700" if is_cur else "#888"
        weight = "bold" if is_cur else "normal"
        date_html = f"<div style='font-size:9px;color:#555'>{short_date}</div>" if (is_cur or i == 0) else ""
        notches.append(f"""<div style='flex:1;text-align:center'>
            {date_html}
            <div style='font-size:11px;color:{color};font-weight:{weight}'>{time_part}</div>
            <div style='width:2px;height:{"16" if is_cur else "10"}px;background:{color};margin:2px auto'></div>
        </div>""")

    return f"""<div style='background:#1a1a2e;border-radius:8px;padding:8px 12px 4px 12px'>
        <div style='display:flex;align-items:flex-end'>{chr(10).join(notches)}</div>
        <div style='height:1px;background:#444;margin:0'></div>
        <input type='range' min='0' max='{n-1}' value='{current_idx}' step='1'
            style='width:100%;margin:4px 0 0 0;accent-color:#FFD700;cursor:pointer'
            oninput="
                document.getElementById('wea-bridge').value=this.value;
                document.getElementById('wea-bridge').dispatchEvent(new Event('change'));
            ">
    </div>"""


def create_app(cache_max_gb: float = 5.0) -> gr.Blocks:
    cache_dir = default_cache_dir()
    cache = Cache(str(cache_dir), max_size_gb=cache_max_gb)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient()

    state = {"composed_frames": [], "frame_labels": []}

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product):
        log_lines = []
        # outputs: image, timeline_html, cape, temp, wind, info, log, alerts
        empty = (None, "", None, None, None, "", "", [])

        def log(msg):
            log_lines.append(msg)

        def out(**kw):
            base = list(empty)
            base[6] = "\n".join(log_lines)
            mapping = {"image": 0, "timeline": 1, "cape": 2, "temp": 3, "wind": 4, "info": 5, "alerts": 7}
            for k, v in kw.items():
                base[mapping[k]] = v
            return tuple(base)

        try:
            from datetime import datetime, timedelta
            try:
                from datetime import UTC
                now = datetime.now(UTC).replace(tzinfo=None)
            except ImportError:
                now = datetime.utcnow()

            if not location_str or not location_str.strip():
                log("ERROR: Enter a location"); yield out(); return

            date = (date or "").strip() or now.strftime("%Y-%m-%d")
            start_utc = (start_utc or "").strip() or (now - timedelta(hours=2)).strftime("%H:%M")
            end_utc = (end_utc or "").strip() or now.strftime("%H:%M")

            log("Resolving location...")
            yield out()

            lat, lon, display, satellite = resolve_location(location_str)
            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']}")
            yield out(info=info)

            # 1. Atmosphere (fast)
            log("Fetching atmosphere...")
            yield out(info=info)
            atmo = atmo_client.fetch(lat, lon, date)
            log(f"Atmosphere: {atmo['source']} ({len(atmo['timestamps'])}h)")
            cape = create_atmosphere_chart(atmo, "cape_cin")
            temp = create_atmosphere_chart(atmo, "temp_dewpoint")
            wind = create_atmosphere_chart(atmo, "wind")
            yield out(info=info, cape=cape, temp=temp, wind=wind)

            # 2. Alerts (fast)
            log("Fetching alerts...")
            yield out(info=info, cape=cape, temp=temp, wind=wind)
            sc = start_utc.replace(":", ""); ec = end_utc.replace(":", "")
            mid = str((int(sc) + int(ec)) // 2).zfill(4)
            alerts_raw = fetch_alerts(lat, lon, f"{date}T{mid[:2]}:{mid[2:]}:00")
            log(f"Found {len(alerts_raw)} alerts")
            alerts_data = _format_alerts(alerts_raw)
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

            # 3. Satellite imagery (pre-projected tiles from GIBS/EUMETSAT)
            # Zoom: 0=40°(continental), 1=30°, 2=20°, 3=10°, 4=5°(city)
            bbox_deg = {0: 40, 1: 30, 2: 20, 3: 10, 4: 5}[int(zoom)]
            date_c = date.replace("-", "")
            log(f"Fetching satellite imagery (bbox={bbox_deg}°)...")
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

            frames = sat_client.fetch_event(
                lat=lat, lon=lon, date=date_c, start_utc=sc, end_utc=ec,
                product=product, zoom=int(zoom), bbox_deg=bbox_deg,
                on_progress=log,
            )
            log(f"Got {len(frames)} frames")

            composed, labels = [], []
            for i, f in enumerate(frames):
                ts = f["timestamp"]
                label = ts.replace("T", " ").replace("Z", " UTC")
                img = add_geo_overlay(f["image"], lat, lon, bbox_deg=bbox_deg)
                img = add_timestamp_label(img, label, f["source"], display)
                composed.append(img)
                labels.append(label)
                if i == 0:
                    yield out(image=img, timeline=_timeline_html(labels, 0),
                              info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

            state["composed_frames"] = composed
            state["frame_labels"] = labels
            log("Done.")
            yield out(image=composed[0] if composed else None,
                      timeline=_timeline_html(labels, 0),
                      info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}\n{traceback.format_exc()}")
            yield out()

    def on_frame_change(bridge_val):
        """Called when the hidden bridge input changes (from HTML slider JS)."""
        try:
            idx = int(float(bridge_val))
        except (ValueError, TypeError):
            return None, ""
        frames = state["composed_frames"]
        labels = state["frame_labels"]
        if not frames or idx >= len(frames):
            return None, ""
        return frames[idx], _timeline_html(labels, current_idx=idx)

    with gr.Blocks(title="Weather Event Analyzer") as app:
        gr.Markdown("# Weather Event Analyzer")

        with gr.Row():
            with gr.Column(scale=1):
                location_input = gr.Textbox(label="Location", placeholder="City name or lat,lon")
                date_input = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-05-31")
                with gr.Row():
                    start_input = gr.Textbox(label="Start UTC", placeholder="07:00")
                    end_input = gr.Textbox(label="End UTC", placeholder="12:00")
                zoom_slider = gr.Slider(minimum=0, maximum=4, step=1, value=2,
                                        label="Zoom (0=wide, 4=close)")
                product_dd = gr.Dropdown(
                    choices=["auto"] + list(SOURCES.keys()),
                    value="auto",
                    label="Satellite Source",
                )
                resolved = gr.Textbox(label="Resolved", interactive=False)
                fetch_btn = gr.Button("Fetch Data", variant="primary")

            with gr.Column(scale=2):
                sat_image = gr.Image(label="Satellite Imagery", type="pil", height=520)
                timeline = gr.HTML(value="<div style='height:60px;background:#1a1a2e;border-radius:8px'></div>")

        # Hidden bridge: HTML slider JS writes here, Gradio .change() reads it
        bridge = gr.Textbox(value="0", elem_id="wea-bridge", visible=False)

        # Arrow key listener (injected once)
        gr.HTML("""<script>
        document.addEventListener('keydown', function(e) {
            var s = document.querySelector('#component-timeline input[type=range]');
            if (!s) s = document.querySelector('input[type=range][max]');
            if (!s || s.closest('[class*=slider]')) return;
            if (e.key === 'ArrowLeft') {
                s.value = Math.max(0, parseInt(s.value) - 1);
                s.dispatchEvent(new Event('input'));
            } else if (e.key === 'ArrowRight') {
                s.value = Math.min(parseInt(s.max), parseInt(s.value) + 1);
                s.dispatchEvent(new Event('input'));
            }
        });
        </script>""")

        with gr.Row():
            cape_plot = gr.Plot(label="CAPE / CIN")
            temp_plot = gr.Plot(label="Temperature / Dewpoint")
            wind_plot = gr.Plot(label="Wind Speed")

        alerts_table = gr.Dataframe(
            headers=["Severity", "Event", "Title", "Intensity", "GPS", "Source", "From", "To"],
            label="Active Severe Weather Alerts", wrap=True,
        )
        status_log = gr.Textbox(label="Status Log", lines=6, interactive=False)

        fetch_btn.click(
            fn=fetch_data,
            inputs=[location_input, date_input, start_input, end_input, zoom_slider, product_dd],
            outputs=[sat_image, timeline, cape_plot, temp_plot, wind_plot,
                     resolved, status_log, alerts_table],
        )
        bridge.change(
            fn=on_frame_change,
            inputs=[bridge],
            outputs=[sat_image, timeline],
        )

    return app


def _format_alerts(alerts):
    rows = []
    for a in alerts:
        props = a.get("properties", {})
        intensity = props.get("intensity_text", "")
        if not intensity or "Magnitude 0" in str(intensity):
            iv = props.get("intensity", "")
            try:
                if iv and float(iv) > 0:
                    intensity = f"{iv} {props.get('intensity_unit', '')}".strip()
                else:
                    intensity = {"flood": "Active flooding", "drought": "Drought"}.get(
                        a.get("event_type", ""), a["severity"].title())
            except (ValueError, TypeError):
                intensity = a["severity"].title()
        lat = props.get("lat", "")
        lon = props.get("lon", "")
        geo = a.get("geometry") or {}
        if not lat and geo.get("coordinates"):
            c = geo["coordinates"]
            lon, lat = c[0], c[1]
        gps = f"{lat}, {lon}" if lat and lon else ""
        rows.append([
            a["severity"].upper(), a["event_type"], a["title"][:60],
            str(intensity)[:30], gps, a.get("source", ""),
            a.get("valid_from", "")[:16], a.get("valid_to", "")[:16],
        ])
    return rows
