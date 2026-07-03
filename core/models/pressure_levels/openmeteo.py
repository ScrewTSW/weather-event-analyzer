from __future__ import annotations

from dataclasses import dataclass

from core.models.pressure_levels.base import PressureLevelSnapshot, PressureLevelHourly


@dataclass
class OpenMeteoPressureSnapshot(PressureLevelSnapshot):
    def cache_key(self) -> str:
        return f"json/pressure/openmeteo/{self.latitude}_{self.longitude}_{self.synoptic_time}.json"


@dataclass
class OpenMeteoPressureHourly(PressureLevelHourly):
    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/pressure/openmeteo/{self.latitude}_{self.longitude}_{date}_hourly.json"
