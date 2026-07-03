import html
import logging
import re

from core.models import Discussion
from core.api_request import CachedRequest

from core.alerts import (
    is_us_location, is_europe_location, is_canada_location,
    is_australia_location, is_japan_location, is_india_location,
    is_south_america_location, is_southeast_asia_location,
    is_southern_africa_location,
)

logger = logging.getLogger(__name__)


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
        dispatch = {
            "spc_md": self._fetch_spc_md,
            "spc_outlook": self._fetch_spc_outlook,
            "estofex": self._fetch_estofex,
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

    def _fetch_estofex(self):
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
