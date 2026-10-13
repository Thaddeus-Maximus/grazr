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


def last_grazed(occs, as_of: datetime) -> tuple[datetime | None, datetime | None]:
    """(occupied since, latest end) at `as_of` for (start, end) pairs.

    "Since" runs back through occupancies that touch end to start, so one herd
    leaving as another arrives counts as one stretch.
    """
    occs = [(s, e) for s, e in occs if s <= as_of]
    since = min((s for s, e in occs if e is None or e > as_of), default=None)
    last = max((e for s, e in occs if e is not None and e <= as_of), default=None)
    if since is not None:
        changed = True
        while changed:
            changed = False
            for s, e in occs:
                if s < since and (e is None or e >= since):
                    since, changed = s, True
    return since, last


def rest_days(since: datetime | None, last: datetime | None, as_of: datetime) -> float | None:
    """Signed days: rested if positive, occupied for that long if negative (never 0.0
    while occupied, so `< 0` always means occupied). None if never grazed."""
    if since is not None:
        return -max((as_of - since).total_seconds() / 86400, 1e-6)
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
