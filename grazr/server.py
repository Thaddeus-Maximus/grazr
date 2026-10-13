"""`grazr server`: stdlib HTTP server + Jinja2 templates (spec §8)."""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from jinja2 import Environment, PackageLoader, select_autoescape

from . import frontmatter, tiles, writes
from .api import Farm, fields, fix_json, gone, parse_ts
from .dates import parse_when
from .index import TS
from .render import Renderer
from .writes import WriteError

STATIC = Path(__file__).parent / "static"
TYPE_PREFIX = {"animals": "a", "herds": "h", "paddocks": "p"}
PREFIX_TYPE = {v: k for k, v in TYPE_PREFIX.items()}
MAX_UPLOAD = 40 * 1024 * 1024


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


class NotFound(Exception):
    pass


def entity_url(eid: str, etype: str) -> str:
    p = TYPE_PREFIX.get(etype)
    return f"/{p}/{quote(eid)}" if p else f"/x/{quote(etype)}/{quote(eid)}"


class App:
    def __init__(self, farm: Farm):
        self.farm = farm
        self.lock = farm.index.lock
        self.env = Environment(loader=PackageLoader("grazr", "templates"),
                               autoescape=select_autoescape(["html"]), trim_blocks=True,
                               lstrip_blocks=True)
        self.env.globals.update(app=self, fields=fields, now=datetime.now, gone=gone)
        self.env.filters.update(ts=self.fmt_ts, days=self.fmt_days, eurl=self.eurl, etype=self.etype,
                                inline=lambda t: self.renderer().inline(t or ""),
                                acres=lambda a: "" if a is None else f"{a:.2f}",
                                q=lambda s: quote(str(s)))

    # --- helpers used by templates -------------------------------------------
    def eurl(self, eid: str) -> str:
        r = self.farm.resolver().resolve(eid)
        if r.kind == "entity":
            return entity_url(r.id, r.etype)
        return f"/search?q={quote(eid)}"

    def etype(self, eid: str) -> str:
        r = self.farm.resolver().resolve(eid)
        return r.etype if r.kind == "entity" else r.kind

    def href(self, target: str):
        r = self.farm.resolver().resolve(target)
        if r.kind == "entity":
            return entity_url(r.id, r.etype), r.etype
        if r.kind == "note":
            return f"/n/{quote(r.id)}", "note"
        return None, "unresolved"

    def image_url(self, name: str) -> str:
        return f"/img?name={quote(name)}"

    def renderer(self) -> Renderer:
        return Renderer(self.href, self.image_url)

    @staticmethod
    def fmt_ts(s, approx=False) -> str:
        dt = parse_ts(s) if isinstance(s, str) else s
        if dt is None:
            return "undated"
        out = dt.strftime("%d %b %Y") if approx and dt.hour == 12 and dt.minute == 0 \
            else dt.strftime("%d %b %Y %H:%M")
        return ("~" if approx else "") + out

    @staticmethod
    def fmt_days(d) -> str:
        if d is None:
            return "never grazed"
        if d == 0:
            return "occupied"
        return f"{d:.0f} days" if d >= 2 else f"{d * 24:.0f} hours"

    def map_config(self) -> dict:
        cfg = self.farm.config
        t = cfg["tiles"] or {}
        return {"center": cfg["map"]["center"], "zoom": cfg["map"]["zoom"],
                "min_zoom": int(t.get("min_zoom", 13)), "max_zoom": int(t.get("max_zoom", 19)),
                "rest_full_days": cfg["rest_full_days"]}

    @staticmethod
    def display_value(v) -> str:
        """How a field shows in a form; unchanged text is not rewritten on save."""
        if v is None:
            return ""
        if isinstance(v, list):
            return "[" + ", ".join(map(str, v)) + "]"
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def types(self) -> list[str]:
        return list(self.farm.config.types)

    def warning_count(self) -> int:
        return self.farm.q1("SELECT count(*) FROM warnings WHERE severity != 'info'")[0]

    def page(self, name: str, **ctx) -> str:
        return self.env.get_template(name).render(**ctx)

    # --- routing ----------------------------------------------------------------
    ROUTES = [
        ("GET", r"/", "home"),
        ("GET", r"/a/(?P<eid>[^/]+)", "animal"),
        ("GET", r"/h/(?P<eid>[^/]+)", "herd"),
        ("GET", r"/p/(?P<eid>[^/]+)", "paddock"),
        ("GET", r"/x/(?P<etype>[^/]+)/(?P<eid>[^/]+)", "other"),
        ("GET", r"/t/(?P<tag>.+)", "tag"),
        ("GET", r"/search", "search"),
        ("GET", r"/n/(?P<path>.+)", "note"),
        ("POST", r"/n/(?P<path>.+)", "note_save"),
        ("GET", r"/lint", "lint"),
        ("POST", r"/lint/fix", "lint_fix"),
        ("GET", r"/new", "new_note"),
        ("POST", r"/new", "new_note_save"),
        ("GET", r"/move", "move"),
        ("POST", r"/move", "move_save"),
        ("POST", r"/move-animals", "move_animals"),
        ("GET", r"/create/(?P<etype>[^/]+)", "create"),
        ("POST", r"/create/(?P<etype>[^/]+)", "create_save"),
        ("GET", r"/edit/(?P<eid>[^/]+)", "edit"),
        ("POST", r"/edit/(?P<eid>[^/]+)", "edit_save"),
        ("GET", r"/draw", "draw"),
        ("POST", r"/draw", "draw_save"),
        ("POST", r"/upload", "upload"),
        ("GET", r"/img", "img"),
        ("GET", r"/api/map", "api_map"),
        ("GET", r"/api/complete", "api_complete"),
        ("GET", r"/(?P<etype>[^/]+)", "listing"),
    ]

    def dispatch(self, method: str, path: str, query: dict, form: dict, files: dict):
        for m, pattern, name in self.ROUTES:
            if m != method:
                continue
            mt = re.fullmatch(pattern, path)
            if mt:
                kw = {k: unquote(v) for k, v in mt.groupdict().items()}
                return getattr(self, "r_" + name)(query=query, form=form, files=files, **kw)
        raise NotFound()

    # --- pages ----------------------------------------------------------------
    def r_home(self, query, **_):
        return self.page("home.html", entries=self.farm.recent(80))

    def _entity(self, eid, etype=None):
        row = self.farm.entity(eid)
        if row is None:
            r = self.farm.resolver().resolve(eid)
            if r.kind == "entity":
                raise Redirect(entity_url(r.id, r.etype))
            raise NotFound()
        if etype and row["type"] != etype:
            raise Redirect(entity_url(row["id"], row["type"]))
        return row

    def _common(self, row):
        f = self.farm
        return dict(e=row, props={k: v for k, v in fields(row).items() if k != "perimeter"},
                    timeline=f.timeline(row["id"]), photos=f.photos(row["id"]),
                    body=self.renderer().document(row["body"] or ""))

    def r_animal(self, eid, **_):
        f = self.farm
        row = self._entity(eid, "animals")
        mem = f.membership(row["id"])
        return self.page(
            "animal.html", **self._common(row), membership=mem,
            paddocks=f.occupying(herd=mem["herd"]) if mem else [],
            pedigree=f.pedigree(row["id"], 4), lambing=f.lambing(row["id"]),
            offspring=f.offspring(row["id"]),
            history=f.q("SELECT * FROM memberships WHERE animal=? ORDER BY start DESC", row["id"]),
            left=gone(row), exit=f.exit(row["id"]))

    def r_herd(self, eid, **_):
        f = self.farm
        row = self._entity(eid, "herds")
        herds = [h["id"] for h in f.entities("herds") if h["id"] != row["id"] and not f.idle(h)]
        return self.page(
            "herd.html", **self._common(row), members=f.members(row["id"]),
            head=f.head(row["id"]), paddocks=f.occupying(herd=row["id"]), herds=herds,
            heads=f.q("SELECT * FROM heads WHERE herd=? ORDER BY start DESC", row["id"]),
            occupancies=f.q("SELECT * FROM occupancies WHERE herd=? ORDER BY start DESC", row["id"]),
            joins=f.q("""SELECT * FROM memberships WHERE herd=? AND inherited=0
                         ORDER BY start DESC LIMIT 200""", row["id"]))

    def r_paddock(self, eid, **_):
        f = self.farm
        row = self._entity(eid, "paddocks")
        rest = f.rest()
        return self.page(
            "paddock.html", **self._common(row),
            rest_exact=rest["exact"].get(row["id"]), rest_effective=rest["effective"].get(row["id"]),
            herds=f.occupying(paddock=row["id"]),
            occupancies=f.q("SELECT * FROM occupancies WHERE paddock=? ORDER BY start DESC", row["id"]),
            children=f.q("SELECT * FROM entities WHERE parent=? ORDER BY id COLLATE NOCASE", row["id"]),
            polygon=row["polygon"])

    def r_other(self, etype, eid, **_):
        row = self._entity(eid, etype)
        return self.page("entity.html", **self._common(row))

    def r_listing(self, etype, query, **_):
        f = self.farm
        if etype not in f.config.types:
            raise NotFound()
        rows = f.entities(etype)
        show_all = query.get("show") == "all"
        series = query.get("series")
        filters = {k: v for k, v in query.items() if k not in ("show", "series", "cols", "sort")}
        today = datetime.now().date().isoformat()
        out = []
        for r in rows:
            fl = fields(r)
            if etype == "animals" and not show_all and gone(r, today):
                continue
            if etype == "herds" and not show_all and f.idle(r):
                continue
            if series and not r["id"].casefold().startswith(series.casefold() + "-"):
                continue
            ok = True
            for k, v in filters.items():
                have = fl.get(k)
                if v == "*":
                    ok = have not in (None, "", [])
                elif v == "!":
                    ok = have in (None, "", [])
                else:
                    ok = have is not None and str(have).casefold() == v.casefold()
                if not ok:
                    break
            if ok:
                out.append(r)
        default_cols = {"animals": ["name", "sex", "born"], "herds": ["species"],
                        "paddocks": ["kind", "forage"]}.get(etype, [])
        cols = [c for c in (query.get("cols") or "").split(",") if c] or \
            [c for c in default_cols if c in f.field_names(etype)]
        rest = f.rest() if etype == "paddocks" else None
        extra = {}
        for r in out:
            if etype == "animals":
                m = f.membership(r["id"])
                extra[r["id"]] = {"herd": m["herd"] if m else None, "exit": f.exit(r["id"])}
            elif etype == "herds":
                extra[r["id"]] = {"size": f.herd_size(r),
                                  "paddocks": [o["paddock"] for o in f.occupying(herd=r["id"])]}
            elif etype == "paddocks":
                extra[r["id"]] = {"rest": rest["effective"].get(r["id"], rest["exact"].get(r["id"])),
                                  "herds": [o["herd"] for o in f.occupying(paddock=r["id"])]}
        return self.page("list.html", etype=etype, rows=out, cols=cols, extra=extra,
                         all_fields=f.field_names(etype), query=query, filters=filters,
                         show_all=show_all, series=[s.name for s in f.config.series])

    def r_tag(self, tag, **_):
        entries = self.farm.tagged(tag)
        return self.page("tag.html", tag=tag, entries=entries,
                         mentioned=self.farm.entities_in([e["id"] for e in entries][:999]))

    def r_search(self, query, **_):
        q = query.get("q", "").strip()
        if q:
            r = self.farm.resolver().resolve(q)
            if r.kind == "entity" and query.get("exact") != "0":
                raise Redirect(entity_url(r.id, r.etype))
        return self.page("search.html", q=q, results=self.farm.search(q) if q else [])

    def r_note(self, path, query, **_):
        p = writes.under(self.farm.root, path)
        if not p.is_file():
            raise NotFound()
        text = p.read_text(encoding="utf-8-sig")
        if query.get("edit"):
            return self.page("note_edit.html", path=path, text=text)
        return self.page("note.html", path=path, html=self.renderer().document(text),
                         warnings=self.farm.q("SELECT * FROM warnings WHERE path=? ORDER BY line", path))

    def r_note_save(self, path, form, **_):
        p = writes.under(self.farm.root, path)
        if not path.lower().endswith(".md"):
            raise WriteError("only .md files can be edited")
        writes.save_raw(p, form.get("text", ""))
        self.farm.refresh()
        raise Redirect(f"/n/{quote(path)}")

    def r_lint(self, query, **_):
        rows = self.farm.warnings(include_info=query.get("all") == "1")
        groups: dict[str, list] = {}
        for w in rows:
            groups.setdefault(w["path"], []).append((w, fix_json(w)))
        return self.page("lint.html", groups=groups, show_all=query.get("all") == "1")

    def r_lint_fix(self, form, **_):
        fix = json.loads(form["fix"])
        self.farm.apply_fix(form["path"], int(form["line"]), fix)
        raise Redirect("/lint" + ("?all=1" if form.get("all") else ""))

    # --- writes ---------------------------------------------------------------
    def _when(self, form) -> datetime | None:
        raw = (form.get("when") or "").strip()
        if not raw:
            return None
        if "T" in raw:      # <input type=datetime-local>
            return datetime.strptime(raw[:16], TS)
        s = parse_when(raw, None)
        if s is None:
            raise WriteError(f"can't read time {raw!r}")
        return s.dt

    def r_new_note(self, query, **_):
        return self.page("new_note.html", text=query.get("text", ""), tags=query.get("tags", ""))

    def r_new_note_save(self, form, **_):
        text = form.get("text", "").strip()
        if not text:
            raise WriteError("the note is empty")
        tags = (form.get("tags") or "").replace(",", " ").split()
        path = writes.new_note(self.farm.root, self.farm.config, text, tags)
        self.farm.refresh()
        raise Redirect(form.get("next") or f"/n/{quote(path)}")

    def r_move(self, query, **_):
        f = self.farm
        herds = [h for h in f.entities("herds")]
        paddocks = [p for p in f.entities("paddocks")]
        return self.page("move.html", herds=herds, paddocks=paddocks, herd=query.get("herd", ""),
                         current={h["id"]: [o["paddock"] for o in f.occupying(herd=h["id"])] for h in herds})

    def r_move_save(self, form, **_):
        herds = form.getlist("herd")
        pads = form.getlist("paddock")
        if form.get("retire"):
            pads, arrow = ["none"], "->"
        else:
            arrow = form.get("arrow", "->")
        line = writes.move_line(herds, pads, arrow, form.get("comment", ""), self._when(form))
        writes.new_note(self.farm.root, self.farm.config, line)
        self.farm.refresh()
        raise Redirect(entity_url(herds[0], "herds") if len(herds) == 1 else "/")

    def r_move_animals(self, form, **_):
        animals = form.getlist("animal")
        dest = (form.get("dest") or "").strip()
        if not dest:
            raise WriteError("pick a destination herd")
        if self.farm.entity(dest) is None:
            if not form.get("create"):
                raise WriteError(f"there is no herd called {dest!r}; tick “create it”")
            writes.new_entity(self.farm.root, "herds", dest,
                              {"species": form.get("species") or None})
        line = writes.move_line(animals, [dest], "->", form.get("comment", ""), self._when(form))
        writes.new_note(self.farm.root, self.farm.config, line)
        self.farm.refresh()
        raise Redirect(form.get("next") or entity_url(dest, "herds"))

    def r_create(self, etype, query, **_):
        f = self.farm
        if etype not in f.config.types:
            raise NotFound()
        nexts = {s.name: f.next_number(s.name) for s in f.config.series}
        return self.page("create.html", etype=etype, series=f.config.series, nexts=nexts,
                         digits=f.config.id_digits, field_names=f.field_names(etype), query=query)

    def r_create_save(self, etype, form, **_):
        f = self.farm
        cfg = f.config
        if etype not in cfg.types:
            raise NotFound()
        values = {}
        if etype == "animals" and form.get("series"):
            s = next(x for x in cfg.series if x.name == form["series"])
            n = int(form.get("number") or f.next_number(s.name))
            eid = f.index.animal_ids.canonical(s.name, n)
            values.update(s.defaults)
        else:
            eid = form.get("name", "")
        for k, v in zip(form.getlist("key"), form.getlist("value")):
            if k.strip():
                pv = writes.parse_value(v)
                if pv is not None:
                    values[k.strip()] = pv
        for k in ("born", "dam", "sire", "parent"):
            if form.get(k):
                values[k] = writes.parse_value(form[k]) if k == "born" else f"[[{form[k].strip(' []')}]]"
        if form.get("anonymous"):
            values["anonymous"] = True
        writes.new_entity(f.root, cfg.types[etype], eid, values)
        f.refresh()
        raise Redirect(entity_url(eid, etype))

    def r_edit(self, eid, **_):
        row = self._entity(eid)
        props = {k: v for k, v in fields(row).items() if k != "perimeter"}
        return self.page("edit.html", e=row, props=props, field_names=self.farm.field_names(row["type"]))

    def r_edit_save(self, eid, form, **_):
        row = self._entity(eid)
        old = fields(row)
        values = {k: v for k, v in zip(form.getlist("key"), form.getlist("value"))
                  if not (k in old and self.display_value(old[k]) == v)}
        removed = [k for k in form.getlist("remove")]
        writes.edit_properties(self.farm.root / row["path"], values, removed)
        self.farm.refresh()
        raise Redirect(entity_url(row["id"], row["type"]))

    def r_draw(self, query, **_):
        f = self.farm
        eid = query.get("id")
        row = f.entity(eid) if eid else None
        grazed = bool(row and f.q1("SELECT 1 FROM occupancies WHERE paddock=?", row["id"]))
        suggestion = ""
        if grazed:
            base, n = row["id"], 98
            while f.entity(f"{base}{chr(n)}") is not None:
                n += 1
            suggestion = f"{base}{chr(n)}"
        return self.page("draw.html", e=row, grazed=grazed, suggestion=suggestion,
                         paddocks=[p["id"] for p in f.entities("paddocks")])

    def r_draw_save(self, form, **_):
        f = self.farm
        pts = json.loads(form["perimeter"])
        perim = [[round(float(lat), 7), round(float(lon), 7)] for lat, lon in pts]
        if len(perim) < 3:
            raise WriteError("draw at least 3 points")
        name = writes.safe_name(form.get("name", ""))
        row = f.entity(name)
        if row is not None:
            if row["type"] != "paddocks":
                raise WriteError(f"{name} is already a {row['type'][:-1]}")
            if f.q1("SELECT 1 FROM occupancies WHERE paddock=?", name):
                raise WriteError(f"{name} has been grazed, so a new shape needs a new name (§4.3)")
            frontmatter.update(f.root / row["path"], {"perimeter": perim})
        else:
            values = {"perimeter": perim}
            if form.get("parent"):
                values = {"parent": f"[[{form['parent']}]]", **values}
            if form.get("copy_from"):
                src = f.entity(form["copy_from"])
                if src is not None:
                    values = {**{k: v for k, v in fields(src).items() if k != "perimeter"}, **values}
            writes.new_entity(f.root, f.config.types["paddocks"], name, values)
        f.refresh()
        raise Redirect(entity_url(name, "paddocks"))

    def r_upload(self, files, **_):
        if "file" not in files:
            raise WriteError("no file")
        filename, data = files["file"]
        name = writes.save_upload(self.farm.root, filename, data)
        return ("application/json", json.dumps({"name": name, "embed": f"![[{name}]]"}).encode())

    def r_img(self, query, **_):
        name = query.get("name", "")
        ix = self.farm.index
        found = ix._find_image(ix.images, name, "")
        if not found:
            raise NotFound()
        p = Path(found).resolve()
        allowed = [self.farm.root] + [
            (Path(d) if Path(d).is_absolute() else self.farm.root / d).resolve()
            for d in self.farm.config["attachment_dirs"] or []]
        if not any(p == a or a in p.parents for a in allowed):
            raise NotFound()
        return ("file", p)

    def r_api_map(self, query, **_):
        as_of = None
        if query.get("as_of"):
            as_of = datetime.strptime(query["as_of"][:10], "%Y-%m-%d").replace(hour=23, minute=59)
        return ("application/json", json.dumps(self.farm.map_data(as_of)).encode())

    def r_api_complete(self, query, **_):
        kind, q = query.get("kind"), query.get("q", "").casefold()
        if kind == "tag":
            items = [t for t in self.farm.tags() if t.casefold().startswith(q)][:15]
        else:
            items = []
            animal = self.farm.index.animal_ids.match(q) if q else None
            if animal and self.farm.entity(animal):
                items.append(animal)
            for r in self.farm.q("SELECT id FROM entities ORDER BY type, id COLLATE NOCASE"):
                if q in r["id"].casefold() and r["id"] not in items:
                    items.append(r["id"])
                if len(items) >= 15:
                    break
        return ("application/json", json.dumps(items).encode())


class Form(dict):
    """Single values by key; all values with getlist()."""

    def __init__(self, multi: dict[str, list[str]]):
        super().__init__({k: v[-1] for k, v in multi.items()})
        self.multi = multi

    def getlist(self, key):
        return self.multi.get(key, [])


def parse_multipart(body: bytes, content_type: str):
    """→ (Form, {name: (filename, bytes)}). Small and strict; `cgi` is gone (D-15)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise WriteError("bad upload")
    boundary = b"--" + m.group(1).encode()
    fields_: dict[str, list[str]] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in body.split(boundary)[1:]:
        if part.startswith(b"--"):
            break
        head, _, data = part.lstrip(b"\r\n").partition(b"\r\n\r\n")
        data = data[:-2] if data.endswith(b"\r\n") else data
        disp = re.search(rb'name="([^"]*)"(?:; filename="([^"]*)")?', head)
        if not disp:
            continue
        name = disp.group(1).decode()
        if disp.group(2) is not None:
            files[name] = (disp.group(2).decode("utf-8", "replace"), data)
        else:
            fields_.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return Form(fields_), files


def make_handler(app: App, port: int):
    class Handler(BaseHTTPRequestHandler):
        server_version = "grazr"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body: bytes, ctype="text/html; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _file(self, p: Path, cache=False):
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            self._send(200, p.read_bytes(), ctype,
                       {"Cache-Control": "max-age=86400" if cache else "no-cache"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def _same_origin(self) -> bool:
            """Refuse cross-site form posts: any web page could target 127.0.0.1."""
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                return True
            host = urlparse(origin).netloc
            return host == self.headers.get("Host")

        def _handle(self, method):
            url = urlparse(self.path)
            path = url.path
            query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            try:
                if method == "GET" and path.startswith("/static/"):
                    p = (STATIC / unquote(path[8:])).resolve()
                    if STATIC.resolve() not in p.parents or not p.is_file():
                        raise NotFound()
                    return self._file(p, cache=True)
                if method == "GET" and path.startswith("/tiles/"):
                    got = tiles.serve(app.farm.root, app.farm.config, path[7:])
                    if got is None:
                        return self._send(404, b"", "text/plain")
                    return self._send(200, got[1], got[0], {"Cache-Control": "max-age=604800"})
                form, files = Form({}), {}
                if method == "POST":
                    if not self._same_origin():
                        return self._send(403, b"cross-site request refused", "text/plain")
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_UPLOAD:
                        return self._send(413, b"too large", "text/plain")
                    body = self.rfile.read(n)
                    ctype = self.headers.get("Content-Type", "")
                    if ctype.startswith("multipart/form-data"):
                        form, files = parse_multipart(body, ctype)
                    else:
                        form = Form(parse_qs(body.decode("utf-8"), keep_blank_values=True))
                with app.lock:
                    out = app.dispatch(method, path, query, form, files)
                if isinstance(out, tuple) and out[0] == "file":
                    return self._file(out[1])
                if isinstance(out, tuple):
                    return self._send(200, out[1], out[0])
                return self._send(200, out.encode("utf-8"))
            except Redirect as r:
                self._send(303, b"", headers={"Location": r.url})
            except NotFound:
                self._send(404, app.page("error.html", title="Not found",
                                         message=f"Nothing at {path}").encode("utf-8"))
            except (WriteError, KeyError, ValueError) as e:
                msg = str(e) if isinstance(e, WriteError) else f"{type(e).__name__}: {e}"
                if path.startswith("/upload"):
                    return self._send(400, json.dumps({"error": msg}).encode(), "application/json")
                self._send(400, app.page("error.html", title="Couldn't save",
                                         message=msg).encode("utf-8"))
            except Exception:
                tb = traceback.format_exc()
                print(tb)
                self._send(500, app.page("error.html", title="Error", message=tb).encode("utf-8"))

        do_HEAD = do_GET

    return Handler


class TileKeeper:
    """Downloads missing imagery in the background: at startup and whenever
    the paddocks change. Only tiles not already cached are fetched."""

    def __init__(self, farm: Farm, limit: int = 20000):
        self.farm = farm
        self.limit = limit
        self.key = None
        self.busy = threading.Lock()

    def check(self):
        with self.farm.index.lock:
            polys = self.farm.paddock_polygons()
            cfg = self.farm.config
        key = (json.dumps(polys), json.dumps(cfg["tiles"]), json.dumps(cfg["map"]))
        if key == self.key or not self.busy.acquire(blocking=False):
            return
        self.key = key
        threading.Thread(target=self._run, args=(polys, cfg), daemon=True).start()

    def _run(self, polys, cfg):
        try:
            last = [0.0]

            def progress(done, total, failed):
                if time.time() - last[0] > 30 or done == total:
                    last[0] = time.time()
                    print(f"tiles: {done}/{total}" + (f" ({failed} failed)" if failed else ""), flush=True)

            got, failed, missing = tiles.download(self.farm.root, cfg, polys, progress=progress,
                                                  limit=self.limit)
            if missing and not got and not failed:
                print(f"tiles: {missing} missing is too many to fetch automatically; "
                      f"run `grazr tiles --zoom …` to choose", flush=True)
            elif missing and failed and not got:
                print("tiles: offline? none could be downloaded; the map will fill in when online",
                      flush=True)
                self.key = None  # try again on the next change
            elif missing:
                print(f"tiles: {got} downloaded" + (f", {failed} failed" if failed else ""), flush=True)
        except Exception:
            traceback.print_exc()
        finally:
            self.busy.release()


def watch(farm: Farm, keeper: TileKeeper, stop: threading.Event, interval: float = 2.0):
    while not stop.wait(interval):
        try:
            if farm.refresh():
                keeper.check()
        except Exception:
            traceback.print_exc()


def run(root: Path, host: str = "127.0.0.1", port: int = 8714, fetch_tiles: bool = True):
    print(f"indexing {root} …", flush=True)
    farm = Farm(root)
    print(farm.summary_text())
    if farm.warnings():
        print("see the Lint page, or run `grazr lint`")
    app = App(farm)
    stop = threading.Event()
    keeper = TileKeeper(farm)
    if fetch_tiles:
        keeper.check()
    else:
        keeper.busy.acquire()   # never fetch
    threading.Thread(target=watch, args=(farm, keeper, stop), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app, port))
    print(f"grazr serving {root} at http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
