"""Centralized cache-aware HTTP request builder.

All external API calls go through CachedRequest. This is the ONLY place
that creates HTTP sessions, sets User-Agent, applies rate limiting, and
tracks request statistics.
"""

import time
import threading
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests


USER_AGENT = "WeatherEventAnalyzer/1.0 (https://github.com/ScrewTSW/weather-event-analyzer)"

# Per-domain rate limits based on API usage policies
# Sources:
#   Open-Meteo: 600/min free tier — https://open-meteo.com/en/terms
#   Nominatim: 1 req/sec strict — https://operations.osmfoundation.org/policies/nominatim/
#   NASA GIBS: ~350K tiles/hr — https://nasa-gibs.github.io/gibs-api-docs/access-advanced-topics/
#   NWS: ~every 30s for alerts — https://weather-gov.github.io/api/general-faqs
#   SPC/NOAA: general NWS limits — https://www.weather.gov/documentation/services-web-alerts
#   Open-Meteo historical forecast: same terms as the forecast API — https://open-meteo.com/en/docs/historical-forecast-api
#   IEM (Iowa Environmental Mesonet): no hard limit, asks for modest automated use —
#     https://mesonet.agron.iastate.edu/api/  (RAOB + SPC MD archives)
#   NOAA GOES-16/17/18/19 on AWS Open Data (NODD): free, no published request limit —
#     https://registry.opendata.aws/noaa-goes/  — read in 250x250 chunks via HTTP Range,
#     ~10-90 small requests per frame; S3 serves thousands of requests/s per prefix
DOMAIN_RATE_LIMITS = {
    "api.open-meteo.com": 0.3,
    "archive-api.open-meteo.com": 0.3,
    "historical-forecast-api.open-meteo.com": 0.3,
    "mesonet.agron.iastate.edu": 0.5,
    "nominatim.openstreetmap.org": 1.0,
    "gibs.earthdata.nasa.gov": 0.05,
    "api.weather.gov": 0.5,
    "www.spc.noaa.gov": 0.5,
    "tgftp.nws.noaa.gov": 0.5,
    "www.estofex.org": 0.3,
    "api.weather.bom.gov.au": 0.3,
    "dd.weather.gc.ca": 0.3,
    "www.gdacs.org": 0.3,
    "www.ncei.noaa.gov": 0.3,
    "www.tsunami.gov": 0.3,
    "nomads.ncep.noaa.gov": 0.5,
    "ws.smn.gob.ar": 0.3,
    "view.eumetsat.int": 0.1,
    "feeds.meteoalarm.org": 0.3,
    "apiprevmet3.inmet.gov.br": 0.3,
    "data.tmd.go.th": 0.3,
    "mausam.imd.gov.in": 0.3,
    "www.weathersa.co.za": 0.3,
    "xml.kishou.go.jp": 0.3,
    "severeweather.wmo.int": 0.3,
    "noaa-goes16.s3.amazonaws.com": 0.02,
    "noaa-goes17.s3.amazonaws.com": 0.02,
    "noaa-goes18.s3.amazonaws.com": 0.02,
    "noaa-goes19.s3.amazonaws.com": 0.02,
}
DEFAULT_RATE_LIMIT = 0.3

# Daily call budgets per domain (None = unlimited)
DOMAIN_DAILY_LIMITS = {
    "api.open-meteo.com": 10000,
    "archive-api.open-meteo.com": 10000,
    "historical-forecast-api.open-meteo.com": 10000,
    "mesonet.agron.iastate.edu": 2000,
    "nominatim.openstreetmap.org": 2000,
    "gibs.earthdata.nasa.gov": None,
    "api.weather.gov": 5000,
    "www.spc.noaa.gov": 5000,
    "tgftp.nws.noaa.gov": 5000,
    "www.estofex.org": 1000,
    "api.weather.bom.gov.au": 5000,
    "dd.weather.gc.ca": 5000,
    "www.gdacs.org": 5000,
    "www.ncei.noaa.gov": 10000,
    "www.tsunami.gov": 5000,
    "nomads.ncep.noaa.gov": 5000,
    "ws.smn.gob.ar": 2000,
    "view.eumetsat.int": None,
    "feeds.meteoalarm.org": 5000,
    "apiprevmet3.inmet.gov.br": 2000,
    "data.tmd.go.th": 2000,
    "mausam.imd.gov.in": 2000,
    "www.weathersa.co.za": 2000,
    "xml.kishou.go.jp": 2000,
    "severeweather.wmo.int": 2000,
    "noaa-goes16.s3.amazonaws.com": 100000,
    "noaa-goes17.s3.amazonaws.com": 100000,
    "noaa-goes18.s3.amazonaws.com": 100000,
    "noaa-goes19.s3.amazonaws.com": 100000,
}
DEFAULT_DAILY_LIMIT = 5000


class DailyLimitExceeded(Exception):
    def __init__(self, domain, limit):
        super().__init__(f"Daily API call limit ({limit}) exceeded for {domain}")
        self.domain = domain
        self.limit = limit


class RequestStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.api_calls = 0
        self.cache_hits = 0
        self._daily_counts: dict[str, int] = {}
        self._daily_reset_date: str = ""

    def record_api_call(self, domain: str = ""):
        with self._lock:
            self.api_calls += 1
            today = time.strftime("%Y-%m-%d")
            if today != self._daily_reset_date:
                self._daily_counts.clear()
                self._daily_reset_date = today
            if domain:
                self._daily_counts[domain] = self._daily_counts.get(domain, 0) + 1

    def record_cache_hit(self):
        with self._lock:
            self.cache_hits += 1

    def daily_count(self, domain: str) -> int:
        with self._lock:
            today = time.strftime("%Y-%m-%d")
            if today != self._daily_reset_date:
                return 0
            return self._daily_counts.get(domain, 0)

    def daily_counts_all(self) -> dict[str, int]:
        with self._lock:
            return dict(self._daily_counts)

    def reset(self):
        with self._lock:
            self.api_calls = 0
            self.cache_hits = 0
            self._daily_counts.clear()


class CachedRequest:
    """Fetch-or-cache wrapper for HTTP API calls.

    Usage:
        req = CachedRequest(cache=cache)
        raw = req.fetch_json(url, cache_key="json/atmo/...", ttl=3600)
    """

    def __init__(self, session: requests.Session = None, cache=None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache = cache
        self.stats = RequestStats()
        self._last_request_per_domain: dict[str, float] = {}
        self._domain_lock = threading.Lock()

    def _rate_limit(self, url: str):
        domain = urlparse(url).hostname or ""
        delay = DOMAIN_RATE_LIMITS.get(domain, DEFAULT_RATE_LIMIT)
        daily_limit = DOMAIN_DAILY_LIMITS.get(domain, DEFAULT_DAILY_LIMIT)
        if daily_limit is not None and self.stats.daily_count(domain) >= daily_limit:
            raise DailyLimitExceeded(domain, daily_limit)
        with self._domain_lock:
            last = self._last_request_per_domain.get(domain, 0)
            elapsed = time.time() - last
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self._last_request_per_domain[domain] = time.time()

    def _domain(self, url: str) -> str:
        return urlparse(url).hostname or ""

    def fetch_json(self, url: str, cache_key: str = None,
                   ttl: int = 3600, params: dict = None, timeout: int = 30) -> dict:
        if cache_key and self.cache:
            cached = self.cache.get_json(cache_key, ttl=ttl)
            if cached is not None:
                self.stats.record_cache_hit()
                return cached

        self._rate_limit(url)
        self.stats.record_api_call(self._domain(url))
        resp = self.session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        raw = resp.json()

        if cache_key and self.cache:
            self.cache.store_json(cache_key, raw, source_url=url, ttl=ttl)

        return raw

    def fetch_text(self, url: str, cache_key: str = None,
                   ttl: int = 3600) -> str:
        if cache_key and self.cache:
            cached = self.cache.get_json(cache_key, ttl=ttl)
            if cached is not None:
                self.stats.record_cache_hit()
                return cached

        self._rate_limit(url)
        self.stats.record_api_call(self._domain(url))
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        text = resp.text

        if cache_key and self.cache:
            self.cache.store_json(cache_key, text, source_url=url, ttl=ttl)

        return text

    def fetch_raw(self, url: str, params: dict = None,
                  timeout: int = 10) -> requests.Response:
        """Raw HTTP GET — for XML/CSV/binary responses that need custom parsing.
        Still goes through the shared session (User-Agent) and tracks stats."""
        self._rate_limit(url)
        self.stats.record_api_call(self._domain(url))
        return self.session.get(url, params=params, timeout=timeout)

    def fetch_range(self, url: str, start: int, end: int, timeout: int = 30) -> bytes:
        """Bytes [start, end) of a remote file via HTTP Range — uncached, for reading
        only the needed blocks of a large archive file."""
        self._rate_limit(url)
        self.stats.record_api_call(self._domain(url))
        resp = self.session.get(url, headers={"Range": f"bytes={start}-{end - 1}"}, timeout=timeout)
        resp.raise_for_status()
        if resp.status_code != 206 and len(resp.content) != end - start:
            raise requests.HTTPError(f"Range {start}-{end - 1} not honoured ({resp.status_code})")
        return resp.content

    def fetch_or_compute(self, cache_key: str, compute_fn: Callable[[], Any],
                         ttl: int = 3600) -> Any:
        """Cache arbitrary computed results. Use when the value requires
        multi-step fetching or parsing that shouldn't be repeated."""
        if cache_key and self.cache:
            cached = self.cache.get_json(cache_key, ttl=ttl)
            if cached is not None:
                self.stats.record_cache_hit()
                return cached

        result = compute_fn()

        if cache_key and self.cache and result is not None:
            self.cache.store_json(cache_key, result, ttl=ttl)

        return result

    def fetch_or_compute_binary(self, cache_key: str,
                                compute_fn: Callable[[], Optional[bytes]]) -> Optional[bytes]:
        """Binary counterpart of fetch_or_compute — e.g. a frame rendered from a
        large download that is not worth keeping itself."""
        if cache_key and self.cache:
            path = self.cache.get(cache_key)
            if path:
                self.stats.record_cache_hit()
                return path.read_bytes()

        result = compute_fn()

        if cache_key and self.cache and result:
            self.cache.store(cache_key, result)
        return result

    def fetch_binary(self, url: str, cache_key: str = None) -> Optional[bytes]:
        if cache_key and self.cache:
            path = self.cache.get(cache_key)
            if path:
                self.stats.record_cache_hit()
                return path.read_bytes()

        # One retry on transient network failure — large WMS renders (GIBS
        # 2048px PNG ≈ 9.5 MB) occasionally stall server-side past the timeout.
        for attempt in range(2):
            self._rate_limit(url)
            self.stats.record_api_call(self._domain(url))
            try:
                resp = self.session.get(url, timeout=30)
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 1:
                    raise
        if resp.status_code == 200 and len(resp.content) > 500:
            if cache_key and self.cache:
                self.cache.store(cache_key, resp.content, source_url=url)
            return resp.content
        return None
