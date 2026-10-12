from datetime import datetime

import pytest

from grazr.dates import Stamp, parse_filename, parse_heading, parse_when, split_at

REF = Stamp(datetime(2026, 10, 12, 7, 7))


def test_filenames():
    assert parse_filename("2026Oct12-0707") == REF
    assert parse_filename("11oct2026-1332").dt == datetime(2026, 10, 11, 13, 32)
    s = parse_filename("2026Oct12")
    assert s.dt == datetime(2026, 10, 12, 12, 0) and s.approx
    assert parse_filename("Sheep Log 2026") is None


@pytest.mark.parametrize("text,dt,approx", [
    ("12Oct2026-0707", datetime(2026, 10, 12, 7, 7), False),
    ("2026-10-12 7:07", datetime(2026, 10, 12, 7, 7), False),
    ("yesterday", datetime(2026, 10, 11, 12, 0), True),
    ("last night", datetime(2026, 10, 11, 21, 0), True),
    ("0815", datetime(2026, 10, 12, 8, 15), False),        # D-7
    ("6pm", datetime(2026, 10, 12, 18, 0), False),
    ("2026-10-12", datetime(2026, 10, 12, 7, 7), True),    # same day: note's time
    ("Oct 3", datetime(2026, 10, 3, 12, 0), True),
])
def test_when(text, dt, approx):
    s = parse_when(text, REF)
    assert (s.dt, s.approx) == (dt, approx)


def test_when_bad():
    assert parse_when("nonsense", REF) is None
    assert parse_when("yesterday", None) is None


def test_split_at():
    s, rest, raw = split_at("@yesterday 3 ewes lambed", REF)
    assert raw == "yesterday" and rest == "3 ewes lambed"
    s, rest, raw = split_at("@12Oct2026-0707 [[Ewe Herd]] -> [[P3]]", REF)
    assert s == REF and rest == "[[Ewe Herd]] -> [[P3]]"


def test_headings():
    assert str(parse_heading("26mar2026")) == "2026-03-26"
    assert str(parse_heading("16MAR2026")) == "2026-03-16"
    assert parse_heading("Sheep Log 2026") is None
    assert parse_heading("Notes") is None
