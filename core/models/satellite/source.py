"""Per-source satellite handlers.

Each source knows its cadence, latency, URL patterns, and how to probe
for the latest available frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


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

    def layer_for(self, ts: str) -> str:
        return self.layer

    def name_for(self, ts: str) -> str:
        return self.name


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


@dataclass(frozen=True)
class PolarPass:
    name: str
    layer: str
    local_hour: float   # equator-crossing local solar time of the daytime pass
    first_date: date    # first day in the GIBS archive (capabilities, checked 2026-11-02)


# Per time slot in preference order — VIIRS has no gaps between swaths, Aqua does
POLAR_SLOTS = (
    (PolarPass("MODIS Terra", "MODIS_Terra_CorrectedReflectance_TrueColor", 10.5, date(2000, 2, 24)),),
    (PolarPass("VIIRS SNPP", "VIIRS_SNPP_CorrectedReflectance_TrueColor", 13.5, date(2015, 11, 24)),
     PolarPass("MODIS Aqua", "MODIS_Aqua_CorrectedReflectance_TrueColor", 13.5, date(2002, 7, 3))),
)


class DailyPolarSnapshot(SatelliteSource):
    """Fallback for dates outside the geostationary archive: the daily polar-orbiter
    mosaics (2000+), one frame per pass at the local solar time it crosses `lon`.

    GIBS snaps TIME to the UTC day, so the frame timestamp is the approximate pass
    time; `passes` maps each timestamp to the layer that was found to have data.
    """

    def __init__(self, day: date, lon: float):
        super().__init__(name="daily_polar", layer="", source_type="gibs_wms",
                         cadence_min=1440, latency_min=180)
        self.day, self.lon = day, lon
        self.passes: dict[str, PolarPass] = {}

    def slots(self) -> list[tuple[str, list[PolarPass]]]:
        """(timestamp, candidate passes) for each slot that has an archive on `day`."""
        out = []
        for slot in POLAR_SLOTS:
            candidates = [p for p in slot if p.first_date <= self.day]
            if candidates:
                hours = candidates[0].local_hour - self.lon / 15
                t = datetime.combine(self.day, datetime.min.time()) + timedelta(hours=hours)
                out.append((self.snap_10min(t).strftime("%Y-%m-%dT%H:%M:%SZ"), candidates))
        return out

    @staticmethod
    def snap_10min(dt: datetime) -> datetime:
        return dt.replace(minute=dt.minute // 10 * 10, second=0, microsecond=0)

    def layer_for(self, ts: str) -> str:
        return self.passes[ts].layer

    def name_for(self, ts: str) -> str:
        return f"{self.passes[ts].name} (daily pass)"


@dataclass(frozen=True)
class GOESArchive:
    """One GOES-R satellite at one position, as archived in NOAA's open-data bucket."""
    name: str
    bucket: str
    side: str                          # "east" / "west" — which select_source products it serves
    sat_lon: float
    first_date: date
    last_date: date | None             # None = still operational
    x_bounds: tuple[float, float]      # CONUS/PACUS sector extent, scan angle (rad)
    y_bounds: tuple[float, float]


# Checked 2026-11-02 against bucket listings and x/y_image_bounds in the files. No files
# exist while a satellite drifts between positions (G16 2017-12-01…12-13, G17 to 2018-11-12).
_EAST_CONUS = ((-0.10136, 0.03864), (0.04424, 0.12824))
_WEST_PACUS = ((-0.07, 0.07), (0.04424, 0.12824))
GOES_ARCHIVES = (
    GOESArchive("GOES-16", "noaa-goes16", "east", -89.5, date(2017, 2, 28), date(2017, 11, 30),
                (-0.07504, 0.06496), (0.04256, 0.12656)),   # checkout position
    GOESArchive("GOES-16", "noaa-goes16", "east", -75.0, date(2017, 12, 14), date(2025, 4, 7),
                *_EAST_CONUS),
    GOESArchive("GOES-19", "noaa-goes19", "east", -75.0, date(2025, 4, 7), None, *_EAST_CONUS),
    GOESArchive("GOES-18", "noaa-goes18", "west", -137.0, date(2023, 1, 4), None, *_WEST_PACUS),
    GOESArchive("GOES-17", "noaa-goes17", "west", -137.0, date(2018, 11, 13), date(2023, 1, 10),
                *_WEST_PACUS),
)


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int


# Per-frame rendering, chosen from the solar elevation over the frame
DAY, TWILIGHT, NIGHT = "day", "twilight", "night"
_MODE_BANDS = {DAY: ("C01", "C02", "C03"), TWILIGHT: ("C01", "C02", "C03", "C13"), NIGHT: ("C13",)}
_MODE_LABEL = {DAY: ("TrueColor", "True Color"), TWILIGHT: ("TrueColor_IR", "True Color + IR"),
               NIGHT: ("Band13_IR", "Band 13 IR")}


class GOESArchiveImagery(SatelliteSource):
    """ABI CONUS/PACUS sector imagery from NOAA's S3 bucket (2017+), the loop for events
    older than the ~90 days GIBS keeps. Each frame is true colour (0.5 km) in daylight,
    Band 13 IR at night, blended across the terminator — `modes` holds the choice per
    timestamp, `files` the S3 objects of the scan used.
    """

    PRODUCT = "ABI-L2-CMIPC"

    def __init__(self, archive: GOESArchive, true_color: bool = True):
        super().__init__(name=f"{archive.name} (NOAA archive)", layer=f"{archive.name}_ABI",
                         source_type="goes_s3", cadence_min=10, latency_min=0)
        self.archive = archive
        self.true_color = true_color
        self.modes: dict[str, str] = {}
        self.files: dict[str, dict[str, S3Object]] = {}

    @staticmethod
    def bands_for(mode: str) -> tuple[str, ...]:
        return _MODE_BANDS[mode]

    def layer_for(self, ts: str) -> str:
        return f"{self.archive.name}_ABI_{_MODE_LABEL[self.modes[ts]][0]}"

    def name_for(self, ts: str) -> str:
        return f"{self.archive.name} {_MODE_LABEL[self.modes[ts]][1]} (NOAA archive)"


def goes_archives_for(day: date, product: str) -> list[GOESArchive]:
    """Archives that were imaging on `day`, the product's own side first."""
    side = "west" if product.startswith("goes_west") else "east"
    live = [a for a in GOES_ARCHIVES
            if a.first_date <= day and (a.last_date is None or day <= a.last_date)]
    return sorted(live, key=lambda a: a.side != side)


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
