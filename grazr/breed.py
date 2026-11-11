"""Breed mixes: `breed: 1/4 Dorper, 1/4 Katahdin, 1/2 St. Croix` (spec §4.1.1).

A breed is a list of shares separated by `,` `;` or `+`. A share is a breed name with
an amount before or after it, as a fraction (`1/4`), a percent (`25%`), a decimal
(`0.25`) or `half`. Breeds without an amount split what's left evenly, so a plain
`Dorper` is 100% Dorper and `Dorper, 1/2 Katahdin` is half each. Shares are scaled to
add up to 1.
"""
from __future__ import annotations

import re

_AMOUNT = r"(?:\d+(?:\.\d+)?\s*%|\d+\s*/\s*\d+|\d*\.\d+|1|half)"
_PART = re.compile(rf"^(?:(?P<a>{_AMOUNT})\s+(?:of\s+)?)?(?P<name>.+?)(?:\s+(?P<b>{_AMOUNT}))?$", re.I)


def key(name: str) -> str:
    """Breeds compare without case, dots or extra spaces: `St Croix` is `St. Croix`."""
    return re.sub(r"\s+", " ", name.replace(".", " ")).strip().casefold()


def _share(s: str) -> float:
    s = s.strip().lower().replace(" ", "")
    if s == "half":
        return 0.5
    if s.endswith("%"):
        return float(s[:-1]) / 100
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    return float(s)


def parse(text) -> list[tuple[str, float]] | None:
    """`breed:` → [(name, share)] adding up to 1, biggest first; None if there's nothing."""
    if text is None or isinstance(text, bool):
        return None
    parts: list[tuple[str, float | None]] = []
    for raw in re.split(r"[,;+]", str(text)):
        raw = raw.strip()
        if not raw:
            continue
        m = _PART.match(raw)
        name, amt = raw, None
        if m and (m.group("a") or m.group("b")) and m.group("name").strip():
            name, amt = m.group("name").strip(), _share(m.group("a") or m.group("b"))
        parts.append((name.strip(" -"), amt))
    if not parts:
        return None
    given = sum(a for _, a in parts if a is not None)
    rest = [n for n, a in parts if a is None]
    each = max(0.0, 1 - given) / len(rest) if rest else 0.0
    return combine([(n, each if a is None else a) for n, a in parts])


def combine(shares) -> list[tuple[str, float]] | None:
    """Add up shares of the same breed (by `key`; None is unknown), scaled to 1."""
    total = sum(s for _, s in shares)
    if total <= 0:
        return None
    out: dict[str | None, list] = {}
    for name, s in shares:
        k = None if name is None else key(name)
        if k in out:
            out[k][1] += s
        else:
            out[k] = [name, s]
    mix = [(n, s / total) for n, s in out.values() if s > 0]
    return sorted(mix, key=lambda p: (p[0] is None, -p[1], key(p[0] or "")))
