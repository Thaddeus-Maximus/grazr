import pytest

from grazr.ids import Resolver, norm


@pytest.mark.parametrize("text", [
    "orange-010", "orange 010", "o 010", "o10", "ewe 010", "e010", "E010",
    "🟠010", "🔶 10", "🟠️010", "o#10", "o_010",
])
def test_animal_spellings(animal_ids, text):
    assert animal_ids.match(text) == "orange-010"


@pytest.mark.parametrize("text", ["P3", "Ewe Herd", "orange", "o", "10", "x010"])
def test_not_animals(animal_ids, text):
    assert animal_ids.match(text) is None


def test_exact_entity_wins_over_animal_rule(animal_ids):
    # D-2: a paddock named B2 is the paddock, not blue-002
    r = Resolver(animal_ids, {"B2": "paddocks", "blue-002": "animals"}, {})
    assert r.resolve("b2").id == "B2"
    assert r.resolve("b 2").id == "blue-002"


def test_resolution(animal_ids):
    r = Resolver(animal_ids, {"orange-010": "animals", "Ewe Herd": "herds"},
                 {"notes/Walnut for Dewormer.md": None})
    assert r.resolve("ewe  herd").id == "Ewe Herd"
    assert r.resolve("o 10").shorthand
    assert not r.resolve("Orange-010").shorthand
    assert r.resolve("Walnut for Dewormer").kind == "note"
    assert r.resolve("none").kind == "none"
    assert r.resolve("o 999").kind == "unresolved"


def test_bare_ids(animal_ids):
    found = [c for _, _, c in animal_ids.find_bare("e010 and ewe 4, 007 wether; plan b 2, #117")]
    assert found == ["orange-010", "orange-004", "blue-007"]


def test_norm():
    assert norm("  Ewe   Herd ") == "ewe herd"
