"""Back up the whole data folder to a .zip, and restore one.

The files are the data, so an export is every file under the root except dot-folders
(`.grazr/` holds the index and map imagery, both rebuilt; `.trash/`, `.obsidian/`, ...).
An import writes the zip's files into the root; any file it would overwrite, and with
`replace` any file the zip doesn't have, goes to `.trash/import-<time>/` first.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import CONFIG_NAME
from .paths import rel


class ArchiveError(Exception):
    pass


def data_files(root: Path):
    """Every file that belongs in a backup, as root-relative posix paths."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.startswith("."):
                yield rel(root, Path(dirpath) / name)


def export(root: Path, out) -> int:
    """Write the zip to `out` (a path or a binary file). → number of files."""
    n = 0
    skip = Path(out).resolve() if isinstance(out, (str, Path)) else None   # a zip saved in the root
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for r in data_files(root):
            if skip and (root / r).resolve() == skip:
                continue
            z.write(root / r, r)
            n += 1
    return n


def export_bytes(root: Path) -> bytes:
    buf = io.BytesIO()
    export(root, buf)
    return buf.getvalue()


def _members(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """→ {root-relative path: entry}. Refuses paths that would land outside the root or
    in a dot-folder, and unwraps a zip of the folder itself (`grazr/grazr.yaml`)."""
    files = [i for i in z.infolist() if not i.is_dir()]
    names = [i.filename.replace("\\", "/") for i in files]
    tops = {n.split("/", 1)[0] for n in names}
    strip = ""
    if len(tops) == 1 and CONFIG_NAME not in names and f"{next(iter(tops))}/{CONFIG_NAME}" in names:
        strip = tops.pop() + "/"
    out = {}
    for info, name in zip(files, names):
        name = name[len(strip):]
        parts = PurePosixPath(name).parts
        if any(x.startswith(".") and x not in (".", "..") for x in parts):
            continue   # .grazr/, .obsidian/, .DS_Store, ...: not data
        if not parts or name.startswith("/") or ":" in name or any(x in (".", "..") for x in parts):
            raise ArchiveError(f"refusing unsafe path in zip: {info.filename}")
        out[name] = info
    if not out:
        raise ArchiveError("the zip has no files")
    return out


def import_(root: Path, src, replace: bool = False, now: datetime | None = None) -> dict:
    """Unpack the zip at `src` (a path or a binary file) into the root.
    → {"written": n, "trashed": n, "trash": folder or None}."""
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise ArchiveError("not a zip file")
    with z:
        members = _members(z)
        if z.testzip() is not None:
            raise ArchiveError("the zip is damaged")
        trash = base = root / ".trash" / f"import-{(now or datetime.now()):%Y%m%d-%H%M%S}"
        n = 2
        while trash.exists():
            trash = base.with_name(f"{base.name} {n}")
            n += 1
        existing = {r.casefold(): r for r in data_files(root)}
        incoming = {r.casefold() for r in members}
        aside = [r for k, r in existing.items() if replace or k in incoming]
        for r in aside:
            dst = trash / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            (root / r).rename(dst)
        for r, info in members.items():
            dst = root / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as f, open(dst, "wb") as g:
                g.write(f.read())
            stamp = time.mktime(info.date_time + (0, 0, -1))
            os.utime(dst, (stamp, stamp))
    return {"written": len(members), "trashed": len(aside),
            "trash": rel(root, trash) if aside else None}

