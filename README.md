# Weather Event Analyzer

Real-time severe weather analysis tool combining satellite imagery, atmospheric soundings, hodographs, frontal detection, mesoscale discussions, and global alerts into a single dashboard.

## Features

- **Satellite Imagery** — GOES-East/West (geocolor + IR), Himawari, Meteosat, MODIS/VIIRS via NASA GIBS
- **Skew-T Log-P Sounding** — Temperature/dewpoint profiles, CAPE/CIN shading, wind barbs in knots, LCL/LFC/EL/SFC markers, 0/−20°C isotherms, SBCAPE/MLCAPE/MUCAPE values
- **Hodograph** — Height-colored wind trace with cubic spline interpolation, Bunkers RM/LM/MW storm motion vectors, wind barb column with pressure labels
- **Helicity** — SRH 0-1km and 0-3km on the CAPE/CIN time-series chart
- **Frontal Detection** — Algorithmic cold/warm/dry front detection with wind-shift verification (experimental)
- **Mesoscale Discussions** — SPC, ESTOFEX, BOM, ECCC, and 10+ global sources auto-selected by location
- **Global Alerts** — GDACS, NWS, ESTOFEX, IBTrACS, PTWC, SPC reports, MeteoAlarm
- **Tooltip Help** — On-hover explanations for each chart section on how to interpret severe weather signals

## Architecture

```
core/
├── api_request.py       # CachedRequest — centralized HTTP with per-domain rate limits
├── cache.py             # SQLite-backed cache (metadata DB + files on disk)
├── models/              # Typed dataclass DTOs (per-domain, per-source)
│   ├── base.py          # BaseDataResult
│   ├── atmosphere/      # AtmosphereData, OpenMeteoForecast/Archive
│   ├── pressure_levels/ # PressureLevelSnapshot/Hourly
│   ├── discussion/      # Discussion, SPCMesoscaleDiscussion, ESTOFEXDiscussion, ...
│   ├── fronts/          # FrontalFeature
│   ├── alerts/          # Alert, SourceResult
│   └── satellite/       # SatelliteFrame
├── atmosphere.py        # Open-Meteo surface data client
├── pressure_levels.py   # Open-Meteo pressure-level client (19 levels, 30-1000 hPa)
├── satellite.py         # NASA GIBS WMS/WMTS tile fetcher
├── fronts.py            # TFP-based frontal detection from gridded data
├── discussions.py       # Mesoscale discussion aggregator (15+ global sources)
├── alerts.py            # Multi-source severe weather alerts
├── visualize.py         # All chart rendering (Skew-T, hodograph, time-series, overlays)
├── location.py          # Geocoding (Nominatim) + satellite source selection
└── projection.py        # Geostationary projection math

app.py                   # Gradio web UI
cli.py                   # CLI subcommands (satellite, atmosphere, alerts, all)
main.py                  # Entry point (--web, --host, --port, --cache-dir)
```

## Data Sources

| Source | Data | Rate Limit |
|--------|------|-----------|
| Open-Meteo | Surface weather, pressure levels (19 layers) | 600/min, 10K/day |
| NASA GIBS | Satellite tiles (GOES, Himawari, Meteosat, MODIS/VIIRS) | ~350K tiles/hr |
| Nominatim | Geocoding | 1 req/sec |
| NWS API | US weather alerts | ~every 30s |
| SPC | Mesoscale discussions, tornado/wind/hail reports | NWS limits |
| ESTOFEX | European convective forecasts + alerts | Polite use |
| GDACS | Global disaster alerts | Polite use |
| IBTrACS | Tropical cyclone tracking | Polite use |
| BOM | Australian warnings | Polite use |
| ECCC | Canadian alerts (CAP-XML) | Polite use |

## Quick Start

```bash
# Install dependencies
python -m pip install -r requirements.txt

# System libs for GRIB/cartopy (Fedora/CentOS)
sudo dnf install eccodes proj geos geos-devel proj-devel

# Launch web UI
python main.py --web

# Launch on 0.0.0.0 for network access
python main.py --web --host 0.0.0.0 --port 7860

# With custom cache directory
python main.py --web --cache-dir ./cache

# CLI usage
python main.py atmosphere --location "Oklahoma City" --date 2026-07-02
python main.py satellite --location "Chicago" --date 2026-07-02 --start 1300 --end 1500
python main.py alerts --location "Tampa, FL"
```

## Testing

```bash
# Unit tests (mocked, no network)
python -m pytest tests/ --ignore=tests/test_e2e_integration.py -v

# E2E integration tests (real API calls)
python -m pytest tests/test_e2e_integration.py -v
```

## Deployment

See [INSTALL.md](INSTALL.md) for full installation and systemd service setup.

See [restore.md](restore.md) for VPS deployment recovery procedures.
