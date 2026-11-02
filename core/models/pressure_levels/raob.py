from __future__ import annotations

from dataclasses import dataclass

from core.models.pressure_levels.base import PressureLevelHourly


@dataclass
class RaobPressureHourly(PressureLevelHourly):
    """Observed radiosonde ascents (IEM archive), interpolated onto the standard levels.

    Only the launch times (00/12 UTC, plus specials) are present — not every hour.
    """
    distance_km: float | None = None

    def cache_key(self) -> str:
        date = self.timestamps[0][:10] if self.timestamps else "unknown"
        return f"json/pressure/raob/{self.station}_{date}_hourly.json"
