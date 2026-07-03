# app.py
import json
from pathlib import Path

import io

import gradio as gr
import matplotlib
matplotlib.use("Agg")
from PIL import Image

from core.cache import Cache, default_cache_dir
from core.location import resolve_location
from core.satellite import SatelliteClient, SOURCES
from core.atmosphere import OpenMeteoClient
from core.alerts import fetch_alerts
from core.visualize import create_atmosphere_chart, add_timestamp_label, add_geo_overlay
from core.pressure_levels import PressureLevelClient, snap_to_synoptic
from core.visualize import create_sounding_chart, create_hodograph_chart
from core.discussions import MesoscaleDiscussionClient
from core.fronts import FrontalAnalyzer


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


def create_app(cache_dir: str = None, cache_max_gb: float = 5.0) -> gr.Blocks:
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    cache = Cache(str(cache_dir), max_size_gb=cache_max_gb)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient()
    pressure_client = PressureLevelClient()
    discussion_client = MesoscaleDiscussionClient()
    frontal_analyzer = FrontalAnalyzer()

    state = {"composed_frames": [], "frame_labels": []}

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product, show_fronts):
        log_lines = []
        # outputs: image, timeline_html, cape, temp, wind, info, log, alerts, sounding, hodo, discussion, synoptic_label
        empty = (None, "", None, None, None, "", "", [], None, None, "", "")

        def log(msg):
            log_lines.append(msg)

        def out(**kw):
            base = list(empty)
            base[6] = "\n".join(log_lines)
            mapping = {"image": 0, "timeline": 1, "cape": 2, "temp": 3, "wind": 4,
                       "info": 5, "alerts": 7, "sounding": 8, "hodo": 9,
                       "discussion": 10, "synoptic_label": 11}
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

            def _fig_to_pil(fig):
                buf = io.BytesIO()
                fig.savefig(buf, format='png', bbox_inches='tight',
                            facecolor=fig.get_facecolor(), edgecolor='none')
                import matplotlib.pyplot as plt
                plt.close(fig)
                buf.seek(0)
                return Image.open(buf).convert("RGB")

            # 1. Atmosphere (fast)
            log("Fetching atmosphere...")
            yield out(info=info)
            atmo = atmo_client.fetch(lat, lon, date)
            log(f"Atmosphere: {atmo.source} ({len(atmo.timestamps)}h)")
            cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin"))
            temp = _fig_to_pil(create_atmosphere_chart(atmo, "temp_dewpoint"))
            wind = _fig_to_pil(create_atmosphere_chart(atmo, "wind"))
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

            # 3. Pressure-level data (sounding, hodograph, helicity)
            log("Fetching pressure-level data...")
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data)
            sc_h = int(start_utc.replace(":", "")[:2]) if start_utc else 12
            synoptic_h = snap_to_synoptic(sc_h)
            synoptic_label = f"Synoptic time: {synoptic_h:02d}Z {date}"

            try:
                profile = pressure_client.fetch_snapshot(lat, lon, date, sc_h)
                sounding = _fig_to_pil(create_sounding_chart(profile))
                hodo = _fig_to_pil(create_hodograph_chart(profile))
                log(f"Sounding/Hodograph: {profile.source} ({synoptic_h:02d}Z)")
            except Exception as e:
                log(f"Sounding/Hodograph failed: {e}")
                sounding, hodo = None, None

            try:
                profile_hourly = pressure_client.fetch_hourly(lat, lon, date)
                cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin", profile_hourly=profile_hourly))
                has_cape = "cape" in atmo.variables
                log("Helicity added to CAPE/CIN chart" + ("" if has_cape else " (CAPE computed from profiles)"))
            except Exception as e:
                log(f"Helicity computation failed: {e}")

            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, synoptic_label=synoptic_label)

            # 4. Mesoscale discussions
            log("Fetching mesoscale discussions...")
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, synoptic_label=synoptic_label)
            try:
                discussions = discussion_client.fetch(lat, lon, date)
                disc_text = ""
                for d in discussions:
                    disc_text += f"### {d.title} ({d.source})\n\n{d.text}\n\n---\n\n"
                if not disc_text:
                    disc_text = "No mesoscale discussions available for this region."
                log(f"Found {len(discussions)} discussion(s)")
            except Exception as e:
                log(f"Discussions failed: {e}")
                disc_text = "Failed to fetch mesoscale discussions."

            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label)

            # 5. Frontal analysis (optional)
            bbox_deg = {0: 40, 1: 30, 2: 20, 3: 10, 4: 5}[int(zoom)]
            detected_fronts = None
            if show_fronts:
                log("Running frontal analysis...")
                yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                          sounding=sounding, hodo=hodo, discussion=disc_text,
                          synoptic_label=synoptic_label)
                try:
                    detected_fronts = frontal_analyzer.fetch_and_analyze(
                        lat, lon, date, run_hour=f"{synoptic_h:02d}",
                        bbox_deg=bbox_deg)
                    n_fronts = len(detected_fronts)
                    log(f"Detected {n_fronts} frontal feature(s)")
                except Exception as e:
                    log(f"Frontal analysis failed: {e}")

            # 6. Satellite imagery (pre-projected tiles from GIBS/EUMETSAT)
            date_c = date.replace("-", "")
            log(f"Fetching satellite imagery (bbox={bbox_deg}°)...")
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label)

            frames = sat_client.fetch_event(
                lat=lat, lon=lon, date=date_c, start_utc=sc, end_utc=ec,
                product=product, zoom=int(zoom), bbox_deg=bbox_deg,
                on_progress=log,
            )
            log(f"Got {len(frames)} frames")

            composed, labels = [], []
            for i, f in enumerate(frames):
                ts = f.timestamp
                label = ts.replace("T", " ").replace("Z", " UTC")
                img = add_geo_overlay(f.image, lat, lon, bbox_deg=bbox_deg,
                                      fronts=detected_fronts)
                img = add_timestamp_label(img, label, f.source, display)
                composed.append(img)
                labels.append(label)
                if i == 0:
                    yield out(image=img, timeline=gr.HTML(value=0, labels_json=json.dumps(labels), frame_count=len(labels)),
                              info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                              sounding=sounding, hodo=hodo, discussion=disc_text,
                              synoptic_label=synoptic_label)

            state["composed_frames"] = composed
            state["frame_labels"] = labels
            log("Done.")
            yield out(image=composed[0] if composed else None,
                      timeline=gr.HTML(value=0, labels_json=json.dumps(labels), frame_count=len(labels)),
                      info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label)

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
            return (None, gr.skip()) + tuple(gr.skip() for _ in range(10))
        return (frames[idx], gr.HTML(value=idx, labels_json=json.dumps(labels), frame_count=len(labels))) + tuple(gr.skip() for _ in range(10))

    TOOLTIP_CSS = """
    <style>
    .wea-tip { position:relative; display:inline-block; cursor:pointer;
               background:#2196F3; color:white; border-radius:50%;
               width:18px; height:18px; text-align:center; font-size:12px;
               line-height:18px; margin-left:8px; vertical-align:middle;
               font-style:italic; font-weight:bold; }
    .wea-tip .wea-tt { visibility:hidden; background:#1a1a2e; color:#ddd;
               border:1px solid #444; border-radius:8px; padding:12px 14px;
               position:absolute; bottom:130%; left:50%; transform:translateX(-50%);
               width:380px; font-size:13px; line-height:1.5; z-index:999;
               box-shadow:0 4px 16px rgba(0,0,0,0.5); font-style:normal;
               font-weight:normal; pointer-events:none; }
    .wea-tip:hover .wea-tt { visibility:visible; }
    .wea-tip .wea-tt::after { content:""; position:absolute; top:100%; left:50%;
               margin-left:-6px; border-width:6px; border-style:solid;
               border-color:#1a1a2e transparent transparent transparent; }
    </style>
    """

    def tip(text):
        safe = text.replace('"', '&quot;').replace("'", "&#39;")
        return f'<span class="wea-tip">i<span class="wea-tt">{safe}</span></span>'

    TIP_SAT = tip(
        "Satellite imagery from GOES, Meteosat, or Himawari. Look for: "
        "rapidly expanding cloud tops (overshooting tops), V-shaped anvils, "
        "back-building cells, and dark shadows indicating tall storms. "
        "Frontal boundaries may be overlaid when enabled."
    )
    TIP_CAPE = tip(
        "CAPE (red fill) = energy available for thunderstorm updrafts. "
        "Values >1000 J/kg suggest strong storms, >2500 J/kg severe potential. "
        "CIN (cyan) = cap strength; high CIN suppresses initiation until a trigger breaks it. "
        "Helicity (magenta) = SRH 0-3km (solid) and 0-1km (dashed); "
        "values >150 m²/s² favor supercells, >300 favor tornadoes."
    )
    TIP_TEMP = tip(
        "Temperature (red) and dewpoint (green) at 2m height over time. "
        "A narrowing spread (T-Td < 3°C) means high moisture. "
        "Watch for rapid dewpoint rises (moisture advection) or sharp temperature drops (outflow boundaries)."
    )
    TIP_WIND = tip(
        "Surface wind speed (cyan) and gusts (red dashed). "
        "Sudden wind shifts or gust spikes can indicate outflow boundaries or frontal passages. "
        "Sustained winds >15 m/s with gusts >25 m/s suggest damaging wind potential."
    )
    TIP_SKEWT = tip(
        "Skew-T Log-P sounding: temperature (red), dewpoint (green), parcel path (yellow dashed). "
        "Red shading = CAPE (positive buoyancy), blue = CIN (cap). "
        "LCL = cloud base, LFC = free convection level, EL = equilibrium level. "
        "Steep lapse rates (temp line tilting far left) and high moisture (green close to red) "
        "indicate strong instability. Wind barbs show speed/direction at each level."
    )
    TIP_HODO = tip(
        "Hodograph shows wind shear profile. Each color = altitude band. "
        "Long, curved traces indicate strong shear favoring supercells. "
        "Clockwise turning (veering) in 0-3km supports right-moving supercells. "
        "RM/LM = Bunkers right/left mover storm motion. "
        "Large area between 0-1km curve and storm motion = high SRH = tornado potential."
    )
    TIP_DISC = tip(
        "Mesoscale discussions from regional weather services (SPC, ESTOFEX, BOM, etc.). "
        "These are expert text analyses of current severe weather potential, "
        "describing expected storm modes, timing, and primary hazards."
    )
    TIP_ALERTS = tip(
        "Active severe weather alerts from global sources. "
        "EXTREME = life-threatening (tornadoes, major hurricanes). "
        "SEVERE = significant damage potential. "
        "Check event type and timing to assess immediate risk."
    )

    with gr.Blocks(title="Weather Event Analyzer") as app:
        gr.Markdown("# Weather Event Analyzer")
        gr.HTML(TOOLTIP_CSS)

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
                fronts_checkbox = gr.Checkbox(label="Show Frontal Analysis (experimental)", value=False)
                resolved = gr.Textbox(label="Resolved", interactive=False)
                fetch_btn = gr.Button("Fetch Data", variant="primary")

            with gr.Column(scale=2):
                gr.HTML(f'<div style="margin-bottom:4px"><b>Satellite Imagery</b> {TIP_SAT}</div>')
                sat_image = gr.Image(label="Satellite Imagery", type="pil", height=520,
                                     show_label=False)
                timeline = gr.HTML(
                    value=0,
                    frame_count=0,
                    labels_json="[]",
                    html_template=TIMELINE_HTML_TEMPLATE,
                    css_template=TIMELINE_CSS,
                    js_on_load=TIMELINE_JS,
                )

        gr.HTML(f'<h3 style="margin:12px 0 4px">Time Series</h3>')
        with gr.Row():
            with gr.Column():
                gr.HTML(f'<div style="margin-bottom:2px"><b>CAPE / CIN / Helicity</b> {TIP_CAPE}</div>')
                cape_plot = gr.Image(label="CAPE / CIN / Helicity", type="pil", show_label=False)
            with gr.Column():
                gr.HTML(f'<div style="margin-bottom:2px"><b>Temperature / Dewpoint</b> {TIP_TEMP}</div>')
                temp_plot = gr.Image(label="Temperature / Dewpoint", type="pil", show_label=False)
            with gr.Column():
                gr.HTML(f'<div style="margin-bottom:2px"><b>Wind Speed</b> {TIP_WIND}</div>')
                wind_plot = gr.Image(label="Wind Speed", type="pil", show_label=False)

        gr.HTML(f'<h3 style="margin:12px 0 4px">Vertical Profile &amp; Analysis</h3>')
        with gr.Row(equal_height=True):
            with gr.Column(scale=1):
                gr.HTML(f'<div style="margin-bottom:2px"><b>Skew-T Sounding</b> {TIP_SKEWT}</div>')
                sounding_plot = gr.Image(label="Skew-T Sounding", type="pil",
                                         show_label=False, height=560)
            with gr.Column(scale=1):
                gr.HTML(f'<div style="margin-bottom:2px"><b>Hodograph</b> {TIP_HODO}</div>')
                hodo_plot = gr.Image(label="Hodograph", type="pil",
                                     show_label=False, height=560)
            with gr.Column(scale=1):
                synoptic_label = gr.Textbox(label="Synoptic Time", interactive=False, lines=1)
                gr.HTML(f'<div style="margin-bottom:2px"><b>Mesoscale Discussion</b> {TIP_DISC}</div>')
                discussion_box = gr.Markdown(
                    label="Mesoscale Discussion", value="",
                    height=500, show_label=False,
                )

        gr.HTML(f'<div style="margin:8px 0 4px"><b>Active Severe Weather Alerts</b> {TIP_ALERTS}</div>')
        alerts_table = gr.Dataframe(
            headers=["Severity", "Event", "Title", "Intensity", "GPS", "Source", "From", "To"],
            label="Active Severe Weather Alerts", wrap=True, show_label=False,
        )
        status_log = gr.Textbox(label="Status Log", lines=6, interactive=False)

        fetch_btn.click(
            fn=fetch_data,
            inputs=[location_input, date_input, start_input, end_input,
                    zoom_slider, product_dd, fronts_checkbox],
            outputs=[sat_image, timeline, cape_plot, temp_plot, wind_plot,
                     resolved, status_log, alerts_table,
                     sounding_plot, hodo_plot, discussion_box, synoptic_label],
            show_progress="hidden",
        )
        timeline.change(
            fn=on_frame_change,
            inputs=[timeline],
            outputs=[sat_image, timeline, cape_plot, temp_plot, wind_plot,
                     resolved, status_log, alerts_table,
                     sounding_plot, hodo_plot, discussion_box, synoptic_label],
            show_progress="hidden",
        )

    return app


def _format_alerts(alerts):
    rows = []
    for a in alerts:
        props = a.properties
        intensity = props.get("intensity_text", "")
        if not intensity or "Magnitude 0" in str(intensity):
            iv = props.get("intensity", "")
            try:
                if iv and float(iv) > 0:
                    intensity = f"{iv} {props.get('intensity_unit', '')}".strip()
                else:
                    intensity = {"flood": "Active flooding", "drought": "Drought"}.get(
                        a.event_type, a.severity.title())
            except (ValueError, TypeError):
                intensity = a.severity.title()
        lat = props.get("lat", "")
        lon = props.get("lon", "")
        geo = a.geometry or {}
        if not lat and geo.get("coordinates"):
            c = geo["coordinates"]
            lon, lat = c[0], c[1]
        gps = f"{lat}, {lon}" if lat and lon else ""
        rows.append([
            a.severity.upper(), a.event_type, a.title[:60],
            str(intensity)[:30], gps, a.source,
            a.valid_from[:16], a.valid_to[:16],
        ])
    return rows
