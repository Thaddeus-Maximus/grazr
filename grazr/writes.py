"""Web-UI write actions (spec §8.2). Each one is a plain file edit, written atomically."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from ruamel.yaml import YAML

from . import frontmatter
from .ids import link_target
from .paths import atomic_write, iter_markdown

_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


class WriteError(ValueError):
    pass


def safe_name(name: str) -> str:
    name = " ".join(name.split()).strip(". ")
    if not name or _BAD_NAME.search(name):
        raise WriteError(f"{name!r} can't be a file name (avoid \\ / : * ? \" < > | # ^ [ ])")
    return name


def under(root: Path, rel: str) -> Path:
    """`rel` inside the data root, or WriteError."""
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise WriteError("path is outside the data root")
    return p


def link(i: str) -> str:
    return f"[[{i}]]"


def new_note(root: Path, config, text: str, tags: list[str] = (), when: datetime | None = None,
             approx: bool = False, now: datetime | None = None) -> str:
    """Write `<notes_dir>/<when>.md` (default: now); → its root-relative path.

    A backdated note is named by its own time, like one written then. When only
    the day is known (`approx`), a `Date:` line marks its entries as approximate.
    """
    now = now or datetime.now()
    when = when or now
    if when > now + timedelta(minutes=5):
        raise WriteError(f"{when:%d %b %Y %H:%M} is in the future")
    stem = when.strftime(config["note_filename"])
    folder = under(root, (config["notes_dir"] or "").strip("/\\") or ".")
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.md"
        n += 1
    body = text.strip("\n") + "\n"
    if approx:
        body = f"Date: {when:%d%b%Y}\n" + body
    tags = [t.lstrip("#") for t in tags if t.strip("# ")]
    if tags:
        body = " ".join("#" + t for t in tags) + "\n" + body
    atomic_write(path, body)
    return path.relative_to(root).as_posix()


def move_line(left: list[str], right: list[str], arrow: str = "->", comment: str = "") -> str:
    if arrow not in ("->", "+>"):
        raise WriteError("arrow must be -> or +>")
    if not left or not right:
        raise WriteError("pick something to move and somewhere to move it")
    line = f"{', '.join(map(link, left))} {arrow} {', '.join(map(link, right))}"
    return line + (f" {comment.strip()}" if comment.strip() else "")


def new_entity(root: Path, folder: str, eid: str, fields: dict, body: str = "") -> str:
    eid = safe_name(eid)
    path = root / folder / f"{eid}.md"
    if path.exists():
        raise WriteError(f"{folder}/{eid}.md already exists")
    atomic_write(path, frontmatter.dump({k: v for k, v in fields.items() if v not in (None, "")},
                                        body + ("\n" if body and not body.endswith("\n") else "")))
    return path.relative_to(root).as_posix()


def parse_value(text: str):
    """A form value → a typed YAML scalar; links stay quoted strings."""
    t = text.strip()
    if t == "":
        return None
    if t.startswith("[["):
        return t
    try:
        v = YAML(typ="safe").load(t)
    except Exception:
        return t
    if isinstance(v, (str, int, float, bool, list)) or v is None or hasattr(v, "isoformat"):
        return v
    return t


def edit_properties(path: Path, values: dict[str, str], removed: list[str]) -> None:
    """Rewrite only the frontmatter; body edits made in Obsidian survive."""
    changes = {}
    for k, raw in values.items():
        k = k.strip()
        if not k or k == "perimeter":
            continue
        v = parse_value(raw)
        if v is None:
            removed.append(k)
        else:
            changes[k] = v
    frontmatter.update(path, changes, tuple(removed))


def save_raw(path: Path, text: str) -> None:
    atomic_write(path, text.replace("\r\n", "\n"))


_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")


def relink(text: str, is_old, new: str) -> tuple[str, int]:
    """Point every `[[link]]` (or `![[embed]]`) whose target `is_old` at `new`, keeping
    any `#heading` and `|alias`. → (text, links changed)."""
    n = 0

    def sub(m):
        nonlocal n
        inner = m.group(1)
        if not is_old(link_target(inner)):
            return m.group(0)
        n += 1
        cut = min((i for i in (inner.find("#"), inner.find("|")) if i >= 0), default=len(inner))
        return f"[[{new}{inner[cut:]}]]"

    return _WIKILINK.sub(sub, text), n


def rename(root: Path, path: str, new: str, is_old) -> tuple[str, int, int]:
    """Rename the entity file at `path` to `new` and rewrite every link to it in every
    file. → (new path, files changed, links changed). Bare mentions are left alone."""
    new = safe_name(new)
    src = under(root, path)
    dst = src.with_name(f"{new}.md")
    if dst.exists() and not dst.samefile(src):
        raise WriteError(f"{dst.relative_to(root).as_posix()} already exists")
    files = links = 0
    for p in iter_markdown(root):
        text = p.read_text(encoding="utf-8-sig")
        out, n = relink(text, is_old, new)
        if n:
            atomic_write(p, out)
            files += 1
            links += n
    if dst != src:
        src.rename(dst)   # also a case-only rename on Windows
    return dst.relative_to(root).as_posix(), files, links


def trash(root: Path, path: str) -> str:
    """Move a file into `.trash/` under the root, keeping its folders (dot-folders aren't
    indexed). → its new root-relative path."""
    src = under(root, path)
    dst = root / ".trash" / path
    n = 2
    while dst.exists():
        dst = root / ".trash" / Path(path).with_name(f"{Path(path).stem} {n}{Path(path).suffix}")
        n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return dst.relative_to(root).as_posix()


def save_upload(root: Path, filename: str, data: bytes, now: datetime | None = None) -> str:
    """Save an image to attachments/; → the file name to embed as ![[name]]."""
    now = now or datetime.now()
    ext = Path(filename or "").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"):
        raise WriteError(f"not an image: {filename}")
    stem = Path(filename).stem if filename and Path(filename).stem not in ("image", "blob") else \
        f"Pasted image {now:%Y%m%d%H%M%S}"
    stem = _BAD_NAME.sub("-", stem).strip() or "image"
    folder = root / "attachments"
    folder.mkdir(exist_ok=True)
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem} {n}{ext}"
        n += 1
    path.write_bytes(data)
    return path.name
