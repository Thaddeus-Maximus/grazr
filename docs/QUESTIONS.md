# Open questions on SPEC.md

Answer under each **Answer:** line. "Rec" is the suggested default if you
just want to write "rec". Answers get folded into SPEC.md and DECISIONS.md.

## Parser

### Q1. Entry boundaries (§5.1)
§5.1 says both "each non-blank line is one entry" and "a paragraph that wraps
across lines (no blank line between) is one entry". Old logs are mostly one
event per line with no blank lines, so the paragraph rule would merge them.

- a) Each line is one entry (drop the paragraph rule)
- b) Each paragraph is one entry
- Rec: a

**Answer:** a

### Q2. Animal-ID rule vs entity names (§5.4)
Links resolve "by the animal-ID rule, then by entity ID", so a paddock named
`B2`, `W1` or `G3` would resolve to `blue-002`, `blue-001` or `green-003`.

- a) Exact entity-ID match first, then the animal-ID rule. A name that also
  matches the animal pattern gets an `id-collision` warning.
- b) Keep the animal-ID rule first and forbid such names
- Rec: a

**Answer:** a

### Q3. `#117`: tag or animal? (§5.4)
`#117` is listed as an unlinked animal mention, but `#word` is also the tag
syntax.

- a) A tag must contain at least one letter (Obsidian's rule), so `#117` is
  never a tag
- b) Something else
- Rec: a

**Answer:** it's not a valid animal (must use the new syntax) and also (a)

### Q4. "The bare tag line" (§5.1)
This isn't defined. Is it:

- a) Any line made up only of `#tags` (and whitespace)
- b) Only the first such line in the file
- Rec: a

**Answer:** i think (a)

### Q5. Where a move's right-hand side ends (§5.3)
In `[[Ewe Herd]] -> [[P3]] because [[orange-010]] limps`, is `orange-010` a
destination?

- a) No. The right side ends at the first token that isn't a link or a
  separator (`,`, `and`, whitespace), and later links are ordinary mentions.
- b) Yes. Every link after the arrow is a destination.
- Rec: a

**Answer:** a

### Q6. Unicode arrows
Should `→` count as `->` (some editors and plugins auto-replace `->`)? Is
there an equivalent for `+>`?

**Answer:** sure

### Q7. Time with no date (§5.2)
For `@0815`, `dateutil` would fill in today's date. Should it use the note's
own date instead?

- Rec: yes, the date of the note's timestamp (or of the current `Date:` or
  heading)

**Answer:** yes

## Derived facts

### Q8. Empty herds keep occupying (§6.2)
If every member of `[[Hospital]]` moves back out, Hospital's occupancy of the
jug never closes, and the jug stays red until Hospital is dissolved.

- a) Close a non-anonymous herd's occupancies automatically when it becomes
  empty (and reopen them if animals join again?)
- b) Leave them open, and lint "empty herd still occupying"
- c) Leave as is: you dissolve or move the herd yourself
- Rec: b

**Answer:** b

### Q9. Merging anonymous herds (§5.3)
A merge moves "the head count for anonymous herds", but `head` is a single
current value in frontmatter, and derived facts are never written back.

- a) v1 ignores head counts in merges (defer to §14.1)
- b) A merge rewrites `head` in both files
- c) Something else
- Rec: a

**Answer:** we need a way to update headcount for anonymous herds and keep a history anyways because animals die.

### Q10. "Area-weighted minimum" (§6.3)
This doesn't have a clear meaning. Effective rest days should be:

- a) The minimum over the paddock's faces (the most recently grazed part
  wins)
- b) The area-weighted mean of the faces' rest days
- c) Show both
- Rec: a for the number, with the map showing the per-face detail

**Answer:** a (i suspect we need to just build something here and it will be not quite right; need MVP)

### Q11. Order of moves with the same timestamp (§6.1)
"File order" isn't defined. This matters because date headings without a
time give every entry 12:00.

- Rec: file path sorted (case-insensitive), then line number

**Answer:** ok

### Q12. Lamb inheritance when the dam is moved
"Whichever herd its dam is in": does an un-moved lamb follow its dam through
her later moves, until the lamb's own first explicit move?

- Rec: yes

**Answer:** the lamb would be part of the herd, so... yes. this shouldnt be a special case.

## Obsidian

### Q13. Aliases may not resolve links
As far as I know, Obsidian uses `aliases` for autocomplete, but a typed
`[[o 010]]` does **not** resolve to `orange-010.md`; clicking it creates
`o 010.md`. Please check this in your vault.

- If aliases don't resolve: should grazr still maintain them (for
  autocomplete only), or should `grazr migrate` / the lint fix rewrite links
  to `[[orange-010|o 010]]`?

**Answer:** would be sweet if grazr just fixed the links; so you can type `o 010` in the field and it gets changes to `orange-010`

### Q14. Which aliases get generated
Every combination of word, emoji, separator and zero-padding gives dozens of
aliases per animal. Which set should be written?

- Rec: `<word> <NNN>` for each series word, plus `<emoji><NNN>` for the first
  emoji only, e.g. `[orange 010, o 010, ewe 010, e010, 🟠010]`

**Answer:** `orange-010`

## Build and implementation

### Q15. Python version
Which minimum Python version? (3.13 removed `cgi`, so photo uploads need
hand-written multipart parsing or an extra dependency.)

- Rec: 3.11+, with hand-written multipart parsing (it's small)

**Answer:** ok

### Q16. Starting herd memberships
After the import, no animal is in any herd. Should `import-ods` also write
starting membership lines, e.g. `[[orange-001]], … -> [[Ewe Herd]]`?

- If yes: which column or rule decides the herd, and what date should the
  lines carry?

**Answer:** ill make this manually

### Q17. ODS column mapping (§11)
Please add the column headers of `Sheep Database.ods` (or a sanitized copy
in `tests/fixtures/`), and say how each one maps.

**Answer:** see Q16

### Q18. Appearance column (§11)
"Written to `color`/`coat`, or the body": which one?

- a) The whole text to `coat`
- b) The body
- c) Split by a rule (what rule?)

**Answer:** see Q16

### Q19. Fixture data root
OK to build a small synthetic data root for tests (a few animals, two
overlapping paddocks, a sample log)? Or should I copy some real notes from
the farm?

**Answer:** yes make some stuff up

## Smaller points

### Q20. Reusing dissolved herd names
Can a herd name such as `Hospital` be reused after it is dissolved (by
clearing `dissolved`)? Or should `Hospital` just never be dissolved?

**Answer:** idk what dissolution tells us

### Q21. Parent area not covered by children (§6.3)
A parent is excluded from faces, so any part of a field that no child
paddock covers disappears from the rest map. Should that remainder become a
face of its own?

- Rec: yes

**Answer:** build and and we'll see how i like it
---

# Round 2

## How I read your answers (correct me if any is wrong)

- **Q3:** `#117` is never a tag and never a mention.
- **Q6:** `→` means `->`. `+>` has no Unicode form.
- **Q12:** Lambs follow their dam's herd until their own first explicit move.
  It's one rule with no special cases.
- **Q14:** No aliases. The `aliases` field and the `grazr aliases` command are
  removed from the spec, and IDs are always written in the canonical form
  (`orange-010`).
- **Q16:** You'll write the starting memberships by hand. Nothing generates
  them.
- **Q10, Q21:** Build the simple version first, then adjust after using it.

## Remaining questions

### R1. Head-count history for anonymous herds (follows Q9)
A head count needs a dated statement in notes, like a move. Proposed syntax:

```
[[Hogs]] = 12            set the count (start, recount)
[[Hogs]] -1 died overnight   change it by a number
```

- A merge adds the left herd's count to the right herd's.
- `head:` in frontmatter is then removed (or kept only as a starting count
  with no date?).
- The herd page shows a count-over-time table, and the lint page warns if a
  count goes negative.

Is that syntax OK, or would you rather write it some other way?

**Answer:** that's fine

### R2. Fixing shorthand links (follows Q13)
"grazr just fixes the links": so `[[o 010]]` (or plain `o 010`?) gets
rewritten to `[[orange-010]]`. Two questions:

1. **When?**
   - a) Automatically, whenever grazr notices the file changed. Risk: it
     rewrites a file you may still be typing in Obsidian.
   - b) One click on the lint page, plus a `grazr fix` command that fixes
     everything at once
   - c) Automatically, but only once the file hasn't changed for a few
     minutes
   - Rec: b first; add c later if b is annoying
2. **What gets fixed?** Only shorthand inside a link (`[[o 010]]`), or also
   bare text like `o 010` / `e010` (which could be a false match)?
   - Rec: fix links automatically; bare text is offered as a lint fix only

**Answer:** 1: b 2: only shorthand

### R3. Does `import-ods` stay? (follows Q17/Q18)
You answered "see Q16" (you'll do it by hand). Does that mean:

- a) Drop `grazr import-ods`; you'll create the animal files yourself
- b) Keep `import-ods` for animal files and dated events, and do only the
  herd memberships by hand. I'd then need the ODS column headers.

**Answer:** a

### R4. Does `#117` stay in the migration? (follows Q3)
§11 has `grazr migrate` rewrite `#117` to the matching animal when exactly one
series has that number. Keep that in the one-time migration, or drop it?

**Answer:** drop it

### R5. What `dissolved` is for (follows Q20)
At the moment `dissolved: <date>` does two things: it **closes the herd's open
occupancies** (so its paddocks start resting) and **hides the herd** from
lists. Without it, the only way to take a herd off a paddock is to move it
somewhere else. Options:

- a) Keep it. Long-lived herds like `Hospital` are never dissolved, just left
  empty (they get the Q8 lint while still on a paddock). One-off herds get
  dissolved, and their names aren't reused.
- b) Replace it with a move to nowhere, e.g. `[[Hospital]] -> [[none]]`,
  which closes its occupancies. A herd with no members and no paddock is
  hidden automatically, so names can be reused freely.
- Rec: b. It's a dated event in notes like everything else, and drops a
  structural field.

**Answer:** b