from __future__ import annotations

import html
import re
from dataclasses import dataclass

from core.models.discussion.base import Discussion


@dataclass
class SPCMesoscaleDiscussion(Discussion):
    md_number: int | None = None
    areas_affected: str = ""

    @classmethod
    def parse(cls, raw_html: str, url: str) -> SPCMesoscaleDiscussion | None:
        pre_match = re.search(r'<pre>(.*?)</pre>', raw_html, re.DOTALL)
        if not pre_match:
            return None
        text = html.unescape(pre_match.group(1)).strip()
        number_match = re.search(r'md(\d+)', url)
        md_num = int(number_match.group(1)) if number_match else None
        areas_match = re.search(r'Areas affected\.\.\.(.*?)(?:\n\n|\n   Concerning)', text, re.DOTALL)
        areas = areas_match.group(1).strip() if areas_match else ""
        return cls(
            source="spc_md", region="US",
            title=f"SPC Mesoscale Discussion #{md_num}" if md_num else "SPC MD",
            text=text[:3000], valid_from="", valid_to="", url=url,
            md_number=md_num, areas_affected=areas,
        )


@dataclass
class SPCOutlook(Discussion):
    day: int = 1

    @classmethod
    def parse(cls, raw_text: str) -> SPCOutlook:
        return cls(
            source="spc_outlook", region="US",
            title="SPC Day 1 Convective Outlook",
            text=raw_text[:3000], valid_from="", valid_to="",
            url="https://www.spc.noaa.gov/products/outlook/day1otlk.html",
        )
