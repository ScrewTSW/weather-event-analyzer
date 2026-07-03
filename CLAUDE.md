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
│   ├── pressure_levels/ # PressureLevelSnapshot, PressureLevelHourly
│   ├── discussion/      # Discussion + per-source: SPC, ESTOFEX, BOM, etc.
│   ├── fronts/          # FrontalFeature
│   ├── alerts/          # Alert, SourceResult
│   └── satellite/       # SatelliteFrame
├── atmosphere.py        # OpenMeteoClient
├── pressure_levels.py   # PressureLevelClient
├── satellite.py         # SatelliteClient
├── fronts.py            # FrontalAnalyzer
├── discussions.py       # MesoscaleDiscussionClient
├── alerts.py            # 7 alert source clients + fetch_alerts orchestrator
├── visualize.py         # All chart rendering (matplotlib/MetPy)
├── location.py          # Geocoding + satellite source selection
└── projection.py        # Geostationary projection math

app.py                   # Gradio web UI
cli.py                   # CLI interface
main.py                  # Entry point
```

## Critical Rules

### HTTP Calls

- **ALL external HTTP calls MUST go through `CachedRequest`** (`core/api_request.py`)
- NEVER use `requests.get()`, `requests.Session()`, or `session.get()` directly
- NEVER define `USER_AGENT` locally — it's set once in `api_request.py`
- NEVER use `time.sleep()` for rate limiting — `CachedRequest._rate_limit()` handles it per-domain
- Use `req.fetch_json()` for JSON APIs, `req.fetch_text()` for HTML/text, `req.fetch_raw()` for XML/CSV, `req.fetch_binary()` for images, `req.fetch_or_compute()` for multi-step cached results

### Rate Limits

Per-domain rate limits are defined in `DOMAIN_RATE_LIMITS` in `api_request.py`. Daily call budgets in `DOMAIN_DAILY_LIMITS`. When adding a new data source:
1. Add its domain to both dictionaries
2. Research the API's usage policy and set appropriate limits
3. Document the source URL in the comments

### Data Models

- ALL data returned from clients MUST be typed dataclasses from `core/models/`
- NEVER return raw dicts from data-fetching functions
- New data sources get their own file under the appropriate `core/models/{domain}/` folder
- Source-specific models extend the domain base class (e.g., `SPCMesoscaleDiscussion(Discussion)`)
- Put `parse()` classmethods on source-specific models when the source needs custom parsing

### Caching

- Cache is SQLite metadata (`cache.db`) + files on disk — NEVER store blobs in SQLite
- JSON API responses: use `req.fetch_json(url, cache_key=..., ttl=...)` — raw JSON cached before parsing
- Multi-step results: use `req.fetch_or_compute(cache_key, compute_fn, ttl=...)` — cache the final computed result
- Binary (satellite tiles): use `req.fetch_binary(url, cache_key=...)` — cached as files on disk
- Cache keys follow convention: `json/{module}/{operation}/{params}.json` or `{type}/{layer}/{params}.ext`

### Charts and Visualization

- All charts use `plt.style.use("dark_background")`
- All charts return `matplotlib.figure.Figure`
- In `app.py`, figures are converted to PIL images via `_fig_to_pil()` for `gr.Image` rendering (enables fullscreen)
- MetPy units: always attach `units` from `metpy.units` before passing to `mpcalc` functions
- Skew-T uses `dpi=150`, hodograph uses `dpi=150`

### Testing

- Unit tests in `tests/test_*.py` — mocked, no network, fast
- E2E integration tests in `tests/test_e2e_integration.py` — real API calls, marked `@pytest.mark.integration`
- Use `_fetch_once()` pattern in E2E tests to share fetched data across test classes
- Mock patches target `core.api_request.requests.Session` (not the module that uses CachedRequest)

### Adding a New Data Source

1. Create model in `core/models/{domain}/{source}.py` extending the domain base
2. Export from `core/models/{domain}/__init__.py`
3. Add domain to `DOMAIN_RATE_LIMITS` and `DOMAIN_DAILY_LIMITS` in `api_request.py`
4. Implement client using `self.req = CachedRequest(cache=cache)` — no raw HTTP
5. Return typed dataclass, never dicts
6. Add unit test (mocked) + E2E test (real API)
7. Update README.md data sources table
