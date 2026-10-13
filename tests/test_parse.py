from datetime import datetime

from grazr.parse import find_move, find_tags, parse_note


def test_moves():
    m = find_move("[[orange-010]], [[orange-020]] and [[blue-007]] -> [[Hospital]]")
    assert m.left == ["orange-010", "orange-020", "blue-007"] and m.right == ["Hospital"]
    m = find_move("moved [[Ewe Herd]] +> [[P3b]] front fence moved")
    assert m.arrow == "+>" and m.left == ["Ewe Herd"] and m.right == ["P3b"]
    m = find_move("[[Ewe Herd]] → [[P3b]], [[P3c]]")
    assert m.arrow == "->" and m.right == ["P3b", "P3c"]


def test_move_right_side_ends_at_prose():
    m = find_move("[[Ewe Herd]] -> [[P3]] because [[orange-010]] limps")   # D-5
    assert m.right == ["P3"]


def test_not_moves():
    assert find_move("thinking about moving Ewe Herd to P3") is None
    assert find_move("[[Ewe Herd]] fed -> lots") is None
    assert find_move("[[a]] went well, then -> [[b]]") is None


def test_tags():
    text = "#vet check [[x#heading]] #117 `#code` http://a.b/#frag #cull/later"
    assert find_tags(text) == ["vet", "cull/later"]


def test_entries_and_timestamps():
    text = "\n".join([
        "---", "x: 1", "---",
        "#sheep #log",
        "first line [[Ewe Herd]]",
        "second line",
        "# 26mar2026",
        "after heading",
        "Date: 2026-03-28 9:00",
        "- [[Ewe Herd]] fed:",
        "  - 20 lb corn",
        "  - @1015 hay",
        "@27mar2026 back-dated",
    ])
    n = parse_note("notes/2026Oct12-0707.md", text)
    by_line = {e.line: e for e in n.entries}
    assert n.tags == ["sheep", "log"]
    assert sorted(by_line) == [5, 6, 8, 10, 11, 12, 13]                     # D-1
    assert by_line[5].stamp.dt == datetime(2026, 10, 12, 7, 7)
    assert by_line[8].stamp.dt == datetime(2026, 3, 26, 12, 0)
    assert by_line[11].inherited == ["Ewe Herd"]
    assert by_line[11].stamp.dt == datetime(2026, 3, 28, 9, 0)
    assert by_line[12].stamp.dt == datetime(2026, 3, 28, 10, 15)
    assert by_line[13].stamp.dt.date().isoformat() == "2026-03-27"
    assert by_line[13].text == "back-dated"
    assert "sheep" in by_line[6].tags


def test_undated_and_bad_date():
    n = parse_note("notes/misc.md", "one\ntwo\nDate: garbage\n")
    assert sorted(w.code for w in n.warnings) == ["bad-date", "undated"]


def test_code_blocks_skipped():
    n = parse_note("notes/2026Oct12-0707.md", "```\n[[a]] -> [[b]]\n```\nreal")
    assert [e.text for e in n.entries] == ["real"]


def test_exits():
    from grazr.parse import find_exit
    e = find_exit("[[blue-003]] slaughtered 69 lb hanging")
    assert (e.targets, e.kind, e.weight, e.unit, e.basis, e.cause) == (["blue-003"], "slaughtered", 69, "lb", "hanging", None)
    e = find_exit("[[a]], [[b]] died, crushed by ewe. sad #vet")
    assert (e.targets, e.kind, e.cause) == (["a", "b"], "died", "crushed by ewe")
    assert find_exit("[[o]] died of bloat").cause == "bloat"
    assert find_exit("found [[x]] dead in [[Pitts]]").cause is None
    assert find_exit("[[a]] limping, dead tired") is None
    assert find_exit("[[Ewe Herd]] fed") is None
