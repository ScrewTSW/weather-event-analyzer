# Data archives — coverage, resolution, fallback order

What each upstream source can return for a **past** date, so the clients know what to
fall back onto. ✅ = verified by a real request (date noted), ❔ = from provider docs / not yet
verified. Update this file whenever a source or a range is checked.

Reference test cases: Tuscaloosa 2011-04-27 21–00 UTC (outbreak), Omaha 2015-09-08,
Nashville 2019-02-14 (ordinary days), Oklahoma City 1982-05-02 (pre-archive), live today.

## Pressure levels (Skew-T, hodograph, SRH) — `core/pressure_levels.py`

Fallback: forecast API → historical forecast API → nearest IEM RAOB station.

| Source | Date range | Spatial | Time | Levels | Notes |
|---|---|---|---|---|---|
| Open-Meteo forecast `api.open-meteo.com/v1/forecast` | today −7 d … +7 d as we call it (`past_days=7`); API allows `past_days` up to 92 ❔ | point, best-match model (HRRR 3 km / GFS 0.25° in US, ICON/ECMWF elsewhere) | hourly | 19 std levels 1000–30 hPa | used when `in_forecast_window(date)` |
| Open-Meteo historical forecast `historical-forecast-api.open-meteo.com` | HTTP 400 before 2016 ✅ (2011); all `null` for 2019 ✅; data for 2021-06-11 ✅ — exact first date ❔ | point, archived model runs | hourly | 19 std levels | |
| Open-Meteo ERA5 archive `archive-api.open-meteo.com/v1/archive` | — | — | — | **pressure-level variables not served** ✅ (units `undefined`, values `null`) | surface vars only (see below) |
| IEM RAOB `mesonet.agron.iastate.edu/json/raob.py` | per station `archive_begin` (e.g. 1953) ✅ 1982 KOKC, 2011 KBMX, 2015 KOAX, 2019 KBNA | 347 stations, **North America only** (2 outside) ✅; we accept ≤ 600 km | 00/12 UTC routine + specials (2011-04-27 18 UTC ✅) | mandatory + significant, interpolated (ln p) onto the 19 std levels | station list: `geojson/network/RAOB.geojson` |

Gap: no free pressure-level archive outside North America before ~2021 (ERA5 pressure
levels exist only via Copernicus CDS, which needs an API key ❔; University of Wyoming
sounding archive is global ❔).

## Surface time series (CAPE, temperature, wind) — `core/atmosphere.py`

| Source | Date range | Spatial | Time |
|---|---|---|---|
| Open-Meteo forecast | today −7 d … +7 d | best-match model | hourly |
| Open-Meteo ERA5 archive | 1940 → ~5 days ago ❔; 1982 ✅ 2011 ✅ | 0.25° (~31 km) ❔ | hourly |

## MSLP field (pressure centres H/L) — `core/fronts.py::fetch_pressure_field`

| Source | Date range | Grid we request | Cost |
|---|---|---|---|
| Open-Meteo forecast multi-point | forecast window | 2° step, ±20° bbox → 441 points | fast |
| Open-Meteo ERA5 multi-point | 1940 → ✅ 2011, 2015 | **4° step** → 121 points | 5–13 s per call ✅, occasionally > 30 s (timeout raised to 90 s) |

## Discussions / outlooks — `core/discussions.py`

| Source | Date range | Filter | Notes |
|---|---|---|---|
| SPC live MDs `spc.noaa.gov/products/md/` | today | **latest 5 nationwide, not point-filtered** | used only when date = today |
| IEM SPC MD archive `json/spcmcd.py?lat&lon` | 2003 → now ✅ (816 MDs at Wichita, 2003–2026) | MD polygons containing the point | text: `api/1/nwstext/{product_id}` ✅; we take 00 UTC date → +36 h |
| SPC Day-1 outlook archive `products/outlook/archive/YYYY/day1otlk_YYYYMMDD_HHMM.html` | 2011 ✅ (1300, 1630); first year ❔ (~2003) | national | we try 1630 → 1300 → 2000 |
| ESTOFEX, ECCC, BOM, JMA, IMD, SMN, INMET, TMD, SAWS, WMO | **live only** — no archive wired | | ESTOFEX archive exists ❔ |

## Storm reports / alerts — `core/alerts.py`

| Source | Date range | Notes |
|---|---|---|
| SPC reports `climo/reports/today_torn.csv` / `YYMMDD_rpts_torn.csv` | `_rpts_` form ✅ 2011 + 2026; 1982 → 404 ✅; first year ❔ (~2004) | the old `YYMMDD_torn.csv` form 404s for every date |
| NWS active alerts | live only | IEM VTEC archive by point exists ❔ (`json/vtec_events_bypoint.py`) — not wired |

## Satellite — `core/satellite.py`, `core/models/satellite/source.py`

| Source | Date range | Spatial | Time | Format |
|---|---|---|---|---|
| NASA GIBS geostationary (GOES-East/West GeoColor, Band 13 IR, Air Mass; Himawari Band 13…) | **rolling ~90 days** ✅ (capabilities 2026-11-02: GOES-East GeoColor from 2026-08-10, Himawari Band 13 from 2026-09-22) | WMS, any bbox | 10 min | PNG/JPEG, transparent outside archive |
| NASA GIBS MODIS Terra true colour | 2000-02-24 → ✅ (capabilities) | WMS, 250 m | **daily** (one pass, ~10:30 local) | |
| NASA GIBS VIIRS SNPP true colour | 2015-11-24 → ✅ (capabilities) | WMS, 250 m–375 m | daily (~13:30 local) | |
| NOAA GOES-16 on AWS `noaa-goes16.s3.amazonaws.com` | 2017 → ❔; 2019-06-11 ✅ | CONUS `ABI-L2-CMIPC` 2 km (band 13) / full disk `CMIPF` | CONUS 5 min (4.7 MB/file ✅), full disk 10 min (27.6 MB ✅) | NetCDF, GOES fixed-grid projection — needs decode + reproject |
| IEM GIS archive `archive/data/YYYY/MM/DD/GIS/sat/awips211/` | 2015 GOES-East IR/VIS/WV ✅; 2019 GOES-West only ✅; 2011 empty ✅ | CONUS Lambert, 4 km (`.wld` 4063.5 m) | ~30 min | PNG + world file |
| Himawari-8/9 on AWS, Meteosat | ❔ | | | Himawari = HSD binary; no free Meteosat archive known |

Current behaviour: `plan_event` probes first/middle/last frame (128 px, uncached); if all
empty it logs "outside the … archive" and returns no timestamps — no 2048 px fetches.
Gap: no satellite imagery before the GIBS window. Candidates, in effort order:
MODIS/VIIRS daily still (2000+/2015+), GOES-16 NetCDF from S3 (2017+, CONUS loop),
IEM 4 km IR loop (patchy).
