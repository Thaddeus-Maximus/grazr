"""Offline base imagery (spec §8.3): cached under .grazr/tiles/z/x/y.

The default `usgs-imagery` source is public domain: the USGS tile cache up to
zoom 16, and full-resolution NAIP (0.6–1 m) rendered per tile above that,
because the tile cache stops at 16. Any XYZ URL template also works.
"""
from __future__ import annotations

import math
import re
import urllib.request
from pathlib import Path

from .paths import state_dir

USGS_TILE = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
NAIP_EXPORT = ("https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/"
               "exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&format=jpg&f=image")
USGS_TILE_MAX = 16
R = 6378137.0
UA = {"User-Agent": "grazr (offline farm map cache)"}


def _url(source: str, z: int, x: int, y: int) -> str:
    if source == "usgs-imagery":
        if z <= USGS_TILE_MAX:
            return USGS_TILE.format(z=z, x=x, y=y)
        n = 2 ** z
        size = 2 * math.pi * R / n
        o = math.pi * R
        bbox = f"{x * size - o:.3f},{o - (y + 1) * size:.3f},{(x + 1) * size - o:.3f},{o - y * size:.3f}"
        return NAIP_EXPORT.format(bbox=bbox)
    return source.format(z=z, x=x, y=y, s="a")


def _path(root: Path, z: int, x: int, y: int) -> Path:
    return state_dir(root) / "tiles" / str(z) / str(x) / f"{y}.jpg"


def fetch(root: Path, source: str, z: int, x: int, y: int, timeout: float = 20,
          retries: int = 1) -> bytes | None:
    p = _path(root, z, x, y)
    if p.exists():
        return p.read_bytes()
    req = urllib.request.Request(_url(source, z, x, y), headers=UA)
    for attempt in range(retries + 1):     # NAIP sometimes times out once
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200 or not r.headers.get("Content-Type", "").startswith("image/"):
                    return None
                data = r.read()
                break
        except OSError:
            if attempt == retries:
                return None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return data


def serve(root: Path, config, rest: str):
    """`z/x/y` → (content type, bytes) from the cache, fetching (and caching) if online."""
    m = re.fullmatch(r"(\d+)/(\d+)/(\d+)(?:\.\w+)?", rest)
    if not m:
        return None
    z, x, y = map(int, m.groups())
    t = config["tiles"] or {}
    if not (int(t.get("min_zoom", 0)) <= z <= int(t.get("max_zoom", 19))):
        return None
    data = fetch(root, t.get("source", "usgs-imagery"), z, x, y, timeout=8, retries=0)
    if data is None:
        return None
    ctype = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return ctype, data


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def plan(bounds: tuple[float, float, float, float], zmin: int, zmax: int):
    """(south, west, north, east) → every (z, x, y) covering it."""
    s, w, n, e = bounds
    for z in range(zmin, zmax + 1):
        x0, y0 = tile_xy(n, w, z)
        x1, y1 = tile_xy(s, e, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


def download(root: Path, config, polygons, zoom: tuple[int, int] | None = None,
             margin_m: float = 300, workers: int = 8, progress=None, limit: int | None = None):
    """Fetch every missing tile around the paddocks. → (fetched, failed, missing before).

    `progress(done, total, failed)` is called as tiles finish. Stops early if
    the first 20 tiles all fail (offline).
    """
    from concurrent.futures import ThreadPoolExecutor

    t = config["tiles"] or {}
    zmin, zmax = zoom or (int(t.get("min_zoom", 13)), int(t.get("max_zoom", 19)))
    bounds = bounds_for(polygons, config["map"]["center"], margin_m)
    todo = [zxy for zxy in plan(bounds, zmin, zmax) if not _path(root, *zxy).exists()]
    if limit is not None and len(todo) > limit:
        return 0, 0, len(todo)
    source = t.get("source", "usgs-imagery")
    done = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ok in pool.map(lambda zxy: fetch(root, source, *zxy) is not None, todo):
            done += 1
            failed += not ok
            if progress:
                progress(done, len(todo), failed)
            if done == 20 and failed == 20:
                pool.shutdown(wait=False, cancel_futures=True)
                break
    return done - failed, failed, len(todo)


def bounds_for(polygons: list[list[tuple[float, float]]], center, margin_m: float = 300):
    """Bounding box of all paddocks plus a margin, or around the map center."""
    pts = [pt for poly in polygons for pt in poly]
    if pts:
        s, n = min(p[0] for p in pts), max(p[0] for p in pts)
        w, e = min(p[1] for p in pts), max(p[1] for p in pts)
    else:
        s = n = float(center[0])
        w = e = float(center[1])
        margin_m = max(margin_m, 800)
    dlat = margin_m / 111_320
    dlon = margin_m / (111_320 * max(math.cos(math.radians((s + n) / 2)), 0.01))
    return s - dlat, w - dlon, n + dlat, e + dlon
