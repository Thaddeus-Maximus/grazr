from datetime import datetime

from grazr.geo import last_grazed, rest_days

T = datetime(2026, 10, 13, 12, 0)


def d(day, hour=12):
    return datetime(2026, 10, day, hour, 0)


def test_rested():
    assert rest_days(*last_grazed([(d(1), d(3))], T), T) == 10


def test_occupied_is_negative_with_duration():
    assert rest_days(*last_grazed([(d(8), None)], T), T) == -5


def test_back_to_back_herds_are_one_stretch():
    # Ewes left as Boys arrived on the 8th; Boys still there: occupied since the 3rd
    occs = [(d(3), d(8)), (d(8), None)]
    assert rest_days(*last_grazed(occs, T), T) == -10


def test_just_moved_on_is_still_occupied_and_never_grazed_is_none():
    assert rest_days(*last_grazed([(T, None)], T), T) < 0
    assert rest_days(*last_grazed([], T), T) is None
    assert rest_days(*last_grazed([(d(20), None)], T), T) is None      # future move ignored
