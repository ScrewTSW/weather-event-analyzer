from __future__ import annotations

from dataclasses import dataclass

from core.models.atmosphere.base import AtmosphereData


@dataclass
class OpenMeteoForecast(AtmosphereData):
    past_days: int = 7

    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/atmosphere/openmeteo-forecast/{self.latitude}_{self.longitude}_{date}.json"


@dataclass
class OpenMeteoArchive(AtmosphereData):
    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/atmosphere/openmeteo-archive/{self.latitude}_{self.longitude}_{date}.json"
