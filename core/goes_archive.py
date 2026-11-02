"""GOES-R ABI archive frames from NOAA's S3 buckets: partial NetCDF reads + rendering.

Only the 250x250 chunks under the requested lon/lat box are downloaded (HTTP Range via
CachedRequest.fetch_range); the HDF5 metadata is read through a small block-cached file
object. Day frames are true colour (C01 blue, C02 red, C03 veggie -> synthetic green),
pan-sharpened to C02's 0.5 km; night frames are colour-enhanced Band 13 IR; the
terminator blends the two by solar elevation per pixel.
"""

from __future__ import annotations

import io
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from PIL import Image

from core.api_request import CachedRequest
from core.models.satellite.source import S3Object
from core.projection import goes_fixed_grid, solar_elevation

# Solar elevation (deg) ramp: IR at or below NIGHT_ELEV, true colour at or above DAY_ELEV
NIGHT_ELEV = 0.0
DAY_ELEV = 8.0

# Band 13 brightness temperature (K) -> RGB: grey for warm surfaces and low cloud,
# colour for cold cloud tops (blue -30 °C ... red -73 °C, white overshooting tops)
IR_STOPS = (
    (330.0, (0, 0, 0)), (240.0, (205, 205, 205)), (239.9, (90, 160, 255)),
    (230.0, (20, 50, 210)), (220.0, (0, 190, 60)), (210.0, (250, 240, 0)),
    (200.0, (230, 20, 0)), (192.0, (120, 0, 0)), (185.0, (255, 255, 255)),
)
WEBP_QUALITY = 85   # 2048 px frame ~0.5-1.5 MB vs ~8 MB as PNG
META_BLOCK = 64 * 1024
MERGE_GAP = 16 * 1024   # join chunk ranges closer than this into one request


class S3RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file for h5py, fetched in cached blocks."""

    def __init__(self, req: CachedRequest, url: str, size: int):
        self.req, self.url, self.size, self.pos = req, url, size, 0
        self.blocks: dict[int, bytes] = {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def tell(self):
        return self.pos

    def _block(self, i: int) -> bytes:
        if i not in self.blocks:
            start = i * META_BLOCK
            self.blocks[i] = self.req.fetch_range(self.url, start, min(self.size, start + META_BLOCK))
        return self.blocks[i]

    def readinto(self, buf):
        n = max(0, min(len(buf), self.size - self.pos))
        out = bytearray()
        while len(out) < n:
            i, off = divmod(self.pos + len(out), META_BLOCK)
            out += self._block(i)[off:off + n - len(out)]
        buf[:n] = out
        self.pos += n
        return n


@dataclass
class _Band:
    """One band's window: where its chunks are, then (after fetch) the decoded values."""
    url: str
    x: np.ndarray                 # full-sector scan angles (rad)
    y: np.ndarray
    chunk: tuple[int, int]
    dtype: np.dtype
    scale: float
    offset: float
    fill: int
    origin: tuple[int, int]       # (row, col) of the window's first chunk
    shape: tuple[int, int]        # window size, whole chunks
    chunks: list[tuple[int, int, int | None, int, bool]] = field(default_factory=list)
    values: np.ndarray | None = None   # float32 physical values, NaN = no data

    def window_coords(self) -> tuple[np.ndarray, np.ndarray]:
        r0, c0 = self.origin
        return self.x[c0:c0 + self.shape[1]], self.y[r0:r0 + self.shape[0]]


def _unpack(var) -> np.ndarray:
    return var[()] * var.attrs["scale_factor"][0] + var.attrs["add_offset"][0]


def _index(grid: np.ndarray, coords: np.ndarray) -> np.ndarray:
    """Nearest index of each scan angle on an evenly spaced axis (float, NaN outside)."""
    i = np.rint((grid - coords[0]) / (coords[1] - coords[0]))
    return np.where((i >= 0) & (i < len(coords)), i, np.nan)


def _locate(req: CachedRequest, obj: S3Object, url: str, geo_out: dict) -> _Band | None:
    """Read the band's metadata and list the chunks covering the output grid. The first
    band also projects the output grid (`geo_out` lats/lons -> gx/gy scan angles)."""
    import h5py
    f = S3RangeFile(req, url, obj.size)
    with h5py.File(f, "r") as h:
        if "gx" not in geo_out:
            p = h["goes_imager_projection"].attrs
            geo_out.update(sat_lon=float(p["longitude_of_projection_origin"][0]),
                           h=float(p["perspective_point_height"][0]),
                           req=float(p["semi_major_axis"][0]), rpol=float(p["semi_minor_axis"][0]))
            gx, gy = goes_fixed_grid(geo_out["lats"], geo_out["lons"], **{
                k: geo_out[k] for k in ("sat_lon", "h", "req", "rpol")})
            geo_out.update(gx=gx, gy=gy)
        gx, gy = geo_out["gx"], geo_out["gy"]
        d = h["CMI"]
        x, y = _unpack(h["x"]), _unpack(h["y"])
        cols, rows = _index(gx, x), _index(gy, y)
        ok = np.isfinite(cols) & np.isfinite(rows)
        if not ok.any():
            return None
        ch = d.chunks
        r0, r1 = int(rows[ok].min()) // ch[0], int(rows[ok].max()) // ch[0]
        c0, c1 = int(cols[ok].min()) // ch[1], int(cols[ok].max()) // ch[1]
        band = _Band(url=url, x=x, y=y, chunk=ch, dtype=d.dtype,
                     scale=float(d.attrs["scale_factor"][0]), offset=float(d.attrs["add_offset"][0]),
                     fill=int(d.attrs["_FillValue"][0]),
                     origin=(r0 * ch[0], c0 * ch[1]),
                     shape=((r1 - r0 + 1) * ch[0], (c1 - c0 + 1) * ch[1]))
        gzip_only = d.compression in (None, "gzip") and not d.shuffle and not d.fletcher32
        if not gzip_only:  # unexpected layout: let h5py read the window (slower, still partial)
            band.values = _physical(band, d[band.origin[0]:band.origin[0] + band.shape[0],
                                            band.origin[1]:band.origin[1] + band.shape[1]])
            return band
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                info = d.id.get_chunk_info_by_coord((r * ch[0], c * ch[1]))
                compressed = d.compression == "gzip" and not (info.filter_mask & 1)
                band.chunks.append((r - r0, c - c0, info.byte_offset, info.size, compressed))
    return band


def _physical(band: _Band, raw: np.ndarray) -> np.ndarray:
    out = raw.astype(np.float32) * band.scale + band.offset
    out[raw == band.fill] = np.nan
    return out


def _spans(band: _Band) -> list[list]:
    """Merge the band's chunks, in file order, into as few byte ranges as possible."""
    spans = []
    for ch in sorted((c for c in band.chunks if c[2] is not None), key=lambda c: c[2]):
        if spans and ch[2] - spans[-1][1] <= MERGE_GAP:
            spans[-1][1] = ch[2] + ch[3]
            spans[-1][2].append(ch)
        else:
            spans.append([ch[2], ch[2] + ch[3], [ch]])
    return spans


def _fetch_all(req: CachedRequest, bands: list[_Band]) -> None:
    """Download every band's chunk ranges in parallel and decode them into `values`."""
    jobs = [(b, span) for b in bands if b.values is None for span in _spans(b)]

    def get(job):
        b, (start, end, _) = job
        return req.fetch_range(b.url, start, end, timeout=60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        blobs = list(pool.map(get, jobs))
    raws = {id(b): np.full(b.shape, b.fill, dtype=b.dtype) for b in bands if b.values is None}
    for (b, (start, _, chunks)), blob in zip(jobs, blobs):
        ch = b.chunk
        for r, c, off, size, compressed in chunks:
            data = blob[off - start:off - start + size]
            arr = np.frombuffer(zlib.decompress(data) if compressed else data, dtype=b.dtype)
            raws[id(b)][r * ch[0]:(r + 1) * ch[0], c * ch[1]:(c + 1) * ch[1]] = arr.reshape(ch)
    for b in bands:
        if b.values is None:
            b.values = _physical(b, raws[id(b)])


def _sample(band: _Band, gx, gy, values: np.ndarray | None = None, step: int = 1) -> np.ndarray:
    """Nearest-neighbour sample of `values` (default band.values) onto the output grid."""
    values = band.values if values is None else values
    x, y = band.window_coords()
    if step > 1:  # coordinates of a block-averaged grid
        x = x[:len(x) // step * step].reshape(-1, step).mean(1)
        y = y[:len(y) // step * step].reshape(-1, step).mean(1)
    cols, rows = _index(gx, x), _index(gy, y)
    ok = np.isfinite(cols) & np.isfinite(rows)
    out = np.full(gx.shape, np.nan, np.float32)
    out[ok] = values[rows[ok].astype(np.intp), cols[ok].astype(np.intp)]
    return out


def _block_mean(a: np.ndarray, step: int) -> np.ndarray:
    h, w = a.shape[0] // step * step, a.shape[1] // step * step
    return a[:h, :w].reshape(h // step, step, w // step, step).mean(axis=(1, 3))


def ir_colorize(tb: np.ndarray) -> np.ndarray:
    """Brightness temperature (K, NaN = no data) -> RGBA uint8, transparent where NaN."""
    temps = [t for t, _ in reversed(IR_STOPS)]
    rgba = np.zeros(tb.shape + (4,), np.uint8)
    for c in range(3):
        rgba[..., c] = np.interp(np.nan_to_num(tb, nan=330.0), temps,
                                 [rgb[c] for _, rgb in reversed(IR_STOPS)])
    rgba[..., 3] = np.where(np.isnan(tb), 0, 255)
    return rgba


def true_color(blue, red, veggie, red_1km=None) -> np.ndarray:
    """Reflectance factors -> RGBA uint8 (CIMSS recipe: green = .45 R + .1 NIR + .45 B,
    gamma 2.2). With `red_1km`, blue/veggie (1 km) are sharpened by red's 0.5 km detail."""
    if red_1km is not None:
        ratio = np.clip(np.where(red_1km > 0.01, red / np.maximum(red_1km, 0.01), 1.0), 0.5, 2.0)
        blue, veggie = blue * ratio, veggie * ratio
    r, b, v = (np.clip(np.nan_to_num(a), 0, 1) for a in (red, blue, veggie))
    g = 0.45 * r + 0.1 * v + 0.45 * b
    rgb = np.stack([r, g, b], axis=-1) ** (1 / 2.2)
    rgba = np.zeros(red.shape + (4,), np.uint8)
    rgba[..., :3] = (rgb * 255).astype(np.uint8)
    rgba[..., 3] = np.where(np.isnan(red) | np.isnan(blue) | np.isnan(veggie), 0, 255)
    return rgba


def output_grid(bbox: tuple, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre lats (column) and lons (row) of a size² equirectangular frame — the
    same frame a GIBS WMS GetMap returns for `bbox` (W, S, E, N)."""
    west, south, east, north = bbox
    lons = (west + (np.arange(size, dtype=np.float32) + 0.5) * (east - west) / size)[None, :]
    lats = (north - (np.arange(size, dtype=np.float32) + 0.5) * (north - south) / size)[:, None]
    return lats, lons


def render_frame(req: CachedRequest, bucket: str, objects: dict[str, S3Object],
                 bbox: tuple, size: int, when: datetime) -> bytes | None:
    """Fetch the given bands (C13 and/or C01+C02+C03) for one scan and render a WebP.
    Both present -> blended across the terminator by per-pixel solar elevation."""
    lats, lons = output_grid(bbox, size)
    geo: dict = {"lats": lats, "lons": lons}
    bands = {}
    for name, obj in sorted(objects.items()):
        band = _locate(req, obj, f"https://{bucket}.s3.amazonaws.com/{obj.key}", geo)
        if band is None:
            return None  # outside this band's sector
        bands[name] = band
    _fetch_all(req, list(bands.values()))
    gx, gy = geo["gx"], geo["gy"]

    layers = []
    if "C02" in bands:
        red = bands["C02"]
        layers.append(true_color(_sample(bands["C01"], gx, gy), _sample(red, gx, gy),
                                 _sample(bands["C03"], gx, gy),
                                 red_1km=_sample(red, gx, gy, _block_mean(red.values, 2), step=2)))
    if "C13" in bands:
        layers.append(ir_colorize(_sample(bands["C13"], gx, gy)))
    rgba = layers[0]
    if len(layers) == 2:
        day, night = layers
        w = np.clip((solar_elevation(lats, lons, when) - NIGHT_ELEV) / (DAY_ELEV - NIGHT_ELEV), 0, 1)
        w = np.where(day[..., 3] > 0, w, 0.0)[..., None]
        rgba = (day * w + night * (1 - w)).astype(np.uint8)
        rgba[..., 3] = np.maximum(day[..., 3], night[..., 3])

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue()
