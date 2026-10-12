"""Link rewriting: `grazr fix` (shorthand links), `grazr migrate` (bare IDs), lint fixes."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from .frontmatter import split
from .ids import AnimalIds, Resolver
from .parse import LINK, URL, CODE, EMBED, _mask
from .paths import atomic_write

_FULL_LINK = re.compile(r"(?<!!)\[\[([^\]\n|#]+)((?:#[^\]\n|]*)?(?:\|[^\]\n]*)?)\]\]")


def fix_shorthand(text: str, resolver: Resolver) -> str:
    """`[[o 010|x]]` → `[[orange-010|x]]` wherever the target is shorthand (D-R2)."""
    def sub(m):
        r = resolver.resolve(m.group(1))
        if r.kind == "entity" and r.shorthand:
            return f"[[{r.id}{m.group(2)}]]"
        return m.group(0)
    return _FULL_LINK.sub(sub, text)


def _outside_links(line: str) -> str:
    return _mask(_mask(_mask(_mask(line, EMBED), LINK), CODE), URL)


def link_bare_in_line(line: str, text: str, to: str) -> str | None:
    """Rewrite the first bare `text` (outside links/code) in `line` as `[[to]]`."""
    masked = _outside_links(line)
    i = masked.find(text)
    while i != -1:
        before = masked[i - 1] if i else " "
        after = masked[i + len(text)] if i + len(text) < len(masked) else " "
        if not (before.isalnum() or after.isalnum()):
            return line[:i] + f"[[{to}]]" + line[i + len(text):]
        i = masked.find(text, i + 1)
    return None


def migrate_text(text: str, animal_ids: AnimalIds, exists=lambda _id: True) -> tuple[str, list[str]]:
    """Bare `e010`, `ewe 004`, `007 wether` → `[[orange-010]]` in a note body (§11)."""
    sp = split(text)
    lines = sp.body.split("\n")
    skipped = []
    in_code = False
    for i, line in enumerate(lines):
        if line.strip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code:
            continue
        hits = sorted(animal_ids.find_bare(_outside_links(line)), reverse=True)
        taken: list[tuple[int, int]] = []
        for s, e, canon in hits:
            if any(s < te and ts < e for ts, te in taken):
                continue
            if not exists(canon):
                skipped.append(f"line {sp.body_line + i}: {line[s:e]!r} → {canon} (no such animal)")
                continue
            line = line[:s] + f"[[{canon}]]" + line[e:]
            taken.append((s, e))
        lines[i] = line
    body = "\n".join(lines)
    if sp.yaml is None:
        return body, skipped
    head = text[: len(text) - len(sp.body)]
    return head + body, skipped


def diff(path: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


def rewrite(root: Path, changes: dict[str, str]) -> None:
    for r, text in changes.items():
        atomic_write(root / r, text)
