# Weather Event Analyzer

Real-time severe weather analysis tool combining satellite imagery, atmospheric soundings, hodographs, mesoscale discussions, and global alerts into a single dashboard.

## Features

- **Satellite Imagery** — GOES-East/West (geocolor + IR), Himawari, Meteosat, MODIS/VIIRS via NASA GIBS. 2048x2048 normalized output. Probe-based latest frame detection with content verification.
- **Skew-T Log-P Sounding** — Temperature/dewpoint profiles with PCHIP interpolation, CAPE/CIN shading, wind barbs in knots, LCL/LFC/EL/SFC markers, 0°C/−20°C isotherms, SBCAPE/MLCAPE/MUCAPE
- **Hodograph** — Height-colored wind trace (purple 0-0.5km through blue 9+km), Bunkers RM/LM/MW storm motion, wind barb column by pressure level in knots
- **Helicity** — SRH 0-1km and 0-3km time series on the CAPE/CIN chart
- **Frontal Detection** — TFP-based cold/warm/dry front detection with wind-shift verification (experimental)
- **Mesoscale Discussions** — SPC, ESTOFEX, BOM, ECCC, and 10+ global sources auto-selected by location
- **Global Alerts** — GDACS, NWS, ESTOFEX, IBTrACS, PTWC, SPC reports, MeteoAlarm
- **Progress Bar** — Fixed-position green fill bar showing fetch stage and PULLED/CACHE status per source
- **Tooltip Help** — On-hover severe weather interpretation guidance for each chart

## Architecture

```
core/
├── api_request.py         # CachedRequest — all HTTP, per-domain rate limits, daily budgets
├── cache.py               # SQLite metadata + files on disk, TTL, cleanup
├── models/                # Typed DTO hierarchy
│   ├── base.py            #   BaseDataResult
│   ├── atmosphere/        #   AtmosphereData + OpenMeteo variants
│   ├── pressure_levels/   #   PressureLevelSnapshot/Hourly
│   ├── discussion/        #   Discussion + per-source parsers (SPC, ESTOFEX, BOM, ...)
│   ├── fronts/            #   FrontalFeature
│   ├── alerts/            #   Alert, SourceResult
│   └── satellite/         #   SatelliteFrame + per-source handlers (GOES, Himawari, Meteosat)
│       └── source.py      #     SatelliteSource classes with cadence/latency/probe logic
├── atmosphere.py          # Open-Meteo surface data
├── pressure_levels.py     # Open-Meteo 19 pressure levels (30-1000 hPa)
├── satellite.py           # Tile fetching, probing, 2048x2048 normalization
├── fronts.py              # TFP frontal detection from gridded data
├── discussions.py         # Mesoscale discussion aggregator
├── alerts.py              # Multi-source severe weather alerts
├── visualize.py           # Chart rendering + render_geo_overlay (one-shot compositing)
├── location.py            # Geocoding (Nominatim) + satellite source selection
└── projection.py          # Geostationary projection math

app.py                     # Gradio web UI
cli.py                     # CLI subcommands
main.py                    # Entry point (--web, --host, --port, --cache-dir)
```

## Data Sources

| Source | Data | Rate Limit |
|--------|------|-----------|
| Open-Meteo | Surface weather, 19 pressure levels (forecast, historical forecast, ERA5 archive) | 600/min, 10K/day |
| IEM (Iowa Environmental Mesonet) | Archived radiosondes (RAOB), archived SPC MDs | Polite use |
| NASA GIBS | Satellite tiles (GOES, Himawari, Meteosat, MODIS/VIIRS) | ~350K tiles/hr |
| Nominatim | Geocoding | 1 req/sec |
| NWS API | US weather alerts | ~every 30s |
| SPC | Mesoscale discussions, storm reports | NWS limits |
| ESTOFEX | European convective forecasts | Polite use |
| GDACS | Global disaster alerts | Polite use |
| IBTrACS | Tropical cyclone tracking | Polite use |
| BOM | Australian warnings | Polite use |
| ECCC | Canadian alerts (CAP-XML) | Polite use |

Past dates fall back to archives — coverage, date ranges and resolution per source are in
[docs/data-archives.md](docs/data-archives.md).

## Quick Start

```bash
python -m pip install -r requirements.txt
python main.py --web
python main.py --web --host 0.0.0.0 --port 7860 --cache-dir ./cache
```

## Testing

```bash
python -m pytest tests/ --ignore=tests/test_e2e_integration.py -v   # unit (mocked)
python -m pytest tests/test_e2e_integration.py -v                    # E2E (real APIs)
```

## Deployment

See [INSTALL.md](INSTALL.md) for dependencies, systemd service setup, and firewall configuration.
