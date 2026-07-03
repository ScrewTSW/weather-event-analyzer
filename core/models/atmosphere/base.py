from __future__ import annotations

from dataclasses import dataclass, field

from core.models.base import BaseDataResult


@dataclass
class VariableTimeSeries:
    data: list[float | None]
    unit: str


@dataclass
class AtmosphereData(BaseDataResult):
    gridded: bool = False
    timestamps: list[str] = field(default_factory=list)
    variables: dict[str, VariableTimeSeries] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None

    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/atmosphere/{self.source}/{self.latitude}_{self.longitude}_{date}.json"
