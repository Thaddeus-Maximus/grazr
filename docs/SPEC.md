# grazr — specification

Recordkeeping for a grazing operation: paddocks, animals, herds, and where the
herds have been. The source requirements and Q&A are in the Obsidian note
`farm/Pasture Management Software.md`; this document supersedes it where they
differ.

## 1. Principles

- **Files are the truth.** Every fact lives in a Markdown file a person can read
  and edit in Obsidian. The SQLite index is derived, disposable, and rebuilt from
  the files at any time. If the two disagree, the files win.
- **General purpose until structure demands otherwise.** There are no fixed
  categories for vet care, feeding, conditions, lifecycle stages or paddock
  types. A plaintext history plus free tags covers all of them. grazr gives a
  field or category special meaning only for a hard reason, which is nearly
  always structural: computing membership, occupancy, lineage or geometry
  requires it. Such fields are listed in §4.0, and that list should stay short.
  Every other frontmatter field is free. It is stored, displayed, searchable and
  filterable, and is never validated against a list.
- **Free prose first.** Notes are written the way they are written today. The
  only required structure is **wikilinks** to name things and **arrows** to move
  them (§5). Everything else is prose, optionally with `#tags`.
- **`cd` is the interface.** grazr is installed once and run against the
  directory you're standing in, like slimpdm:
  `cd D:/farm/docs/grazr && grazr server`. That directory is the **data root**.
- **Warn, don't refuse.** Anything grazr can't parse becomes a warning on the
  lint page (§10). Nothing is dropped silently, and a bad line never blocks the rest.
- **Git is history.** grazr never commits. Corrections overwrite, and `git log`
  is the audit trail.
- **KISS, but use libraries for hard problems** (geometry, geodesic area, YAML
  round-tripping, date parsing).
- Built to scale to about 10 herds, 1,000 animals and 10 years of notes, and
  structured so it could be published as a FOSS project.

## 2. Locations

| What | Where |
|---|---|
| Code repo | `D:/HE/grazr` |
| Install | `pip install -e D:/HE/grazr`, which puts `grazr` on PATH |
| Data root | `D:/farm/docs/grazr`, inside the Obsidian vault through the `D:/obsidian/farm` junction, and inside the `D:/farm` git repo |
| Index and cache | `<root>/.grazr/`, which grazr writes with its own `.gitignore` containing `*` |

The existing `#sheep` notes are moved or copied into the data root by hand.

## 3. Data root layout

```
grazr/                      ← data root
  grazr.yaml                farm settings (§3.1) — part of the data, committed
  animals/orange-010.md     one file per animal (§4.1)
  herds/Ewe Herd.md         one file per herd (§4.2)
  paddocks/P3.md            one file per paddock / field / barn / lot (§4.3)
  notes/2026Oct12-0707.md   timestamped notes (§5); the web UI writes here
  attachments/              images uploaded through the web UI
  *.md, any subfolder       any other .md is also scanned as a note
  .grazr/                   index.sqlite, map tiles, caches (ignored)
```

Every `.md` file outside `animals/`, `herds/`, `paddocks/` and `.grazr/` is a
**note**.

### 3.1 `grazr.yaml`

This file holds farm-specific vocabulary, so grazr has nothing hard-coded about
this farm:

```yaml
units: {area: acres, length: feet, weight: lb, temperature: F}
timezone: America/Chicago
note_filename: "%Y%b%d-%H%M"        # 2026Oct12-0707 — what grazr writes
id_digits: 3                         # orange-010
rest_full_days: 60                   # map colour reaches full green here
map:
  center: [40.35, -89.13]            # Funks Grove Twp, McLean Co., IL; set by panning
  zoom: 16
tiles: {source: usgs-imagery, min_zoom: 13, max_zoom: 19}
attachment_dirs: [attachments, D:/obsidian]   # where ![[image]] embeds are looked up

series:                              # tag series = tag colour, NOT sex
  orange:
    words: [orange, o, ewe, e]       # `e` is legacy (e010), kept for migration
    emoji: [🟠, 🔶, 🔸, 🟧]
    defaults: {species: sheep, sex: ewe}
  blue:
    words: [blue, b, wether, w]
    emoji: [🔵, 🔷, 🔹, 🟦]
    defaults: {species: sheep, sex: wether}
  green:
    words: [green, g, ram]
    emoji: [🟢, 🟩]
    defaults: {species: sheep, sex: ram}
```

New species and new tag schemes are added by adding a series here (Q98).

## 4. Entities

An entity's **ID is its filename stem**. IDs are unique across all three
folders, so a herd and a paddock can't share a name. A clash is a lint error,
and both files are indexed under their paths until it is fixed.

Frontmatter is YAML, edited by Obsidian's Properties panel or by the web UI.
Links in frontmatter are quoted wikilinks (`dam: "[[orange-117]]"`). Every
field is optional. The body of an entity file is undated prose: a description,
shown at the top of its page.

### 4.0 Structural fields (the whole list)

These are the only fields grazr interprets. Each one is here because something
derived depends on it.

| Entity | Field | Why it is structural |
|---|---|---|
| animal | `dam`, `sire` | lineage, offspring, birth type, lamb-follows-dam |
| animal | `born` | lamb membership start, birth type |
| animal | `left` | ends membership; hides the animal by default. A date, or `unknown` when it's gone but the date isn't recorded. Usually derived from an exit statement (§5.3.1) instead |
| animal | `birth_type` | override for a derived value |
| herd | `anonymous` | a herd with no individual animals, only a head count (§5.3) |
| paddock | `perimeter` | geometry, area, rest days |
| paddock | `parent` | containment; parents draw as outlines (§6.3) |

Everything else is free: `species`, `sex`, `breed`, `name`, `color`, `coat`,
`source`, `status`, `cull`, `forage`, `kind`, `water`, or anything you invent
later. Free fields appear on the entity's page, are searchable, and can filter
any list (`/animals?cull=*`, `/paddocks?kind=barn`, §8.1). The web UI's
property form suggests field names already used on other entities of the same
type, rather than using a fixed schema.

### 4.0.1 Entity types

`animals`, `herds` and `paddocks` are built in only because move semantics
(§5.3) need them. Any other folder can be declared an entity type in
`grazr.yaml`:

```yaml
types:
  equipment: {folder: equipment}
  water:     {folder: water}       # tanks, hydrants
```

Each declared type gets its own pages, lists, wikilink resolution, mentions and
timelines. Nothing else about it is interpreted. This is how water points,
fences, gates, equipment or people (Q88) get added without changing any code.

### 4.1 Animals — `animals/<id>.md`

**IDs.** A tagged animal is `<series>-<number>`, zero-padded to `id_digits`:
`orange-010`, `blue-010`. These are two different animals (Q95). Numbers are
never reused within a series. An untagged animal is named, e.g. `animals/Ram.md`
(Q96). An animal that isn't owned, such as a borrowed ram, is a normal animal
file; noting that it is borrowed is free text.

**Accepted spellings.** Any of these, case-insensitive, resolve to `orange-010`:
`[[orange-010]]`, `[[orange 010]]`, `[[o 010]]`, `[[o10]]`, `[[ewe 010]]`,
`[[e010]]`, `[[🟠010]]`, `[[🔶 10]]`.
The rule is: series word or emoji, then an optional space, `-`, `_` or `#`, then
digits, with leading zeros ignored. Emoji variation selectors are stripped
before matching.

**Shorthand is fixed, not aliased.** Obsidian only follows the canonical form,
so a shorthand link like `[[o 010]]` is an `shorthand-link` lint warning with a
one-click fix, and `grazr fix` rewrites every one to `[[orange-010]]`. Only
text inside `[[…]]` is rewritten; bare text is never touched. grazr writes no
`aliases:` (D-14).

```yaml
---
born: 2025-04-16         # structural
dam: "[[orange-117]]"    # structural
sire: "[[Ram]]"          # structural
left:                    # structural: date the animal left the farm
birth_type:              # structural override; normally derived (§6.4)
# everything below is free — examples only
species: sheep
sex: ewe
name: Bad Momma
color: brown
coat: really wooly, dark spots around nose
source: born here
status: slaughtered
cull: photosensitivity
---
Free description.
```

Animals with a `left` date in the past are hidden from lists and the map by
default. A toggle shows them. *Why* an animal left (sold, died, slaughtered) is
free text, in `status` or in a note.

### 4.2 Herds — `herds/<name>.md`

A herd is a set of animals that are physically together. An animal is in
**exactly one herd at a time** (Q34). Membership is never listed in the herd
file. It is derived from moves (§5.3).

```yaml
---
species: sheep
anonymous: false      # true → no individual animals, just a head count (hogs)
---
```

Herds are expected to be created and retired often (Q35). There is no special
split or merge operation. A split is a new herd plus moving some animals into
it. A merge is `[[Herd A]] -> [[Herd B]]` (§5.3). A herd is retired by moving
it to `[[none]]`. A herd with no members (or a head count of 0) and no open
occupancy is **idle**: hidden from lists by default, and its name can be reused
freely (D-20).

### 4.3 Paddocks — `paddocks/<name>.md`

Everything with a perimeter is a paddock: strips, fields, the barn, the lot,
sacrifice areas, jugs.

```yaml
---
parent: "[[South Field]]"   # structural; optional
perimeter: …                # structural (below)
# free — examples only
kind: barn
forage: orchardgrass, white clover, chicory
soil:
water: tank by gate
shade: none
fence: ayrmesh netting
slope:
drainage: wet in spring
perimeter:           # [lat, lon] pairs, outer ring, drawn in the web UI
  - [40.351234, -89.131234]
  - [40.351300, -89.130100]
  - [40.350400, -89.130050]
---
Prose about the paddock.
```

- **A paddock is one exact polygon.** Temporary fence polygons get reused by
  picking the same paddock again (Q38, Q39). If the shape changes, it is a new
  paddock (Q100). The web UI's "edit shape" on a paddock that has ever been
  grazed saves a new paddock (suggesting the name `P3b`). A paddock with no
  occupancies can be edited in place.
- **Parents.** A paddock that other paddocks name as `parent` is a container,
  such as a perimeter-fenced field (Q102). That role comes from the links, not
  from a `kind` value. Herds may occupy a parent directly.
- **Barn, lot, sacrifice area and jug** are ordinary paddocks. `kind:` is a free
  label for display and filtering, with no special behaviour (Q103).
- Area is computed (geodesic acres), never stored.
- Perimeters live in frontmatter rather than separate GeoJSON files (Q60).
  `grazr geojson` exports every paddock as one FeatureCollection for GIS tools.

## 5. Notes

### 5.1 Entries

A note is split into **entries**:
- Each non-blank line is one entry, except headings, the frontmatter, and
  **tag lines** (lines made up only of `#tags`). Consecutive lines are separate
  entries even with no blank line between them (D-1).
- A list item is one entry. Nested child items are their own entries, but they
  **inherit** their parent's date and mentions. So under "- [[Ewe Herd]] fed:",
  the child "- 20 lb soaked corn" is attached to Ewe Herd too.
- Tags on a tag line apply to every entry in the note.

Each entry records its file, line number, timestamp, text, tags, mentions,
embedded images, and the move (if any).

### 5.2 Timestamps

An entry's time comes from the first of these that applies:

1. **`@<when>`** at the start of the entry, e.g. `@12Oct2026-0707`,
   `@2026-10-12 7:07`, `@yesterday`. This applies to that entry only.
2. **`Date: <when>`** on its own line. This applies to all following entries
   until the next `Date:` or date heading.
3. **A date heading** such as `# 26mar2026` or `### 16MAR2026`. This applies to
   all following entries. It means old logs like `Sheep Log 2026.md` work without
   being rewritten.
4. **The filename**, in either form: `YYYYMMMDD-HHmm` (e.g. `2026Oct12-0707`) or
   the legacy `DDMonYYYY-HHMM` (e.g. `11Oct2026-1332`). Case doesn't matter, and
   the time part is optional.
5. Otherwise the entry is **undated**. It is still searchable, but it is missing
   from timelines and moves, and it gets a lint warning.

`<when>` accepts any reasonable format. The parser tries the farm formats first,
then `dateutil`. Relative words (`yesterday`, `last night`) are relative to the
note's own timestamp. A time with no date (`@0815`) takes the date that rule 2,
3 or 4 would give (D-7). If there is a date but no time, grazr uses the time of day
of the note's own timestamp when it's the same day, and otherwise 12:00. Either
way the time is flagged as approximate. Times are local wall-clock time in
`timezone`.

### 5.3 Moves

A move is the only structured statement in a note. It is an entry containing
**wikilinks, an arrow, then more wikilinks**:

```
[[Ewe Herd]] -> [[P3]]
@12Oct2026-0707 [[Ewe Herd]] +> [[P3b]] front fence moved, back fence left open
[[orange-010]], [[orange-020]], [[blue-007]] -> [[Hospital]]
[[Feeders]] -> [[Ewe Herd]]
```

The arrow may also be written `→` (same as `->`). Links on either side are
separated by commas, "and", or whitespace. Prose may come before the first link
or after the last one, and becomes the entry's text. The right-hand side ends at
the first token that isn't a link or a separator, so in
`[[Ewe Herd]] -> [[P3]] because [[orange-010]] limps`, orange-010 is an ordinary
mention (D-5).
Whether an entry is a move depends only on the arrow, so "thinking about moving
Ewe Herd to P3" is not a move unless it contains a link, an arrow and a link.

What a move means depends on what is on each side:

| Left | Arrow | Right | Meaning at time *t* |
|---|---|---|---|
| herd(s) | `->` | paddock(s) | **Move.** The herd's open occupancies not listed on the right are closed. Listed paddocks that are already open continue. Listed paddocks that aren't open are opened. |
| herd(s) | `+>` | paddock(s) | **Extend.** The listed paddocks are opened and nothing is closed. Use this for moving the front fence with back-grazing allowed (Q99). |
| animal(s) | `->` | herd | **Membership.** Each animal leaves its current herd and joins this one. |
| herd | `->` | herd | **Merge.** All current members, and the head count for anonymous herds, move to the right-hand herd. The left herd's occupancies close and it is empty from then on. |
| herd(s) | `->` | `[[none]]` | **Retire.** All the herd's open occupancies close. `none` is reserved and needs no file. |

Any other combination, such as an animal to a paddock, is a lint warning and
has no effect.

**Head counts** for anonymous herds are dated statements too (D-9):

```
[[Hogs]] = 12                 set the count (start, recount)
[[Hogs]] -1 died overnight    change it by a number (+3, -1)
```

A merge adds the left herd's count to the right herd's. A count that goes
negative is a lint warning.

### 5.3.1 Exits

How an animal left is a dated statement too: one or more animal links, then an
exit word, then optionally a weight and a cause.

```
@31May2026 [[blue-003]] slaughtered 69 lb hanging
[[orange-002]] died of bloat
[[blue-012]] died, crushed by ewe
[[blue-007]], [[blue-008]] sold 95 lb live
```

- Exit words are set in `grazr.yaml` as `exit_words` (default: died, dead,
  slaughtered, butchered, processed, sold, culled).
- The weight is a number, `lb` or `kg`, and an optional basis: live, hanging,
  hot, carcass or dressed.
- The cause is the text after `of`, `from`, a comma, a colon or a dash, up to
  the end of the sentence. `found [[x]] dead in [[Pitts]]` has no cause.
- The statement's time ends the animal's membership and hides it, like `left`.
  If both an exit and a `left:` date exist, the earlier wins; if they differ by
  more than a day, that's a `left-conflict` warning. A second exit for the same
  animal is an `exit-twice` warning. To put one ewe in a jug, move her into a herd such as `[[Hospital]]`
and move that herd to the jug. A herd may occupy several paddocks at once, and
several herds may occupy one paddock (Q42).

Because `->` closes only paddocks not listed on the right, shifting a strip
where the old strip is released is
`[[Ewe Herd]] -> [[P3c]]`. Shifting it while keeping access to P3b is
`[[Ewe Herd]] -> [[P3b]], [[P3c]]` or `+> [[P3c]]`.

### 5.4 Mentions, tags, images

- **Mentions** are `[[wikilinks]]` (Q91, Q92). Each link is resolved by exact
  entity ID first, then by the animal-ID rule, case- and
  whitespace-insensitive (D-2). An entity whose ID also matches the animal-ID
  rule (a paddock named `B2`) is an `id-collision` warning. `|alias` and
  `#heading` parts are ignored. A link to another note, like
  `[[Walnut for Dewormer]]`, is kept as a note link. A link that resolves to
  nothing is a lint warning.
- **Unlinked mentions**: bare text that looks like a tagged animal ID (`e010`,
  `ewe 004`) is **not** attached to the animal. It shows on the lint page with a
  one-click "link it" fix that rewrites the text as `[[orange-010]]`. A bare
  number like `#117` is never an animal (D-3).
- **Tags** are `#word` tokens (not headings) containing at least one letter, as
  in Obsidian, so `#117` is not a tag. Nothing is predefined (Q48). The web
  editor autosuggests tags already in use.
- **Images** are `![[Pasted image 2026….png]]` or `![](path)`. They are
  resolved the way Obsidian resolves them: by filename, searching
  `attachment_dirs`, then the data root. A missing image is a lint warning.

An entry with no mentions appears in the feed and in search, and gets an
`unattached` lint note (Q109).

## 6. Derived facts

All of these are computed in the index on each rebuild. None is stored in the
files.

### 6.1 Membership

The membership intervals `(animal, herd, start, end)` come from membership and
merge moves in time order. An animal's membership ends at `left`. A move that
names an animal after its `left` date gets a lint warning. Two statements at
exactly the same time are applied in order of file path (case-insensitive),
then line number (D-11).

**Lambs** (Q36): an animal with `dam` and `born` and no membership move yet is
a member of whichever herd its dam is in, from `born` until its own first
explicit move, following the dam through her moves (D-12). These intervals are
marked *inherited*.

### 6.2 Occupancy (grazing periods)

The occupancy intervals `(herd, paddock, start, end|open)` come from herd→paddock
moves, merges and moves to `[[none]]`. These intervals are the grazing periods.
A herd that is empty but still has an open occupancy gets an `empty-occupying`
warning (D-8).

### 6.2.1 Head counts

Head-count intervals `(herd, count, start, end|open)` for anonymous herds come
from `=`/`+N`/`-N` statements and merges, in time order.

### 6.3 Rest days and overlap

Temp-fence polygons overlap (Q101), so rest is computed **spatially**:

1. All paddock polygons are cut
   into disjoint **faces** where they overlap, using shapely's union and
   polygonize.
2. Each face's last-grazed time is the latest occupancy end among all paddocks
   that contain it, or "occupied now" if any of those occupancies is still open.
3. A paddock's **rest days** are shown two ways: *exact* (its own occupancies
   only) and *effective* (the minimum across its faces, so the most recently
   grazed part wins; this accounts for overlapping polygons that were grazed
   since) (D-10).

A parent paddock is drawn as an outline and is excluded from step 1, so its
children's faces fill it. The part of a parent that no child covers becomes a
face of its own (D-21). Grazing a parent directly counts for every face inside
it. This is the MVP; expect to adjust it after use.

### 6.4 Lineage and lambing

- **Pedigree**: dam and sire, followed up to 4 generations.
- **Offspring**: every animal whose `dam` or `sire` is this animal.
- **Birth type** (Q110): the number of animals with the same dam whose `born`
  dates are within 2 days of each other (1 is single, 2 twin, 3 triplet), unless
  `birth_type` is set.
- **Lambing summary** per dam per year (Q111): lambs born, the dates, sex and
  birth type, and which lambs are still active.

### 6.5 Event visibility

An entry appears on the timeline of:
- every entity it mentions;
- every animal that was a member of a mentioned herd at the entry's time
  (Q49, Q50), shown as "via Ewe Herd";
- a paddock's parent field, rolled up from the paddock and marked as such.

## 7. Index

The index is SQLite at `<root>/.grazr/index.sqlite`, rebuilt in full by
`grazr index`. The server rebuilds incrementally: it polls file modification
times every couple of seconds, re-parses any changed file, then recomputes §6,
which is cheap at this scale.

Tables:
- `files`
- `entities`
- `entries`
- `mentions`
- `tags`
- `images`
- `moves`
- `memberships`
- `occupancies`
- `faces`
- `warnings`
- `entries_fts`: an FTS5 free-text search over entries and entity bodies (Q71)

The whole schema is internal. If it changes, grazr deletes the index and
rebuilds it.

## 8. Web UI

`grazr server` starts the web server at `http://127.0.0.1:8714`. It uses the
standard-library HTTP server, as slimpdm does, with Jinja2 templates. It
listens on 127.0.0.1 only and has no login, because it is a single-seat tool on
the desktop. `--host` exists for later use. Every page works without
JavaScript except the map and the editors. Leaflet and its drawing plugin are
bundled with grazr (no CDN), so it works offline.

### 8.1 Pages

| URL | Content |
|---|---|
| `/` | **Home**: the map (§8.3), with herd labels on the paddocks each herd occupies, and the recent-entries feed beside it (Q65). |
| `/a/<id>` | **Animal**: properties, status, current herd and paddock, a 4-generation pedigree, offspring and lambing by year, herd history, timeline (direct mentions, "via herd" entries, and moves), and photos. |
| `/h/<id>` | **Herd**: current members as a table with checkboxes for bulk moves, current paddock(s), occupancy and membership history, and timeline. |
| `/p/<id>` | **Paddock**: its polygon on a small map, properties, acres, rest days (exact and effective), occupancy history, child paddocks, timeline, and photos. |
| `/<type>` | A list for any entity type, filterable by **any** frontmatter field (`?cull=*` means has a value, `?kind=barn` means equals), by series, and by left/current. Columns can be chosen. A filter can be bookmarked by its URL. |
| `/t/<tag>` | Every entry with that tag, plus the entities those entries mention. This covers the cull list without a dedicated page: `/animals?cull=*` gives the official list, and `/t/cull` gives the passing remarks (Q30). |
| `/search?q=` | Free-text search across all notes and entity bodies. |
| `/n/<path>` | View a note rendered, with an "edit raw" mode. |
| `/lint` | All warnings, grouped by file, with one-click fixes where one exists. |

### 8.2 Write actions (v1, Q112)

All writes are plain file edits. Each one is written to a temporary file and
renamed into place, so a file is never half-written.

| Action | What it writes |
|---|---|
| New note | `notes/<when>.md` in the `note_filename` format, containing the tag line and the text. `when` defaults to now and can be backdated ("15 Dec 2025", "yesterday 6pm"); a date with no time adds a `Date:` line so the entries are approximate. Future times are refused. The editor autocompletes `[[` from entity IDs and `#` from existing tags. |
| Move herd | A move line (`->` or `+>`) in a new note, named by the move's time (backdating works as for a new note). The form picks a herd, picks paddocks on the map or from a list, and takes an optional time and comment. |
| Move animals | A multi-select on a herd page or in the animal list, plus a destination herd. It writes one membership line. |
| New animal / herd | A new entity file from series defaults. |
| Edit properties | A frontmatter form. It reads the file again at save time and rewrites only the frontmatter, so body edits made in Obsidian survive. |
| Draw / edit paddock | Draw a polygon and set its name and properties, which writes a paddock file. Edit shape follows §4.3. |
| Attach photo | Upload or paste into the note editor. The image is saved to `attachments/` and `![[name]]` is inserted. |
| Edit any file | A raw text editor. Last save wins between grazr and Obsidian (Q106). |

The web UI writes the same syntax a person would type, so a note written in
grazr and one written in Obsidian are indistinguishable (Q89 option c).
Frontmatter is round-tripped with `ruamel.yaml`, so comments and key order are
preserved.

### 8.3 Map

- Offline base imagery, downloaded once with `grazr tiles` (Q79). The default
  source is **USGS imagery** (public domain), which is safe to cache and to ship
  in a FOSS project. The USGS tile cache stops at zoom 16 (about 2.4 m/pixel), so
  above that grazr renders each tile from the USGS NAIP image service
  (0.6–1 m). NAIP is slow, about 1–2 s per tile, so `grazr tiles` is a one-time
  wait. The server serves cached tiles and fetches and caches missing ones when
  online. A different XYZ URL can be set in `tiles:`.
- Faces are coloured by days since last grazed: **red** for grazed just now,
  shading to **green** at `rest_full_days` or more, and grey for never grazed
  (Q66). Occupied faces have a heavy outline and the herd's name.
- Fields are drawn as outlines. Clicking a polygon opens the paddock.
- A legend and a "days rested as of" date picker let you view the map as it was
  on a past date.

## 9. CLI

| Command | Does |
|---|---|
| `grazr server [--port] [--no-tiles]` | Indexes, prints the lint summary, downloads any missing tiles in the background (again whenever paddocks change), and runs the web UI (§8). |
| `grazr index` | Rebuilds the index and prints a summary of warnings. |
| `grazr lint` | Prints warnings as `file:line code message`. |
| `grazr fix [--apply]` | Rewrites shorthand links (`[[o 010]]`) to canonical IDs. Dry-run by default: prints a diff. |
| `grazr tiles [--zoom 13-19]` | Downloads missing imagery covering the paddocks plus a margin, or the map view if there are no paddocks yet. |
| `grazr geojson [-o file]` | Exports all paddocks as one GeoJSON FeatureCollection. |
| `grazr migrate [paths] [--apply]` | One-time rewrite of old ID spellings to wikilinks (§11). Dry-run by default: prints a diff. |

The server and the CLI both call one shared `api` module, as in slimpdm.

## 10. Lint warnings

| Code | Meaning |
|---|---|
| `unresolved-link` | A link resolves to no entity and no file. |
| `unlinked-mention` | Bare text looks like an animal ID. |
| `id-collision` | Two entity files share an ID, or two tag spellings normalize to the same ID. |
| `undated` | No timestamp source applies (§5.2). |
| `bad-date` | An `@` or `Date:` value that can't be parsed. |
| `unattached` | The entry mentions nothing (informational). |
| `move-types` | An arrow with an invalid combination of left and right types. |
| `move-after-left` | A move involves an animal after its `left` date. |
| `move-unknown` | A move names something that doesn't resolve. |
| `shorthand-link` | A link uses a shorthand animal spelling (`[[o 010]]`); fixable. |
| `empty-occupying` | A herd has no members but still occupies a paddock. |
| `negative-head` | A head count goes below zero. |
| `left-conflict` | An animal's `left:` date and its exit statement disagree. |
| `exit-twice` | A second exit statement for the same animal. |
| `left-unknown` | `left: unknown`: gone, but the date isn't recorded (informational). |
| `bad-perimeter` | The polygon has fewer than 3 points, crosses itself, or has bad coordinates. |
| `missing-image` | An embedded image wasn't found. |
| `bad-frontmatter` | The YAML doesn't parse, or a **structural** field has the wrong type (e.g. `born` isn't a date). Free fields are never checked. |

## 11. One-time migration

1. **Animal files are created by hand** from `Sheep Database.ods`, along with
   the starting herd memberships. There is no importer (D-16, D-R3).
2. **`grazr migrate`** over the old notes after they have been moved in. It
   rewrites `e010`/`E020` → `[[orange-010]]`, `w005` → `[[blue-005]]`,
   `ewe 004` → `[[orange-004]]` and `007 wether` → `[[blue-007]]`. Bare numbers
   like `#117` are left alone (D-R4). Anything else is left alone and reported.
   The output is a diff to review, then `--apply`, then a commit by hand (Q19).
3. Herd membership and occupancy history before migration exist only as prose
   ("moved all sheep south of pasture behind burn pile"). Arrows can be added to
   old notes by hand where the history matters. grazr does not guess.

## 12. Code layout

```
D:/HE/grazr/
  pyproject.toml        Python 3.11+; deps: ruamel.yaml, python-dateutil, shapely, pyproj, jinja2
  README.md
  docs/SPEC.md          this file
  docs/DECISIONS.md     why, as decisions get made
  grazr/
    cli.py  api.py  server.py
    paths.py            data root = cwd; .grazr/ setup
    config.py           grazr.yaml, series
    ids.py              link → entity resolution, animal-ID normalization
    dates.py            filename / @ / Date: / heading parsing
    frontmatter.py      ruamel round-trip read/write, atomic writes
    parse.py            note → entries, mentions, tags, images, moves
    index.py            SQLite build, incremental update, FTS
    derive.py           memberships, occupancies, lineage, birth type
    geo.py              faces, rest days, acres (pyproj.Geod), geojson
    tiles.py
    lint.py
    migrate.py          migrate and fix (link rewriting)
    templates/  static/ (bundled Leaflet + Leaflet-Geoman)
  tests/                parser, dates, ids, move semantics, faces — fixture data roots
```

## 13. Out of scope for v1

These were each answered "no" or "not yet":
- alerts and reminders
- reports
- printouts
- CSV, PDF or ODS export (GeoJSON export is in)
- feed and finance tracking
- tasks and follow-ups
- condition tracking with open/closed state
- grazing plans
- device integrations
- voice entry
- regulatory records
- multiple users and authentication
- syncing (handled by git and LiveSync)
- the public site
- an Obsidian plugin
- physical tag tracking (scrapie/EID numbers, lost tags)
- forage measurements as structured data (free prose and photos work today)

## 14. Open questions (decide when they come up)

1. *(Resolved: head counts are dated statements, §5.3.)*
2. **Lifecycle events** (Q28). Exits (death, slaughter, sale) are statements (§5.3.1). Add note-level
   syntax for birth, weaning or castration only if something derived needs it.
3. **Breeding records** (Q32). Ram-in and ram-out could just be moves of the ram
   into and out of a herd. If so, expected lambing windows can be derived from
   membership with no new syntax.
4. **Prefixes for new species** go in `series:` when the animals arrive.
5. **Obsidian's Ctrl+N** creates notes at the vault root, outside the data root.
   Either move the notes in by hand, or point the unique-note plugin at
   `farm/grazr/notes`.
