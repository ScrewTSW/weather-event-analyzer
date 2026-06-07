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
from core.visualize import compose_frame, create_atmosphere_chart, create_timelapse_gif


def _build_timeline_html(labels, current_idx=0):
    if not labels:
        return "<div style='height:50px'></div>"
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
        notches.append(f"""<div style='flex:1;text-align:center;cursor:pointer' onclick="
            document.getElementById('wea-slider').value={i};
            document.getElementById('wea-slider').dispatchEvent(new Event('input'));
        ">
            {date_html}
            <div style='font-size:11px;color:{color};font-weight:{weight}'>{time_part}</div>
            <div style='width:2px;height:{"16" if is_cur else "10"}px;background:{color};margin:2px auto'></div>
        </div>""")

    slider_html = f"""<input type='range' id='wea-slider' min='0' max='{n-1}' value='{current_idx}' step='1'
        style='width:100%;margin:0;accent-color:#FFD700;cursor:pointer'
        oninput="
            let v=parseInt(this.value);
            let el=document.querySelector('input[data-testid=\\'number-input\\']');
            if(el){{el.value=v;el.dispatchEvent(new Event('input',{{bubbles:true}}))}}
        ">"""

    return f"""<div style='background:#1a1a2e;border-radius:8px;padding:8px 12px;margin:4px 0'>
        <div style='display:flex;align-items:flex-end'>{chr(10).join(notches)}</div>
        <div style='height:1px;background:#444;margin:0 0 4px 0'></div>
        {slider_html}
    </div>
    <script>
    document.addEventListener('keydown',function(e){{
        let s=document.getElementById('wea-slider');
        if(!s)return;
        if(e.key==='ArrowLeft'){{s.value=Math.max(0,parseInt(s.value)-1);s.dispatchEvent(new Event('input'))}}
        if(e.key==='ArrowRight'){{s.value=Math.min({n-1},parseInt(s.value)+1);s.dispatchEvent(new Event('input'))}}
    }});
    </script>"""


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
        empty = (None, "<div></div>", 0, None, None, None, "", "", [])

        def log(msg):
            log_lines.append(msg)

        def partial(**kw):
            base = list(empty)
            base[7] = "\n".join(log_lines)
            for k, v in kw.items():
                idx = {"image": 0, "timeline": 1, "frame": 2, "cape": 3, "temp": 4, "wind": 5, "info": 6, "alerts": 8}[k]
                base[idx] = v
            return tuple(base)

        try:
            if not location_str or not location_str.strip():
                log("ERROR: Enter a location")
                yield partial()
                return

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
                log(f"Start: {start_utc} UTC (2h ago)")
            if not end_utc or not end_utc.strip():
                end_utc = now.strftime("%H:%M")
                log(f"End: {end_utc} UTC (now)")

            log("Resolving location...")
            yield partial()

            lat, lon, display, satellite = resolve_location(location_str)
            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']} ({satellite['slider_id']})")
            yield partial(info=info)

            date_compact = date.strip().replace("-", "")
            start_clean = start_utc.replace(":", "")
            end_clean = end_utc.replace(":", "")
            log(f"Time range: {start_clean}-{end_clean} UTC on {date_compact}")

            log("Downloading satellite tiles...")
            yield partial(info=info)

            frames = sat_client.fetch_event(
                satellite=satellite, product=product,
                date=date_compact, start_utc=start_clean, end_utc=end_clean,
                lat=lat, lon=lon, zoom=int(zoom), bbox_deg=20.0,
                on_progress=log,
            )
            log(f"Downloaded {len(frames)} satellite frames")
            yield partial(info=info)

            log("Rendering map views...")
            yield partial(info=info)

            composed = []
            labels = []
            for i, frame in enumerate(frames):
                ts_str = str(frame["timestamp"])
                ts_fmt = f"{ts_str[:4]}-{ts_str[4:6]}-{ts_str[6:8]} {ts_str[8:10]}:{ts_str[10:12]} UTC"
                img = compose_frame(
                    satellite_image=frame["image"], timestamp=ts_fmt,
                    satellite_name=satellite["name"], location_str=display,
                    sat_lon=satellite["lon"], center_lat=lat, center_lon=lon,
                    bbox_deg=20.0, tile_bounds=frame.get("tile_bounds"), zoom=frame.get("zoom"),
                )
                composed.append(img)
                labels.append(ts_fmt)
                log(f"  Rendered frame {i+1}/{len(frames)}")
                if i == 0:
                    yield partial(image=img, timeline=_build_timeline_html(labels, 0), info=info)

            state["composed_frames"] = composed
            state["frame_labels"] = labels

            log("Fetching atmosphere data...")
            tl_html = _build_timeline_html(labels, 0) if labels else "<div></div>"
            first_img = composed[0] if composed else None
            yield partial(image=first_img, timeline=tl_html, info=info)

            atmo = atmo_client.fetch(lat, lon, date)
            log(f"Atmosphere: {atmo['source']} ({len(atmo['timestamps'])} hours)")

            cape_fig = create_atmosphere_chart(atmo, "cape_cin")
            temp_fig = create_atmosphere_chart(atmo, "temp_dewpoint")
            wind_fig = create_atmosphere_chart(atmo, "wind")
            yield partial(image=first_img, timeline=tl_html, cape=cape_fig, temp=temp_fig, wind=wind_fig, info=info)

            log("Fetching severe weather alerts...")
            yield partial(image=first_img, timeline=tl_html, cape=cape_fig, temp=temp_fig, wind=wind_fig, info=info)

            start_h = int(start_clean) if start_clean else 0
            end_h = int(end_clean) if end_clean else 2359
            mid_h = str((start_h + end_h) // 2).zfill(4)
            alert_time = f"{date}T{mid_h[:2]}:{mid_h[2:]}:00"
            alerts = fetch_alerts(lat, lon, alert_time)
            log(f"Found {len(alerts)} alerts")

            alerts_data = []
            for a in alerts:
                props = a.get("properties", {})
                intensity_text = props.get("intensity_text", "")
                if not intensity_text or intensity_text.strip() in ("Magnitude 0", "Magnitude 0 "):
                    intensity_val = props.get("intensity", "")
                    try:
                        if intensity_val and float(intensity_val) > 0:
                            intensity_text = f"{intensity_val} {props.get('intensity_unit', '')}".strip()
                        else:
                            ev = a.get("event_type", "")
                            intensity_text = {"flood": "Active flooding", "drought": "Drought conditions"}.get(ev, a["severity"].title())
                    except (ValueError, TypeError):
                        intensity_text = a["severity"].title()
                alert_lat = props.get("lat", "")
                alert_lon = props.get("lon", "")
                geo = a.get("geometry") or {}
                if not alert_lat and geo.get("coordinates"):
                    coords = geo["coordinates"]
                    alert_lon, alert_lat = coords[0], coords[1]
                gps = f"{alert_lat}, {alert_lon}" if alert_lat and alert_lon else ""
                alerts_data.append([
                    a["severity"].upper(), a["event_type"], a["title"][:60],
                    intensity_text[:30], gps, a.get("source", ""),
                    a.get("valid_from", "")[:16], a.get("valid_to", "")[:16],
                ])

            log("Done.")
            yield partial(image=first_img, timeline=tl_html, cape=cape_fig, temp=temp_fig, wind=wind_fig, info=info, alerts=alerts_data)

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}")
            log_lines.append(traceback.format_exc())
            yield partial()

    def update_frame(idx):
        idx = int(idx)
        frames = state["composed_frames"]
        labels = state["frame_labels"]
        if not frames or idx >= len(frames):
            return None, "<div style='height:50px'></div>"
        tl_html = _build_timeline_html(labels, current_idx=idx)
        return frames[idx], tl_html

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
                timeline_html = gr.HTML(value="<div style='height:50px'></div>")
                frame_idx = gr.Number(value=0, visible=False, precision=0)

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
            outputs=[sat_image, timeline_html, frame_idx, cape_plot, temp_plot, wind_plot, resolved_info, status_log, alerts_table],
        )

        frame_idx.change(
            fn=update_frame,
            inputs=[frame_idx],
            outputs=[sat_image, timeline_html],
        )

    return app
