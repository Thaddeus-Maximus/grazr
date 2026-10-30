# grazr

Recordkeeping for a grazing operation (paddocks, animals, herds, and where the
herds have been), kept as plain Markdown files you can edit in Obsidian. The
SQLite index is derived and disposable; the files are the truth.

## Use

```
pip install -e .
cd D:/farm/docs/grazr          # the data root
grazr server                   # http://127.0.0.1:8714
```

| Command | Does |
|---|---|
| `grazr server [--port] [--no-tiles]` | Index, print the lint summary, fetch missing imagery in the background, and run the web UI |
| `grazr index` | Rebuild the index and summarize warnings |
| `grazr lint [--all]` | Print warnings as `file:line code message` |
| `grazr fix [--apply]` | Rewrite shorthand links (`[[o 010]]` → `[[orange-010]]`) |
| `grazr migrate [paths] [--apply]` | One-time: link bare IDs like `e010` in old notes |
| `grazr tiles [--zoom 13-19]` | Download missing imagery around the paddocks (the server does this too) |
| `grazr geojson [-o file]` | Export paddocks for GIS |
| `grazr export [-o file.zip]` | Back up all the data to a .zip (also on the Backup page) |
| `grazr import file.zip [--replace]` | Restore a backup; files it replaces go to `.trash/import-<time>/` |

`fix` and `migrate` print a diff until you pass `--apply`. grazr never commits.

## Writing notes

```
#sheep
[[Ewe Herd]] -> [[P3]]                       move (leave paddocks not listed)
[[Ewe Herd]] +> [[P3b]] back fence open      extend (keep current paddocks)
[[orange-010]], [[blue-007]] -> [[Hospital]] move animals between herds
[[Hospital]] -> [[none]]                     take a herd off every paddock
[[Hogs]] = 12                                head count (anonymous herds)
[[blue-003]] slaughtered 69 lb hanging       an animal leaves (died, sold, ...)
[[orange-002]] died of bloat                 ... with a cause
@yesterday 6pm [[o 117]] limping #vet        back-dated entry
```

See `docs/SPEC.md` for the full rules and `docs/DECISIONS.md` for why.

## Develop

```
pip install -e ".[dev]"
pytest
```

Leaflet (BSD-2) and Leaflet-Geoman free (MIT) are bundled in `grazr/static/vendor`.
Default imagery is public-domain USGS: the USGSImageryOnly tile cache to zoom 16,
and USGS NAIP rendered per tile above that.
