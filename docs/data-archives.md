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
| NASA GIBS MODIS Terra true colour | 2000-02-24 → ✅ (capabilities; gaps e.g. 2000-04-26/27) | WMS, 250 m | **daily** mosaic (~10:30 local) | 2011 ✅, 2019 ✅; 1999 → 294-byte black PNG |
| NASA GIBS MODIS Aqua true colour | 2002-07-03 → ✅ (capabilities; gap 2022-04-01…04-16) | WMS, 250 m | daily mosaic (~13:30 local) | 2011 ✅; black wedges between swaths at low latitudes |
| NASA GIBS VIIRS SNPP true colour | 2015-11-24 → ✅ (capabilities; several 2022–24 gaps) | WMS, 375 m | daily mosaic (~13:30 local) | 2019 ✅ (US + Germany); no swath gaps |
| EUMETSAT `view.eumetsat.int` (Meteosat IR/VIS) | recent only — **HTTP 502** for 2011 and 2019 ✅ | WMS | 15 min | first date ❔ |
| NOAA GOES-R on AWS `noaa-goes{16,17,18,19}.s3.amazonaws.com` — **wired** (`GOESArchiveImagery`) | East: G16 2017-02-28…2017-11-30 at 89.5° W, G16 2017-12-14…2025-04-07 at 75.2° W, G19 2025-04-07 → (listings ✅). West: G17 2018-11-13…2023-01-10, G18 2023-01-04 → | CONUS / PACUS `ABI-L2-CMIPC`: C02 0.5 km, C01/C03 1 km (true colour by day), C13 2 km (IR by night) | 5 min (we take the scan nearest each 10-min frame) | C13 4.7 MB, C02 ~72 MB ✅ — only the 250x250 chunks under the box are range-read; fixed-grid → reprojected locally; full disk `CMIPF` (27.6 MB) not used |
| IEM GIS archive `archive/data/YYYY/MM/DD/GIS/sat/awips211/` | 2015 GOES-East IR/VIS/WV ✅; 2019 GOES-West only ✅; 2011 empty ✅ | CONUS Lambert, 4 km (`.wld` 4063.5 m) | ~30 min | PNG + world file |
| Himawari-8/9 on AWS, Meteosat | ❔ | | | Himawari = HSD binary; no free Meteosat archive known |

Fallback: geostationary (GIBS / EUMETSAT) → GOES-R S3 archive (`GOESArchiveImagery`) → daily polar passes (`DailyPolarSnapshot`).
`plan_event` probes the first/middle/last frame (128 px, uncached). If all are empty it
probes one frame per polar slot — MODIS Terra (~10:30 local), then VIIRS → MODIS Aqua
(~13:30 local) — and returns those that have data, timestamped at the pass's approximate
UTC time over the requested longitude (`10:30 − lon/15 h`). GIBS snaps TIME to the UTC day.
Before 2000-02-24 nothing is probed and no frames are returned. Verified 2026-11-02:
Tuscaloosa 2011 (Terra + Aqua), Nashville 2019 and Germany 2019 (Terra + VIIRS), OKC 1982 (none).

GOES-R archive: when the GIBS probes are empty, `plan_event` checks whether the location's
scan angle lies inside the CONUS (East) / PACUS (West) sector of a satellite imaging that
day (the product's own side first), lists the S3 hours of the window (cached, ~free) and
maps each 10-min frame to the scan starting within 5 min. Frames download the NetCDF
uncached, reproject nearest-neighbour onto the same lon/lat box GIBS would return and
cache only the rendered PNG (`goes/…`). No files exist while a satellite drifts
(G16 2017-12-01…12-13 → polar fallback). Outside the sectors (South America, Europe,
Pacific beyond Hawaii) there is no listing call. Verified 2026-11-02: Mayfield KY
2021-12-11 02 UTC (7/7 frames, ~1.7 s each), central Kansas 2017-05-18 (89.5° W period),
coastline overlay aligned.
Still missing: Himawari/Meteosat loops for old events (Himawari HSD on AWS ❔).
