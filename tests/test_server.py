"""Every page renders, and every write action writes what a person would type."""
import html
import json
import re
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
    tags = re.findall(r'eartag s-(\w+)" href="[^"]*" title="([^"]+)">([^<]*)<', body)
    assert tags and all(t == f"{s}-{n}" and n.isdigit() for s, t, n in tags)
    assert ".eartag.s-orange{--tag-bg:#f7931e;--tag-fg:#111}" in body
    assert ".eartag.s-blue{--tag-bg:#1f64c8;--tag-fg:#fff}" in body


def test_untagged_and_colour_eartags(web, farm, farm_root):
    (farm_root / "animals" / "white-012.md").write_text("---\nsex: ewe\n---\n", encoding="utf-8")
    (farm_root / "notes" / "tags.md").write_text("[[Ram]] and [[white-012|Snowy]]\n", encoding="utf-8")
    farm.refresh()
    _, _, body = web("/n/notes/tags.md")
    assert 'class="link animals eartag untagged" href="/a/Ram" title="Ram">Ram<' in body
    assert 'class="link animals eartag s-white" href="/a/white-012" title="white-012">012<' in body
    assert ".eartag.s-white{--tag-bg:#f7f7f2;--tag-fg:#111}" in body


def test_rename_everywhere(web, farm_root):
    # an item's file: renamed, and every link to it (shorthand, aliased, frontmatter) rewritten
    status, _, body = web("/api/rename", {"path": "animals/orange-117.md", "new": "orange-118"})
    assert status == 200, body
    j = json.loads(body)
    assert j["old"] == "orange-117" and j["new"] == "orange-118" and j["path"] == "animals/orange-118.md"
    assert j["links"] >= 2 and j["url"] == "/a/orange-118"
    assert not (farm_root / "animals" / "orange-117.md").exists()
    assert '"[[orange-118]]"' in (farm_root / "animals" / "orange-010.md").read_text(encoding="utf-8")
    assert "[[orange-118]], [[blue-007]]" in (farm_root / "notes" / "2026Apr01-0800.md").read_text(encoding="utf-8")
    assert web("/a/orange-118")[0] == 200
    # onto another item: refused, as JSON
    status, _, body = web("/api/rename", {"path": "animals/orange-118.md", "new": "blue-007"})
    assert status == 400 and "already taken" in json.loads(body)["error"]
    assert web("/api/rename", {"path": "nowhere.md", "new": "x"})[0] == 400


def test_rename_with_unsaved_text_and_notes(web, farm, farm_root):
    # the editor's unsaved text is saved, then renamed along with everything else
    text = (farm_root / "herds" / "Hospital.md").read_text(encoding="utf-8") + "see [[Hospital]]\n"
    j = json.loads(web("/api/rename", {"path": "herds/Hospital.md", "new": "Sick Pen", "text": text})[2])
    assert j["path"] == "herds/Sick Pen.md"
    assert "see [[Sick Pen]]" in (farm_root / "herds" / "Sick Pen.md").read_text(encoding="utf-8")
    # a plain note, and links to it
    (farm_root / "notes" / "Plan.md").write_text("see [[Sheep Log 2026#May|the log]]\n", encoding="utf-8")
    farm.refresh()
    j = json.loads(web("/api/rename", {"path": "notes/Sheep Log 2026.md", "new": "Flock Log"})[2])
    assert j["path"] == "notes/Flock Log.md" and j["links"] == 1
    assert (farm_root / "notes" / "Plan.md").read_text(encoding="utf-8") == "see [[Flock Log#May|the log]]\n"
    assert 'data-rename="notes/Flock Log.md"' in web("/n/notes/Flock%20Log.md")[2]


def test_animals_live_only_by_default(web):
    body = web("/animals")[2]
    assert 'name="live" value="1" checked' in body and 'href="/a/blue-007"' not in body
    body = web("/animals?applied=1")[2]                     # box unticked
    assert 'name="live" value="1" checked' not in body and 'href="/a/blue-007"' in body
    assert 'href="/a/blue-007"' not in web("/animals?applied=1&live=1")[2]


def test_dated_lines_in_an_item_file_join_its_timeline(web, farm, farm_root):
    p = farm_root / "animals" / "orange-010.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nA good ewe.\n@31MAR2024 administered 2cc CD/T\n"
                 "- @28APR2024 booster\n", encoding="utf-8")
    farm.refresh()
    texts = [e["text"] for e in farm.timeline("orange-010")]
    assert "[[orange-010]] administered 2cc CD/T" in texts and "[[orange-010]] booster" in texts
    assert not any("good ewe" in t for t in texts)              # undated: the description
    assert not [w for w in farm.warnings(include_info=True) if w["path"] == "animals/orange-010.md"]
    assert "administered 2cc CD/T" in web("/")[2]                # and in the Recent feed


def test_delete_moves_to_trash(web, farm_root):
    assert 'action="/delete/notes/2026Apr01-0800.md"' in web("/n/notes/2026Apr01-0800.md")[2]
    status, h, _ = web("/delete/notes/2026Apr01-0800.md", {})
    assert status == 303 and h["Location"] == "/"
    assert not (farm_root / "notes" / "2026Apr01-0800.md").exists()
    assert (farm_root / ".trash" / "notes" / "2026Apr01-0800.md").exists()
    assert web("/n/notes/2026Apr01-0800.md")[0] == 404
    status, h, _ = web("/delete/herds/Hogs.md", {})                    # an item: back to its list
    assert h["Location"] == "/herds" and (farm_root / ".trash" / "herds" / "Hogs.md").exists()
    assert web("/delete/nowhere.md", {})[0] == 400


def test_save_with_a_new_name_renames(web, farm_root):
    body = web("/n/notes/Sheep%20Log%202026.md?edit=1")[2]
    assert 'name="name" value="Sheep Log 2026"' in body
    (farm_root / "notes" / "Plan.md").write_text("see [[Sheep Log 2026]]\n", encoding="utf-8")
    status, h, _ = web("/n/notes/Sheep%20Log%202026.md", {"text": "hello\n", "name": "Flock Log"})
    assert status == 303 and h["Location"] == "/n/notes/Flock%20Log.md"
    assert (farm_root / "notes" / "Flock Log.md").read_text(encoding="utf-8") == "hello\n"
    assert (farm_root / "notes" / "Plan.md").read_text(encoding="utf-8") == "see [[Flock Log]]\n"


def test_paddock_perimeter(web):
    assert "1.49 acres · 1,029 ft perimeter" in web("/p/P3")[2]
    assert ">Perimeter</th>" in web("/paddocks")[2]


def test_notes_dir_setting(web, farm, farm_root):
    cfg = farm_root / "grazr.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + 'notes_dir: ""\n', encoding="utf-8")
    farm.refresh()
    status, h, _ = web("/new", {"text": "[[orange-010]] at the top", "when": "2026-05-01 07:30"})
    assert status == 303 and h["Location"] == "/n/2026May01-0730.md"
    assert any(e["path"] == "2026May01-0730.md" for e in farm.timeline("orange-010"))


def test_editing_an_items_file_returns_to_the_item(web):
    view = web("/n/animals/orange-010.md")[2]
    assert 'href="/a/orange-010">Back to orange-010</a>' in view
    edit = web("/n/animals/orange-010.md?edit=1")[2]
    assert 'href="/a/orange-010">Cancel</a>' in edit
    status, h, _ = web("/n/animals/orange-010.md", {"text": "---\nsex: ewe\n---\nnice\n", "name": "orange-010"})
    assert status == 303 and h["Location"] == "/a/orange-010"
    status, h, _ = web("/n/herds/Hogs.md", {"text": "pigs\n", "name": "Pigs"})   # renamed: the new page
    assert h["Location"] == "/h/Pigs"
    # a plain note still goes back to the note, with no "Back to" button
    assert web("/n/notes/2026Apr01-0800.md", {"text": "x\n"})[1]["Location"] == "/n/notes/2026Apr01-0800.md"
    assert "Back to" not in web("/n/notes/2026Apr01-0800.md")[2]


def test_pedigree_only_animals(web, farm, farm_root):
    (farm_root / "animals" / "purple-09.md").write_text("---\nsex: ewe\npedigree_only: true\n---\n", encoding="utf-8")
    p = farm_root / "animals" / "orange-117.md"
    p.write_text(p.read_text(encoding="utf-8").replace("---\n", "---\ndam: purple-09\n", 1), encoding="utf-8")
    farm.refresh()
    assert 'href="/a/purple-09"' not in web("/animals")[2]               # not one of my animals
    shown = web("/animals?applied=1")[2]                                 # unless leavers are shown
    assert 'href="/a/purple-09"' in shown and "pedigree only" in shown
    assert "Pedigree only: not on this farm" in web("/a/purple-09")[2]
    assert 'href="/a/purple-09"' in web("/a/orange-117")[2]              # still in the pedigree
    assert [o["id"] for o in farm.offspring("purple-09")] == ["orange-117"]


def test_list_columns_and_sorting(web):
    body = web("/animals")[2]
    assert "<table data-sortable>" in body and 'class="colpick"' in body
    assert '<th data-key="f:born">born</th>' in body                       # default columns
    assert "data-key=\"f:" not in web("/animals?cols=")[2]                 # every column unticked
    body = web("/animals?cols=dam,born&sort=-f:born")[2]
    assert body.index('data-key="f:dam"') < body.index('data-key="f:born"')
    assert 'name="sort" value="-f:born"' in body                           # kept for the next Apply


def test_feed_inventory_and_usage(web, farm, farm_root):
    from datetime import datetime
    (farm_root / "feeds").mkdir()
    (farm_root / "feeds" / "Corn screenings.md").write_text(
        "---\nsizes: {bucket: 20}\naliases: [screenings]\n---\n", encoding="utf-8")
    (farm_root / "notes" / "Feed log.md").write_text(
        "@01Oct2026-0800 [[Corn screenings]] = 20 buckets\n"
        "@05Oct2026-0800 Fed 1 bucket of soaked [[Corn screenings]] to [[Ewe Herd]]\n"
        "@06Oct2026-0800 bought 10 lb [[Corn screenings]]\n"
        "@07Oct2026-0800 Fed 2 scoops [[Corn screenings]] to [[Ewe Herd]]\n"
        "@08Oct2026-0800 Fed 1 bucket of screenings to [[Ewe Herd]]\n", encoding="utf-8")
    farm.refresh()
    ledger = farm.feed_ledger("Corn screenings")
    assert [(x["kind"], x["lb"], x["on_hand"]) for x in ledger] == [
        ("count", 400, 400), ("use", 20, 380), ("buy", 10, 390)]
    assert ledger[1]["herds"] == ["Ewe Herd"]
    st = farm.feed_status("Corn screenings", now=datetime(2026, 10, 10))
    assert st["on_hand"] == 390 and round(st["per_day"], 2) == 3.33 and round(st["days_left"]) == 117
    codes = {w["code"]: w for w in farm.warnings(include_info=True) if w["path"] == "notes/Feed log.md"}
    assert "scoop" in codes["feed-unit"]["message"]                     # unknown measure: say how to fix
    fix = json.loads(codes["unlinked-feed"]["fix"])
    assert fix == {"kind": "link", "text": "screenings", "to": "Corn screenings|screenings"}
    farm.apply_fix("notes/Feed log.md", codes["unlinked-feed"]["line"], fix)
    assert "Fed 1 bucket of [[Corn screenings|screenings]] to [[Ewe Herd]]" in \
        (farm_root / "notes" / "Feed log.md").read_text(encoding="utf-8")
    assert farm.feed_ledger("Corn screenings")[-1]["on_hand"] == 370
    assert farm.herd_feed("Ewe Herd", now=datetime(2026, 10, 10)) == [{"feed": "Corn screenings", "lb": 40, "times": 2}]
    page = web("/f/Corn%20screenings")[2]
    assert "370 lb (18.5 buckets)" in page and "<option>bucket</option>" in page
    assert "Corn screenings" in web("/feeds")[2]
    # the record form writes a note line a person would write
    status, h, _ = web("/feed/Corn%20screenings", {"kind": "use", "qty": "2", "unit": "bucket",
                                                    "herd": "Ewe Herd", "when": "2026-10-09T07:00"})
    assert status == 303 and h["Location"] == "/f/Corn%20screenings"
    assert (farm_root / "notes" / "2026Oct09-0700.md").read_text(encoding="utf-8") == \
        "Fed 2 buckets [[Corn screenings]] to [[Ewe Herd]]\n"
    web("/feed/Corn%20screenings", {"kind": "count", "qty": "15", "unit": "bucket", "when": "2026-10-09T08:00"})
    assert farm.feed_ledger("Corn screenings")[-1]["on_hand"] == 300


def test_feed_amount_without_a_number(farm, farm_root):
    (farm_root / "feeds").mkdir()
    (farm_root / "feeds" / "Hay.md").write_text("---\nsizes: {bale: 900}\naliases: [hay]\n---\n", encoding="utf-8")
    (farm_root / "notes" / "Hay log.md").write_text(
        "@01Dec2025-0800 Gave bale of [[Hay]]\n"           # 1 bale
        "@02Dec2025-0800 Took the rest of [[Hay]] away\n"   # not an amount
        "@03Dec2025-0800 Gave bale of hay\n", encoding="utf-8")
    farm.refresh()
    assert [(x["kind"], x["lb"], x["written"]) for x in farm.feed_ledger("Hay")] == [("use", 900, "1 bale")]
    w = [w for w in farm.warnings(include_info=True) if w["path"] == "notes/Hay log.md" and w["code"] != "unattached"]
    assert [(x["line"], x["code"]) for x in w] == [(3, "unlinked-feed")]


def test_feed_standing_rate(web, farm, farm_root):
    from datetime import datetime
    (farm_root / "feeds").mkdir()
    (farm_root / "feeds" / "Corn screenings.md").write_text(
        "---\nsizes: {bucket: 20}\naliases: [screenings]\n---\n", encoding="utf-8")
    (farm_root / "notes" / "Feed log.md").write_text(
        "@01Oct2026-0800 [[Corn screenings]] = 20 buckets\n"
        "@02Oct2026-1200 Started feeding 1 bucket of [[Corn screenings]] per day to [[Ewe Herd]]\n"
        "@05Oct2026-1200 Fed 1 bucket of [[Corn screenings]] to [[Ewe Herd]]\n"   # one extra
        "@06Oct2026-0900 [[Ewe Herd]] down to half a bucket of [[Corn screenings]] a day\n"
        "@09Oct2026-1800 Stopped feeding [[Corn screenings]]\n", encoding="utf-8")
    farm.refresh()
    now = datetime(2026, 10, 12)
    ledger = farm.feed_ledger("Corn screenings", now)
    daily = [(x["ts"][5:10], x["lb"]) for x in ledger if x.get("daily")]
    # 1 bucket on 2-5 Oct; half from the 6th (09:00, so the 6th, 7th, 8th and 9th); none after the stop
    assert daily == [("10-02", 20), ("10-03", 20), ("10-04", 20), ("10-05", 20),
                     ("10-06", 10), ("10-07", 10), ("10-08", 10), ("10-09", 10)]
    st = farm.feed_status("Corn screenings", now=now)
    assert st["on_hand"] == 400 - 80 - 20 - 40 and not st["rates"]
    assert farm.herd_feed("Ewe Herd", now=now) == [{"feed": "Corn screenings", "lb": 140, "times": 9}]
    rows = farm.feed_ledger_rows(ledger)
    assert [(x["kind"], x.get("days")) for x in rows if x["kind"] != "count"] == [
        ("rate", None), ("use", 3), ("use", None), ("use", 1), ("rate", None), ("use", 4), ("stop", None)]
    # still running: per day is the rate
    (farm_root / "notes" / "Feed log.md").write_text(
        "@01Oct2026-0800 [[Corn screenings]] = 20 buckets\n"
        "@02Oct2026-1200 Started feeding 1 bucket of [[Corn screenings]] per day to [[Ewe Herd]]\n", encoding="utf-8")
    farm.refresh()
    st = farm.feed_status("Corn screenings", now=now)
    assert st["per_day"] == 20 and st["on_hand"] == 400 - 200 and st["days_left"] == 10
    page = web("/f/Corn%20screenings")[2]
    assert "fed daily" in page and "running" in page
    chart = json.loads(html.unescape(page.split("data-chart='")[1].split("'")[0]))   # the on-hand line
    assert chart["points"][0][:3] == ["2026-10-01T08:00", 400, "count"] and chart["per_day"] == 20
    web("/feed/Corn%20screenings", {"kind": "stop", "herd": "Ewe Herd", "when": "2026-10-13T07:00"})
    assert (farm_root / "notes" / "2026Oct13-0700.md").read_text(encoding="utf-8") == \
        "Stopped feeding [[Corn screenings]] to [[Ewe Herd]]\n"
    web("/feed/Corn%20screenings", {"kind": "rate", "qty": "2", "unit": "bucket", "herd": "Ewe Herd",
                                     "when": "2026-10-13T08:00"})
    assert (farm_root / "notes" / "2026Oct13-0800.md").read_text(encoding="utf-8") == \
        "Started feeding 2 buckets [[Corn screenings]] per day to [[Ewe Herd]]\n"


def test_animal_pager(web):
    page = web("/a/orange-010")[2]
    assert "data-pager=" in page and "/a/orange-011" in page.split("data-pager=")[1].split(">")[0]


def test_sync_conflict_copies_are_skipped(farm, farm_root):
    (farm_root / "notes" / "Feed log.sync-conflict-20261015-101500-ABCDEFG.md").write_text(
        "@01Oct2026-0800 [[Ewe Herd]] -> [[P3]]\n", encoding="utf-8")
    farm.refresh()
    assert not farm.q("SELECT * FROM entries WHERE path LIKE '%sync-conflict%'")
    w = [w for w in farm.warnings() if w["code"] == "sync-conflict"]
    assert [x["path"] for x in w] == ["notes/Feed log.sync-conflict-20261015-101500-ABCDEFG.md"]


def test_phone_bits(web):
    status, h, body = web("/manifest.webmanifest")
    assert status == 200 and json.loads(body)["display"] == "standalone"
    page = web("/")[2]
    assert 'class="tabbar"' in page and 'rel="manifest"' in page
    assert 'data-insert-for="text"' in web("/new")[2]
    assert 'id="gps"' in web("/draw")[2]       # GPS corners; app.js adds the location dot to every map
