"""fetch_data streaming: stopwatch placeholders, per-chart yields, per-frame satellite."""
import tempfile
from unittest.mock import patch, MagicMock

import gradio as gr
from PIL import Image

import app as app_module
from app import FETCH_OUTPUTS, PENDING_OUTPUTS
from core.models.satellite.base import SatelliteFrame

HOURS = ["2026-11-02T12:00", "2026-11-02T15:00", "2026-11-02T18:00"]
FRAME_TS = ["2026-11-02T15:00:00Z", "2026-11-02T15:10:00Z", "2026-11-02T17:50:00Z"]


def _client(**attrs):
    c = MagicMock()
    c.req.stats.cache_hits = 0
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


def _is_skip(v):
    return isinstance(v, dict) and v == {"__type__": "update"}


def _sent(v):
    """Value actually delivered to the browser (None if skipped)."""
    if _is_skip(v):
        return None
    if isinstance(v, dict) and v.get("__type__") == "update":
        return v.get("value")
    return v


def _run_fetch(args=("Wichita", "2026-11-02", "15:00", "15:20"), latest=None, mocks=None):
    """Run fetch_data with every external dependency mocked; return yields + mocks.
    `latest` is what the live-mode probe finds; `mocks` collects the client mocks."""
    sat = _client()
    sat.probe_latest.return_value = latest
    source = MagicMock()
    sat.plan_event.return_value = (source, FRAME_TS)
    sat.iter_frames.side_effect = lambda *a, **k: iter(
        SatelliteFrame(source="GOES-East", timestamp=ts,
                       image=Image.new("RGBA", (32, 32), (60 * n, 90, 90, 255)),
                       layer="x", bbox=(0, 1, 0, 1))
        for n, ts in enumerate(FRAME_TS))

    profile_hourly = MagicMock(timestamps=HOURS, station=None)
    pressure = _client()
    pressure.fetch_hourly.return_value = profile_hourly
    atmo = _client()
    atmo.fetch.return_value = MagicMock(source="open-meteo", timestamps=HOURS, variables={})
    disc = _client()
    disc.fetch.return_value = []
    if mocks is not None:
        mocks.update(sat=sat, atmo=atmo, pressure=pressure, disc=disc)

    def chart(kind):
        return lambda *a, **k: (kind, a, k)

    snapshots = []
    with patch.object(app_module, "SatelliteClient", return_value=sat), \
         patch.object(app_module, "OpenMeteoClient", return_value=atmo), \
         patch.object(app_module, "PressureLevelClient", return_value=pressure), \
         patch.object(app_module, "MesoscaleDiscussionClient", return_value=disc), \
         patch.object(app_module, "resolve_location",
                      return_value=(37.69, -97.33, "Wichita", {"name": "GOES-East", "slider_id": "goes"})), \
         patch.object(app_module, "create_atmosphere_chart", side_effect=lambda a, kind, **k: (kind, k)), \
         patch.object(app_module, "create_sounding_chart", side_effect=lambda p: ("sounding", p)), \
         patch.object(app_module, "create_hodograph_chart", side_effect=lambda p: ("hodo", p)), \
         patch.object(app_module, "extract_snapshot",
                      side_effect=lambda ph, i: snapshots.append(i) or i), \
         patch.object(app_module, "_fig_to_pil", side_effect=lambda fig: fig), \
         patch.object(app_module, "find_pressure_extrema", return_value=[]), \
         patch.object(app_module, "fetch_alerts", return_value=[]), \
         patch.object(app_module, "add_timestamp_label", side_effect=lambda img, *a: img), \
         patch("core.visualize.render_geo_overlay",
               return_value=Image.new("RGBA", (32, 32), (0, 0, 0, 0))):
        blocks = app_module.create_app(tempfile.mkdtemp())
        fns = {f.fn.__name__: f.fn for f in blocks.fns.values()}
        rows = list(fns["fetch_data"](*args, 2, "auto", False))
    return rows, snapshots, fns["on_frame_change"], fns["export_animation"]


def _column(rows, name):
    i = FETCH_OUTPUTS.index(name)
    return [r[i] for r in rows]


class TestFetchDataStreaming:
    def test_every_output_starts_on_the_stopwatch(self):
        rows, *_ = _run_fetch()
        first = dict(zip(FETCH_OUTPUTS, rows[0]))
        for name in PENDING_OUTPUTS:
            assert first[name]["elem_classes"] == ["wea-pending"], name

    def test_every_stopwatch_is_cleared_exactly_once(self):
        rows, *_ = _run_fetch()
        for name in PENDING_OUTPUTS:
            cleared = [v for v in _column(rows, name)
                       if isinstance(v, dict) and v.get("elem_classes") == []]
            assert len(cleared) == 1, name

    def test_charts_are_not_resent_on_every_progress_tick(self):
        rows, *_ = _run_fetch()
        sent = {n: [_sent(v) for v in _column(rows, n) if not _is_skip(v)][1:]  # [0] = reset
                for n in ("cape", "temp", "wind", "discussion", "alerts")}
        assert [k for k, _ in sent["cape"]] == ["cape_cin", "cape_cin"]  # plain, then + SRH
        assert "profile_hourly" in sent["cape"][1][1]
        assert len(sent["temp"]) == 1 and len(sent["wind"]) == 1
        assert len(sent["discussion"]) == 1 and len(sent["alerts"]) == 1

    def test_each_chart_arrives_in_its_own_yield(self):
        rows, *_ = _run_fetch()
        first_seen = {}
        for n, row in enumerate(rows[1:], start=1):
            for name, v in zip(FETCH_OUTPUTS, row):
                if name in ("cape", "temp", "wind", "sounding", "hodo") and not _is_skip(v):
                    first_seen.setdefault(name, n)
        assert len(set(first_seen.values())) == 5

    def test_only_hours_the_frames_need_are_rendered(self):
        _, snapshots, *_ = _run_fetch()
        # Window start 15:00 -> 15 UTC (index 1) first; 17:50 frame -> 18 UTC (index 2).
        # 12 UTC is never rendered; each hour renders sounding + hodo exactly once.
        assert snapshots == [1, 1, 2, 2]

    def test_sounding_follows_streamed_frames_when_hour_changes(self):
        rows, *_ = _run_fetch()
        sat = [i for i, v in enumerate(_column(rows, "image")) if not _is_skip(v)][1:]
        sent = [not _is_skip(rows[i][FETCH_OUTPUTS.index("sounding")]) for i in sat]
        assert sent == [False, False, True]
        assert _sent(rows[sat[-1]][FETCH_OUTPUTS.index("synoptic_label")]) == "Sounding time: 18:00 UTC"

    def test_satellite_frames_stream_one_per_yield(self):
        rows, *_ = _run_fetch()
        images = [v for v in _column(rows, "image")[1:] if not _is_skip(v)]
        timelines = [v for v in _column(rows, "timeline")[1:] if not _is_skip(v)]
        assert len(images) == len(FRAME_TS)
        assert [t.props["frame_count"] for t in timelines] == [1, 2, 3]
        assert [t.value for t in timelines] == [0, 1, 2]

    def test_ends_ready(self):
        rows, *_ = _run_fetch()
        assert "Ready" in rows[-1][FETCH_OUTPUTS.index("progress")]


class TestFrameChangeDedup:
    def test_timeline_echo_of_streamed_frame_is_skipped(self):
        _, _, on_frame_change, _ = _run_fetch()
        # fetch_data finished on frame 2; the timeline.change it caused must not resend it
        assert _is_skip(on_frame_change(2)[0])

    def test_scrubbing_sends_frame_and_sounding_only_when_hour_changes(self):
        _, snapshots, on_frame_change, _ = _run_fetch()
        back = on_frame_change(0)
        assert not _is_skip(back[0])
        assert not _is_skip(back[1]) and not _is_skip(back[2])  # 18 UTC -> 15 UTC sounding
        # Frames 0 and 1 are both closest to 15 UTC — no sounding re-sent
        again = on_frame_change(1)
        assert not _is_skip(again[0])
        assert _is_skip(again[1]) and _is_skip(again[2])
        assert snapshots == [1, 1, 2, 2]  # scrubbing reuses rendered soundings


class TestExportAnimation:
    def test_webp_holds_every_streamed_frame(self):
        *_, export_animation = _run_fetch()
        out = export_animation("WebP (2048 px)")
        assert out["visible"] and out["value"].endswith("Wichita_2026-11-02_1500-1520UTC.webp")
        im = Image.open(out["value"])
        assert im.format == "WEBP" and im.n_frames == len(FRAME_TS)

    def test_gif_is_downscaled(self):
        *_, export_animation = _run_fetch()
        im = Image.open(export_animation("GIF (1024 px)")["value"])
        assert im.format == "GIF" and im.size == (1024, 1024) and im.n_frames == len(FRAME_TS)


class TestLiveWindow:
    def test_window_across_midnight_starts_on_the_previous_day(self):
        """Latest frame 00:30 UTC -> window 21:30 the day before to 00:30, not 21:30 that
        day to 00:30 the next (a window in the future: every probe empty)."""
        mocks = {}
        _run_fetch(args=("Wichita", "", "", ""), latest="2026-11-03T00:30:00Z", mocks=mocks)
        kw = mocks["sat"].plan_event.call_args.kwargs
        assert (kw["date"], kw["start_utc"], kw["end_utc"]) == ("20261102", "2130", "0030")
        assert mocks["atmo"].fetch.call_args.args[2] == "2026-11-02"
        assert mocks["pressure"].fetch_hourly.call_args.args[2] == "2026-11-02"

    def test_window_within_one_day_keeps_its_date(self):
        mocks = {}
        _run_fetch(args=("Wichita", "", "", ""), latest="2026-11-03T18:30:00Z", mocks=mocks)
        kw = mocks["sat"].plan_event.call_args.kwargs
        assert (kw["date"], kw["start_utc"], kw["end_utc"]) == ("20261103", "1530", "1830")
