# Weather Event Analyzer — Agent Instructions

## Commands

- Always use `python -m pytest` instead of bare `pytest`
- Always use `python -m pip` instead of bare `pip`

## Project Structure

```
core/
├── api_request.py       # CachedRequest — THE ONLY class that makes HTTP calls
├── cache.py             # SQLite metadata DB + files on disk
├── models/              # Typed dataclass DTOs
│   ├── base.py          # BaseDataResult (all models extend this)
│   ├── atmosphere/      # AtmosphereData, VariableTimeSeries, OpenMeteo*
│   ├── pressure_levels/ # PressureLevelSnapshot/Hourly, extract_snapshot()
│   ├── discussion/      # Discussion + per-source: SPC, ESTOFEX, BOM, etc.
│   ├── fronts/          # FrontalFeature
│   ├── alerts/          # Alert, SourceResult
│   └── satellite/       # SatelliteFrame, SatelliteSource + per-source handlers
│       └── source.py    # GOESEastGeocolor, HimawariIR, MeteosatIR, etc.
├── atmosphere.py        # OpenMeteoClient (cached)
├── pressure_levels.py   # PressureLevelClient (cached), snap_to_synoptic, extract_snapshot
├── satellite.py         # SatelliteClient: probe_latest, _has_content, _normalize 2048x2048
├── fronts.py            # FrontalAnalyzer (experimental), find_pressure_extrema
├── discussions.py       # MesoscaleDiscussionClient (cached)
├── alerts.py            # 7 alert clients + fetch_alerts orchestrator (cached, 5min TTL)
├── visualize.py         # Charts (Skew-T, hodograph, time-series), render_geo_overlay
├── location.py          # Geocoding (Nominatim, cached 24h) + satellite source selection
└── projection.py        # Geostationary projection math

app.py                   # Gradio web UI
cli.py                   # CLI interface
main.py                  # Entry point (--web, --host, --port, --cache-dir)
```

## Critical Rules

### HTTP Calls

- **ALL external HTTP calls MUST go through `CachedRequest`** (`core/api_request.py`)
- NEVER use `requests.get()`, `requests.Session()`, or `session.get()` directly
- NEVER define `USER_AGENT` locally — set once in `api_request.py`
- NEVER use `time.sleep()` for rate limiting — `CachedRequest._rate_limit()` handles per-domain
- NEVER cache probe requests — probes use `_probe_frame()` / `fetch_raw()`, not `fetch_binary()`
- Methods: `fetch_json()`, `fetch_text()`, `fetch_raw()` (XML/CSV), `fetch_binary()` (images), `fetch_or_compute()` (multi-step)

### Rate Limits & Daily Budgets

Defined in `DOMAIN_RATE_LIMITS` and `DOMAIN_DAILY_LIMITS` in `api_request.py`. Every domain that the app calls must be listed. When adding a new source:
1. Add its domain to both dicts
2. Research the API's usage policy
3. Document the source URL in comments

### Satellite Sources

Each satellite source is a `SatelliteSource` class in `core/models/satellite/source.py`:
- Defines cadence, latency, layer name, URL type
- `estimate_latest()` returns expected latest available time (now - latency)
- `generate_timestamps()` builds the frame window
- `select_source(lat, lon, product)` returns the right handler by region

Probing:
- `probe_latest()` walks backward from `estimate_latest()`, checks `_has_content()` (mean pixel > 8)
- Probes use 128px throwaway images via `_probe_frame()` — NEVER cached
- Full frames fetched at `OUTPUT_SIZE` (2048px) and normalized via `_normalize()`

### Data Models

- ALL data from clients MUST be typed dataclasses from `core/models/`
- NEVER return raw dicts
- Per-source models extend the domain base (e.g., `SPCMesoscaleDiscussion(Discussion)`)
- Cache keys include size for images to prevent probe/full-res collision

### Caching

- SQLite metadata (`cache.db`) + files on disk — NEVER blobs in SQLite
- `_conn()` recreates the cache directory if deleted
- JSON: `fetch_json(url, cache_key, ttl)` — raw JSON cached before DTO parsing
- Binary: `fetch_binary(url, cache_key)` — satellite tiles on disk
- Computed: `fetch_or_compute(cache_key, fn, ttl)` — for multi-step results (alerts, discussions)
- Cache keys: `json/{module}/{operation}/{params}.json` or `wms/{layer}/{ts}/{params}_{size}.jpg`
- Daily cleanup at midnight UTC, 7-day max age

### Visualization

- All charts: `plt.style.use("dark_background")`, return `matplotlib.figure.Figure`
- `app.py` converts figures to PIL via `_fig_to_pil()` for `gr.Image` (enables fullscreen)
- MetPy units: always attach before `mpcalc` calls
- `render_geo_overlay()` renders coastlines/borders ONCE on transparent canvas, composited onto each frame — never per-frame cartopy
- Skew-T and hodograph at `dpi=150`

### Fetch Pipeline Order (app.py)

1. Resolve location (geocode, cached 24h)
2. Probe latest satellite frame (if live mode — no date/time specified)
3. Atmosphere surface data → render CAPE/Temp/Wind charts
4. Pressure levels → render sounding + hodograph, add helicity to CAPE chart
5. Mesoscale discussions
6. Frontal analysis (optional)
7. Satellite imagery (render geo overlay once, composite per frame)
8. **Alerts (LAST — slowest, multiple external sources can timeout)**

Error handler preserves accumulated state — charts stay visible if a later stage crashes.

Soundings render on demand: only the hour nearest the window start, then the hour nearest each
streamed frame (`render_sounding`/`render_hodo`, cached per hour, one `render_lock` for pyplot).
Hours are counted from 00 UTC on the event date, so a next-day 00 UTC RAOB is hour 24.

### Historical dates

Each client falls back to an archive when the date is outside the live window — see
`docs/data-archives.md` for ranges/resolution, and update it when a source is checked.
- Pressure levels: forecast (±7 d) → historical forecast (~2021+) → nearest IEM RAOB (N. America)
- MDs/outlook: IEM point MD archive (2003+) + SPC outlook archive when date ≠ today
- MSLP extrema: ERA5 archive (4° grid, 90 s timeout); SPC reports: `YYMMDD_rpts_torn.csv`
- Satellite: `plan_event` probes first/middle/last frame; outside the geostationary archive
  (GIBS ~90 days, EUMETSAT 502s) it tries `GOESArchiveImagery` — CMIPC NetCDF from
  `noaa-goes16…19` S3 (2017+, CONUS/PACUS sector only). Per frame by solar elevation
  (`modes[ts]`): ≥8° true colour C01–C03 (0.5 km), ≤0° Band 13 IR, between blended.
  `goes_archive.render_frame` reads only the HDF5 chunks under the box (`fetch_range`);
  only the rendered WebP is cached — then `DailyPolarSnapshot`, one MODIS/VIIRS
  daily-mosaic frame per pass (2000+). Sources pick layer/name per timestamp via
  `layer_for(ts)` / `name_for(ts)` — use those, not `source.layer`, when building URLs

Progressive UI: `fetch_data` yields through `out()`/`step()`, which send only the outputs passed
(everything else is `gr.skip()`) — never re-send charts or 2048 px frames on a progress tick.
Outputs in `PENDING_OUTPUTS` carry the `wea-pending` class (CSS stopwatch) until their first
value. Satellite frames stream via `SatelliteClient.plan_event()` + `iter_frames()` generator.

### Timeline Slider

- `on_frame_change` outputs only image/sounding/hodo/label — never the timeline (feedback loop)
- Backend updates to the timeline value ALSO fire `.change`; `state["shown_idx"]`/`["shown_hour"]`
  make `on_frame_change` skip what is already on screen
- Notches are rendered by `TIMELINE_HTML_TEMPLATE` itself — Gradio re-renders the template on
  every prop change, so JS-built DOM is wiped. JS uses delegated listeners on `element`
- JS fires `trigger('change')` on slider input / arrow keys (not when a text field has focus)

### Testing

- Unit tests: `tests/test_*.py` — mocked, no network
- E2E tests: `tests/test_e2e_integration.py` — real API calls, `@pytest.mark.integration`
- `_fetch_once()` shares data across E2E test classes to avoid rate limits
- Mock patches target `core.api_request.requests.Session`

### Adding a New Data Source

1. Create model in `core/models/{domain}/{source}.py` extending domain base
2. Export from `core/models/{domain}/__init__.py`
3. Add domain to `DOMAIN_RATE_LIMITS` and `DOMAIN_DAILY_LIMITS`
4. Implement client with `self.req = CachedRequest(cache=cache)`
5. Return typed dataclass, never dicts
6. Add unit test (mocked) + E2E test (real API)
7. Update README.md
