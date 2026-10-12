"""YAML frontmatter: safe reads, ruamel round-trip writes (spec §4, §8.2)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .paths import atomic_write

_LINK = re.compile(r"^\s*\[\[([^\]]+)\]\]\s*$")


@dataclass
class Split:
    yaml: str | None     # frontmatter text without the --- fences, or None
    body: str
    body_line: int       # 1-based line number of the body's first line


def split(text: str) -> Split:
    lines = text.split("\n")
    if lines and lines[0].rstrip("\r") == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") in ("---", "..."):
                return Split("\n".join(lines[1:i]), "\n".join(lines[i + 1:]), i + 2)
    return Split(None, text, 1)


def load(text: str) -> tuple[dict, Split, str | None]:
    """→ (fields, split, error). Unparseable YAML gives {} and an error message."""
    sp = split(text)
    if sp.yaml is None or not sp.yaml.strip():
        return {}, sp, None
    try:
        data = YAML(typ="safe").load(sp.yaml)
    except YAMLError as e:
        return {}, sp, str(e).splitlines()[0] if str(e) else "invalid YAML"
    if data is None:
        return {}, sp, None
    if not isinstance(data, dict):
        return {}, sp, "frontmatter is not a mapping"
    return {str(k): v for k, v in data.items()}, sp, None


def link_value(v) -> str | None:
    """`"[[orange-117]]"` (or a bare `orange-117`) → `orange-117`."""
    if v is None:
        return None
    if isinstance(v, list):
        # Obsidian writes a single link property as a one-item list sometimes
        v = v[0] if len(v) == 1 else None
        if v is None:
            return None
    s = str(v).strip()
    if not s:
        return None
    m = _LINK.match(s)
    inner = m.group(1) if m else s
    return inner.split("|", 1)[0].split("#", 1)[0].strip() or None


def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def update(path: Path, changes: dict, remove: tuple[str, ...] = ()) -> None:
    """Re-read the file and rewrite only its frontmatter (body edits survive)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    sp = split(text)
    y = _rt()
    data = y.load(sp.yaml) if sp.yaml and sp.yaml.strip() else None
    if data is None:
        from ruamel.yaml.comments import CommentedMap
        data = CommentedMap()
    for k, v in changes.items():
        data[k] = v
    for k in remove:
        data.pop(k, None)
    buf = io.StringIO()
    if len(data):
        y.dump(data, buf)
    fm = buf.getvalue()
    body = sp.body if sp.yaml is not None else text
    out = f"---\n{fm}---\n{body}" if fm else body
    atomic_write(path, out)


def dump(fields: dict, body: str = "") -> str:
    """A new file's text."""
    if not fields:
        return body
    buf = io.StringIO()
    _rt().dump(fields, buf)
    return f"---\n{buf.getvalue()}---\n{body}"
