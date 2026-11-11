import pytest

from grazr.breed import parse
from grazr.frontmatter import update

from test_server import web  # noqa: F401  (fixture)


@pytest.mark.parametrize("text, mix", [
    ("1/4 Dorper, 1/4 Katahdin, 1/2 St. Croix", [("St. Croix", .5), ("Dorper", .25), ("Katahdin", .25)]),
    ("Dorper", [("Dorper", 1)]),
    ("Dorper 25%, Katahdin 75%", [("Katahdin", .75), ("Dorper", .25)]),
    ("Dorper, 1/2 Katahdin", [("Dorper", .5), ("Katahdin", .5)]),          # the rest is Dorper's
    ("half Dorper + half St Croix", [("Dorper", .5), ("St Croix", .5)]),
    ("0.25 Dorper; 0.75 dorper", [("Dorper", 1)]),                        # same breed adds up
    ("1/2 Dorper, 1/2 Dorper, 1/2 Katahdin", [("Dorper", 2 / 3), ("Katahdin", 1 / 3)]),  # scaled to 1
])
def test_parse(text, mix):
    got = parse(text)
    assert [n for n, _ in got] == [n for n, _ in mix]
    assert [round(s, 6) for _, s in got] == [round(s, 6) for _, s in mix]


def test_parse_nothing():
    assert parse(None) is None and parse("") is None and parse(" , ") is None


def test_breed_from_dam_and_sire(web, farm, farm_root):
    a = farm_root / "animals"
    update(a / "orange-117.md", {"breed": "1/4 Dorper, 1/4 Katahdin, 1/2 St. Croix"})
    update(a / "Ram.md", {"breed": "Dorper"})
    farm.refresh()
    b = farm.breed("orange-010")                     # dam orange-117, sire Ram
    assert not b["own"]
    assert [(n, round(s, 4)) for n, s in b["mix"]] == [("Dorper", .625), ("St. Croix", .25), ("Katahdin", .125)]
    assert farm.breed("orange-117")["own"]
    # a parent with no breed is unknown for its half
    update(a / "Ram.md", {}, ["breed"])
    farm.refresh()
    assert [(n, round(s, 4)) for n, s in farm.breed("orange-010")["mix"]] == [
        ("St. Croix", .25), ("Dorper", .125), ("Katahdin", .125), (None, .5)]
    page = web("/a/orange-010")[2]
    assert "from dam and sire" in page and 'class="breedbar"' in page and "unknown <b>50%</b>" in page
    # segments go in breed order, not by size: Dorper, Katahdin, St. Croix, unknown
    bar = page.split('class="breedbar"')[1].split("</div>")[0]
    assert bar.split('aria-label="')[1].split('"')[0] == "Dorper 12.5%, Katahdin 12.5%, St. Croix 25%, unknown 50%"
    page = web("/animals?cols=breed")[2]
    assert 'class="breedbar small"' in page and "St. Croix 25%" in page
    # a default column, with a colour key naming the breeds in view
    page = web("/animals")[2]
    assert 'data-key="f:breed"' in page and 'class="breedbar small"' in page
    key = page.split('class="breedlegend small"')[1].split("</div>")[0]
    assert [k.split("</i>")[1].split("<")[0] for k in key.split("<i ")[1:]] == ["Dorper", "Katahdin", "St. Croix", "unknown"]
