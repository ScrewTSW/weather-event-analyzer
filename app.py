# app.py
import json
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


TIMELINE_HTML_TEMPLATE = """
<div style="background:#1a1a2e;border-radius:8px;padding:8px 12px 4px">
    <div id="tl-notches" style="display:flex;align-items:flex-end"></div>
    <div style="height:1px;background:#444;margin:0"></div>
    <input type="range" min="0" max="${Math.max(0,(frame_count||1)-1)}"
           value="${value||0}" step="1" id="tl-slider"
           style="width:100%;margin:4px 0 0;accent-color:#FFD700;cursor:pointer"
           ${(frame_count||0)<=1?'disabled':''}>
</div>
"""

TIMELINE_CSS = """
#tl-slider:disabled { opacity:0.3; cursor:default; }
"""

TIMELINE_JS = """
function renderNotches() {
    var c = element.querySelector('#tl-notches');
    if (!c) return;
    var labels = [];
    try { labels = JSON.parse(props.labels_json || '[]'); } catch(e) {}
    c.innerHTML = '';
    labels.forEach(function(ts, i) {
        var parts = ts.replace(' UTC','').split(' ');
        var timePart = parts.length > 1 ? parts[parts.length-1] : ts;
        var dp = parts[0] ? parts[0].split('-') : [];
        var shortDate = dp.length === 3 ? dp[1]+'/'+dp[2] : '';
        var isCur = (i === (props.value||0));
        var color = isCur ? '#FFD700' : '#888';
        var weight = isCur ? 'bold' : 'normal';
        var dateHtml = (isCur || i === 0) ? '<div style=\"font-size:9px;color:#555\">'+shortDate+'</div>' : '';
        var d = document.createElement('div');
        d.style.cssText = 'flex:1;text-align:center';
        d.innerHTML = dateHtml +
            '<div style=\"font-size:11px;color:'+color+';font-weight:'+weight+'\">'+timePart+'</div>' +
            '<div style=\"width:2px;height:'+(isCur?16:10)+'px;background:'+color+';margin:2px auto\"></div>';
        c.appendChild(d);
    });
}

var slider = element.querySelector('#tl-slider');
slider.addEventListener('input', function(e) {
    props.value = parseInt(e.target.value);
    renderNotches();
    trigger('change', {frame_index: props.value});
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        var s = element.querySelector('#tl-slider');
        if (!s || s.disabled) return;
        var mx = parseInt(s.max);
        var v = parseInt(s.value);
        if (e.key === 'ArrowLeft') v = Math.max(0, v-1);
        else v = Math.min(mx, v+1);
        s.value = v;
        props.value = v;
        renderNotches();
        trigger('change', {frame_index: v});
    }
});

watch(['value','labels_json','frame_count'], function() {
    var s = element.querySelector('#tl-slider');
    if (s) {
        s.max = Math.max(0, (props.frame_count||1)-1);
        s.value = props.value||0;
        s.disabled = (props.frame_count||0) <= 1;
    }
    renderNotches();
});

renderNotches();
"""


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
                    yield out(image=img, timeline=gr.HTML(value=0, labels_json=json.dumps(labels), frame_count=len(labels)),
                              info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

            state["composed_frames"] = composed
            state["frame_labels"] = labels
            log("Done.")
            yield out(image=composed[0] if composed else None,
                      timeline=gr.HTML(value=0, labels_json=json.dumps(labels), frame_count=len(labels)),
                      info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}\n{traceback.format_exc()}")
            yield out()

    def on_frame_change(timeline_value, evt: gr.EventData = None):
        """Called when the timeline slider triggers 'change' via JS trigger()."""
        import json
        try:
            idx = int(timeline_value) if timeline_value is not None else 0
        except (ValueError, TypeError):
            idx = 0
        frames = state["composed_frames"]
        labels = state["frame_labels"]
        if not frames or idx >= len(frames):
            return None, gr.skip()
        return frames[idx], gr.HTML(value=idx, labels_json=json.dumps(labels), frame_count=len(labels))

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
                timeline = gr.HTML(
                    value=0,
                    frame_count=0,
                    labels_json="[]",
                    html_template=TIMELINE_HTML_TEMPLATE,
                    css_template=TIMELINE_CSS,
                    js_on_load=TIMELINE_JS,
                )

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
            show_progress="hidden",
        )
        timeline.change(
            fn=on_frame_change,
            inputs=[timeline],
            outputs=[sat_image, timeline],
            show_progress="hidden",
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
