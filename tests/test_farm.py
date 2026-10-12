"""Derived facts on the fixture farm (spec §6) and file rewrites."""
from grazr.api import fix_json


def rows(farm, q, *a):
    return [tuple(r) for r in farm.db.execute(q, a)]


def test_memberships_and_lambs(farm):
    m = rows(farm, "SELECT herd, start, end, inherited FROM memberships WHERE animal='orange-011'")
    assert ("Ewe Herd", "2026-04-17T00:00", "2026-10-12T07:07", 1) in m     # follows dam
    assert ("Hospital", "2026-10-12T07:07", None, 0) in m
    assert rows(farm, "SELECT end FROM memberships WHERE animal='blue-007'") == [("2026-09-01T00:00",)]


def test_occupancy(farm):
    o = rows(farm, "SELECT herd, paddock, end FROM occupancies")
    assert ("Ewe Herd", "P3", "2026-10-12T07:07") in o
    assert ("Ewe Herd", "P3b", None) in o


def test_head_counts(farm):
    h = rows(farm, "SELECT count, end FROM heads WHERE herd='Hogs' ORDER BY start")
    assert h == [(5, "2026-10-12T09:00"), (4, None)]


def test_birth_type(farm):
    assert rows(farm, "SELECT birth_type FROM entities WHERE id='orange-010'") == [(2,)]


def test_via_herd_visibility(farm):
    tl = [(r["path"], r["line"], r["via"]) for r in farm.timeline("orange-010")]
    assert ("notes/2026Oct12-0707.md", 5, "Ewe Herd") in tl   # nested under "[[Ewe Herd]] fed:"
    assert not any(p == "notes/2026Apr01-0800.md" for p, _, _ in tl)   # before she was born


def test_paddock_rollup(farm):
    tl = [(r["path"], r["line"], r["via"]) for r in farm.timeline("South Field")]
    assert ("notes/2026Oct12-0707.md", 2, "P3b") in tl


def test_faces(farm):
    faces = rows(farm, "SELECT face_id, group_concat(paddock) FROM face_paddocks GROUP BY face_id")
    sets = sorted(sorted(p.split(",")) for _, p in faces)
    assert ["P3", "P3b", "South Field"] in sets
    assert ["South Field"] in sets                                          # D-21


def test_warnings(farm):
    codes = {w["code"] for w in farm.warnings()}
    assert {"shorthand-link", "unlinked-mention", "unresolved-link", "bad-date"} <= codes


def test_fix_shorthand(farm):
    changes = farm.fix_all()
    assert set(changes) == {"animals/orange-011.md", "notes/2026Oct12-0707.md",
                            "notes/Sheep Log 2026.md"}
    _, new = changes["notes/Sheep Log 2026.md"]
    assert "[[orange-010]]" in new and "[[o 010]]" not in new


def test_one_click_link(farm, farm_root):
    w = next(w for w in farm.warnings() if w["code"] == "unlinked-mention")
    assert farm.apply_fix(w["path"], w["line"], fix_json(w))
    text = (farm_root / w["path"]).read_text(encoding="utf-8")
    assert "[[orange-010]] looks fine" in text
    assert not [x for x in farm.warnings() if x["code"] == "unlinked-mention"]


def test_migrate(farm):
    changes, _ = farm.migrate()
    assert "[[orange-010]] looks fine" in changes["notes/2026Oct12-0707.md"][1]


def test_retire_and_empty_occupying(farm, farm_root):
    note = farm_root / "notes" / "2026Oct13-0800.md"
    note.write_text("[[orange-011]] -> [[Ewe Herd]]\n", encoding="utf-8")
    farm.refresh()
    assert any(w["code"] == "empty-occupying" for w in farm.warnings())
    note.write_text("[[orange-011]] -> [[Ewe Herd]]\n[[Hospital]] -> [[none]]\n", encoding="utf-8")
    farm.refresh()
    assert not any(w["code"] == "empty-occupying" for w in farm.warnings())
    assert rows(farm, "SELECT end FROM occupancies WHERE herd='Hospital'") == [("2026-10-13T08:00",)]


def test_bad_moves(farm, farm_root):
    (farm_root / "notes" / "2026Oct14-0800.md").write_text(
        "[[orange-010]] -> [[P3]]\n[[Ewe Herd]] -> [[Ghost]]\n[[blue-007]] -> [[Hospital]]\n",
        encoding="utf-8")
    farm.refresh()
    codes = {(w["line"], w["code"]) for w in farm.warnings()
             if w["path"] == "notes/2026Oct14-0800.md"}
    assert codes == {(1, "move-types"), (2, "move-unknown"), (3, "move-after-left")}
