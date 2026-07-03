# app.py
import json
from pathlib import Path

import io

import gradio as gr
import matplotlib
matplotlib.use("Agg")
from PIL import Image

from core.cache import Cache, default_cache_dir
from core.location import resolve_location, init_cache as init_location_cache
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
    init_location_cache(cache)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient(cache=cache)
    pressure_client = PressureLevelClient(cache=cache)
    discussion_client = MesoscaleDiscussionClient(cache=cache)
    frontal_analyzer = FrontalAnalyzer()

    state = {"composed_frames": [], "frame_labels": []}

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product, show_fronts):
        log_lines = []
        # outputs: image, timeline_html, cape, temp, wind, info, log, alerts, sounding, hodo, discussion, synoptic_label, progress
        empty = (None, "", None, None, None, "", "", [], None, None, "", "", "")

        import logging
        _file_logger = logging.getLogger("wea")
        if not _file_logger.handlers:
            _fh = logging.FileHandler("weather-event-analyzer.log")
            _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
            _file_logger.addHandler(_fh)
            _file_logger.setLevel(logging.INFO)

        def log(msg):
            log_lines.append(msg)
            _file_logger.info(msg)

        def progress_html(text, pct):
            return f'<div style="position:relative;background:#222;border-radius:6px;height:28px;overflow:hidden"><div style="background:#2a7a3a;height:100%;width:{pct}%;transition:width 0.3s"></div><div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;font-weight:bold">{text}</div></div>'

        last_state = list(empty)
        mapping = {"image": 0, "timeline": 1, "cape": 2, "temp": 3, "wind": 4,
                   "info": 5, "alerts": 7, "sounding": 8, "hodo": 9,
                   "discussion": 10, "synoptic_label": 11, "progress": 12}

        def out(**kw):
            for k, v in kw.items():
                last_state[mapping[k]] = v
            last_state[6] = "\n".join(log_lines)
            return tuple(last_state)

        try:
            from datetime import datetime, timedelta
            try:
                from datetime import UTC
                now = datetime.now(UTC).replace(tzinfo=None)
            except ImportError:
                now = datetime.utcnow()

            if not location_str or not location_str.strip():
                log("ERROR: Enter a location"); yield out(); return

            date = (date or "").strip()
            start_utc = (start_utc or "").strip()
            end_utc = (end_utc or "").strip()
            is_live = not date and not start_utc and not end_utc
            date = date or now.strftime("%Y-%m-%d")

            log("Resolving location...")
            yield out(progress=progress_html("Resolving location...", 5))

            lat, lon, display, satellite = resolve_location(location_str)
            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']}")
            yield out(info=info, progress=progress_html("Resolving location...", 5))

            # Probe for latest available satellite frame when using live mode
            if is_live:
                log("Probing for latest satellite data...")
                yield out(info=info, progress=progress_html("Probing latest satellite data...", 8))
                bbox_deg = {0: 40, 1: 30, 2: 20, 3: 10, 4: 5}[int(zoom)]
                latest = sat_client.probe_latest(lat, lon, product=product, bbox_deg=bbox_deg)
                if latest:
                    from datetime import datetime as dt_parse
                    latest_dt = dt_parse.fromisoformat(latest.replace("Z", ""))
                    end_utc = latest_dt.strftime("%H:%M")
                    start_utc = (latest_dt - timedelta(hours=2)).strftime("%H:%M")
                    date = latest_dt.strftime("%Y-%m-%d")
                    log(f"Latest frame: {latest} — window {start_utc}-{end_utc} UTC")
                else:
                    start_utc = (now - timedelta(hours=2)).strftime("%H:%M")
                    end_utc = now.strftime("%H:%M")
                    log("Could not probe latest — using approximate time")
            else:
                start_utc = start_utc or (now - timedelta(hours=2)).strftime("%H:%M")
                end_utc = end_utc or now.strftime("%H:%M")

            def _fig_to_pil(fig):
                buf = io.BytesIO()
                fig.savefig(buf, format='png', bbox_inches='tight',
                            facecolor=fig.get_facecolor(), edgecolor='none')
                import matplotlib.pyplot as plt
                plt.close(fig)
                buf.seek(0)
                return Image.open(buf).convert("RGB")

            # 1. Atmosphere (fast)
            yield out(info=info, progress=progress_html("Fetching atmospheric data...", 15))
            h_before = atmo_client.req.stats.cache_hits
            atmo = atmo_client.fetch(lat, lon, date)
            atmo_tag = "CACHE" if atmo_client.req.stats.cache_hits > h_before else "PULLED"
            log(f"{atmo_tag} atmosphere: {atmo.source} ({len(atmo.timestamps)}h)")
            yield out(info=info, progress=progress_html("Rendering charts...", 25))
            cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin"))
            temp = _fig_to_pil(create_atmosphere_chart(atmo, "temp_dewpoint"))
            wind = _fig_to_pil(create_atmosphere_chart(atmo, "wind"))
            log("Rendered time-series charts")
            yield out(info=info, cape=cape, temp=temp, wind=wind, progress=progress_html("Rendering charts...", 25))

            # 2. Alerts
            yield out(info=info, cape=cape, temp=temp, wind=wind, progress=progress_html("Fetching alerts...", 60))
            sc = start_utc.replace(":", ""); ec = end_utc.replace(":", "")
            mid = str((int(sc) + int(ec)) // 2).zfill(4)
            _alerts_cache_before = cache.stats().get("json", {}).get("count", 0) if cache else 0
            alerts_raw = fetch_alerts(lat, lon, f"{date}T{mid[:2]}:{mid[2:]}:00", cache=cache)
            _alerts_cache_after = cache.stats().get("json", {}).get("count", 0) if cache else 0
            alert_tag = "CACHE" if _alerts_cache_after == _alerts_cache_before else "PULLED"
            log(f"{alert_tag} {len(alerts_raw)} alerts")
            alerts_data = _format_alerts(alerts_raw)
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data, progress=progress_html("Fetching alerts...", 60))

            # 3. Pressure-level data (sounding, hodograph, helicity)
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data, progress=progress_html("Fetching pressure-level data...", 35))
            sc_h = int(start_utc.replace(":", "")[:2]) if start_utc else 12
            synoptic_h = snap_to_synoptic(sc_h)
            synoptic_label = f"Synoptic time: {synoptic_h:02d}Z {date}"

            try:
                h_before = pressure_client.req.stats.cache_hits
                profile = pressure_client.fetch_snapshot(lat, lon, date, sc_h)
                snap_tag = "CACHE" if pressure_client.req.stats.cache_hits > h_before else "PULLED"
                log(f"{snap_tag} pressure snapshot ({synoptic_h:02d}Z)")
                yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data, progress=progress_html("Rendering sounding & hodograph...", 50))
                sounding = _fig_to_pil(create_sounding_chart(profile))
                hodo = _fig_to_pil(create_hodograph_chart(profile))
                log("Rendered sounding & hodograph")
            except Exception as e:
                log(f"Sounding/Hodograph failed: {e}")
                sounding, hodo = None, None

            try:
                h_before = pressure_client.req.stats.cache_hits
                profile_hourly = pressure_client.fetch_hourly(lat, lon, date)
                hourly_tag = "CACHE" if pressure_client.req.stats.cache_hits > h_before else "PULLED"
                log(f"{hourly_tag} pressure hourly")
                cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin", profile_hourly=profile_hourly))
                has_cape = "cape" in atmo.variables
                log("Helicity added to CAPE/CIN chart" + ("" if has_cape else " (CAPE computed from profiles)"))
            except Exception as e:
                log(f"Helicity computation failed: {e}")

            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, synoptic_label=synoptic_label, progress=progress_html("Rendering sounding & hodograph...", 50))

            # 4. Mesoscale discussions
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, synoptic_label=synoptic_label, progress=progress_html("Fetching mesoscale discussions...", 70))
            try:
                h_before = discussion_client.req.stats.cache_hits
                discussions = discussion_client.fetch(lat, lon, date)
                disc_hits = discussion_client.req.stats.cache_hits - h_before
                disc_tag = "CACHE" if disc_hits > 0 else "PULLED"
                disc_text = ""
                for d in discussions:
                    disc_text += f"=== {d.title} ({d.source}) ===\n\n{d.text}\n\n{'─' * 60}\n\n"
                if not disc_text:
                    disc_text = "No mesoscale discussions available for this region."
                log(f"{disc_tag} {len(discussions)} discussion(s)")
            except Exception as e:
                log(f"Discussions failed: {e}")
                disc_text = "Failed to fetch mesoscale discussions."

            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label, progress=progress_html("Fetching mesoscale discussions...", 70))

            # 5. Frontal analysis (optional)
            if not is_live:
                bbox_deg = {0: 40, 1: 30, 2: 20, 3: 10, 4: 5}[int(zoom)]
            detected_fronts = None
            if show_fronts:
                log("Running frontal analysis...")
                yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                          sounding=sounding, hodo=hodo, discussion=disc_text,
                          synoptic_label=synoptic_label, progress=progress_html("Analyzing frontal boundaries...", 75))
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
                      synoptic_label=synoptic_label, progress=progress_html("Loading satellite imagery...", 80))

            frames = sat_client.fetch_event(
                lat=lat, lon=lon, date=date_c, start_utc=sc, end_utc=ec,
                product=product, zoom=int(zoom), bbox_deg=bbox_deg,
                on_progress=log,
            )
            log(f"Got {len(frames)} frames")
            log("Compositing satellite frames...")
            yield out(info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label, progress=progress_html("Compositing satellite frames...", 90))

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
                              synoptic_label=synoptic_label, progress=progress_html("Compositing satellite frames...", 90))

            state["composed_frames"] = composed
            state["frame_labels"] = labels
            log("Done.")
            yield out(image=composed[0] if composed else None,
                      timeline=gr.HTML(value=0, labels_json=json.dumps(labels), frame_count=len(labels)),
                      info=info, cape=cape, temp=temp, wind=wind, alerts=alerts_data,
                      sounding=sounding, hodo=hodo, discussion=disc_text,
                      synoptic_label=synoptic_label, progress=progress_html(f"Ready — {synoptic_label}", 100))

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}\n{traceback.format_exc()}")
            error_bar = f'<div style="position:relative;background:#4a1111;border-radius:6px;height:28px;overflow:hidden"><div style="background:#8b0000;height:100%;width:100%"></div><div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;font-weight:bold">Error: {str(e)[:80]}</div></div>'
            yield out(progress=error_bar)

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
            return (None, gr.skip()) + tuple(gr.skip() for _ in range(11))
        return (frames[idx], gr.HTML(value=idx, labels_json=json.dumps(labels), frame_count=len(labels))) + tuple(gr.skip() for _ in range(11))

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
    #wea-progress { position:fixed !important; top:0 !important; left:0 !important;
                    right:0 !important; z-index:999 !important;
                    padding:0 !important; margin:0 !important; }
    #wea-progress > div { border-radius:0 !important; }
    .gradio-container { padding-top:32px !important; }
    #wea-discussion textarea { height:560px !important; min-height:560px !important; }
    </style>
    """

    def tip(text):
        safe = text.replace('"', '&quot;').replace("'", "&#39;")
        return f'<span class="wea-tip">i<span class="wea-tt">{safe}</span></span>'

    TIP_SAT = tip(
        "Satellite imagery from GOES, Meteosat, or Himawari (geocolor or IR). "
        "Severe weather signs: rapidly expanding cloud tops (overshooting tops), "
        "V-shaped or enhanced-V anvils, back-building cells, and persistent cold cloud tops in IR. "
        "In geocolor: dark shadows cast by tall storms indicate strong updrafts. "
        "Watch for cloud-free slots behind cold fronts and clearing ahead of dry lines."
    )
    TIP_CAPE = tip(
        "Time series of convective energy and shear. "
        "CAPE (red fill): energy for updrafts. >1000 J/kg = strong storms, >2500 = severe potential. "
        "CIN (cyan line): cap inhibiting initiation. High CIN delays storms until a trigger (front, boundary, heating) breaks it. "
        "Rapidly falling CIN + rising CAPE = storms imminent. "
        "Helicity (magenta): SRH 0-3km solid, 0-1km dashed. "
        "SRH >150 m²/s² favors supercells, >300 favors tornadoes."
    )
    TIP_TEMP = tip(
        "Surface temperature (red) and dewpoint (green) at 2m over time. "
        "T-Td spread <3°C = high moisture, supports strong storms. "
        "Rapid dewpoint rise = moisture advection (fuel for storms). "
        "Sharp temperature drop + wind shift = outflow boundary or frontal passage. "
        "Dewpoint pooling (steady high Td) ahead of a boundary is a key severe weather ingredient."
    )
    TIP_WIND = tip(
        "Surface wind speed (cyan) and gusts (red dashed) at 10m. "
        "Wind shift (direction change) + gust spike = outflow boundary, frontal passage, or mesoscale boundary. "
        "Sustained >15 m/s with gusts >25 m/s = damaging wind potential. "
        "Veering winds (shifting clockwise over time) indicate warm air advection and increasing shear."
    )
    TIP_SKEWT = tip(
        "Vertical temperature/moisture profile at the selected synoptic time. "
        "Temperature (red), dewpoint (green), parcel path (yellow dashed). "
        "Red shading = CAPE, blue = CIN. LCL = cloud base, LFC = free convection, EL = storm top. "
        "Cyan dotted = 0°C (freezing level), purple dotted = −20°C (hail growth zone). "
        "Deep layer between 0°C and −20°C = larger hail potential. "
        "SBCAPE/MLCAPE/MUCAPE shown in bottom-left. "
        "Green moist adiabats, red-brown dry adiabats, teal mixing ratio lines labeled in g/kg."
    )
    TIP_HODO = tip(
        "Wind shear profile with color-coded altitude bands and wind barbs by pressure level. "
        "Purple = 0-0.5km, red = 0.5-1km, orange = 1-3km, yellow = 3-6km, green = 6-9km, blue = 9+km. "
        "RM (magenta) / LM (cyan) = Bunkers right/left mover storm motion, MW = mean wind. "
        "Supercell signatures: long curved trace with strong clockwise veering in 0-3km. "
        "Tornado potential: strong 0-1km veering (large purple/red curve bowing away from origin), "
        "SRH 0-1km >150 m²/s², 0-1km bulk shear >20 kt, and RM well separated from the 0-0.5km wind."
    )
    TIP_DISC = tip(
        "Mesoscale discussions from regional weather services (SPC for US, ESTOFEX for Europe, BOM for Australia, etc.). "
        "These are expert text analyses describing current mesoscale environment, expected storm modes, "
        "timing windows, primary hazards (tornado, hail, wind), and watch issuance probability. "
        "Updated every 30 min to 2 hours depending on the source."
    )
    TIP_ALERTS = tip(
        "Active severe weather alerts from global sources (NWS, GDACS, ESTOFEX, IBTrACS, PTWC, SPC, MeteoAlarm). "
        "EXTREME = life-threatening (tornadoes, major hurricanes, tsunamis). "
        "SEVERE = significant damage potential (large hail, damaging winds, flash flooding). "
        "Sorted by severity. Check event type, timing, and distance from your location."
    )

    with gr.Blocks(title="Weather Event Analyzer") as app:
        gr.Markdown("# Weather Event Analyzer")
        gr.HTML(TOOLTIP_CSS)

        progress_bar = gr.HTML(
            value='<div style="position:relative;background:#222;border-radius:6px;height:28px;overflow:hidden"><div style="background:#2a7a3a;height:100%;width:0%;transition:width 0.3s"></div><div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#ccc;font-weight:bold">Ready</div></div>',
            elem_id="wea-progress",
        )

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
                synoptic_label = gr.Textbox(visible=False)
                gr.HTML(f'<div style="margin-bottom:2px"><b>Mesoscale Discussion</b> {TIP_DISC}</div>')
                discussion_box = gr.Textbox(
                    label="Mesoscale Discussion", value="",
                    lines=30, max_lines=100, interactive=False,
                    show_label=False, elem_id="wea-discussion",
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
                     sounding_plot, hodo_plot, discussion_box, synoptic_label,
                     progress_bar],
            show_progress="hidden",
        )
        timeline.change(
            fn=on_frame_change,
            inputs=[timeline],
            outputs=[sat_image, timeline, cape_plot, temp_plot, wind_plot,
                     resolved, status_log, alerts_table,
                     sounding_plot, hodo_plot, discussion_box, synoptic_label,
                     progress_bar],
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
