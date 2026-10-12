"""Farm settings from ``grazr.yaml`` (spec §3.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

CONFIG_NAME = "grazr.yaml"

BUILTIN_TYPES = {"animals": "animals", "herds": "herds", "paddocks": "paddocks"}

DEFAULTS = {
    "units": {"area": "acres", "length": "feet", "weight": "lb", "temperature": "F"},
    "timezone": "America/Chicago",
    "note_filename": "%Y%b%d-%H%M",
    "id_digits": 3,
    "rest_full_days": 60,
    "map": {"center": [40.35, -89.13], "zoom": 16},
    "tiles": {"source": "usgs-imagery", "min_zoom": 13, "max_zoom": 19},
    "attachment_dirs": ["attachments"],
    "series": {},
    "types": {},
}


@dataclass
class Series:
    name: str
    words: list[str]
    emoji: list[str]
    defaults: dict


@dataclass
class Config:
    raw: dict
    series: list[Series] = field(default_factory=list)
    # entity type name -> folder (relative to root)
    types: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def id_digits(self) -> int:
        return int(self.raw["id_digits"])

    def type_for_folder(self, folder: str) -> str | None:
        for name, f in self.types.items():
            if f.casefold() == folder.casefold():
                return name
        return None


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    data: dict = {}
    if path.exists():
        loaded = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    return from_dict(data)


def from_dict(data: dict) -> Config:
    raw = {**DEFAULTS, **(data or {})}
    series = []
    for name, s in (raw.get("series") or {}).items():
        s = s or {}
        words = [str(w) for w in (s.get("words") or [])]
        if name not in words:
            words.insert(0, str(name))
        series.append(Series(
            name=str(name),
            words=words,
            emoji=[str(e) for e in (s.get("emoji") or [])],
            defaults=dict(s.get("defaults") or {}),
        ))
    types = dict(BUILTIN_TYPES)
    for name, t in (raw.get("types") or {}).items():
        types[str(name)] = str((t or {}).get("folder", name))
    return Config(raw=raw, series=series, types=types)
