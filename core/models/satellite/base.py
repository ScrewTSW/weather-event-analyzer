from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models.base import BaseDataResult


@dataclass
class SatelliteFrame(BaseDataResult):
    timestamp: str = ""
    image: Any = None  # PIL.Image.Image
    layer: str = ""
    bbox: tuple[float, float, float, float] | None = None  # (south, north, west, east)
