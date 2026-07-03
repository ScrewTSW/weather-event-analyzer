from __future__ import annotations

from dataclasses import dataclass, field

from core.models.base import BaseDataResult


@dataclass
class Alert(BaseDataResult):
    event_type: str = ""
    severity: str = ""
    title: str = ""
    description: str = ""
    geometry: dict | None = None
    valid_from: str = ""
    valid_to: str = ""
    source_url: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class SourceResult:
    source: str
    alerts: list[Alert]
