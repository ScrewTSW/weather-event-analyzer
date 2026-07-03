from __future__ import annotations

from dataclasses import dataclass

from core.models.base import BaseDataResult


@dataclass
class Discussion(BaseDataResult):
    region: str = ""
    title: str = ""
    text: str = ""
    valid_from: str = ""
    valid_to: str = ""
    url: str = ""

    def cache_key(self) -> str:
        return f"json/discussion/{self.source}/{self.title}.json"
