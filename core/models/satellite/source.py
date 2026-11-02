"""Per-source satellite handlers.

Each source knows its cadence, latency, URL patterns, and how to probe
for the latest available frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class SatelliteSource:
    name: str
    layer: str
    source_type: str        # "gibs_wms", "gibs_wmts", "eumetsat_wms"
    cadence_min: int = 10
    latency_min: int = 40   # typical delay before data appears
    matrix: str = "500m"
    max_zoom: int = 6

    def snap_time(self, dt: datetime) -> datetime:
        return dt.replace(
            minute=(dt.minute // self.cadence_min) * self.cadence_min,
            second=0, microsecond=0,
        )

    def estimate_latest(self) -> datetime:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delayed = now - timedelta(minutes=self.latency_min)
        return self.snap_time(delayed)

    def generate_timestamps(self, start: datetime, end: datetime) -> list[str]:
        start = self.snap_time(start)
        timestamps = []
        t = start
        while t <= end:
            timestamps.append(t.strftime("%Y-%m-%dT%H:%M:%SZ"))
            t += timedelta(minutes=self.cadence_min)
        return timestamps


class GOESEastGeocolor(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="goes_east_geocolor",
            layer="GOES-East_ABI_GeoColor",
            source_type="gibs_wms",
            cadence_min=10,
            latency_min=40,
        )


class GOESWestGeocolor(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="goes_west_geocolor",
            layer="GOES-West_ABI_GeoColor",
            source_type="gibs_wms",
            cadence_min=10,
            latency_min=40,
        )


class GOESEastIR(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="goes_east",
            layer="GOES-East_ABI_Band13_Clean_Infrared",
            source_type="gibs_wmts",
            cadence_min=10,
            latency_min=40,
            matrix="500m",
            max_zoom=6,
        )


class GOESWestIR(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="goes_west",
            layer="GOES-West_ABI_Band13_Clean_Infrared",
            source_type="gibs_wmts",
            cadence_min=10,
            latency_min=40,
            matrix="500m",
            max_zoom=6,
        )


class HimawariIR(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="himawari",
            layer="Himawari_AHI_Band13_Clean_Infrared",
            source_type="gibs_wmts",
            cadence_min=10,
            latency_min=50,
            matrix="500m",
            max_zoom=6,
        )


class HimawariTruecolor(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="himawari_truecolor",
            layer="Himawari_AHI_Band3_Red_Visible_1km",
            source_type="gibs_wms",
            cadence_min=10,
            latency_min=50,
        )


class MeteosatIR(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="meteosat_ir",
            layer="msg_fes:ir108",
            source_type="eumetsat_wms",
            cadence_min=15,
            latency_min=30,
        )


class MeteosatVis(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="meteosat_vis",
            layer="msg_fes:vis006",
            source_type="eumetsat_wms",
            cadence_min=15,
            latency_min=30,
        )


class MODISTerra(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="modis_terra",
            layer="MODIS_Terra_CorrectedReflectance_TrueColor",
            source_type="gibs_wmts",
            cadence_min=1440,
            latency_min=180,
            matrix="250m",
            max_zoom=8,
        )


class VIIRS(SatelliteSource):
    def __init__(self):
        super().__init__(
            name="viirs",
            layer="VIIRS_SNPP_CorrectedReflectance_TrueColor",
            source_type="gibs_wmts",
            cadence_min=1440,
            latency_min=180,
            matrix="250m",
            max_zoom=8,
        )


SOURCES = {
    "goes_east": GOESEastIR(),
    "goes_west": GOESWestIR(),
    "goes_east_geocolor": GOESEastGeocolor(),
    "goes_west_geocolor": GOESWestGeocolor(),
    "himawari": HimawariIR(),
    "himawari_truecolor": HimawariTruecolor(),
    "meteosat_ir": MeteosatIR(),
    "meteosat_vis": MeteosatVis(),
    "modis_terra": MODISTerra(),
    "viirs": VIIRS(),
}


def select_source(lat: float, lon: float, product: str = "auto") -> SatelliteSource:
    if product != "auto" and product in SOURCES:
        return SOURCES[product]
    if -125 <= lon <= -30:
        return SOURCES["goes_east_geocolor"]
    if -180 <= lon <= -125 or 150 <= lon <= 180:
        return SOURCES["goes_west_geocolor"]
    if 90 <= lon < 150:
        return SOURCES["himawari"]
    return SOURCES["meteosat_ir"]
