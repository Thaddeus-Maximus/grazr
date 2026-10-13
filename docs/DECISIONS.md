# Decisions

Numbers match `QUESTIONS.md` (D-n ↔ Qn, D-Rn ↔ Rn). Decided 2026-10-12.

- **D-1** Each line is one entry. Consecutive lines are not merged into a paragraph, because old logs are one event per line.
- **D-2** Links resolve by exact entity ID first, then by the animal-ID rule. An entity ID that also matches the animal pattern is an `id-collision`.
- **D-3** `#117` is neither a tag (tags need a letter) nor an animal. Animals are named in the new syntax only.
- **D-4** A tag line is any line made up only of `#tags`. Its tags apply to the whole note.
- **D-5** A move's right-hand side ends at the first token that isn't a link or a separator.
- **D-6** `→` is accepted for `->`. `+>` has no Unicode form.
- **D-7** A time with no date takes the note's date.
- **D-8** ~~Empty herds keep their occupancies, with an `empty-occupying` warning.~~ Revised 2026-10-13: when a herd's last animal leaves, it comes off its paddocks at that moment and goes idle. Splitting animals out and later moving them all back one by one shouldn't leave the empty herd on a paddock.
- **D-9** Anonymous herds keep head-count history as dated statements (`[[Hogs]] = 12`, `[[Hogs]] -1`), because animals die. `head:` in frontmatter is gone.
- **D-10** Effective rest is the minimum over faces. It's an MVP, to be revisited after use.
- **D-11** Statements at the same time are ordered by path (case-insensitive), then line number.
- **D-12** Lambs follow the dam's herd until their own first move. It's the same rule as membership, not a special case.
- **D-13 / D-14** No `aliases:`. Shorthand links are fixed to canonical IDs instead, so Obsidian can follow them.
- **D-15** Python 3.11+. Multipart upload parsing is written by hand, because `cgi` is gone in 3.13.
- **D-16** Starting herd memberships are written by hand.
- **D-19** Tests use a synthetic fixture data root.
- **D-20** `dissolved` is replaced by moving a herd to the reserved `[[none]]`. Idle herds (no members, no occupancy) are hidden, and their names can be reused.
- **D-21** The part of a parent that no child covers is its own face. It's an MVP.
- **D-R2** Shorthand fixing is opt-in: a one-click lint fix or `grazr fix`. It applies only inside `[[…]]`, never to bare text.
- **D-R3** No ODS importer. Animal files are created by hand.
- **D-R4** `migrate` doesn't rewrite bare numbers like `#117`.
