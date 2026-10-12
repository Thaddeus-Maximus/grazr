"""Timestamps from filenames, `@when`, `Date:` lines and date headings (spec §5.2).

All datetimes are naive local wall-clock time in the farm's timezone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from dateutil import parser as du

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"

# 2026Oct12-0707   (what grazr writes)
_NEW = re.compile(rf"^(\d{{4}})({MONTHS})(\d{{1,2}})(?:[-_ ]?(\d{{2}}):?(\d{{2}}))?$", re.I)
# 12Oct2026-0707   (legacy, also used in `@` and headings: 26mar2026)
_OLD = re.compile(rf"^(\d{{1,2}})({MONTHS})(\d{{4}})(?:[-_ ]?(\d{{2}}):?(\d{{2}}))?$", re.I)

_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$|^(\d{2})(\d{2})$", re.I)

# token that can continue a `@when` beyond its first word
_DATEISH = re.compile(rf"^(\d{{1,4}}(st|nd|rd|th)?,?|({MONTHS})[a-z]*\.?,?|\d{{1,2}}:\d{{2}}(am|pm)?|am|pm)$", re.I)

RELATIVE = {
    "today": (0, None),
    "yesterday": (-1, None),
    "tomorrow": (1, None),
    "last night": (-1, time(21, 0)),
    "this morning": (0, time(7, 0)),
    "this afternoon": (0, time(15, 0)),
    "this evening": (0, time(19, 0)),
    "tonight": (0, time(21, 0)),
}


@dataclass(frozen=True)
class Stamp:
    dt: datetime
    approx: bool = False   # the time of day was filled in, not written

    def __lt__(self, other):
        return self.dt < other.dt


def _mon(s: str) -> int:
    s = s.lower()[:3]
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(s) + 1


def _farm(text: str):
    """Farm formats → (date, time|None) or None."""
    t = text.strip()
    m = _NEW.match(t)
    if m:
        y, mo, d, hh, mm = m.groups()
    else:
        m = _OLD.match(t)
        if not m:
            return None
        d, mo, y, hh, mm = m.groups()
    try:
        day = date(int(y), _mon(mo), int(d))
        tm = time(int(hh), int(mm)) if hh is not None else None
    except ValueError:
        return None
    return day, tm


def parse_filename(stem: str) -> Stamp | None:
    """Note filename → timestamp; date-only names are approximate (noon)."""
    got = _farm(stem)
    if not got:
        return None
    day, tm = got
    if tm is None:
        return Stamp(datetime.combine(day, time(12, 0)), approx=True)
    return Stamp(datetime.combine(day, tm))


def _parse_time(text: str) -> time | None:
    m = _TIME.match(text.strip())
    if not m:
        return None
    if m.group(4):
        hh, mm, ap = int(m.group(4)), int(m.group(5)), None
    else:
        if m.group(2) is None and m.group(3) is None:
            return None  # a bare "7" is not a time
        hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap.startswith("p") and hh < 12:
            hh += 12
        elif ap.startswith("a") and hh == 12:
            hh = 0
    try:
        return time(hh, mm)
    except ValueError:
        return None


_A = datetime(2000, 1, 1, 0, 0)
_B = datetime(2001, 2, 2, 1, 1)


def _dateutil(text: str):
    """→ (date|None, time|None) using dateutil, detecting which parts were written."""
    try:
        a = du.parse(text, default=_A)
        b = du.parse(text, default=_B)
    except (ValueError, OverflowError):
        return None
    has_day = a.day == b.day and a.month == b.month
    has_year = a.year == b.year
    has_time = a.hour == b.hour or a.minute == b.minute
    if not has_day and not has_time:
        return None
    day = None
    if has_day:
        day = date(a.year, a.month, a.day) if has_year else (a.month, a.day)
    return day, (time(a.hour, a.minute) if has_time else None)


def combine(day: date | None, tm: time | None, ref: Stamp | None) -> Stamp | None:
    """Fill in whatever wasn't written from the reference timestamp (§5.2)."""
    if day is None and tm is None:
        return None
    if day is None:
        if ref is None:
            return None
        return Stamp(datetime.combine(ref.dt.date(), tm))        # D-7
    if tm is not None:
        return Stamp(datetime.combine(day, tm))
    if ref is not None and ref.dt.date() == day:
        return Stamp(datetime.combine(day, ref.dt.time()), approx=True)
    return Stamp(datetime.combine(day, time(12, 0)), approx=True)


def parse_when(text: str, ref: Stamp | None) -> Stamp | None:
    """Any reasonable date/time, relative to `ref` (the note's timestamp)."""
    t = text.strip().rstrip(":").strip()
    if not t:
        return None
    low = " ".join(t.lower().split())
    for word, (days, tm) in RELATIVE.items():
        if low == word or low.startswith(word + " "):
            if ref is None:
                return None
            rest = low[len(word):].strip()
            day = ref.dt.date() + timedelta(days=days)
            if rest:
                tm2 = _parse_time(rest)
                if tm2 is None:
                    return None
                return Stamp(datetime.combine(day, tm2))
            if tm is not None:
                return Stamp(datetime.combine(day, tm), approx=True)
            return combine(day, None, ref)
    got = _farm(t)
    if got:
        return combine(got[0], got[1], ref)
    tm = _parse_time(t)
    if tm is not None:
        return combine(None, tm, ref)
    if not any(c.isdigit() for c in t) and low not in RELATIVE:
        return None
    got = _dateutil(t)
    if not got:
        return None
    day, tm = got
    if isinstance(day, tuple):  # month/day without a year: take the ref's year
        if ref is None:
            return None
        try:
            day = date(ref.dt.year, *day)
        except ValueError:
            return None
    return combine(day, tm, ref)


def split_at(text: str, ref: Stamp | None):
    """`@when rest…` at the start of an entry → (Stamp|None, rest, raw_when|None).

    Takes the longest run of up to four words after `@` that parses. Words after
    the first must look like part of a date or time, so `@yesterday 3 ewes`
    stops after `yesterday`.
    """
    if not text.startswith("@"):
        return None, text, None
    words = text[1:].split(" ")
    if not words or not words[0]:
        return None, text, None
    candidates = [1]
    n = 1
    while n < min(4, len(words)):
        w = words[n]
        two = " ".join(words[:n + 1]).lower()
        if _DATEISH.match(w) or _TIME.match(w) or two in RELATIVE or \
                any(r.startswith(two) for r in RELATIVE):
            n += 1
            candidates.append(n)
        else:
            break
    for n in reversed(candidates):
        raw = " ".join(words[:n])
        stamp = parse_when(raw, ref)
        if stamp is not None:
            return stamp, " ".join(words[n:]).lstrip(), raw
    return None, " ".join(words[1:]).lstrip(), words[0]


def parse_heading(text: str) -> date | None:
    """A heading that is only a date (`# 26mar2026`) → that date."""
    t = text.strip()
    if not t or not any(c.isdigit() for c in t):
        return None
    got = _farm(t)
    if got:
        return got[0]
    got = _dateutil(t)
    if not got or got[0] is None or isinstance(got[0], tuple):
        return None
    return got[0]
