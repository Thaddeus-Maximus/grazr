"""Memberships, occupancies, head counts, birth types (spec §6).

Everything here is recomputed from parsed entries and entity files; nothing is
written back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable

from .ids import Resolved
from .model import Entity
from .parse import Entry, Warning


@dataclass
class Membership:
    animal: str
    herd: str
    start: datetime
    end: datetime | None
    inherited: bool
    path: str
    line: int


@dataclass
class Occupancy:
    herd: str
    paddock: str
    start: datetime
    end: datetime | None
    path: str
    line: int


@dataclass
class HeadCount:
    herd: str
    count: int
    start: datetime
    end: datetime | None
    path: str
    line: int


@dataclass
class MoveRec:
    """A resolved move statement, for timelines."""
    path: str
    line: int
    dt: datetime
    kind: str            # "move" | "extend" | "membership" | "merge" | "retire" | "head"
    left: list[str]
    right: list[str]


@dataclass
class ExitRec:
    animal: str
    dt: datetime
    kind: str
    weight: float | None
    unit: str | None
    basis: str | None
    cause: str | None
    path: str
    line: int


@dataclass
class Derived:
    memberships: list[Membership] = field(default_factory=list)
    occupancies: list[Occupancy] = field(default_factory=list)
    heads: list[HeadCount] = field(default_factory=list)
    moves: list[MoveRec] = field(default_factory=list)
    birth_types: dict[str, int] = field(default_factory=dict)
    exits: list[ExitRec] = field(default_factory=list)
    left_at: dict[str, datetime] = field(default_factory=dict)   # effective departure
    warnings: list[Warning] = field(default_factory=list)


def _day(d) -> datetime:
    return datetime.combine(d, time(0, 0))


class _State:
    def __init__(self, entities: dict[str, Entity], out: Derived):
        self.ent = entities
        self.out = out
        self.cur: dict[str, tuple[str, datetime, bool, str, int]] = {}   # animal → herd…
        self.following: dict[str, str] = {}        # lamb → dam
        self.explicit: set[str] = set()            # animals with an explicit move
        self.occ: dict[str, dict[str, tuple[datetime, str, int]]] = {}
        self.head: dict[str, tuple[int, datetime, str, int]] = {}

    # membership -----------------------------------------------------------
    def members(self, herd: str) -> list[str]:
        return [a for a, v in self.cur.items() if v[0] == herd]

    def set_herd(self, animal: str, herd: str | None, t: datetime, inherited: bool,
                 path: str, line: int, _seen: set | None = None):
        seen = _seen or set()
        if animal in seen:
            return
        seen.add(animal)
        old = self.cur.pop(animal, None)
        if old is not None:
            if old[0] == herd:
                self.cur[animal] = old
                herd = None  # unchanged; still propagate to followers below
            elif old[1] != t:   # a same-instant hop (followed dam, then moved) isn't history
                self.out.memberships.append(
                    Membership(animal, old[0], old[1], t, old[2], old[3], old[4]))
        if herd is not None:
            self.cur[animal] = (herd, t, inherited, path, line)
        new = self.cur.get(animal)
        for lamb, dam in list(self.following.items()):
            if dam == animal:
                self.set_herd(lamb, new[0] if new else None, t, True, path, line, seen)
        if old is not None and (new is None or new[0] != old[0]):
            self.emptied(old[0], t)

    def emptied(self, herd: str, t: datetime):
        """A herd whose last animal just left comes off its paddocks (D-8)."""
        ent = self.ent.get(herd)
        if ent is not None and not ent.anonymous and not self.members(herd):
            for pad in list(self.occ.get(herd, {})):
                self.close_occ(herd, pad, t)

    # occupancy ------------------------------------------------------------
    def close_occ(self, herd: str, paddock: str, t: datetime):
        start, path, line = self.occ[herd].pop(paddock)
        self.out.occupancies.append(Occupancy(herd, paddock, start, t, path, line))

    def open_occ(self, herd: str, paddock: str, t: datetime, path: str, line: int):
        self.occ.setdefault(herd, {})
        if paddock not in self.occ[herd]:
            self.occ[herd][paddock] = (t, path, line)

    # head counts ----------------------------------------------------------
    def set_head(self, herd: str, n: int, t: datetime, path: str, line: int):
        old = self.head.get(herd)
        if old is not None:
            self.out.heads.append(HeadCount(herd, old[0], old[1], t, old[2], old[3]))
        self.head[herd] = (n, t, path, line)
        if n < 0:
            self.out.warnings.append(Warning(path, line, "negative-head",
                                             f"{herd} head count goes to {n}"))
        if n <= 0:          # none left: off its paddocks, like an emptied herd (D-8)
            for pad in list(self.occ.get(herd, {})):
                self.close_occ(herd, pad, t)


def derive(entities: dict[str, Entity], entries: list[Entry],
           resolve: Callable[[str], Resolved], now: datetime | None = None) -> Derived:
    out = Derived()
    st = _State(entities, out)
    events = []   # (dt, path key, line, seq, kind, payload)
    seq = 0

    def add(dt, path, line, kind, payload):
        nonlocal seq
        events.append((dt, path.casefold(), line, seq, kind, payload))
        seq += 1

    animals = {k: e for k, e in entities.items() if e.type == "animals"}
    _exits(animals, entries, resolve, out)
    dams: dict[str, str] = {}
    st.left_at = out.left_at
    for aid, a in animals.items():
        if a.dam:
            r = resolve(a.dam)
            if r.kind == "entity" and r.etype == "animals":
                dams[aid] = r.id
        if a.born and aid in dams:
            add(_day(a.born), "", 0, "born", aid)
        if aid in out.left_at:
            add(out.left_at[aid], "", 0, "left", aid)

    for e in entries:
        if e.stamp is None:
            continue
        if e.move is not None:
            add(e.stamp.dt, e.path, e.line, "move", e)
        elif e.head is not None:
            add(e.stamp.dt, e.path, e.line, "head", e)

    events.sort(key=lambda x: x[:4])

    def warn(e: Entry, code: str, msg: str):
        out.warnings.append(Warning(e.path, e.line, code, msg))

    for dt, _pk, _line, _seq, kind, p in events:
        if kind == "born":
            if p in st.explicit or entities[p].left_unknown:
                continue      # gone on an unknown date: don't place it in a herd
            dam = dams[p]
            st.following[p] = dam
            if dam in st.cur:
                st.set_herd(p, st.cur[dam][0], dt, True, entities[p].path, 1)
        elif kind == "left":
            st.following.pop(p, None)
            for lamb, dam in list(st.following.items()):
                if dam == p:
                    del st.following[lamb]      # lambs stay where they are
            st.set_herd(p, None, dt, False, entities[p].path, 1)
        elif kind == "head":
            h = p.head
            r = resolve(h.target)
            if r.kind != "entity" or r.etype != "herds" or not entities[r.id].anonymous:
                continue  # "+2 lambs" next to a normal herd is just prose
            cur = st.head.get(r.id, (0,))[0]
            n = h.n if h.op == "=" else cur + h.n if h.op == "+" else cur - h.n
            st.set_head(r.id, n, dt, p.path, p.line)
            out.moves.append(MoveRec(p.path, p.line, dt, "head", [r.id], [str(n)]))
        elif kind == "move":
            _apply_move(st, p, dt, resolve, warn)

    for animal, (herd, start, inh, path, line) in st.cur.items():
        out.memberships.append(Membership(animal, herd, start, None, inh, path, line))
    for herd, ps in st.occ.items():
        for paddock, (start, path, line) in ps.items():
            out.occupancies.append(Occupancy(herd, paddock, start, None, path, line))
            ent = entities.get(herd)
            empty = (st.head.get(herd, (None,))[0] == 0) if ent and ent.anonymous \
                else not st.members(herd)
            if empty:
                out.warnings.append(Warning(path, line, "empty-occupying",
                                            f"{herd} has no animals but still occupies {paddock}"))
    for herd, (n, start, path, line) in st.head.items():
        out.heads.append(HeadCount(herd, n, start, None, path, line))

    out.birth_types = birth_types(animals, dams)
    return out


def _apply_move(st: _State, e: Entry, t: datetime, resolve, warn):
    m = e.move
    left = [resolve(x) for x in m.left]
    right = [resolve(x) for x in m.right]
    unknown = [raw for raw, r in zip(m.left + m.right, left + right)
               if r.kind == "unresolved"]
    if unknown:
        warn(e, "move-unknown", "move names unknown " + ", ".join(f"[[{u}]]" for u in unknown))
        return
    lt = {r.etype if r.kind == "entity" else r.kind for r in left}
    rt = {r.etype if r.kind == "entity" else r.kind for r in right}
    L = [r.id for r in left]
    R = [r.id for r in right]
    ent = st.ent

    if lt == {"herds"} and rt == {"paddocks"}:
        for h in L:
            if m.arrow == "->":
                for pad in list(st.occ.get(h, {})):
                    if pad not in R:
                        st.close_occ(h, pad, t)
            for pad in R:
                st.open_occ(h, pad, t, e.path, e.line)
        st.out.moves.append(MoveRec(e.path, e.line, t, "move" if m.arrow == "->" else "extend", L, R))
    elif m.arrow == "+>":
        warn(e, "move-types", "`+>` only extends herds onto paddocks")
    elif lt == {"herds"} and rt == {"none"}:
        for h in L:
            for pad in list(st.occ.get(h, {})):
                st.close_occ(h, pad, t)
        st.out.moves.append(MoveRec(e.path, e.line, t, "retire", L, []))
    elif lt == {"animals"} and rt == {"herds"} and len(R) == 1:
        herd = R[0]
        if ent[herd].anonymous:
            warn(e, "move-types", f"{herd} is anonymous: it has a head count, not animals")
            return
        moved = []
        for a in L:
            left_at = st.left_at.get(a)
            if left_at is not None and t >= left_at:
                warn(e, "move-after-left", f"{a} left on {left_at:%d %b %Y}")
                continue
            st.explicit.add(a)
            st.following.pop(a, None)
            st.set_herd(a, herd, t, False, e.path, e.line)
            moved.append(a)
        st.out.moves.append(MoveRec(e.path, e.line, t, "membership", moved, R))
    elif lt == {"herds"} and rt == {"herds"} and len(R) == 1:
        dest = R[0]
        for h in L:
            if h == dest:
                continue
            for pad in list(st.occ.get(h, {})):
                st.close_occ(h, pad, t)
            for a in st.members(h):
                st.set_herd(a, dest, t, st.cur[a][2], e.path, e.line)
            if ent[h].anonymous:
                n = st.head.get(h, (0,))[0]
                if not ent[dest].anonymous:
                    warn(e, "move-types", f"merging anonymous {h} into {dest}, which counts animals")
                else:
                    st.set_head(dest, st.head.get(dest, (0,))[0] + n, t, e.path, e.line)
                st.set_head(h, 0, t, e.path, e.line)
        st.out.moves.append(MoveRec(e.path, e.line, t, "merge", L, R))
    else:
        def names(rs):
            return ", ".join(sorted(rs))
        warn(e, "move-types",
             f"can't move {names(lt)} to {names(rt)}" + ("" if len(R) == 1 else " (one destination herd)"))


def _exits(animals: dict[str, Entity], entries: list[Entry], resolve, out: Derived):
    """Exit statements and `left:` fields → each animal's departure (spec §5.3.1).

    The earlier of the two wins; if both exist and differ by more than a day,
    that's a `left-conflict`.
    """
    for e in sorted((x for x in entries if x.exit is not None and x.stamp is not None),
                    key=lambda x: (x.stamp.dt, x.path.casefold(), x.line)):
        for t in e.exit.targets:
            r = resolve(t)
            if r.kind != "entity" or r.etype != "animals":
                continue
            if any(x.animal == r.id for x in out.exits):
                out.warnings.append(Warning(e.path, e.line, "exit-twice",
                                            f"{r.id} already {next(x.kind for x in out.exits if x.animal == r.id)}"))
                continue
            out.exits.append(ExitRec(r.id, e.stamp.dt, e.exit.kind, e.exit.weight, e.exit.unit,
                                     e.exit.basis, e.exit.cause, e.path, e.line))
    by_animal = {x.animal: x for x in out.exits}
    for aid, a in animals.items():
        ex = by_animal.get(aid)
        field_dt = _day(a.left) if a.left else None
        if ex and field_dt and abs((ex.dt - field_dt).total_seconds()) > 86400:
            out.warnings.append(Warning(a.path, 1, "left-conflict",
                                        f"`left: {a.left}` but {ex.kind} on {ex.dt:%d %b %Y} ({ex.path}:{ex.line})"))
        dts = [d for d in (field_dt, ex.dt if ex else None) if d is not None]
        if dts:
            out.left_at[aid] = min(dts)


def birth_types(animals: dict[str, Entity], dams: dict[str, str]) -> dict[str, int]:
    """Siblings born within 2 days of each other share a birth (spec §6.4)."""
    by_dam: dict[str, list[tuple]] = {}
    for aid, dam in dams.items():
        if animals[aid].born:
            by_dam.setdefault(dam, []).append((animals[aid].born, aid))
    out: dict[str, int] = {}
    for kids in by_dam.values():
        kids.sort()
        group = [kids[0]]
        for k in kids[1:] + [None]:
            if k is not None and (k[0] - group[-1][0]).days <= 2:
                group.append(k)
                continue
            for _, aid in group:
                out[aid] = len(group)
            if k is not None:
                group = [k]
    for aid, a in animals.items():
        if a.birth_type is not None:
            out[aid] = a.birth_type
    return out
