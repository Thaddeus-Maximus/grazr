"""Link → entity resolution and animal-ID normalization (spec §4.1, §5.4)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config

NONE_ID = "none"  # reserved target for retiring a herd (§5.3)

_VARIATION = re.compile("[︎️]")
_SPACE = re.compile(r"\s+")


def norm(s: str) -> str:
    """Case- and whitespace-insensitive key for an ID or link target."""
    return _SPACE.sub(" ", _VARIATION.sub("", s)).strip().casefold()


def link_target(inner: str) -> str:
    """`[[target#heading|alias]]` inner text → target."""
    return inner.split("|", 1)[0].split("#", 1)[0].strip()


class AnimalIds:
    """Turns spellings like `o 10`, `e010`, `🟠010` into `orange-010`."""

    def __init__(self, config: Config):
        self.digits = config.id_digits
        self._by_token: dict[str, str] = {}
        for s in config.series:
            for w in s.words:
                self._by_token[w.casefold()] = s.name
            for e in s.emoji:
                self._by_token[_VARIATION.sub("", e)] = s.name
        tokens = sorted(self._by_token, key=len, reverse=True)
        alt = "|".join(re.escape(t) for t in tokens) or r"(?!)"
        self._link = re.compile(rf"^({alt})[ \-_#]?0*(\d+)$", re.IGNORECASE)
        # Bare text in prose (unlinked mentions / migrate). One-letter words
        # must touch the number or use `-`, so "plan b 2" isn't an animal.
        words = sorted((t for t in tokens if t.isalpha()), key=len, reverse=True)
        long_alt = "|".join(re.escape(w) for w in words if len(w) > 1) or r"(?!)"
        short_alt = "|".join(re.escape(w) for w in words if len(w) == 1) or r"(?!)"
        self._bare = re.compile(
            rf"(?<![\w\[#/.])(?:({long_alt})[ \-_]?|({short_alt})-?)(\d{{1,4}})(?![\w\]])",
            re.IGNORECASE)
        # "007 wether" (number, then a full series word)
        self._bare_rev = re.compile(
            rf"(?<![\w\[#/.])(\d{{1,4}})[ \-_]({long_alt})(?![\w\]])", re.IGNORECASE)
        self.series_names = {s.name.casefold(): s.name for s in config.series}

    def canonical(self, series: str, number: int | str) -> str:
        return f"{series}-{int(number):0{self.digits}d}"

    def match(self, text: str) -> str | None:
        """Canonical animal ID if `text` is an animal spelling, else None."""
        m = self._link.match(_VARIATION.sub("", text.strip()))
        if not m:
            return None
        return self.canonical(self._by_token[m.group(1).casefold()], m.group(2))

    def find_bare(self, text: str):
        """Yield (start, end, canonical_id) for bare animal IDs in prose."""
        for m in self._bare.finditer(text):
            word = m.group(1) or m.group(2)
            yield m.start(), m.end(), self.canonical(self._by_token[word.casefold()], m.group(3))
        for m in self._bare_rev.finditer(text):
            yield m.start(), m.end(), self.canonical(self._by_token[m.group(2).casefold()], m.group(1))


@dataclass
class Resolved:
    kind: str           # "entity" | "note" | "none" | "unresolved"
    id: str | None      # entity ID or note path
    etype: str | None = None
    shorthand: bool = False   # resolved through the animal rule, not literally


class Resolver:
    def __init__(self, animal_ids: AnimalIds, entities: dict[str, str],
                 notes: dict[str, str], aliases: dict[str, str] | None = None):
        """
        entities: entity ID → type.  notes: note path → (unused) for stems.
        aliases: an entity's other names (`aliases:` in its file) → its ID.
        """
        self.animal_ids = animal_ids
        self.entity_type = entities
        self._entities = {norm(i): i for i in entities}
        self._aliases = {norm(a): i for a, i in (aliases or {}).items() if i in entities}
        self._notes: dict[str, str] = {}
        for path in notes:
            stem = path.rsplit("/", 1)[-1]
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            self._notes.setdefault(norm(stem), path)
            self._notes.setdefault(norm(path[:-3] if path.lower().endswith(".md") else path), path)

    def resolve(self, target: str) -> Resolved:
        key = norm(target)
        if key in self._entities:                       # D-2: exact ID first
            eid = self._entities[key]
            return Resolved("entity", eid, self.entity_type[eid])
        animal = self.animal_ids.match(target)
        if animal is not None:
            short = key != norm(animal)
            eid = self._entities.get(norm(animal))
            if eid:
                return Resolved("entity", eid, self.entity_type[eid], shorthand=short)
            return Resolved("unresolved", animal, "animals", shorthand=short)
        if key in self._aliases:
            eid = self._aliases[key]
            return Resolved("entity", eid, self.entity_type[eid])
        if key == NONE_ID:
            return Resolved("none", NONE_ID)
        if key in self._notes:
            return Resolved("note", self._notes[key])
        return Resolved("unresolved", None)
