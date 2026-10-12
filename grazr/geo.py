"""Paddock geometry: faces, acres, rest days, GeoJSON (spec §4.3, §6.3)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from pyproj import Geod
from shapely import set_precision
from shapely.geometry import Polygon, mapping
from shapely.ops import polygonize, unary_union

from .model import Entity
from .parse import Warning

GEOD = Geod(ellps="WGS84")
SQM_PER_ACRE = 4046.8564224
GRID = 1e-7   # ~1 cm in degrees; snaps nearly-coincident fence lines


def polygon(e: Entity) -> Polygon | None:
    if not e.perimeter:
        return None
    return Polygon([(lon, lat) for lat, lon in e.perimeter])


def check(e: Entity) -> Polygon | None:
    """A valid polygon, or None with a bad-perimeter warning on the entity."""
    if e.perimeter is None:
        return None
    if len(e.perimeter) < 3:
        e.warnings.append(Warning(e.path, 1, "bad-perimeter", "perimeter has fewer than 3 points", "error"))
        return None
    p = polygon(e)
    if not p.is_valid or p.area == 0:
        e.warnings.append(Warning(e.path, 1, "bad-perimeter", "perimeter crosses itself or has no area", "error"))
        return None
    return p


def acres(p: Polygon) -> float:
    area, _ = GEOD.geometry_area_perimeter(p)
    return abs(area) / SQM_PER_ACRE


@dataclass
class Face:
    geom: Polygon
    paddocks: list[str]      # every paddock (parents included) containing it
    acres: float


def faces(polys: dict[str, Polygon]) -> list[Face]:
    """Cut all paddock polygons into disjoint faces where they overlap.

    Parents are included in the linework, so the part of a parent no child
    covers comes out as its own face (D-21).
    """
    if not polys:
        return []
    snapped = {k: set_precision(p, GRID) for k, p in polys.items()}
    lines = unary_union([p.exterior for p in snapped.values() if not p.is_empty])
    out = []
    for f in polygonize(lines):
        if f.area == 0:
            continue
        pt = f.representative_point()
        inside = sorted(k for k, p in snapped.items() if p.contains(pt))
        if inside:
            out.append(Face(f, inside, acres(f)))
    return out


def last_grazed(occs, as_of: datetime) -> tuple[bool, datetime | None]:
    """(occupied at as_of, latest end before as_of) for (start, end) pairs."""
    occupied, last = False, None
    for start, end in occs:
        if start > as_of:
            continue
        if end is None or end > as_of:
            occupied = True
        elif last is None or end > last:
            last = end
    return occupied, last


def rest_days(occupied: bool, last: datetime | None, as_of: datetime) -> float | None:
    """0 while occupied, None if never grazed."""
    if occupied:
        return 0.0
    if last is None:
        return None
    return (as_of - last).total_seconds() / 86400


def geojson(paddocks: list[Entity]) -> str:
    feats = []
    for e in paddocks:
        p = polygon(e)
        if p is None or len(e.perimeter) < 3:
            continue
        props = {k: v for k, v in e.fields.items() if k != "perimeter"}
        props = json.loads(json.dumps(props, default=str))
        props["id"] = e.id
        if p.is_valid:
            props["acres"] = round(acres(p), 3)
        feats.append({"type": "Feature", "id": e.id, "properties": props, "geometry": mapping(p)})
    return json.dumps({"type": "FeatureCollection", "features": feats}, indent=1)
