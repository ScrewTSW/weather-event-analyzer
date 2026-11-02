# app.py
import json
import re
import tempfile
from pathlib import Path

import io

import gradio as gr
import matplotlib
matplotlib.use("Agg")
from PIL import Image

from core.cache import Cache, default_cache_dir
from core.location import resolve_location, init_cache as init_location_cache
from core.satellite import SatelliteClient
from core.models.satellite.source import SOURCES
from core.atmosphere import OpenMeteoClient
from core.alerts import fetch_alerts
from core.visualize import create_atmosphere_chart, add_timestamp_label, add_geo_overlay
from core.pressure_levels import PressureLevelClient, snap_to_synoptic, extract_snapshot
from core.visualize import create_sounding_chart, create_hodograph_chart
from core.visualize import create_timelapse_webp, create_timelapse_gif
from core.discussions import MesoscaleDiscussionClient
from core.fronts import FrontalAnalyzer, find_pressure_extrema


# The notches are part of the template: Gradio re-renders it whenever a prop
# changes (every slider move sets props.value), which wiped JS-built notches.
TIMELINE_HTML_TEMPLATE = """
<div style="background:#1a1a2e;border-radius:8px;padding:8px 12px 4px">
    <div id="tl-notches" style="display:flex;align-items:flex-end">${(() => {
        let labels = [];
        try { labels = JSON.parse(labels_json || '[]'); } catch (e) {}
        const cur = value || 0;
        return labels.map((ts, i) => {
            const parts = ts.replace(' UTC', '').split(' ');
            const time = (parts[parts.length - 1] || '').slice(0, 5);
            const dp = parts.length > 1 ? parts[0].split('-') : [];
            const isCur = i === cur;
            const color = isCur ? '#FFD700' : '#888';
            const date = (isCur || i === 0) && dp.length === 3
                ? `<div style="font-size:9px;color:#555">${dp[1]}/${dp[2]}</div>` : '';
            return `<div style="flex:1;text-align:center">${date}`
                + `<div style="font-size:11px;color:${color};font-weight:${isCur ? 'bold' : 'normal'}">${time}</div>`
                + `<div style="width:2px;height:${isCur ? 16 : 10}px;background:${color};margin:2px auto"></div></div>`;
        }).join('');
    })()}</div>
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
// Delegated on `element`: the slider node is replaced on every re-render
function setFrame(v) {
    if (v === (props.value || 0)) return;
    props.value = v;
    trigger('change', {frame_index: v});
}

element.addEventListener('input', function(e) {
    if (e.target.id === 'tl-slider') setFrame(parseInt(e.target.value));
});

document.addEventListener('keydown', function(e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    var t = e.target;
    // The focused slider steps itself (and fires 'input'); text fields keep their arrows
    if (t && (t.id === 'tl-slider' || t.isContentEditable ||
              /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    var s = element.querySelector('#tl-slider');
    if (!s || s.disabled) return;
    var v = parseInt(s.value) + (e.key === 'ArrowLeft' ? -1 : 1);
    setFrame(Math.max(0, Math.min(parseInt(s.max), v)));
});
"""

# fetch_data output order — must match the fetch_btn.click outputs list
FETCH_OUTPUTS = ("image", "timeline", "cape", "temp", "wind", "info", "log", "alerts",
                 "sounding", "hodo", "discussion", "synoptic_label", "progress")
# Outputs that show the stopwatch placeholder until their first result arrives
PENDING_OUTPUTS = ("image", "cape", "temp", "wind", "sounding", "hodo", "discussion", "alerts")

# Animation export: label -> (extension, resize to px or None, writer).
# GIF is halved — at 2048 px and 256 colours it runs ~4x the size of the WebP.
EXPORT_FORMATS = {
    "WebP (2048 px)": ("webp", None, create_timelapse_webp),
    "GIF (1024 px)": ("gif", 1024, create_timelapse_gif),
}

# Animated stopwatch over an output that is still being prepared: the face
# (crown + dial) is an SVG on ::before, the sweeping hand is ::after.
PENDING_CSS = """
.wea-pending { position:relative; min-height:120px; }
.wea-pending > * { opacity:0.25; transition:opacity 0.3s; }
.wea-pending::before, .wea-pending::after {
    content:""; position:absolute; left:50%; top:50%; z-index:20; pointer-events:none; }
.wea-pending::before {
    width:64px; height:72px; transform:translate(-32px,-40px);
    background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='72' viewBox='0 0 64 72'%3E%3Crect x='27' y='0' width='10' height='6' rx='2' fill='%23FFD700'/%3E%3Crect x='30' y='5' width='4' height='5' fill='%23FFD700'/%3E%3Ccircle cx='32' cy='40' r='27' fill='%231a1a2e' stroke='%23FFD700' stroke-width='4'/%3E%3Cg stroke='%23888' stroke-width='2'%3E%3Cline x1='32' y1='17' x2='32' y2='22'/%3E%3Cline x1='55' y1='40' x2='50' y2='40'/%3E%3Cline x1='32' y1='63' x2='32' y2='58'/%3E%3Cline x1='9' y1='40' x2='14' y2='40'/%3E%3C/g%3E%3Ccircle cx='32' cy='40' r='3' fill='%23FFD700'/%3E%3C/svg%3E") no-repeat; }
.wea-pending::after {
    width:3px; height:20px; margin-left:-1.5px; margin-top:-20px; border-radius:2px;
    background:#FFD700; transform-origin:50% 100%;
    animation:wea-sweep 2s steps(16) infinite; }
@keyframes wea-sweep { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
"""


def _fig_to_pil(fig):
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def create_app(cache_dir: str = None, cache_max_gb: float = 5.0) -> gr.Blocks:
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    cache = Cache(str(cache_dir), max_size_gb=cache_max_gb)
    init_location_cache(cache)
    sat_client = SatelliteClient(cache)
    atmo_client = OpenMeteoClient(cache=cache)
    pressure_client = PressureLevelClient(cache=cache)
    discussion_client = MesoscaleDiscussionClient(cache=cache)
    frontal_analyzer = FrontalAnalyzer()

    state = {"composed_frames": [], "frame_labels": [], "profile_hourly": None,
             "sounding_by_hour": {}, "hodo_by_hour": {},
             "shown_idx": None, "shown_hour": None}

    def fetch_data(location_str, date, start_utc, end_utc, zoom, product, show_fronts):
        log_lines = []

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

        # Only outputs passed to out() are sent; the rest are gr.skip() so
        # charts and 2048 px frames aren't re-sent on every progress tick.
        # Outputs in `pending` show the stopwatch until their first value.
        pending = set(PENDING_OUTPUTS)

        def out(progress=None, clear_pending=False, **kw):
            row = []
            for name in FETCH_OUTPUTS:
                if name in kw:
                    v = kw[name]
                    if name in pending:
                        pending.discard(name)
                        v = gr.update(value=v, elem_classes=[])
                    row.append(v)
                elif name == "log":
                    row.append("\n".join(log_lines))
                elif name == "progress" and progress is not None:
                    row.append(progress)
                elif clear_pending and name in pending:
                    row.append(gr.update(elem_classes=[]))
                else:
                    row.append(gr.skip())
            if clear_pending:
                pending.clear()
            return tuple(row)

        def step(text, pct, **kw):
            return out(progress=progress_html(text, pct), **kw)

        try:
            from datetime import datetime, timedelta
            try:
                from datetime import UTC
                now = datetime.now(UTC).replace(tzinfo=None)
            except ImportError:
                now = datetime.utcnow()

            if not location_str or not location_str.strip():
                log("ERROR: Enter a location"); yield out(); return

            # Clear the previous run and put every output on the stopwatch
            composed, labels = [], []
            state.update(composed_frames=composed, frame_labels=labels,
                         profile_hourly=None, sounding_by_hour={}, hodo_by_hour={},
                         shown_idx=None, shown_hour=None)
            blank = {"image": None, "cape": None, "temp": None, "wind": None,
                     "sounding": None, "hodo": None, "discussion": "", "alerts": []}
            yield tuple(
                gr.update(value=blank[n], elem_classes=["wea-pending"]) if n in blank
                else gr.HTML(value=0, labels_json="[]", frame_count=0) if n == "timeline"
                else "" if n == "log"
                else progress_html("Resolving location...", 5) if n == "progress"
                else gr.skip()
                for n in FETCH_OUTPUTS)

            date = (date or "").strip()
            start_utc = (start_utc or "").strip()
            end_utc = (end_utc or "").strip()
            is_live = not date and not start_utc and not end_utc
            date = date or now.strftime("%Y-%m-%d")

            log("Resolving location...")
            lat, lon, display, satellite = resolve_location(location_str)
            info = f"{display}\n{satellite['name']} ({satellite['slider_id']})"
            log(f"Location: {display}")
            log(f"Satellite: {satellite['name']}")
            yield step("Resolving location...", 5, info=info)

            bbox_deg = {0: 40, 1: 30, 2: 20, 3: 10, 4: 5}[int(zoom)]

            # Probe for latest available satellite frame when using live mode
            if is_live:
                log("Probing for latest satellite data...")
                yield step("Probing latest satellite data...", 8)
                from core.models.satellite.source import select_source as _select_src
                _sat_source = _select_src(lat, lon, product)
                latest = sat_client.probe_latest(_sat_source, lat, lon, bbox_deg=bbox_deg, on_progress=log)
                if latest:
                    from datetime import datetime as dt_parse
                    latest_dt = dt_parse.fromisoformat(latest.replace("Z", ""))
                    end_utc = latest_dt.strftime("%H:%M")
                    start_utc = (latest_dt - timedelta(hours=3)).strftime("%H:%M")
                    date = latest_dt.strftime("%Y-%m-%d")
                    log(f"Latest frame: {latest} — window {start_utc}-{end_utc} UTC")
                else:
                    start_utc = (now - timedelta(hours=3)).strftime("%H:%M")
                    end_utc = now.strftime("%H:%M")
                    log("Could not probe latest — using approximate time")
            else:
                start_utc = start_utc or (now - timedelta(hours=2)).strftime("%H:%M")
                end_utc = end_utc or now.strftime("%H:%M")

            # 1. Atmosphere (fast) — each chart is sent as soon as it renders
            yield step("Fetching atmospheric data...", 12)
            h_before = atmo_client.req.stats.cache_hits
            atmo = atmo_client.fetch(lat, lon, date)
            atmo_tag = "CACHE" if atmo_client.req.stats.cache_hits > h_before else "PULLED"
            log(f"{atmo_tag} atmosphere: {atmo.source} ({len(atmo.timestamps)}h)")
            yield step("Rendering CAPE / CIN...", 15)
            cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin"))
            yield step("Rendering temperature / dewpoint...", 18, cape=cape)
            yield step("Rendering wind...", 21,
                       temp=_fig_to_pil(create_atmosphere_chart(atmo, "temp_dewpoint")))
            yield step("Fetching pressure-level data...", 24,
                       wind=_fig_to_pil(create_atmosphere_chart(atmo, "wind")))
            log("Rendered time-series charts")

            sc = start_utc.replace(":", ""); ec = end_utc.replace(":", "")
            place = re.sub(r"[^A-Za-z0-9]+", "-", display.split(",")[0]).strip("-") or "location"
            state["export_name"] = f"{place}_{date}_{sc}-{ec}UTC"

            # 2. Pressure-level data (sounding, hodograph, helicity)
            sc_h = int(sc[:2]) if sc else 12
            synoptic_h = snap_to_synoptic(sc_h)
            synoptic_label = f"Sounding time: {synoptic_h:02d}:00 UTC"

            try:
                h_before = pressure_client.req.stats.cache_hits
                profile_hourly = pressure_client.fetch_hourly(lat, lon, date)
                hourly_tag = "CACHE" if pressure_client.req.stats.cache_hits > h_before else "PULLED"
                log(f"{hourly_tag} pressure hourly ({len(profile_hourly.timestamps)}h)")
                state["profile_hourly"] = profile_hourly

                hours = []
                for i, ts in enumerate(profile_hourly.timestamps):
                    try:
                        hours.append((int(ts.split("T")[1].split(":")[0]), i))
                    except (ValueError, IndexError):
                        continue
                # Synoptic hour first so it is on screen while the rest render
                hours.sort(key=lambda hi: hi[0] != synoptic_h)

                sounding_by_hour = state["sounding_by_hour"]
                hodo_by_hour = state["hodo_by_hour"]
                for n, (h, i) in enumerate(hours):
                    profile = extract_snapshot(profile_hourly, i)
                    sounding_by_hour[h] = _fig_to_pil(create_sounding_chart(profile))
                    if n == 0:
                        state["shown_hour"] = h
                        synoptic_label = f"Sounding time: {h:02d}:00 UTC"
                        yield step("Rendering hodograph...", 30,
                                   sounding=sounding_by_hour[h], synoptic_label=synoptic_label)
                    hodo_by_hour[h] = _fig_to_pil(create_hodograph_chart(profile))
                    if n == 0:
                        yield step("Adding helicity to CAPE / CIN...", 33, hodo=hodo_by_hour[h])
                        cape = _fig_to_pil(create_atmosphere_chart(atmo, "cape_cin", profile_hourly=profile_hourly))
                        has_cape = "cape" in atmo.variables
                        log("Helicity added to CAPE/CIN chart" + ("" if has_cape else " (CAPE computed from profiles)"))
                        yield step(f"Pre-rendering soundings 1/{len(hours)}...", 36, cape=cape)
                    else:
                        yield step(f"Pre-rendering soundings {n + 1}/{len(hours)}...",
                                   36 + 24 * (n + 1) // len(hours))
                log(f"Pre-rendered {len(sounding_by_hour)} soundings & hodographs")
            except Exception as e:
                log(f"Sounding/Hodograph failed: {e}")
            # Stop the stopwatch even if nothing rendered
            yield step("Fetching mesoscale discussions...", 62,
                       **{k: None for k in ("sounding", "hodo") if k in pending})

            # 3. Mesoscale discussions
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
            yield step("Detecting pressure centers...", 68, discussion=disc_text)

            # 4. Frontal analysis + pressure extrema
            detected_fronts = None
            pressure_extrema = None
            if show_fronts:
                log("Running frontal analysis...")
                yield step("Analyzing frontal boundaries...", 70)
                try:
                    detected_fronts = frontal_analyzer.fetch_and_analyze(
                        lat, lon, date, run_hour=f"{synoptic_h:02d}",
                        bbox_deg=bbox_deg)
                    n_fronts = len(detected_fronts)
                    log(f"Detected {n_fronts} frontal feature(s)")
                except Exception as e:
                    log(f"Frontal analysis failed: {e}")

            log("Detecting pressure centers...")
            try:
                pressure_extrema = find_pressure_extrema(
                    lat, lon, date, hour=synoptic_h, bbox_deg=bbox_deg)
                log(f"Found {len(pressure_extrema)} pressure center(s)")
            except Exception as e:
                log(f"Pressure extrema failed: {e}")

            # 5. Satellite imagery — each frame is composited and shown as it
            #    arrives, and the timeline grows a node per frame
            date_c = date.replace("-", "")
            log(f"Fetching satellite imagery (bbox={bbox_deg}°)...")
            yield step("Loading satellite imagery...", 72)
            source, timestamps = sat_client.plan_event(
                lat=lat, lon=lon, date=date_c, start_utc=sc, end_utc=ec,
                product=product, bbox_deg=bbox_deg, on_progress=log)

            # Coastlines/borders rendered once (on the first frame), composited onto each
            from core.visualize import render_geo_overlay
            geo_overlay = None
            for f in sat_client.iter_frames(source, timestamps, lat, lon, zoom=int(zoom),
                                            bbox_deg=bbox_deg, on_progress=log):
                if geo_overlay is None:
                    log("Rendering geo overlay...")
                    geo_overlay = render_geo_overlay(
                        f.image.width, f.image.height,
                        lat, lon, bbox_deg=bbox_deg, fronts=detected_fronts,
                        pressure_extrema=pressure_extrema)
                    log(f"Geo overlay rendered ({geo_overlay.width}x{geo_overlay.height})")
                label = f.timestamp.replace("T", " ").replace("Z", " UTC")
                img = Image.alpha_composite(f.image.convert("RGBA"), geo_overlay)
                img = add_timestamp_label(img, label, f.source, display)
                composed.append(img)
                labels.append(label)
                idx = len(composed) - 1
                state["shown_idx"] = idx  # the timeline.change this triggers must not resend it
                done = timestamps.index(f.timestamp) + 1
                yield step(f"Satellite frames {done}/{len(timestamps)}...",
                           72 + 23 * done // len(timestamps),
                           image=img,
                           timeline=gr.HTML(value=idx, labels_json=json.dumps(labels),
                                            frame_count=len(labels)))

            # 6. Alerts (last — slowest due to multiple external sources)
            log("Fetching alerts...")
            yield step("Fetching alerts...", 95, **({"image": None} if "image" in pending else {}))
            mid = str((int(sc) + int(ec)) // 2).zfill(4)
            _alerts_cache_before = cache.stats().get("json", {}).get("count", 0) if cache else 0
            alerts_raw = fetch_alerts(lat, lon, f"{date}T{mid[:2]}:{mid[2:]}:00", cache=cache)
            _alerts_cache_after = cache.stats().get("json", {}).get("count", 0) if cache else 0
            alert_tag = "CACHE" if _alerts_cache_after == _alerts_cache_before else "PULLED"
            log(f"{alert_tag} {len(alerts_raw)} alerts")

            log("Done.")
            yield step(f"Ready — {synoptic_label}", 100, alerts=_format_alerts(alerts_raw))

        except Exception as e:
            import traceback
            log_lines.append(f"ERROR: {e}\n{traceback.format_exc()}")
            error_bar = f'<div style="position:relative;background:#4a1111;border-radius:6px;height:28px;overflow:hidden"><div style="background:#8b0000;height:100%;width:100%"></div><div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;font-weight:bold">Error: {str(e)[:80]}</div></div>'
            yield out(progress=error_bar, clear_pending=True)

    def on_frame_change(timeline_value, evt: gr.EventData = None):
        """Called when the timeline slider triggers 'change' via JS trigger().

        Also fires when fetch_data moves the timeline while streaming frames,
        so anything already on screen is skipped instead of re-sent.
        """
        skip = gr.skip()
        try:
            idx = int(timeline_value) if timeline_value is not None else 0
        except (ValueError, TypeError):
            idx = 0
        frames = state["composed_frames"]
        labels = state["frame_labels"]
        if not frames or idx >= len(frames):
            return (skip,) * 4

        frame_out = skip if idx == state["shown_idx"] else frames[idx]
        state["shown_idx"] = idx

        sounding_map = state.get("sounding_by_hour", {})
        hodo_map = state.get("hodo_by_hour", {})
        if sounding_map and idx < len(labels):
            parts = labels[idx].strip().split(" ")
            time_part = parts[-2] if len(parts) >= 2 else "12:00"
            try:
                frame_h = int(time_part.split(":")[0])
            except (ValueError, IndexError):
                frame_h = 12

            best_h = min(sounding_map, key=lambda h: abs(h - frame_h))
            if best_h != state["shown_hour"] and best_h in hodo_map:
                state["shown_hour"] = best_h
                return (frame_out, sounding_map[best_h], hodo_map[best_h],
                        f"Sounding time: {best_h:02d}:00 UTC")

        return (frame_out, skip, skip, skip)

    def export_animation(fmt):
        """Write the composed satellite frames as an animated WebP or GIF for download."""
        frames = list(state["composed_frames"])
        if len(frames) < 2:
            gr.Warning("Fetch at least two satellite frames first.")
            return gr.update(value=None, visible=False)
        ext, size, writer = EXPORT_FORMATS[fmt]
        if size:
            frames = [f.resize((size, size), Image.LANCZOS) for f in frames]
        # One reused directory — Gradio copies the file into its own cache on return
        export_dir = Path(tempfile.gettempdir()) / "wea-export"
        export_dir.mkdir(exist_ok=True)
        for old in export_dir.glob("*"):
            old.unlink(missing_ok=True)
        path = export_dir / f"{state.get('export_name', 'satellite')}.{ext}"
        writer(frames, str(path), fps=4)
        return gr.update(value=str(path), visible=True)

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
    #wea-fixed-bar { position:fixed !important; top:0 !important; left:0 !important;
                     right:0 !important; z-index:999 !important;
                     padding:0 !important; margin:0 !important;
                     background:#111 !important; gap:0 !important;
                     border-bottom:1px solid #333; }
    #wea-fixed-bar > div { gap:0 !important; padding:0 !important; margin:0 !important; }
    #wea-progress { padding:0 !important; margin:0 !important; min-height:0 !important; }
    #wea-progress > div { border-radius:0 !important; padding:0 !important; margin:0 !important; }
    #wea-timeline { padding:0 !important; margin:0 !important; min-height:0 !important; }
    #wea-timeline > div { padding:0 4px !important; margin:0 !important; }
    .gradio-container { padding-top:64px !important; }
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
        gr.HTML(TOOLTIP_CSS + f"<style>{PENDING_CSS}</style>")

        with gr.Column(elem_id="wea-fixed-bar"):
            progress_bar = gr.HTML(
                value='<div style="position:relative;background:#222;border-radius:6px;height:28px;overflow:hidden"><div style="background:#2a7a3a;height:100%;width:0%;transition:width 0.3s"></div><div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#ccc;font-weight:bold">Ready</div></div>',
                elem_id="wea-progress",
            )
            timeline = gr.HTML(
                value=0,
                frame_count=0,
                labels_json="[]",
                html_template=TIMELINE_HTML_TEMPLATE,
                css_template=TIMELINE_CSS,
                js_on_load=TIMELINE_JS,
                elem_id="wea-timeline",
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
                with gr.Row(equal_height=True):
                    export_fmt = gr.Radio(list(EXPORT_FORMATS), value="WebP (2048 px)",
                                          show_label=False, container=False, scale=2)
                    export_btn = gr.Button("Export animation", size="sm", scale=1)
                export_file = gr.File(label="Animation", visible=False, interactive=False)

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
            outputs=[sat_image, sounding_plot, hodo_plot, synoptic_label],
            show_progress="hidden",
        )
        export_btn.click(
            fn=export_animation,
            inputs=[export_fmt],
            outputs=[export_file],
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
