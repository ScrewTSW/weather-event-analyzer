from __future__ import annotations

from dataclasses import dataclass, field

from core.models.base import BaseDataResult


@dataclass
class PressureLevelSnapshot(BaseDataResult):
    synoptic_time: str | None = None
    pressure_levels: list[int] = field(default_factory=list)
    temperature: list[float | None] = field(default_factory=list)
    dewpoint: list[float | None] = field(default_factory=list)
    wind_speed: list[float | None] = field(default_factory=list)
    wind_direction: list[float | None] = field(default_factory=list)
    geopotential_height: list[float | None] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    elevation: float | None = None  # surface height MSL (m); levels below it are extrapolated

    def cache_key(self) -> str:
        return f"json/pressure/{self.source}/{self.latitude}_{self.longitude}_{self.synoptic_time}.json"


@dataclass
class PressureLevelHourly(BaseDataResult):
    pressure_levels: list[int] = field(default_factory=list)
    timestamps: list[str] = field(default_factory=list)
    temperature: list[list[float | None]] = field(default_factory=list)
    dewpoint: list[list[float | None]] = field(default_factory=list)
    wind_speed: list[list[float | None]] = field(default_factory=list)
    wind_direction: list[list[float | None]] = field(default_factory=list)
    geopotential_height: list[list[float | None]] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    elevation: float | None = None  # surface height MSL (m); levels below it are extrapolated

    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/pressure/{self.source}/{self.latitude}_{self.longitude}_{date}_hourly.json"
