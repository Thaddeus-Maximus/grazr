"""Entities and their structural fields (spec §4.0)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .frontmatter import link_value
from .parse import Warning


@dataclass
class Entity:
    id: str
    type: str                 # "animals", "herds", "paddocks", or a declared type
    path: str
    fields: dict              # all frontmatter, free fields included
    body: str = ""
    body_line: int = 1
    # structural, validated
    born: date | None = None
    left: date | None = None
    left_unknown: bool = False   # `left: unknown`: gone, date not recorded
    dam: str | None = None    # raw link target; resolved by the index
    sire: str | None = None
    parent: str | None = None
    birth_type: int | None = None
    anonymous: bool = False
    perimeter: list[tuple[float, float]] | None = None   # [(lat, lon), ...]
    warnings: list[Warning] = field(default_factory=list)


def _date(e: Entity, key: str):
    v = e.fields.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    from . import dates
    got = dates.parse_when(str(v), None)
    if got is not None:
        return got.dt.date()
    e.warnings.append(Warning(e.path, 1, "bad-frontmatter", f"`{key}` is not a date: {v!r}", "error"))
    return None


def _perimeter(e: Entity):
    v = e.fields.get("perimeter")
    if v in (None, "", []):
        return None
    pts = []
    try:
        for p in v:
            lat, lon = float(p[0]), float(p[1])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
            pts.append((lat, lon))
    except (TypeError, ValueError, IndexError, KeyError):
        e.warnings.append(Warning(e.path, 1, "bad-perimeter",
                                  "perimeter must be a list of [lat, lon] pairs", "error"))
        return None
    return pts


def build(eid: str, etype: str, path: str, fields: dict, body: str, body_line: int) -> Entity:
    e = Entity(eid, etype, path, fields, body, body_line)
    if etype == "animals":
        e.born = _date(e, "born")
        lv = fields.get("left")
        if lv is True or (isinstance(lv, str) and lv.strip().lower() in ("unknown", "yes", "?")):
            e.left_unknown = True
            e.warnings.append(Warning(path, 1, "left-unknown", "left on an unknown date", "info"))
        else:
            e.left = _date(e, "left")
        e.dam = link_value(fields.get("dam"))
        e.sire = link_value(fields.get("sire"))
        bt = fields.get("birth_type")
        if bt not in (None, ""):
            try:
                e.birth_type = int(bt)
            except (TypeError, ValueError):
                e.warnings.append(Warning(path, 1, "bad-frontmatter",
                                          f"`birth_type` is not a number: {bt!r}", "error"))
    elif etype == "herds":
        a = fields.get("anonymous")
        if isinstance(a, str):
            a = a.strip().lower() in ("true", "yes", "1")
        e.anonymous = bool(a)
    elif etype == "paddocks":
        e.parent = link_value(fields.get("parent"))
        e.perimeter = _perimeter(e)
    return e
