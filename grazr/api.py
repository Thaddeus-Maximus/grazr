"""Operations shared by the CLI and the web server."""
from __future__ import annotations

import json
from pathlib import Path

from . import geo, migrate
from .ids import Resolver
from .index import Index
from .paths import atomic_write


class Farm:
    def __init__(self, root: Path):
        self.root = root
        self.index = Index(root)
        self.index.refresh(force=True)

    @property
    def db(self):
        return self.index.db

    def refresh(self) -> bool:
        return self.index.refresh()

    def resolver(self) -> Resolver:
        ix = self.index
        ents = {r["id"]: r["type"] for r in self.db.execute("SELECT id, type FROM entities")}
        return Resolver(ix.animal_ids, ents, dict.fromkeys(ix.cache))

    # --- lint ------------------------------------------------------------
    def warnings(self, include_info: bool = False):
        q = "SELECT * FROM warnings"
        if not include_info:
            q += " WHERE severity != 'info'"
        return self.db.execute(q + " ORDER BY path COLLATE NOCASE, line, code").fetchall()

    def apply_fix(self, path: str, line: int, fix: dict) -> bool:
        """One-click lint fix on one line of one file. → whether anything changed."""
        p = self.root / path
        text = p.read_text(encoding="utf-8-sig")
        lines = text.split("\n")
        if not 1 <= line <= len(lines):
            return False
        old = lines[line - 1]
        if fix["kind"] == "link":
            new = migrate.link_bare_in_line(old, fix["text"], fix["to"])
        elif fix["kind"] == "shorthand":
            new = migrate.fix_shorthand(old, self.resolver())
        else:
            return False
        if new is None or new == old:
            return False
        lines[line - 1] = new
        atomic_write(p, "\n".join(lines))
        self.refresh()
        return True

    # --- bulk rewrites -----------------------------------------------------
    def fix_all(self) -> dict[str, tuple[str, str]]:
        """Every file whose shorthand links would change → (old, new)."""
        res = self.resolver()
        out = {}
        for r in sorted(self.index.cache, key=str.casefold):
            old = self.index.cache[r].text
            new = migrate.fix_shorthand(old, res)
            if new != old:
                out[r] = (old, new)
        return out

    def migrate(self, paths: list[str] | None = None):
        ents = {r["id"] for r in self.db.execute("SELECT id FROM entities")}
        out, skipped = {}, {}
        for r, c in sorted(self.index.cache.items(), key=lambda kv: kv[0].casefold()):
            if c.kind != "note":
                continue
            if paths and not any(r == p or r.startswith(p.rstrip("/") + "/") for p in paths):
                continue
            new, sk = migrate.migrate_text(c.text, self.index.animal_ids, ents.__contains__)
            if new != c.text:
                out[r] = (c.text, new)
            if sk:
                skipped[r] = sk
        return out, skipped

    # --- timelines (spec §6.5) --------------------------------------------
    def timeline(self, entity: str, limit: int | None = None):
        """Entries for an entity, newest first, each with `via` (None = direct).

        Direct mentions, entries mentioning a herd while the animal was in it,
        and entries mentioning a child paddock.
        """
        q = """
        SELECT * FROM (
        SELECT e.*, NULL AS via, min(x.inherited) AS inherited
          FROM mentions x JOIN entries e ON e.id = x.entry_id
         WHERE x.kind = 'entity' AND x.target = :id
         GROUP BY e.id
        UNION
        SELECT e.*, m.herd AS via, min(x.inherited) AS inherited
          FROM memberships m
          JOIN mentions x ON x.kind = 'entity' AND x.target = m.herd
          JOIN entries e ON e.id = x.entry_id
         WHERE m.animal = :id AND e.ts >= m.start AND (m.end IS NULL OR e.ts < m.end)
           AND e.id NOT IN (SELECT entry_id FROM mentions WHERE target = :id)
         GROUP BY e.id
        UNION
        SELECT e.*, c.id AS via, min(x.inherited) AS inherited
          FROM entities c
          JOIN mentions x ON x.kind = 'entity' AND x.target = c.id
          JOIN entries e ON e.id = x.entry_id
         WHERE c.parent = :id
           AND e.id NOT IN (SELECT entry_id FROM mentions WHERE target = :id)
         GROUP BY e.id)
        ORDER BY ts IS NULL, ts DESC, path, line"""
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.db.execute(q, {"id": entity}).fetchall()

    # --- export ----------------------------------------------------------
    def geojson(self) -> str:
        ents = [c.parsed for c in self.index.cache.values()
                if c.kind == "entity" and c.etype == "paddocks"]
        return geo.geojson(sorted(ents, key=lambda e: e.id.casefold()))

    def summary(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]  # noqa: E731
        return {
            "files": q("SELECT count(*) FROM files"),
            "animals": q("SELECT count(*) FROM entities WHERE type='animals'"),
            "herds": q("SELECT count(*) FROM entities WHERE type='herds'"),
            "paddocks": q("SELECT count(*) FROM entities WHERE type='paddocks'"),
            "entries": q("SELECT count(*) FROM entries"),
            "moves": q("SELECT count(*) FROM moves"),
            "warnings": q("SELECT count(*) FROM warnings WHERE severity != 'info'"),
        }


def fix_json(row) -> dict | None:
    return json.loads(row["fix"]) if row["fix"] else None
