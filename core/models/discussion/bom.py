from __future__ import annotations

from dataclasses import dataclass

from core.models.discussion.base import Discussion


@dataclass
class BOMWarning(Discussion):
    @classmethod
    def parse(cls, warning_dict: dict, url: str) -> BOMWarning:
        title = warning_dict.get("title", "BOM Warning") if isinstance(warning_dict, dict) else "BOM Warning"
        text = warning_dict.get("short_text", "") if isinstance(warning_dict, dict) else str(warning_dict)
        return cls(
            source="bom", region="Australia",
            title=title, text=text[:3000],
            valid_from="", valid_to="", url=url,
        )
