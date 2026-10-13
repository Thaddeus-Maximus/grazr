"""Every page renders, and every write action writes what a person would type."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from grazr.server import App, make_handler


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture
def web(farm):
    app = App(farm)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, 0))
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(NoRedirect)

    def call(path, data=None, headers=None, raw=None, ctype=None):
        body = raw if raw is not None else (
            urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None)
        req = urllib.request.Request(base + path, data=body, headers=headers or {})
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with opener.open(req) as r:
                return r.status, r.headers, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode("utf-8", "replace")

    yield call
    httpd.shutdown()


@pytest.mark.parametrize("path", [
    "/", "/animals", "/animals?show=all", "/animals?cull=*", "/animals?series=orange",
    "/herds", "/herds?show=all", "/paddocks", "/a/orange-010", "/a/orange-117", "/a/blue-007",
    "/h/Ewe%20Herd", "/h/Hogs", "/p/P3", "/p/South%20Field", "/p/Barn", "/t/sheep",
    "/search?q=corn&exact=0", "/n/notes/2026Oct12-0707.md", "/n/notes/2026Oct12-0707.md?edit=1",
    "/lint", "/lint?all=1", "/new", "/move", "/move?herd=Hogs", "/create/animals",
    "/create/herds", "/edit/orange-010", "/draw", "/draw?id=P3", "/api/map",
    "/api/map?as_of=2026-05-01", "/api/complete?kind=link&q=o%2011", "/api/complete?kind=tag&q=s",
    "/static/app.js", "/static/vendor/leaflet/leaflet.js", "/img?name=gate.png",
])
def test_pages(web, path):
    status, _, body = web(path)
    assert status == 200, body[:2000]


def test_not_found_and_redirects(web):
    assert web("/a/orange-999")[0] == 404
    assert web("/n/../../etc/passwd")[0] in (400, 404)
    status, h, _ = web("/a/o%2010")                        # shorthand → canonical page
    assert status == 303 and h["Location"] == "/a/orange-010"
    status, h, _ = web("/search?q=ewe%20herd")             # exact name jumps to the page
    assert status == 303 and h["Location"] == "/h/Ewe%20Herd"


def test_map_data(web):
    data = json.loads(web("/api/map")[2])
    kinds = {f["properties"]["kind"] for f in data["features"]}
    assert kinds == {"face", "paddock"}
    p3b = next(f for f in data["features"] if f["properties"].get("id") == "P3b")
    assert p3b["properties"]["herds"] == ["Ewe Herd"]


def test_complete_shorthand(web):
    assert json.loads(web("/api/complete?kind=link&q=o%2011")[2])[0] == "orange-011"


def test_new_note(web, farm_root):
    status, h, _ = web("/new", {"text": "[[Ewe Herd]] fed hay", "tags": "sheep"})
    assert status == 303
    path = urllib.parse.unquote(h["Location"][3:])
    assert (farm_root / path).read_text(encoding="utf-8") == "#sheep\n[[Ewe Herd]] fed hay\n"


def test_move_herd(web, farm, farm_root):
    status, _, body = web("/move", {"herd": "Ewe Herd", "paddock": ["P3", "P3b"], "arrow": "+>",
                                    "comment": "back fence open", "when": ""})
    assert status == 303, body
    texts = [p.read_text(encoding="utf-8") for p in (farm_root / "notes").glob("*.md")]
    assert "[[Ewe Herd]] +> [[P3]], [[P3b]] back fence open\n" in texts
    assert {o["paddock"] for o in farm.occupying(herd="Ewe Herd")} == {"P3", "P3b"}


def test_move_animals_creating_herd(web, farm):
    status, _, body = web("/move-animals", {"animal": ["orange-010", "orange-011"],
                                            "dest": "Weaned", "create": "1"})
    assert status == 303, body
    assert [m["id"] for m in farm.members("Weaned")] == ["orange-010", "orange-011"]


def test_create_and_edit_animal(web, farm, farm_root):
    status, h, body = web("/create/animals", {"series": "orange", "number": "", "born": "2026-10-01",
                                              "dam": "o 117", "key": ["name"], "value": ["Pip"]})
    assert status == 303, body
    assert h["Location"] == "/a/orange-118"
    text = (farm_root / "animals" / "orange-118.md").read_text(encoding="utf-8")
    assert "sex: ewe" in text and "dam: '[[o 117]]'" in text.replace('"', "'") and "name: Pip" in text
    (farm_root / "animals" / "orange-118.md").write_text(text + "Body written in Obsidian.\n", encoding="utf-8")
    farm.refresh()
    status, _, body = web("/edit/orange-118", {"key": ["name", "sex", "color"], "value": ["Pip", "ewe", "brown"],
                                                "remove": ["sex"]})
    assert status == 303, body
    text = (farm_root / "animals" / "orange-118.md").read_text(encoding="utf-8")
    assert "color: brown" in text and "sex:" not in text and text.endswith("Body written in Obsidian.\n")


def test_draw_paddock_and_grazed_shape_is_refused(web, farm):
    pts = [[40.3497, -89.1307], [40.3497, -89.1304], [40.3493, -89.1304]]
    status, _, body = web("/draw", {"name": "P4", "parent": "South Field", "perimeter": json.dumps(pts)})
    assert status == 303, body
    assert farm.entity("P4")["parent"] == "South Field" and farm.entity("P4")["acres"] > 0
    status, _, body = web("/draw", {"name": "P3", "perimeter": json.dumps(pts)})
    assert status == 400 and "new name" in body


def test_upload_multipart(web, farm_root):
    boundary = "XyZ"
    raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"ewe.png\"\r\n"
           f"Content-Type: image/png\r\n\r\n").encode() + b"\x89PNG\r\n\x1a\nDATA" + f"\r\n--{boundary}--\r\n".encode()
    status, _, body = web("/upload", raw=raw, ctype=f"multipart/form-data; boundary={boundary}")
    assert status == 200, body
    assert json.loads(body)["embed"] == "![[ewe.png]]"
    assert (farm_root / "attachments" / "ewe.png").read_bytes() == b"\x89PNG\r\n\x1a\nDATA"


def test_lint_fix(web, farm):
    w = next(w for w in farm.warnings() if w["code"] == "shorthand-link" and w["path"].startswith("notes/Sheep"))
    status, _, _ = web("/lint/fix", {"path": w["path"], "line": w["line"], "fix": w["fix"]})
    assert status == 303
    assert not [x for x in farm.warnings() if x["code"] == "shorthand-link" and x["path"] == w["path"]]


def test_cross_site_post_refused(web):
    status, _, _ = web("/new", {"text": "spam"}, headers={"Origin": "https://evil.example"})
    assert status == 403


def test_backdated_note(web, farm, farm_root):
    status, h, body = web("/new", {"text": "[[Ewe Herd]] -> [[P3]]", "tags": "", "when": "15 Dec 2025 08:00"})
    assert status == 303, body
    assert h["Location"] == "/n/notes/2025Dec15-0800.md"
    assert (farm_root / "notes" / "2025Dec15-0800.md").read_text(encoding="utf-8") == "[[Ewe Herd]] -> [[P3]]\n"
    assert farm.q1("SELECT ts FROM moves WHERE path='notes/2025Dec15-0800.md'")["ts"] == "2025-12-15T08:00"


def test_backdated_note_date_only_is_approximate(web, farm, farm_root):
    status, h, body = web("/new", {"text": "sheared [[orange-117]]", "when": "2026-03-02"})
    assert status == 303, body
    path = urllib.parse.unquote(h["Location"][3:])
    assert (farm_root / path).read_text(encoding="utf-8").startswith("Date: 02Mar2026\n")
    row = farm.q1("SELECT ts, approx FROM entries WHERE path=?", path)
    assert row["ts"].startswith("2026-03-02") and row["approx"] == 1


def test_backdated_move_and_bad_when(web, farm_root):
    status, _, body = web("/move", {"herd": "Hogs", "paddock": ["P3"], "arrow": "->", "when": "2026-05-01T07:30"})
    assert status == 303, body
    assert (farm_root / "notes" / "2026May01-0730.md").read_text(encoding="utf-8") == "[[Hogs]] -> [[P3]]\n"
    assert web("/new", {"text": "x", "when": "the day after the storm"})[0] == 400
    assert web("/new", {"text": "x", "when": "2099-01-01"})[0] == 400


def test_eartag_links(web):
    _, _, body = web("/h/Ewe%20Herd")
    assert 'class="link animals eartag s-orange"' in body
    assert ".eartag.s-orange{--tag-bg:#f7931e;--tag-fg:#111}" in body
    assert ".eartag.s-blue{--tag-bg:#1f64c8;--tag-fg:#fff}" in body
