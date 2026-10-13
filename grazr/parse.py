"""Note → entries with timestamps, links, tags, images, moves (spec §5)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import dates
from .dates import Stamp
from .frontmatter import split
from .ids import AnimalIds, link_target

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".avif")

LINK = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
EMBED = re.compile(r"!\[\[([^\]\n]+)\]\]")
MD_IMAGE = re.compile(r"!\[[^\]\n]*\]\(<?([^)>\n]+?)>?\)")
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
CODE = re.compile(r"`[^`\n]*`")
TAG = re.compile(r"(?:(?<=\s)|^)#([^\s#.,;:!?()\[\]{}\"'`]+)")
ARROW = re.compile(r"\+>|->|→")
SEP = re.compile(r"^\s*(?:,|and\b|&)?\s*$", re.I)
HEAD = re.compile(r"\[\[([^\]\n]+)\]\]\s*(?:=\s*(\d+)\b|([+-])(\d+)\b)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[.\]\s+)?(.*)$")
DATE_LINE = re.compile(r"^\s*date\s*:\s*(.*)$", re.I)
QUOTE = re.compile(r"^\s*(?:>\s?)+")
WHEN_START = re.compile(r"^@(?=\d|today|yesterday|tomorrow|last\b|this\b|tonight|"
                        r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)


@dataclass
class Warning:
    path: str
    line: int
    code: str
    message: str
    severity: str = "warning"   # "error" | "warning" | "info"
    fix: dict | None = None     # {"kind": ..., ...} for one-click fixes


@dataclass
class Move:
    left: list[str]
    arrow: str          # "->" or "+>"
    right: list[str]


@dataclass
class Head:
    target: str
    op: str             # "=", "+", "-"
    n: int


EXIT_WORDS = ("died", "dead", "slaughtered", "butchered", "processed", "sold", "culled")
WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(lb|lbs|kg)\b\.?(?:\s+(live|hanging|hot|carcass|dressed))?", re.I)


# `died of bloat`, `died, crushed by ewe`, `dead — coyote`: up to the end of the sentence
CAUSE = re.compile(r"\s*(?:(?:of|from)\b|,|:|-|–|—)\s*([^.;#]+)", re.I)


@dataclass
class Exit:
    """`[[a]], [[b]] slaughtered 66 lb hanging` (spec §5.3.1)."""
    targets: list[str]
    kind: str               # the exit word, lower case
    weight: float | None = None
    unit: str | None = None
    basis: str | None = None  # live, hanging, ...
    cause: str | None = None  # "bloat" in `[[x]] died of bloat`


@dataclass
class Entry:
    path: str
    line: int
    stamp: Stamp | None
    text: str                     # entry text without list marker or @when
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)          # raw targets, own
    inherited: list[str] = field(default_factory=list)      # from parent items
    images: list[str] = field(default_factory=list)
    move: Move | None = None
    head: Head | None = None
    exit: Exit | None = None
    parent_line: int | None = None
    bare: list[tuple[int, int, str]] = field(default_factory=list)  # unlinked IDs


@dataclass
class Note:
    path: str
    stamp: Stamp | None
    tags: list[str]
    entries: list[Entry]
    warnings: list[Warning]


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def is_tag(word: str) -> bool:
    return any(c.isalpha() for c in word)   # D-3: #117 is not a tag


def find_tags(text: str) -> list[str]:
    masked = _mask(_mask(_mask(_mask(text, EMBED), LINK), CODE), URL)
    out = []
    for m in TAG.finditer(masked):
        t = m.group(1).rstrip("/-")
        if t and is_tag(t) and t not in out:
            out.append(t)
    return out


def is_tag_line(text: str) -> bool:
    words = text.split()
    return bool(words) and all(w.startswith("#") and len(w) > 1 and is_tag(w[1:]) for w in words)


def find_images(text: str) -> list[str]:
    out = []
    for m in EMBED.finditer(text):
        t = m.group(1).split("|", 1)[0].strip()
        if t.lower().endswith(IMAGE_EXT):
            out.append(t)
    for m in MD_IMAGE.finditer(text):
        t = m.group(1).strip()
        if not URL.match(t):
            out.append(t)
    return out


def find_move(text: str) -> Move | None:
    """`[[a]], [[b]] -> [[c]] and [[d]] prose` → Move (spec §5.3, D-5)."""
    links = [(m.start(), m.end(), link_target(m.group(1))) for m in LINK.finditer(text)]
    if not links:
        return None
    masked = _mask(_mask(text, EMBED), LINK)
    am = ARROW.search(masked)
    if not am:
        return None
    a0, a1 = am.span()
    before = [l for l in links if l[1] <= a0]
    after = [l for l in links if l[0] >= a1]
    left: list[str] = []
    edge = a0
    for s, e, t in reversed(before):
        gap = text[e:edge]
        if not (gap.strip() == "" if edge == a0 else SEP.match(gap)):
            break
        left.insert(0, t)
        edge = s
    right: list[str] = []
    edge = a1
    for s, e, t in after:
        gap = text[edge:s]
        if not (gap.strip() == "" if edge == a1 else SEP.match(gap)):
            break
        right.append(t)
        edge = e
    if not left or not right:
        return None
    arrow = "+>" if am.group(0) == "+>" else "->"
    return Move(left, arrow, right)


def find_head(text: str) -> Head | None:
    m = HEAD.search(text)
    if not m:
        return None
    if m.group(2) is not None:
        return Head(link_target(m.group(1)), "=", int(m.group(2)))
    return Head(link_target(m.group(1)), m.group(3), int(m.group(4)))


def find_exit(text: str, words=EXIT_WORDS) -> Exit | None:
    """Animal links, then an exit word: `[[x]] died`, `[[a]] and [[b]] sold 95 lb live`."""
    links = list(LINK.finditer(text))
    if not links:
        return None
    alt = "|".join(re.escape(w) for w in words)
    for i in range(len(links)):
        targets = [link_target(links[i].group(1))]
        end = links[i].end()
        j = i + 1
        while j < len(links) and SEP.match(text[end:links[j].start()]):
            targets.append(link_target(links[j].group(1)))
            end = links[j].end()
            j += 1
        m = re.match(rf"\s*,?\s*(?:was\s+|were\s+|got\s+)?({alt})\b", text[end:], re.I)
        if m:
            ex = Exit(targets, m.group(1).lower())
            rest = text[end + m.end():]
            w = WEIGHT.search(rest)
            if w:
                ex.weight = float(w.group(1))
                ex.unit = "kg" if w.group(2).lower() == "kg" else "lb"
                ex.basis = w.group(3).lower() if w.group(3) else None
                rest = rest[:w.start()] + rest[w.end():]
            c = CAUSE.match(rest)
            if c:
                cause = TAG.sub("", c.group(1)).strip(" ,;:-–—")
                ex.cause = cause or None
            return ex
    return None


def parse_note(path: str, text: str, animal_ids: AnimalIds | None = None,
               exit_words=EXIT_WORDS) -> Note:
    stem = path.rsplit("/", 1)[-1]
    stem = stem[:-3] if stem.lower().endswith(".md") else stem
    note_stamp = dates.parse_filename(stem)
    sp = split(text)
    warnings: list[Warning] = []
    entries: list[Entry] = []
    note_tags: list[str] = []
    ctx = note_stamp                   # current Date:/heading timestamp
    stack: list[tuple[int, Entry]] = []
    in_code = False
    in_comment = False
    undated: list[int] = []

    for i, raw in enumerate(sp.body.split("\n")):
        lineno = sp.body_line + i
        line = raw.rstrip("\r")
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            stack.clear()
            continue
        if in_code:
            continue
        if stripped.startswith("%%"):
            if not (stripped.endswith("%%") and len(stripped) > 2):
                in_comment = not in_comment
            continue
        if in_comment:
            continue
        if not stripped:
            continue
        hm = HEADING.match(line)
        if hm:
            stack.clear()
            day = dates.parse_heading(hm.group(2))
            if day is not None:
                ctx = dates.combine(day, None, note_stamp)
            continue
        dm = DATE_LINE.match(line)
        if dm:
            stack.clear()
            got = dates.parse_when(dm.group(1), note_stamp)
            if got is None:
                warnings.append(Warning(path, lineno, "bad-date",
                                        f"can't read date {dm.group(1).strip()!r}"))
            else:
                ctx = got
            continue
        if is_tag_line(stripped):
            for t in stripped.split():
                if t[1:] not in note_tags:
                    note_tags.append(t[1:])
            continue

        content = QUOTE.sub("", line)
        lm = LIST.match(content)
        parent = None
        if lm:
            indent = len(lm.group(1).expandtabs(4))
            content = lm.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1] if stack else None
        else:
            indent = None
            stack.clear()
            content = content.strip()
        if not content:
            continue

        stamp = None
        if WHEN_START.match(content):
            ref = parent.stamp if parent and parent.stamp else ctx
            stamp, rest, raw_when = dates.split_at(content, ref)
            if stamp is None:
                warnings.append(Warning(path, lineno, "bad-date", f"can't read date @{raw_when}"))
            else:
                content = rest
        if stamp is None:
            stamp = parent.stamp if parent else ctx

        e = Entry(path=path, line=lineno, stamp=stamp, text=content)
        e.links = [link_target(m.group(1)) for m in LINK.finditer(content)]
        e.tags = find_tags(content)
        e.images = find_images(content)
        e.move = find_move(content)
        if e.move is None:
            e.head = find_head(content)
            if e.head is None:
                e.exit = find_exit(content, exit_words)
        if parent is not None:
            e.parent_line = parent.line
            e.inherited = [l for l in parent.links + parent.inherited if l not in e.links]
        if animal_ids is not None:
            masked = _mask(_mask(_mask(_mask(content, EMBED), LINK), CODE), URL)
            e.bare = list(animal_ids.find_bare(masked))
        if stamp is None:
            undated.append(lineno)
        entries.append(e)
        if indent is not None:
            stack.append((indent, e))

    if undated:
        n = len(undated)
        warnings.append(Warning(
            path, undated[0], "undated",
            f"{n} entr{'y has' if n == 1 else 'ies have'} no date: add `Date:`, a date "
            f"heading, `@when`, or a dated filename"))
    for e in entries:
        for t in note_tags:
            if t not in e.tags:
                e.tags.append(t)
    return Note(path, note_stamp, note_tags, entries, warnings)
