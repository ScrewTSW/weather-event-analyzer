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
    station: str | None = None  # radiosonde site, when observed rather than modelled
    # Surface level at `elevation`: 2 m temperature/dewpoint, 10 m wind. The parcel for
    # SBCAPE starts here — the first standard level is often ~100 m above the ground
    surface_pressure: float | None = None
    surface_temperature: float | None = None
    surface_dewpoint: float | None = None
    surface_wind_speed: float | None = None
    surface_wind_direction: float | None = None

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
    station: str | None = None  # radiosonde site, when observed rather than modelled
    # Surface level per timestamp — see PressureLevelSnapshot
    surface_pressure: list[float | None] = field(default_factory=list)
    surface_temperature: list[float | None] = field(default_factory=list)
    surface_dewpoint: list[float | None] = field(default_factory=list)
    surface_wind_speed: list[float | None] = field(default_factory=list)
    surface_wind_direction: list[float | None] = field(default_factory=list)

    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/pressure/{self.source}/{self.latitude}_{self.longitude}_{date}_hourly.json"
