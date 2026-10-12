"""The data root is the current directory (spec §1, §2)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

STATE_DIR = ".grazr"


def data_root(path: str | Path | None = None) -> Path:
    return Path(path or os.getcwd()).resolve()


def state_dir(root: Path) -> Path:
    d = root / STATE_DIR
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    return d


def rel(root: Path, path: Path) -> str:
    """Root-relative path with forward slashes; this is a file's key everywhere."""
    return path.relative_to(root).as_posix()


def iter_markdown(root: Path):
    """Every .md file under the root, skipping .grazr/ and other dot-folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.lower().endswith(".md"):
                yield Path(dirpath) / name


def atomic_write(path: Path, text: str) -> None:
    """Write to a temp file in the same folder, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".grazr-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
