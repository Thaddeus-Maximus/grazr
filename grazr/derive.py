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
class Derived:
    memberships: list[Membership] = field(default_factory=list)
    occupancies: list[Occupancy] = field(default_factory=list)
    heads: list[HeadCount] = field(default_factory=list)
    moves: list[MoveRec] = field(default_factory=list)
    birth_types: dict[str, int] = field(default_factory=dict)
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
            else:
                self.out.memberships.append(
                    Membership(animal, old[0], old[1], t, old[2], old[3], old[4]))
        if herd is not None:
            self.cur[animal] = (herd, t, inherited, path, line)
        new = self.cur.get(animal)
        for lamb, dam in list(self.following.items()):
            if dam == animal:
                self.set_herd(lamb, new[0] if new else None, t, True, path, line, seen)

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
    dams: dict[str, str] = {}
    for aid, a in animals.items():
        if a.dam:
            r = resolve(a.dam)
            if r.kind == "entity" and r.etype == "animals":
                dams[aid] = r.id
        if a.born and aid in dams:
            add(_day(a.born), "", 0, "born", aid)
        if a.left:
            add(_day(a.left), "", 0, "left", aid)

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
            if p in st.explicit:
                continue
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
            left_on = ent[a].left
            if left_on is not None and t >= _day(left_on):
                warn(e, "move-after-left", f"{a} left on {left_on}")
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
