import html
import logging
import re
from datetime import datetime, timedelta, timezone

from core.models import Discussion
from core.models.discussion import SPCMesoscaleDiscussion, SPCOutlook
from core.api_request import CachedRequest

from core.alerts import (
    is_us_location, is_europe_location, is_canada_location,
    is_australia_location, is_japan_location, is_india_location,
    is_south_america_location, is_southeast_asia_location,
    is_southern_africa_location,
)

logger = logging.getLogger(__name__)

# Past dates: SPC products from their archives instead of the live pages
IEM_MCD_URL = "https://mesonet.agron.iastate.edu/json/spcmcd.py?lat={lat:.3f}&lon={lon:.3f}"
IEM_TEXT_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext/{pid}"
SPC_OUTLOOK_ARCHIVE_URL = ("https://www.spc.noaa.gov/products/outlook/archive/"
                           "{year}/day1otlk_{ymd}_{hhmm}.html")
SPC_OUTLOOK_ISSUANCES = ("1630", "1300", "2000")  # pre-event first; 2000 only as a fallback
MAX_ARCHIVE_MDS = 8
ARCHIVE_TTL = 7 * 86400  # past products never change


def _is_today(date):
    return not date or date == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _clean_html(raw):
    text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def select_sources(lat, lon):
    sources = []
    if is_us_location(lat, lon):
        sources.extend(["spc_md", "spc_outlook"])
    if is_canada_location(lat, lon):
        sources.append("eccc")
    if is_europe_location(lat, lon):
        sources.append("estofex")
    if is_australia_location(lat, lon):
        sources.append("bom")
    if is_japan_location(lat, lon):
        sources.append("jma")
    if is_india_location(lat, lon):
        sources.append("imd")
    if is_south_america_location(lat, lon):
        sources.extend(["smn", "inmet"])
    if is_southeast_asia_location(lat, lon):
        sources.append("tmd")
    if is_southern_africa_location(lat, lon):
        sources.append("saws")
    if not sources:
        sources.append("wmo_swic")
    return sources


class MesoscaleDiscussionClient:
    def __init__(self, cache=None):
        self.req = CachedRequest(cache=cache)

    def fetch(self, lat, lon, date):
        sources = select_sources(lat, lon)
        results = []
        live = _is_today(date)
        dispatch = {
            "spc_md": self._fetch_spc_md if live else lambda: self._fetch_spc_md_archive(lat, lon, date),
            "spc_outlook": self._fetch_spc_outlook if live else lambda: self._fetch_spc_outlook_archive(date),
            "estofex": lambda: self._fetch_estofex(date),
            "eccc": self._fetch_eccc,
            "bom": self._fetch_bom,
            "jma": self._fetch_jma,
            "imd": self._fetch_imd,
            "smn": self._fetch_smn,
            "inmet": self._fetch_inmet,
            "tmd": self._fetch_tmd,
            "saws": self._fetch_saws,
            "wmo_swic": self._fetch_wmo,
        }
        for src in sources:
            fn = dispatch.get(src)
            if fn:
                try:
                    results.extend(fn())
                except Exception as e:
                    logger.warning(f"Discussion fetch failed for {src}: {e}")
        return results

    def _fetch_spc_md(self):
        def _compute():
            index_html = self.req.fetch_text("https://www.spc.noaa.gov/products/md/")
            md_links = re.findall(r'href="(md\d{4}\.html)"', index_html)
            results = []
            for link in md_links[:5]:
                md_url = f"https://www.spc.noaa.gov/products/md/{link}"
                md_html = self.req.fetch_text(md_url)
                pre_match = re.search(r'<pre>(.*?)</pre>', md_html, re.DOTALL)
                if pre_match:
                    text = html.unescape(pre_match.group(1)).strip()
                else:
                    text = _clean_html(md_html)
                number = re.search(r'md(\d+)', link)
                title = f"SPC Mesoscale Discussion #{number.group(1)}" if number else "SPC MD"
                results.append(vars(Discussion(
                    source="spc_md", region="US",
                    title=title, text=text[:3000],
                    valid_from="", valid_to="", url=md_url,
                )))
            return results

        cached = self.req.fetch_or_compute("json/discussion/spc_md_index.json", _compute, ttl=1800)
        return [Discussion(**d) for d in cached] if cached else []

    def _fetch_spc_outlook(self):
        def _compute():
            text = self.req.fetch_text("https://tgftp.nws.noaa.gov/data/raw/ac/acus01.kwns.swo.dy1.txt")
            return vars(Discussion(
                source="spc_outlook", region="US",
                title="SPC Day 1 Convective Outlook",
                text=text[:3000], valid_from="", valid_to="",
                url="https://www.spc.noaa.gov/products/outlook/day1otlk.html",
            ))

        cached = self.req.fetch_or_compute("json/discussion/spc_outlook.json", _compute, ttl=3600)
        return [Discussion(**cached)] if cached else []

    def _fetch_spc_md_archive(self, lat, lon, date):
        """MDs whose area covered (lat, lon) during the SPC convective day of `date`.

        IEM keeps every SPC MD polygon since 2003 and answers point queries; the
        convective day runs 12 UTC -> 12 UTC, widened to start at 00 UTC.
        """
        raw = self.req.fetch_json(IEM_MCD_URL.format(lat=lat, lon=lon),
                                  cache_key=f"json/discussion/iem_mcd/{lat:.3f}_{lon:.3f}.json",
                                  ttl=86400)
        day = datetime.fromisoformat(date)
        start = day.strftime("%Y-%m-%dT%H:%M")
        end = (day + timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M")
        mds = sorted((m for m in raw.get("mcds", [])
                      if start <= (m.get("utc_issue") or "")[:16] < end),
                     key=lambda m: m["utc_issue"])[:MAX_ARCHIVE_MDS]
        results = []
        for m in mds:
            pid = m["product_id"]
            text = self.req.fetch_text(IEM_TEXT_URL.format(pid=pid),
                                       cache_key=f"json/discussion/iem_text/{pid}.json",
                                       ttl=ARCHIVE_TTL)
            areas = re.search(r"AREAS AFFECTED\.\.\.(.*?)\n\n", text, re.DOTALL | re.IGNORECASE)
            results.append(SPCMesoscaleDiscussion(
                source="spc_md", region="US",
                title=f"SPC Mesoscale Discussion #{m['product_num']}",
                text=text.strip()[:3000],
                valid_from=m.get("utc_issue") or "", valid_to=m.get("utc_expire") or "",
                url=m.get("spcurl") or "", md_number=m["product_num"],
                areas_affected=areas.group(1).strip() if areas else "",
            ))
        return results

    def _fetch_spc_outlook_archive(self, date):
        ymd = date.replace("-", "")
        for hhmm in SPC_OUTLOOK_ISSUANCES:
            url = SPC_OUTLOOK_ARCHIVE_URL.format(year=ymd[:4], ymd=ymd, hhmm=hhmm)
            try:
                page = self.req.fetch_text(url, cache_key=f"json/discussion/spc_outlook/{ymd}_{hhmm}.json",
                                           ttl=ARCHIVE_TTL)
            except Exception:
                continue
            pre = re.search(r"<pre>(.*?)</pre>", page, re.DOTALL)
            if not pre:
                continue
            outlook = SPCOutlook.parse(html.unescape(pre.group(1)).strip())
            outlook.title = f"SPC Day 1 Convective Outlook ({date} {hhmm} UTC)"
            outlook.url = url
            return [outlook]
        return []

    def _fetch_estofex(self, date=""):
        def _compute():
            raw = self.req.fetch_text("https://www.estofex.org/cgi-bin/polygon/showforecast.cgi?text=yes")
            text = _clean_html(raw)
            return vars(Discussion(
                source="estofex", region="Europe",
                title="ESTOFEX Convective Forecast",
                text=text[:3000], valid_from="", valid_to="",
                url="https://www.estofex.org/cgi-bin/polygon/showforecast.cgi?text=yes",
            ))

        cached = self.req.fetch_or_compute("json/discussion/estofex.json", _compute, ttl=7200)
        if cached and date:
            disc = Discussion(**cached)
            valid_match = re.search(
                r'(\d{1,2}\s+\w+\s+\d{4})\s+\d{2}:\d{2}\s+to\s+(\d{1,2}\s+\w+\s+\d{4})',
                disc.text)
            if valid_match:
                from datetime import datetime
                try:
                    start = datetime.strptime(valid_match.group(1), "%d %b %Y").strftime("%Y-%m-%d")
                    end = datetime.strptime(valid_match.group(2), "%d %b %Y").strftime("%Y-%m-%d")
                    if date < start or date > end:
                        period = f"{valid_match.group(1)} to {valid_match.group(2)}"
                        disc.text = f"⚠ This forecast covers {period}, not the requested date {date}.\n\n{disc.text}"
                except ValueError:
                    pass
            return [disc]
        return [Discussion(**cached)] if cached else []

    def _fetch_eccc(self):
        try:
            self.req.fetch_text("https://dd.weather.gc.ca/today/alerts/cap/")
            return [Discussion(
                source="eccc", region="Canada",
                title="Environment Canada Alerts",
                text="See ECCC alerts feed for current warnings.",
                valid_from="", valid_to="",
                url="https://dd.weather.gc.ca/today/alerts/cap/",
            )]
        except Exception as e:
            logger.warning(f"ECCC fetch failed: {e}")
        return []

    def _fetch_bom(self):
        url = "https://api.weather.bom.gov.au/v1/warnings"
        try:
            data = self.req.fetch_json(url)
            results = []
            warnings = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
            for w in warnings[:5]:
                title = w.get("title", "BOM Warning") if isinstance(w, dict) else "BOM Warning"
                text = w.get("short_text", "") if isinstance(w, dict) else str(w)
                results.append(Discussion(
                    source="bom", region="Australia",
                    title=title, text=text[:3000],
                    valid_from="", valid_to="", url=url,
                ))
            return results
        except Exception as e:
            logger.warning(f"BOM fetch failed: {e}")
        return []

    def _fetch_jma(self):
        return [Discussion(source="jma", region="Japan",
                           title="JMA Severe Weather Information",
                           text="See xml.kishou.go.jp for JMA alerts (Japanese).",
                           valid_from="", valid_to="",
                           url="https://xml.kishou.go.jp/xmlpull.html")]

    def _fetch_imd(self):
        return [Discussion(source="imd", region="India",
                           title="IMD Nowcast/Warnings",
                           text="See mausam.imd.gov.in for IMD warnings.",
                           valid_from="", valid_to="",
                           url="https://mausam.imd.gov.in")]

    def _fetch_smn(self):
        url = "https://ws.smn.gob.ar/alerts/type/AL"
        try:
            data = self.req.fetch_json(url)
            results = []
            for alert in (data if isinstance(data, list) else [])[:5]:
                results.append(Discussion(
                    source="smn", region="Argentina",
                    title=alert.get("title", "SMN Alert"),
                    text=alert.get("description", "")[:3000],
                    valid_from=alert.get("date", ""),
                    valid_to="", url=url,
                ))
            return results
        except Exception as e:
            logger.warning(f"SMN fetch failed: {e}")
        return []

    def _fetch_inmet(self):
        return [Discussion(source="inmet", region="Brazil",
                           title="INMET Warnings",
                           text="See apiprevmet3.inmet.gov.br for INMET alerts.",
                           valid_from="", valid_to="",
                           url="https://apiprevmet3.inmet.gov.br/avisos/rss")]

    def _fetch_tmd(self):
        return [Discussion(source="tmd", region="Thailand",
                           title="TMD Weather Warnings",
                           text="See data.tmd.go.th for TMD alerts.",
                           valid_from="", valid_to="",
                           url="https://data.tmd.go.th/api/")]

    def _fetch_saws(self):
        return [Discussion(source="saws", region="South Africa",
                           title="SAWS Warnings",
                           text="See weathersa.co.za for SAWS warnings.",
                           valid_from="", valid_to="",
                           url="https://www.weathersa.co.za/home/warnings")]

    def _fetch_wmo(self):
        return [Discussion(source="wmo_swic", region="Global",
                           title="WMO Severe Weather Information Centre",
                           text="See severeweather.wmo.int for global severe weather alerts.",
                           valid_from="", valid_to="",
                           url="https://severeweather.wmo.int")]
