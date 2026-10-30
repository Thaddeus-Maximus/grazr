"""`grazr <command>`, run from inside the data root (spec §9)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .migrate import diff, rewrite
from .paths import data_root


def _farm(args):
    from .api import Farm
    return Farm(data_root(args.root))


def cmd_index(args):
    farm = _farm(args)
    print(farm.summary_text())
    if farm.warnings():
        print("run `grazr lint` for details")
    return 0


def cmd_lint(args):
    farm = _farm(args)
    rows = farm.warnings(include_info=args.all)
    for w in rows:
        print(f"{w['path']}:{w['line']} {w['code']} {w['message']}")
    return 1 if any(w["severity"] == "error" for w in rows) else 0


def _show_or_apply(farm, changes: dict, apply: bool, what: str):
    if not changes:
        print(f"nothing to {what}")
        return 0
    if apply:
        rewrite(farm.root, {r: new for r, (_, new) in changes.items()})
        print(f"rewrote {len(changes)} file(s)")
    else:
        for r, (old, new) in changes.items():
            sys.stdout.write(diff(r, old, new))
        print(f"\n{len(changes)} file(s) would change; rerun with --apply")
    return 0


def cmd_fix(args):
    farm = _farm(args)
    return _show_or_apply(farm, farm.fix_all(), args.apply, "fix")


def cmd_migrate(args):
    farm = _farm(args)
    changes, skipped = farm.migrate(args.paths or None)
    for r, items in skipped.items():
        for s in items:
            print(f"skipped {r} {s}", file=sys.stderr)
    return _show_or_apply(farm, changes, args.apply, "migrate")


def cmd_geojson(args):
    farm = _farm(args)
    out = farm.geojson()
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
    return 0


def cmd_export(args):
    from datetime import datetime
    from . import archive
    root = data_root(args.root)
    out = Path(args.output or f"grazr-{datetime.now():%Y-%m-%d}.zip").resolve()
    n = archive.export(root, out)
    print(f"wrote {n} files to {out}")
    return 0


def cmd_import(args):
    from . import archive
    root = data_root(args.root)
    try:
        got = archive.import_(root, args.zip, replace=args.replace)
    except archive.ArchiveError as e:
        print(f"grazr: {e}", file=sys.stderr)
        return 1
    print(f"imported {got['written']} files")
    if got["trash"]:
        print(f"moved {got['trashed']} replaced file(s) to {got['trash']}")
    return 0


def cmd_server(args):
    from .server import run
    run(data_root(args.root), host=args.host, port=args.port, fetch_tiles=not args.no_tiles)
    return 0


def cmd_tiles(args):
    from . import tiles
    farm = _farm(args)
    zoom = None
    if args.zoom:
        lo, _, hi = args.zoom.partition("-")
        zoom = (int(lo), int(hi or lo))

    def progress(done, total, failed):
        if done % 50 == 0 or done == total:
            print(f"\r{done}/{total} ({failed} failed)", end="", flush=True)

    got, failed, missing = tiles.download(
        farm.root, farm.config, farm.paddock_polygons(), zoom, args.margin,
        progress=progress, limit=None if args.yes else 20000)
    if missing and not got and not failed:
        print(f"{missing} tiles missing; that's a lot, so narrow --zoom or pass --yes")
        return 1
    print(f"\n{got} downloaded, {failed} failed" if missing else "all tiles already cached")
    return 0 if not failed else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="grazr", description="Grazing records in Markdown.")
    ap.add_argument("--version", action="version", version=f"grazr {__version__}")
    ap.add_argument("--root", help="data root (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("index", help="rebuild the index and summarize warnings").set_defaults(fn=cmd_index)
    p = sub.add_parser("lint", help="print warnings as file:line code message")
    p.add_argument("--all", action="store_true", help="include informational notes")
    p.set_defaults(fn=cmd_lint)
    p = sub.add_parser("fix", help="rewrite shorthand links like [[o 010]] to [[orange-010]]")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_fix)
    p = sub.add_parser("migrate", help="one-time: link bare animal IDs in old notes")
    p.add_argument("paths", nargs="*")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_migrate)
    p = sub.add_parser("server", help="index, then run the web UI (fetches missing imagery)")
    p.add_argument("--port", type=int, default=8714)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-tiles", action="store_true", help="don't download missing map imagery")
    p.set_defaults(fn=cmd_server)
    p = sub.add_parser("tiles", help="download map imagery for offline use")
    p.add_argument("--zoom", help="e.g. 13-19 (default from grazr.yaml)")
    p.add_argument("--margin", type=float, default=300, help="metres around the paddocks")
    p.add_argument("--yes", action="store_true", help="don't stop at 20,000 tiles")
    p.set_defaults(fn=cmd_tiles)
    p = sub.add_parser("geojson", help="export paddocks as a GeoJSON FeatureCollection")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_geojson)

    p = sub.add_parser("export", help="back up all the data to a .zip")
    p.add_argument("-o", "--output", help="default: grazr-<date>.zip")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import", help="restore a .zip made by export (replaced files go to .trash/)")
    p.add_argument("zip")
    p.add_argument("--replace", action="store_true",
                   help="also move files the zip doesn't have to .trash/, so the folder matches it")
    p.set_defaults(fn=cmd_import)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
