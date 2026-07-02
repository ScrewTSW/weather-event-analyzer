# cli.py
import argparse
import sys
from pathlib import Path

from core.cache import Cache, default_cache_dir
from core.location import resolve_location
from core.satellite import SatelliteClient
from core.atmosphere import OpenMeteoClient
from core.visualize import compose_frame, create_atmosphere_chart, create_timelapse_gif, create_timelapse_mp4
from core.pressure_levels import PressureLevelClient, snap_to_synoptic
from core.visualize import create_sounding_chart, create_hodograph_chart


def add_subparsers(subparsers):
    # satellite subcommand
    sat_parser = subparsers.add_parser("satellite", help="Download satellite imagery")
    sat_parser.add_argument("--location", required=True, help="City name or lat,lon")
    sat_parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    sat_parser.add_argument("--start", required=True, help="Start time UTC (HHMM or HH:MM)")
    sat_parser.add_argument("--end", required=True, help="End time UTC (HHMM or HH:MM)")
    sat_parser.add_argument("--zoom", type=int, default=2, help="Zoom level 0-4")
    sat_parser.add_argument("--product", default="geocolor", help="SLIDER product")
    sat_parser.add_argument("--bbox", type=float, default=5.0, help="Bounding box degrees")
    sat_parser.add_argument("--output", default="./output", help="Output directory")

    # atmosphere subcommand
    atmo_parser = subparsers.add_parser("atmosphere", help="Fetch atmosphere data")
    atmo_parser.add_argument("--location", help="City name or lat,lon")
    atmo_parser.add_argument("--lat", type=float, help="Latitude")
    atmo_parser.add_argument("--lon", type=float, help="Longitude")
    atmo_parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    atmo_parser.add_argument("--output", default="./output", help="Output directory")

    # all subcommand
    all_parser = subparsers.add_parser("all", help="Fetch everything and generate visualizations")
    all_parser.add_argument("--location", required=True, help="City name or lat,lon")
    all_parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    all_parser.add_argument("--start", required=True, help="Start time UTC")
    all_parser.add_argument("--end", required=True, help="End time UTC")
    all_parser.add_argument("--zoom", type=int, default=2, help="Zoom level 0-4")
    all_parser.add_argument("--product", default="geocolor", help="SLIDER product")
    all_parser.add_argument("--output", default="./output", help="Output directory")

    # alerts subcommand
    alerts_parser = subparsers.add_parser("alerts", help="Fetch severe weather alerts")
    alerts_parser.add_argument("--location", required=True, help="City name or lat,lon")
    alerts_parser.add_argument("--date", help="Date YYYY-MM-DD (default: now)")
    alerts_parser.add_argument("--time", help="Time HH:MM UTC (default: now)")


def run_cli(args):
    cache = Cache(str(default_cache_dir()), max_size_gb=getattr(args, "cache_max", 5.0))

    if args.command == "satellite":
        _run_satellite(args, cache)
    elif args.command == "atmosphere":
        _run_atmosphere(args, cache)
    elif args.command == "all":
        _run_all(args, cache)
    elif args.command == "alerts":
        _run_alerts(args, cache)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


def _run_satellite(args, cache):
    lat, lon, display, satellite = resolve_location(args.location)
    print(f"Location: {display}")
    print(f"Satellite: {satellite['name']}")

    client = SatelliteClient(cache)
    date_compact = args.date.replace("-", "")
    start = args.start.replace(":", "")
    end = args.end.replace(":", "")

    frames = client.fetch_event(
        satellite=satellite, product=args.product,
        date=date_compact, start_utc=start, end_utc=end,
        lat=lat, lon=lon, zoom=args.zoom,
        bbox_deg=getattr(args, "bbox", 5.0),
        on_progress=lambda msg: print(f"  {msg}"),
    )

    output_dir = Path(args.output) / f"{display.replace(' ', '_').replace(',', '')}_{args.date}"
    output_dir.mkdir(parents=True, exist_ok=True)

    composed_frames = []
    for frame in frames:
        ts = frame["timestamp"]
        ts_str = str(ts)
        ts_formatted = f"{ts_str[:4]}-{ts_str[4:6]}-{ts_str[6:8]} {ts_str[8:10]}:{ts_str[10:12]} UTC"
        composed = compose_frame(
            satellite_image=frame["image"],
            timestamp=ts_formatted,
            satellite_name=satellite["name"],
            location_str=display,
            center_x=frame["image"].width // 2,
            center_y=frame["image"].height // 2,
        )
        path = output_dir / f"satellite_{ts}.png"
        composed.save(path)
        composed_frames.append(composed)
        print(f"  Saved: {path}")

    if composed_frames:
        gif_path = output_dir / "timelapse.gif"
        create_timelapse_gif(composed_frames, str(gif_path))
        print(f"  Timelapse GIF: {gif_path}")

        mp4_path = output_dir / "timelapse.mp4"
        create_timelapse_mp4(composed_frames, str(mp4_path))
        print(f"  Timelapse MP4: {mp4_path}")

    print(f"Done. {len(frames)} frames saved to {output_dir}")


def _run_atmosphere(args, cache):
    if args.location:
        lat, lon, display, _ = resolve_location(args.location)
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        display = f"{lat}, {lon}"
    else:
        print("Error: provide --location or --lat/--lon", file=sys.stderr)
        sys.exit(1)

    print(f"Location: {display}")

    client = OpenMeteoClient()
    data = client.fetch(lat, lon, args.date)
    print(f"Source: {data['source']}")
    print(f"Timestamps: {len(data['timestamps'])}")

    output_dir = Path(args.output) / f"{display.replace(' ', '_').replace(',', '')}_{args.date}"
    output_dir.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")

    for chart_type in ["cape_cin", "temp_dewpoint", "wind"]:
        try:
            fig = create_atmosphere_chart(data, chart_type)
            path = output_dir / f"chart_{chart_type}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            print(f"  Saved: {path}")
        except Exception as e:
            print(f"  Skipped {chart_type}: {e}")

    # Pressure-level charts
    pressure_client = PressureLevelClient()
    try:
        profile = pressure_client.fetch_snapshot(lat, lon, args.date, 12)
        for name, fn in [("sounding", create_sounding_chart), ("hodograph", create_hodograph_chart)]:
            try:
                fig = fn(profile)
                path = output_dir / f"chart_{name}.png"
                fig.savefig(path, dpi=150, bbox_inches="tight")
                print(f"  Saved: {path}")
            except Exception as e:
                print(f"  Skipped {name}: {e}")
    except Exception as e:
        print(f"  Pressure-level data failed: {e}")

    print(f"Done. Charts saved to {output_dir}")


def _run_all(args, cache):
    args.bbox = getattr(args, "bbox", 5.0)
    _run_satellite(args, cache)
    _run_atmosphere(args, cache)


def _run_alerts(args, cache):
    lat, lon, display, _ = resolve_location(args.location)
    print(f"Location: {display}")

    from core.alerts import fetch_alerts
    from datetime import datetime, UTC

    if args.date and args.time:
        dt_str = f"{args.date}T{args.time}:00"
    elif args.date:
        dt_str = f"{args.date}T12:00:00"
    else:
        dt_str = datetime.now(UTC).replace(tzinfo=None).isoformat()

    print(f"Querying alerts for: {dt_str}")
    alerts = fetch_alerts(lat, lon, dt_str)

    if not alerts:
        print("No active alerts found.")
        return

    print(f"\n{'='*80}")
    print(f"Found {len(alerts)} alerts:")
    print(f"{'='*80}")
    for a in alerts:
        severity = a['severity'].upper()
        marker = {'EXTREME': '!!!', 'SEVERE': '!! ', 'MODERATE': '!  ', 'MINOR': '   '}.get(severity, '   ')
        print(f"\n{marker} [{severity}] {a['event_type']}")
        print(f"    {a['title']}")
        if a.get('valid_from'):
            print(f"    From: {a['valid_from'][:19]}  To: {a.get('valid_to', 'ongoing')[:19]}")
        print(f"    Source: {a.get('source', 'unknown')}")
