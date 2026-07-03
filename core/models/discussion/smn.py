from __future__ import annotations

from dataclasses import dataclass

from core.models.discussion.base import Discussion


@dataclass
class SMNDiscussion(Discussion):
    @classmethod
    def parse(cls, alert_dict: dict, url: str) -> SMNDiscussion:
        return cls(
            source="smn", region="Argentina",
            title=alert_dict.get("title", "SMN Alert"),
            text=alert_dict.get("description", "")[:3000],
            valid_from=alert_dict.get("date", ""),
            valid_to="", url=url,
        )
