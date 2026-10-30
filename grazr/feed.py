"""Feed: what the herds eat and what's on hand (spec §6.6).

A feed is a file in `feeds/`. Amounts are kept in pounds; a feed's `sizes` say what
its other measures weigh (`sizes: {bucket: 20, bale: 50}`). In notes, an amount next
to a feed link is a feed event:

    Fed 1 bucket [[Corn screenings]] to [[Ewes]]      usage (by the herds linked)
    bought 40 bales [[Alfalfa]]                         purchase
    [[Corn screenings]] = 20 buckets                    stock count
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LB_PER = {"lb": 1.0, "kg": 2.20462, "ton": 2000.0, "t": 2000.0, "oz": 1 / 16}
_UNIT_ALIASES = {"lbs": "lb", "pound": "lb", "pounds": "lb", "#": "lb", "kgs": "kg", "kilo": "kg",
                 "kilos": "kg", "kilogram": "kg", "kilograms": "kg", "tons": "ton", "tonne": "ton"}
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 0.5, "half a": 0.5, "a half": 0.5}
NUM = (r"(?P<n>\d+(?:\.\d+)?(?:\s*/\s*\d+)?"
       r"|(?:half a|a half|half|an?|one|two|three|four|five|six|seven|eight|nine|ten)(?![A-Za-z]))")
UNIT = r"(?P<u>[A-Za-z#]+)"
LINK = r"\[\[(?P<t>[^\]\n|#]+)(?:#[^\]\n|]*)?(?:\|[^\]\n]*)?\]\]"
# up to two describing words between an amount and its feed (`1 bucket of soaked [[Corn]]`),
# but not joining words: in `[[Corn]] 2 buckets and [[Soy]] 1 bucket` the 2 is Corn's
FILLER = r"(?:(?!(?:and|or|to|for|with|then|plus|per|each|into|on|in|from)\b)[A-Za-z()-]+\s+){0,2}?"
# `20 lb of soaked [[Corn]]`, `1 bucket [[Corn]]`, `half bucket of [[Soy]]`, `3 [[Hay bales]]`
_BEFORE = re.compile(rf"(?<![\w.]){NUM}\s*(?:{UNIT}\s+)?(?:of\s+)?{FILLER}{LINK}", re.I)
# `[[Corn]] 20 lb`, `[[Corn]]: 2 buckets`; a count is `[[Corn]] = 20 buckets`
_AFTER = re.compile(rf"{LINK}\s*(?P<op>[:=,-])?\s*{NUM}\s*{UNIT}?(?![\w])", re.I)
# no number: `gave bale of [[Hay]]` is 1 bale. Only for measure words (or the feed's own
# sizes), so `rest of [[Hay]]` or `lots of [[Hay]]` isn't an amount
_IMPLIED = re.compile(rf"(?<![\w.])(?P<u>[A-Za-z]+)\s+of\s+{FILLER}{LINK}", re.I)
MEASURES = {"bale", "bucket", "bag", "sack", "scoop", "pail", "flake", "cup", "can", "tote", "tub",
            "pan", "bushel", "load", "wagon", "handful", "crockpot", "crockpotfull", "bucketful", "scoopful"}
# a standing rate: `Started feeding 1 bucket of [[Corn]] per day to [[Ewes]]`
RATE = re.compile(r"\b(?:per|a|each|every)\s+day\b|\bdaily\b|/\s*day\b", re.I)
STOP = re.compile(r"\b(?:stop(?:ped|s)?|quit|no more|discontinued|ended|took (?:them|her|him|it) off)\b", re.I)
BUY = re.compile(r"\b(bought|buy|purchased?|picked up|delivered|delivery|got in)\b", re.I)


def number(s: str) -> float:
    s = s.strip().lower()
    if s in _WORDS:
        return float(_WORDS[s])
    if "/" in s:
        a, b = (x.strip() for x in s.split("/", 1))
        return float(a) / float(b)
    return float(s)


def unit_name(u: str) -> str:
    u = u.strip().lower()
    u = _UNIT_ALIASES.get(u, u)
    if u in LB_PER:
        return u
    if u.endswith(("shes", "ches", "xes", "sses")):     # boxes, batches
        return u[:-2]
    if len(u) > 2 and u.endswith("s"):                   # buckets, bales, bags
        return u[:-1]
    return u


def sizes(fields: dict) -> dict[str, float]:
    """A feed's `sizes` → {measure: pounds}. A mapping (`{bucket: 20}`) or text as the
    web form saves it (`bucket = 20, bale = 50`)."""
    raw = fields.get("sizes")
    pairs: list[tuple[str, object]] = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, str):
        pairs = re.findall(r"([A-Za-z][A-Za-z ]*?)\s*['\"]?\s*[:=]?\s*(\d+(?:\.\d+)?)", raw)
    out = {}
    for k, v in pairs:
        try:
            out[unit_name(str(k))] = float(str(v).split()[0])
        except (ValueError, IndexError):
            continue
    return out


@dataclass
class Amount:
    feed_target: str      # the link's target as written
    qty: float
    unit: str             # "" when none was written
    op: str = ""          # "=" for a stock count
    start: int = 0
    end: int = 0
    implied: bool = False  # no number written (`bale of [[Hay]]`): 1, if `unit` is a measure


def amounts(text: str) -> list[Amount]:
    """Every amount written next to a feed-looking link in `text` (one per link).
    An amount before a link belongs to it (`1 bucket [[Corn]]`) unless the link is
    followed by `=` or `:` and an amount (`[[Corn]] = 20 buckets`)."""
    before: dict[int, Amount] = {}
    for m in _BEFORE.finditer(text):
        u = m.group("u") or ""
        before[m.start("t") - 2] = Amount(m.group("t").strip(), number(m.group("n")),
                                          "" if u.lower() == "of" else u, "", m.start(), m.end())
    for m in _IMPLIED.finditer(text):
        k = m.start("t") - 2
        if k not in before and unit_name(m.group("u")) not in LB_PER:
            before[k] = Amount(m.group("t").strip(), 1.0, m.group("u"), "", m.start(), m.end(), True)
    taken = {a.start for a in before.values()}
    out = dict(before)
    for m in _AFTER.finditer(text):
        op = m.group("op") or ""
        if op not in ("=", ":") and (m.start() in out or m.start("n") in taken):
            continue                    # that number belongs to the next link
        out[m.start()] = Amount(m.group("t").strip(), number(m.group("n")), m.group("u") or "",
                                "=" if op == "=" else "", m.start(), m.end())
    return [out[k] for k in sorted(out)]


def is_measure(unit: str, feed_sizes: dict[str, float]) -> bool:
    u = unit_name(unit)
    return u in MEASURES or u in feed_sizes


def pounds(a: Amount, feed_sizes: dict[str, float]) -> float | None:
    """The amount in pounds, or None if its measure is unknown for this feed."""
    if not a.unit:
        return a.qty * feed_sizes["each"] if "each" in feed_sizes else a.qty
    u = unit_name(a.unit)
    if u in LB_PER:
        return a.qty * LB_PER[u]
    if u in feed_sizes:
        return a.qty * feed_sizes[u]
    return None


@dataclass
class Event:
    path: str
    line: int
    ts: str | None
    feed: str
    kind: str              # "use" | "buy" | "count"
    lb: float
    written: str           # "1 bucket"
    herds: list[str] = field(default_factory=list)


def unlinked(text: str, aliases: list[str], feed_sizes: dict[str, float] | None = None) -> str | None:
    """An alias written as an amount of feed but not linked (`1 bucket of screenings`,
    `bale of hay`) → the alias as written, for a lint fix."""
    masked = re.sub(r"\[\[[^\]\n]*\]\]", lambda m: " " * len(m.group(0)), text)
    for alias in sorted(aliases, key=len, reverse=True):
        m = re.search(rf"(?<![\w.]){NUM}\s*(?:{UNIT}\s+)?(?:of\s+)?{FILLER}"
                      rf"(?P<a>{re.escape(alias)})(?!\w)", masked, re.I)
        if m:
            return text[m.start("a"):m.end("a")]
        for m in re.finditer(rf"(?<![\w.])(?P<u>[A-Za-z]+)\s+of\s+{FILLER}(?P<a>{re.escape(alias)})(?!\w)",
                             masked, re.I):
            if is_measure(m.group("u"), feed_sizes or {}):
                return text[m.start("a"):m.end("a")]
    return None
