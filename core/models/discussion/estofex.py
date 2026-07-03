from __future__ import annotations

import html
import re
from dataclasses import dataclass

from core.models.discussion.base import Discussion


def _clean_html(raw: str) -> str:
    text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


@dataclass
class ESTOFEXDiscussion(Discussion):
    @classmethod
    def parse(cls, raw_html: str, url: str) -> ESTOFEXDiscussion:
        text = _clean_html(raw_html)
        return cls(
            source="estofex", region="Europe",
            title="ESTOFEX Convective Forecast",
            text=text[:3000], valid_from="", valid_to="", url=url,
        )
