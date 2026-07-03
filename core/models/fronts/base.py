from __future__ import annotations

from dataclasses import dataclass, field

from core.models.base import BaseDataResult


@dataclass
class FrontalFeature:
    type: str
    lats: list[float]
    lons: list[float]
    is_merger: bool = False
    label: str = ""
