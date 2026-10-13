"""The SQLite index: derived, disposable, rebuilt from the files (spec §7)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import geo
from .derive import Derived, derive
from .frontmatter import load as load_fm
from .ids import AnimalIds, Resolver, norm
from .model import Entity, build as build_entity
from .parse import IMAGE_EXT, Note, Warning, find_images, parse_note
from .paths import iter_markdown, rel, state_dir

SCHEMA = 6
TS = "%Y-%m-%dT%H:%M"
UNKNOWN = "unknown"   # entities.left_on for `left: unknown`

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files (path TEXT PRIMARY KEY, kind TEXT, type TEXT, mtime REAL);
CREATE TABLE entities (
  id TEXT PRIMARY KEY, type TEXT, path TEXT, fields TEXT, body TEXT,
  born TEXT, left_on TEXT, dam TEXT, sire TEXT, parent TEXT,
  anonymous INTEGER, birth_type INTEGER, acres REAL, polygon TEXT);
CREATE TABLE entries (
  id INTEGER PRIMARY KEY, path TEXT, line INTEGER, ts TEXT, approx INTEGER,
  text TEXT, parent_line INTEGER, move_kind TEXT);
CREATE TABLE mentions (entry_id INTEGER, kind TEXT, target TEXT, inherited INTEGER);
CREATE TABLE tags (entry_id INTEGER, tag TEXT);
CREATE TABLE images (entry_id INTEGER, entity TEXT, name TEXT, path TEXT);
CREATE TABLE moves (path TEXT, line INTEGER, ts TEXT, kind TEXT, lhs TEXT, rhs TEXT);
CREATE TABLE memberships (animal TEXT, herd TEXT, start TEXT, end TEXT, inherited INTEGER, path TEXT, line INTEGER);
CREATE TABLE occupancies (herd TEXT, paddock TEXT, start TEXT, end TEXT, path TEXT, line INTEGER);
CREATE TABLE exits (animal TEXT PRIMARY KEY, ts TEXT, kind TEXT, weight REAL, unit TEXT, basis TEXT, cause TEXT, path TEXT, line INTEGER);
CREATE TABLE heads (herd TEXT, count INTEGER, start TEXT, end TEXT, path TEXT, line INTEGER);
CREATE TABLE faces (id INTEGER PRIMARY KEY, geojson TEXT, acres REAL);
CREATE TABLE face_paddocks (face_id INTEGER, paddock TEXT);
CREATE TABLE warnings (path TEXT, line INTEGER, code TEXT, severity TEXT, message TEXT, fix TEXT);
CREATE VIRTUAL TABLE fts USING fts5(text, path UNINDEXED, line UNINDEXED, entry_id UNINDEXED, entity UNINDEXED);
CREATE INDEX mentions_target ON mentions(target);
CREATE INDEX mentions_entry ON mentions(entry_id);
CREATE INDEX tags_tag ON tags(tag);
CREATE INDEX entries_ts ON entries(ts);
CREATE INDEX entries_path ON entries(path, line);
CREATE INDEX entities_parent ON entities(parent);
CREATE INDEX memberships_animal ON memberships(animal);
CREATE INDEX memberships_herd ON memberships(herd);
CREATE INDEX occupancies_herd ON occupancies(herd);
CREATE INDEX occupancies_paddock ON occupancies(paddock);
"""


def ts(dt: datetime | None) -> str | None:
    return dt.strftime(TS) if dt else None


@dataclass
class _Cached:
    mtime: float
    size: int
    kind: str                  # "entity" | "note"
    etype: str | None
    text: str
    parsed: object             # Entity or Note


class Index:
    def __init__(self, root: Path, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or state_dir(root) / "index.sqlite"
        self.lock = threading.RLock()
        self.cache: dict[str, _Cached] = {}
        self.config = None
        self._config_mtime = None
        self.faces_key = None
        self.faces: list[geo.Face] = []
        self.generation = 0
        self.images: dict[str, str] = {}
        self.db = self._open()

    # --- database --------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if self.db_path.exists():
            try:
                db = sqlite3.connect(self.db_path, check_same_thread=False)
                v = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if v and int(v[0]) == SCHEMA:
                    db.row_factory = sqlite3.Row
                    return db
                db.close()
            except sqlite3.DatabaseError:
                pass
            self.db_path.unlink()
        db = sqlite3.connect(self.db_path, check_same_thread=False)
        db.executescript(DDL)
        db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
        db.commit()
        db.row_factory = sqlite3.Row
        return db

    # --- scanning --------------------------------------------------------
    def _classify(self, relpath: str) -> tuple[str, str | None]:
        top = relpath.split("/", 1)[0] if "/" in relpath else None
        etype = self.config.type_for_folder(top) if top else None
        return ("entity", etype) if etype else ("note", None)

    def refresh(self, force: bool = False) -> bool:
        """Re-parse changed files and rebuild if anything changed. → changed?"""
        with self.lock:
            cpath = self.root / config_mod.CONFIG_NAME
            cm = cpath.stat().st_mtime if cpath.exists() else None
            if self.config is None or cm != self._config_mtime:
                self.config = config_mod.load(self.root)
                self._config_mtime = cm
                self.animal_ids = AnimalIds(self.config)
                self.cache.clear()
                force = True
            seen = set()
            changed = force
            for p in iter_markdown(self.root):
                r = rel(self.root, p)
                seen.add(r)
                try:
                    s = p.stat()
                except OSError:
                    continue
                c = self.cache.get(r)
                if c and c.mtime == s.st_mtime and c.size == s.st_size:
                    continue
                self._parse(r, p, s)
                changed = True
            for gone in set(self.cache) - seen:
                del self.cache[gone]
                changed = True
            if changed:
                self._rebuild()
                self.generation += 1
            return changed

    def _parse(self, r: str, p: Path, s: os.stat_result):
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            text = ""
            parsed = Note(r, None, [], [], [Warning(r, 1, "bad-frontmatter", f"can't read file: {exc}", "error")])
            self.cache[r] = _Cached(s.st_mtime, s.st_size, "note", None, text, parsed)
            return
        kind, etype = self._classify(r)
        if kind == "entity":
            fields, sp, err = load_fm(text)
            stem = r.rsplit("/", 1)[-1][:-3]
            ent = build_entity(stem, etype, r, fields, sp.body, sp.body_line)
            if err:
                ent.warnings.append(Warning(r, 1, "bad-frontmatter", err, "error"))
            if etype == "paddocks":
                geo.check(ent)
            parsed = ent
        else:
            parsed = parse_note(r, text, self.animal_ids, tuple(self.config["exit_words"]))
        self.cache[r] = _Cached(s.st_mtime, s.st_size, kind, etype, text, parsed)

    # --- rebuild ---------------------------------------------------------
    def _rebuild(self):
        warnings: list[Warning] = []
        entities: dict[str, Entity] = {}
        seen_norm: dict[str, str] = {}
        for r in sorted(self.cache, key=str.casefold):
            c = self.cache[r]
            if c.kind != "entity":
                continue
            e: Entity = c.parsed
            warnings.extend(e.warnings)
            key = norm(e.id)
            if key in seen_norm:
                other = entities[seen_norm[key]]
                warnings.append(Warning(r, 1, "id-collision",
                                        f"ID {e.id!r} is also used by {other.path}", "error"))
                e = Entity(**{**e.__dict__, "id": r})
            elif key == "none":
                warnings.append(Warning(r, 1, "id-collision", "`none` is reserved (moving a herd to [[none]] retires it)", "error"))
            else:
                animal = self.animal_ids.match(e.id)
                if animal is not None and norm(animal) != key:
                    warnings.append(Warning(r, 1, "id-collision",
                                            f"{e.id!r} also reads as animal {animal}; "
                                            f"{'rename the file to ' + animal if e.type == 'animals' else 'links to it win over the animal'}"))
            seen_norm.setdefault(key, e.id)
            entities[e.id] = e
        notes = {r: c.parsed for r, c in self.cache.items() if c.kind == "note"}
        resolver = Resolver(self.animal_ids, {k: e.type for k, e in entities.items()},
                            {r: None for r in self.cache})
        rcache: dict[str, object] = {}

        def resolve(t):
            if t not in rcache:
                rcache[t] = resolver.resolve(t)
            return rcache[t]

        entries = [e for n in notes.values() for e in n.entries]
        for n in notes.values():
            warnings.extend(n.warnings)
        der = derive(entities, entries, resolve)
        warnings.extend(der.warnings)

        # frontmatter links
        for e in entities.values():
            for key in ("dam", "sire", "parent"):
                t = getattr(e, key)
                if t:
                    self._check_link(e.path, None, t, resolve(t), warnings)

        polys = {}
        for e in entities.values():
            if e.type == "paddocks" and e.perimeter and not any(
                    w.code == "bad-perimeter" for w in e.warnings):
                polys[e.id] = geo.polygon(e)
        fkey = tuple(sorted((k, tuple(entities[k].perimeter)) for k in polys))
        if fkey != self.faces_key:
            self.faces = geo.faces(polys)
            self.faces_key = fkey

        images = self.images = self._image_lookup()
        self._write(entities, notes, der, resolve, polys, images, warnings)

    def _check_link(self, path, line, target, r, warnings, text_line=None):
        if r.kind == "unresolved":
            warnings.append(Warning(path, line or 1, "unresolved-link", f"[[{target}]] doesn't match any file"))
        elif r.shorthand and r.kind == "entity":
            warnings.append(Warning(path, line or 1, "shorthand-link", f"[[{target}]] → [[{r.id}]]",
                                    fix={"kind": "shorthand", "from": target, "to": r.id}))

    def _image_lookup(self) -> dict[str, str]:
        """Filename (casefolded) → path, searching attachment_dirs then the root."""
        found: dict[str, str] = {}
        dirs = []
        for d in self.config["attachment_dirs"] or []:
            p = Path(d)
            dirs.append(p if p.is_absolute() else self.root / p)
        dirs.append(self.root)
        for d in dirs:
            if not d.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(d):
                dirnames[:] = [x for x in dirnames if not x.startswith(".")]
                for f in filenames:
                    if f.lower().endswith(IMAGE_EXT):
                        found.setdefault(f.casefold(), str(Path(dirpath) / f))
        return found

    def _find_image(self, images, name, note_path):
        if "/" in name or "\\" in name:
            p = (self.root / note_path).parent / name
            if p.exists():
                return str(p)
            p = self.root / name
            if p.exists():
                return str(p)
        return images.get(name.replace("\\", "/").rsplit("/", 1)[-1].casefold())

    def _write(self, entities, notes, der: Derived, resolve, polys, images, warnings):
        """Replace every table's rows in one transaction, batched per table.

        "Via herd" and parent-paddock visibility (§6.5) are not materialized:
        they are joins over memberships and `parent` at query time (see api).
        """
        def rid(t):
            r = resolve(t) if t else None
            return r.id if r is not None and r.kind == "entity" else t

        ent_rows, fts_rows, img_rows = [], [], []
        for e in entities.values():
            p = polys.get(e.id)
            ent_rows.append((
                e.id, e.type, e.path, json.dumps(e.fields, default=str), e.body,
                str(e.born) if e.born else None,
                der.left_at[e.id].date().isoformat() if e.id in der.left_at
                else UNKNOWN if e.left_unknown else None,
                rid(e.dam), rid(e.sire), rid(e.parent), int(e.anonymous),
                der.birth_types.get(e.id), geo.acres(p) if p is not None else None,
                json.dumps(e.perimeter) if e.perimeter else None))
            if e.body.strip():
                fts_rows.append((e.body, e.path, e.body_line, None, e.id))
            for img in find_images(e.body):
                img_rows.append((None, e.id, img, self._find_image(images, img, e.path)))

        move_kind = {(mv.path, mv.line): mv.kind for mv in der.moves}
        entry_rows, mention_rows, tag_rows = [], [], []
        eid = 0
        for n in notes.values():
            for en in n.entries:
                eid += 1
                entry_rows.append((eid, en.path, en.line, ts(en.stamp.dt) if en.stamp else None,
                                   int(en.stamp.approx) if en.stamp else 0, en.text,
                                   en.parent_line, move_kind.get((en.path, en.line))))
                fts_rows.append((en.text, en.path, en.line, eid, None))
                attached = False
                move_names = en.move.left + en.move.right if en.move else ()
                for inherited, targets in ((0, en.links), (1, en.inherited)):
                    for t in targets:
                        r = resolve(t)
                        if inherited == 0 and not (r.kind == "unresolved" and t in move_names):
                            self._check_link(en.path, en.line, t, r, warnings)
                        if r.kind in ("entity", "note"):
                            mention_rows.append((eid, r.kind, r.id, inherited))
                            attached = attached or r.kind == "entity"
                if not attached and en.move is None:
                    warnings.append(Warning(en.path, en.line, "unattached", "entry mentions nothing", "info"))
                tag_rows.extend((eid, t) for t in en.tags)
                for img in en.images:
                    found = self._find_image(images, img, en.path)
                    img_rows.append((eid, None, img, found))
                    if not found:
                        warnings.append(Warning(en.path, en.line, "missing-image", f"image not found: {img}"))
                for s, e_, canon in en.bare:
                    if canon in entities:
                        text = en.text[s:e_].strip()
                        warnings.append(Warning(en.path, en.line, "unlinked-mention",
                                                f"{text!r} looks like [[{canon}]]",
                                                fix={"kind": "link", "text": text, "to": canon}))

        face_rows, fp_rows = [], []
        for i, f in enumerate(self.faces, 1):
            face_rows.append((i, json.dumps(geo.mapping(f.geom)), f.acres))
            fp_rows.extend((i, p) for p in f.paddocks)

        tables = {
            "files": [(r, c.kind, c.etype, c.mtime) for r, c in self.cache.items()],
            "entities": ent_rows,
            "entries": entry_rows,
            "mentions": mention_rows,
            "tags": tag_rows,
            "images": img_rows,
            "moves": [(mv.path, mv.line, ts(mv.dt), mv.kind, json.dumps(mv.left), json.dumps(mv.right))
                      for mv in der.moves],
            "memberships": [(m.animal, m.herd, ts(m.start), ts(m.end), int(m.inherited), m.path, m.line)
                            for m in der.memberships],
            "occupancies": [(o.herd, o.paddock, ts(o.start), ts(o.end), o.path, o.line)
                            for o in der.occupancies],
            "exits": [(x.animal, ts(x.dt), x.kind, x.weight, x.unit, x.basis, x.cause, x.path, x.line)
                      for x in der.exits],
            "heads": [(h.herd, h.count, ts(h.start), ts(h.end), h.path, h.line) for h in der.heads],
            "faces": face_rows,
            "face_paddocks": fp_rows,
            "warnings": [(w.path, w.line, w.code, w.severity, w.message,
                          json.dumps(w.fix) if w.fix else None) for w in warnings],
        }
        db = self.db
        db.execute("BEGIN")
        for name, rows in tables.items():
            db.execute(f"DELETE FROM {name}")
            if rows:
                marks = ",".join("?" * len(rows[0]))
                db.executemany(f"INSERT INTO {name} VALUES ({marks})", rows)
        db.execute("DELETE FROM fts")
        db.executemany("INSERT INTO fts (text, path, line, entry_id, entity) VALUES (?,?,?,?,?)", fts_rows)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)",
                   (datetime.now().isoformat(timespec="seconds"),))
        db.commit()
