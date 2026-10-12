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
    s = farm.summary()
    print(f"{s['files']} files: {s['animals']} animals, {s['herds']} herds, "
          f"{s['paddocks']} paddocks, {s['entries']} entries, {s['moves']} moves")
    counts = {}
    for w in farm.warnings():
        counts[w["code"]] = counts.get(w["code"], 0) + 1
    if counts:
        print(f"{s['warnings']} warnings: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
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
    p = sub.add_parser("geojson", help="export paddocks as a GeoJSON FeatureCollection")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_geojson)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
