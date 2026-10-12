"""Operations shared by the CLI and the web server."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import geo, migrate
from .ids import Resolver
from .index import TS, Index
from .paths import atomic_write


def parse_ts(s: str | None) -> datetime | None:
    return datetime.strptime(s, TS) if s else None


def fields(row) -> dict:
    return json.loads(row["fields"]) if row and row["fields"] else {}


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
        if getattr(self, "_resolver_gen", None) != ix.generation:
            ents = {r["id"]: r["type"] for r in self.db.execute("SELECT id, type FROM entities")}
            self._resolver = Resolver(ix.animal_ids, ents, dict.fromkeys(ix.cache))
            self._resolver_gen = ix.generation
        return self._resolver

    @property
    def config(self):
        return self.index.config

    def q(self, sql: str, *args):
        return self.db.execute(sql, args).fetchall()

    def q1(self, sql: str, *args):
        return self.db.execute(sql, args).fetchone()

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

    # --- entities --------------------------------------------------------
    def entity(self, eid: str):
        return self.q1("SELECT * FROM entities WHERE id = ?", eid)

    def entities(self, etype: str):
        return self.q("SELECT * FROM entities WHERE type = ? ORDER BY id COLLATE NOCASE", etype)

    def field_names(self, etype: str) -> list[str]:
        names = set()
        for r in self.entities(etype):
            names.update(fields(r))
        names.discard("perimeter")
        return sorted(names, key=str.casefold)

    def tags(self) -> list[str]:
        return [r[0] for r in self.q("SELECT tag FROM tags GROUP BY tag ORDER BY count(*) DESC")]

    def next_number(self, series: str) -> int:
        """Numbers are never reused within a series (§4.1)."""
        n = 0
        prefix = series.casefold() + "-"
        for r in self.q("SELECT id FROM entities WHERE type='animals'"):
            i = r["id"].casefold()
            if i.startswith(prefix) and i[len(prefix):].isdigit():
                n = max(n, int(i[len(prefix):]))
        return n + 1

    # --- herds and memberships ----------------------------------------------
    @staticmethod
    def _at(at: datetime | None) -> str:
        return (at or datetime.now()).strftime(TS)

    def membership(self, animal: str, at: datetime | None = None):
        t = self._at(at)
        return self.q1("""SELECT * FROM memberships WHERE animal = ? AND start <= ?
                          AND (end IS NULL OR end > ?) ORDER BY start DESC""", animal, t, t)

    def members(self, herd: str, at: datetime | None = None):
        t = self._at(at)
        return self.q("""SELECT e.*, m.start AS joined, m.inherited FROM memberships m
                         JOIN entities e ON e.id = m.animal
                         WHERE m.herd = ? AND m.start <= ? AND (m.end IS NULL OR m.end > ?)
                         ORDER BY e.id COLLATE NOCASE""", herd, t, t)

    def head(self, herd: str, at: datetime | None = None) -> int | None:
        t = self._at(at)
        r = self.q1("""SELECT count FROM heads WHERE herd = ? AND start <= ?
                       AND (end IS NULL OR end > ?)""", herd, t, t)
        return r["count"] if r else None

    def occupying(self, herd: str | None = None, paddock: str | None = None,
                  at: datetime | None = None):
        t = self._at(at)
        sql = "SELECT * FROM occupancies WHERE start <= ? AND (end IS NULL OR end > ?)"
        args = [t, t]
        if herd:
            sql += " AND herd = ?"
            args.append(herd)
        if paddock:
            sql += " AND paddock = ?"
            args.append(paddock)
        return self.q(sql + " ORDER BY start", *args)

    def herd_size(self, herd_row, at: datetime | None = None) -> int | None:
        if herd_row["anonymous"]:
            return self.head(herd_row["id"], at)
        return len(self.members(herd_row["id"], at))

    def idle(self, herd_row) -> bool:
        """No animals and no paddock: hidden by default, name reusable (D-20)."""
        return not self.herd_size(herd_row) and not self.occupying(herd=herd_row["id"])

    # --- lineage (§6.4) ---------------------------------------------------
    def pedigree(self, eid: str | None, depth: int = 4):
        if not eid or depth == 0:
            return None
        row = self.entity(eid)
        node = {"id": eid, "exists": row is not None, "dam": None, "sire": None}
        if row is not None and depth > 1:
            node["dam"] = self.pedigree(row["dam"], depth - 1)
            node["sire"] = self.pedigree(row["sire"], depth - 1)
        return node

    def offspring(self, eid: str):
        return self.q("""SELECT * FROM entities WHERE type='animals' AND (dam = ? OR sire = ?)
                         ORDER BY born, id""", eid, eid)

    def lambing(self, eid: str) -> list[dict]:
        """Per year: lambs born to this dam, dates, sex, birth type, still here."""
        years: dict[str, list] = {}
        for r in self.q("SELECT * FROM entities WHERE type='animals' AND dam = ? ORDER BY born, id", eid):
            years.setdefault((r["born"] or "????")[:4], []).append(r)
        today = datetime.now().date().isoformat()
        out = []
        for y, lambs in sorted(years.items(), reverse=True):
            out.append({"year": y, "lambs": [{
                "id": r["id"], "born": r["born"], "sex": fields(r).get("sex"),
                "birth_type": r["birth_type"],
                "active": not (r["left_on"] and r["left_on"] <= today)} for r in lambs]})
        return out

    # --- paddocks: rest (§6.3) ----------------------------------------------
    def parents(self) -> set[str]:
        return {r[0] for r in self.q("SELECT DISTINCT parent FROM entities WHERE parent IS NOT NULL")}

    def _face_paddocks(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for r in self.q("SELECT face_id, paddock FROM face_paddocks"):
            out.setdefault(r[0], []).append(r[1])
        return out

    def rest(self, as_of: datetime | None = None) -> dict:
        """Rest days per face and per paddock (exact, effective) at `as_of`.

        0 means occupied, None means never grazed.
        """
        as_of = as_of or datetime.now()
        occ: dict[str, list] = {}
        for o in self.q("SELECT paddock, start, end FROM occupancies"):
            occ.setdefault(o["paddock"], []).append((parse_ts(o["start"]), parse_ts(o["end"])))
        exact = {p: geo.rest_days(*geo.last_grazed(pairs, as_of), as_of) for p, pairs in occ.items()}
        faces, effective = {}, {}
        for fid, pads in self._face_paddocks().items():
            pairs = [pr for p in pads for pr in occ.get(p, [])]
            days = geo.rest_days(*geo.last_grazed(pairs, as_of), as_of)
            faces[fid] = days
            for p in pads:          # D-10: the most recently grazed face wins
                if days is not None and (effective.get(p) is None or days < effective[p]):
                    effective[p] = days
                else:
                    effective.setdefault(p, None)
        return {"faces": faces, "exact": exact, "effective": effective}

    def map_data(self, as_of: datetime | None = None) -> dict:
        as_of = as_of or datetime.now()
        rest = self.rest(as_of)
        parents = self.parents()
        herds_on: dict[str, list[str]] = {}
        for o in self.occupying(at=as_of):
            herds_on.setdefault(o["paddock"], []).append(o["herd"])
        face_pads = self._face_paddocks()
        feats = []
        for f in self.q("SELECT * FROM faces"):
            pads = face_pads.get(f["id"], [])
            feats.append({"type": "Feature", "geometry": json.loads(f["geojson"]), "properties": {
                "kind": "face", "id": f["id"], "acres": f["acres"],
                "rest": rest["faces"].get(f["id"]),
                "paddocks": [p for p in pads if p not in parents] or pads,
                "herds": sorted({h for p in pads for h in herds_on.get(p, [])})}})
        for p in self.q("SELECT * FROM entities WHERE type='paddocks' AND polygon IS NOT NULL"):
            pts = json.loads(p["polygon"])
            if len(pts) < 3:
                continue
            ring = [[lon, lat] for lat, lon in pts] + [[pts[0][1], pts[0][0]]]
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {
                              "kind": "paddock", "id": p["id"], "parent": p["id"] in parents,
                              "acres": p["acres"], "rest_exact": rest["exact"].get(p["id"]),
                              "rest_effective": rest["effective"].get(p["id"]),
                              "herds": herds_on.get(p["id"], [])}})
        return {"type": "FeatureCollection", "features": feats,
                "as_of": as_of.strftime(TS), "rest_full_days": self.config["rest_full_days"]}

    # --- feeds --------------------------------------------------------------
    def recent(self, limit: int = 60):
        return self.q("""SELECT * FROM entries WHERE ts IS NOT NULL AND ts <= ?
                         ORDER BY ts DESC, path, line LIMIT ?""",
                      datetime.now().strftime(TS), limit)

    def tagged(self, tag: str):
        return self.q("""SELECT e.* FROM tags t JOIN entries e ON e.id = t.entry_id
                         WHERE t.tag = ? COLLATE NOCASE ORDER BY e.ts IS NULL, e.ts DESC""", tag)

    def entities_in(self, entry_ids: list[int]):
        if not entry_ids:
            return []
        marks = ",".join("?" * len(entry_ids))
        return self.q(f"""SELECT x.target AS id, count(*) AS n, min(e.type) AS type
                          FROM mentions x JOIN entities e ON e.id = x.target
                          WHERE x.kind='entity' AND x.entry_id IN ({marks})
                          GROUP BY x.target ORDER BY n DESC, x.target""", *entry_ids)

    def search(self, query: str, limit: int = 200):
        words = re.findall(r"[\w-]+", query)
        if not words:
            return []
        fts = " ".join('"' + w + '"*' for w in words)
        return self.q("""SELECT f.text, f.path, f.line, f.entity, e.ts, e.id AS entry_id
                         FROM fts f LEFT JOIN entries e ON e.id = f.entry_id
                         WHERE fts MATCH ? ORDER BY rank LIMIT ?""", fts, limit)

    def photos(self, eid: str):
        rows = list(self.q("SELECT * FROM images WHERE entity = ? AND path IS NOT NULL", eid))
        ids = [r["id"] for r in self.timeline(eid)]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows += self.q(f"""SELECT i.* FROM images i WHERE i.entry_id IN ({marks})
                               AND i.path IS NOT NULL""", *chunk)
        return rows

    def note_entries(self, path: str):
        return self.q("SELECT * FROM entries WHERE path = ? ORDER BY line", path)

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
