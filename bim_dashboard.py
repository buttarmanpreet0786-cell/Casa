#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Urban Mixed-Use BIM Dashboard  -  Python edition
=================================================

A Python (Streamlit) rewrite of the single-file HTML/JavaScript dashboard.

Run it with:

    pip install streamlit plotly pandas
    streamlit run bim_dashboard.py

What is ported
--------------
* House and Building project types, Executive / Technical modes
* The requirement-driven plan generator (every area comes from the floor plans)
* Floor plans, site plan and building section drawn as SVG (blueprint / white paper)
* Doors, windows, stairs, elevators and balconies, with a per-floor element editor
* All analysis tabs: overview, floors, commercial, residential, parking & EV,
  space utilization, verification, circulation, BIM elements, schedules, report
* Auto-fit of floors and basements to the area targets
* CSV / SVG / Markdown downloads

What changed
------------
* The 3D view is a Plotly model (orbit, zoom, floor cut-away). The first-person
  WebGL "Walk inside" mode and click-on-plan placement are not ported; elements
  are placed with the "Add at centre" button and edited in the tables instead.
"""
from __future__ import annotations

import copy
import csv
import html as _html
import io
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components


# ============================================================================
#  tiny helpers (JavaScript-flavoured so the port stays close to the original)
# ============================================================================
class O(dict):
    """dict with attribute access; a missing attribute is None (like JS undefined)."""

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v


def clone(x):
    return copy.deepcopy(x)


def jr(x):
    """JavaScript Math.round (half up)."""
    return math.floor(x + 0.5)


def r2(v):
    return jr(v * 100) / 100


def _grp(s: str) -> str:
    """Indian digit grouping (12,34,567) like toLocaleString('en-IN')."""
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def fmt(n) -> str:
    n = jr(n)
    return ("-" if n < 0 else "") + _grp(str(abs(n)))


def f1(n) -> str:
    v = jr(n * 10) / 10
    s = f"{abs(v):.1f}"
    ip, fr = s.split(".")
    return ("-" if v < 0 else "") + _grp(ip) + "." + fr


def pct(a, b):
    return a / b * 100 if b > 0 else None


def fp(v) -> str:
    return "—" if v is None else f1(v) + "%"


m2 = fmt
esc = lambda s: _html.escape(str(s), quote=True)  # noqa: E731


def rng(seed):
    """mulberry32, identical sequence to the JavaScript version."""
    a = [seed & 0xFFFFFFFF]
    M32 = 0xFFFFFFFF

    def nxt():
        a[0] = (a[0] + 0x6D2B79F5) & M32
        t = a[0]
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return nxt


# ============================================================================
#  requirement vocabulary
# ============================================================================
ST = {"ok": "Satisfied", "review": "Needs Review", "missing": "Not Configured", "na": "Not required"}
MARK = {"ok": "✓", "review": "!", "missing": "✕", "na": "–"}
EMOJI = {"ok": "✅", "review": "⚠️", "missing": "❌", "na": "➖"}
ZN = {"commercial": "Commercial", "residential": "Residential", "parking": "Parking"}
COL = {
    "commercial": "#7A4DB3", "residential": "#1E8C8C", "parking": "#5C6E7E", "ev": "#2F6FE4",
    "circ": "#B9C4C9", "svc": "#8D9AA1", "ink": "#12222B", "muted": "#56666F", "line": "#CBD5D9",
}


def chip(s, t=None):
    return f"{EMOJI[s]} {t or ST[s]}"


def worst(*a):
    return "missing" if "missing" in a else "review" if "review" in a else "ok"


FUNCS = {
    "retail": O(label="Commercial: Retail", zone="commercial", h=4.5),
    "office": O(label="Commercial: Office", zone="commercial", h=3.6),
    "res": O(label="Residential", zone="residential", h=3.2),
    "park": O(label="Parking (podium)", zone="parking", h=3.0),
}
FAC = {"commercial": "Commercial spaces", "residential": "Residential units", "parking": "Cars + EV charging"}
BAY_W, BAY_D, AISLE, BASE_H = 2.5, 5, 6, 3.6
BAY_AREA = BAY_W * BAY_D
UNITS = [O(t="1BHK", a=75), O(t="2BHK", a=112), O(t="3BHK", a=165)]
SHOPS = [
    ("Anchor Retail", "Retail"), ("Restaurant", "Food & Beverage"), ("Café", "Food & Beverage"),
    ("Bank Branch", "Services"), ("Boutique", "Retail"), ("Clinic", "Services"),
    ("Retail Shop", "Retail"), ("Pharmacy", "Retail"), ("Bakery", "Food & Beverage"),
    ("Salon", "Services"), ("Electronics", "Retail"), ("Bookshop", "Retail"),
]
OCC = {"Retail": "Mercantile", "Food & Beverage": "Assembly", "Office": "Business", "Services": "Business / institutional"}
TYPES = ["Retail", "Food & Beverage", "Office", "Services", "1BHK", "2BHK", "3BHK", "Car park", "EV bay", "Circulation", "Service"]
_WORDS = ["Ground", "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth",
          "Tenth", "Eleventh", "Twelfth"]


def ord_(n):
    if n < 13:
        return _WORDS[n] + " Floor"
    if n % 10 == 1 and n % 100 != 11:
        s = "st"
    elif n % 10 == 2 and n % 100 != 12:
        s = "nd"
    elif n % 10 == 3 and n % 100 != 13:
        s = "rd"
    else:
        s = "th"
    return f"{n}{s} Floor"


BASE = O(name="Urban Mixed-Use Development", plotW=60, plotD=40, front=5.0, rear=4.0, side=3.0, base=1.5,
         maxCov=75, basements=2, commFn="retail", commSpaces=8, officeSpaces=6, mix=[20, 45, 35],
         ev=16, allocPct=78, tComm=2500, tRes=9000, tBays=110, tEv=12, floors=[], edits=O())


def _preset(**kw):
    p = clone(BASE)
    p.update(kw)
    return p


PRESETS = {
    "ref": _preset(floors=["retail"] * 2 + ["res"] * 7),
    "res": _preset(name="Residential Block", basements=1, floors=["res"] * 5, tComm=0, tRes=5000, tBays=55, tEv=6, ev=8),
    "office": _preset(name="Office Tower", basements=3, commFn="office", floors=["retail"] + ["office"] * 11,
                      tComm=12000, tRes=0, tBays=150, tEv=15, ev=15),
    "podium": _preset(name="Podium Residential", basements=0, floors=["retail", "park", "park"] + ["res"] * 6,
                      tComm=1000, tRes=7000, tBays=100, tEv=10, ev=12),
    "blank": _preset(name="My Building", basements=0, floors=["res"], tComm=0, tRes=0, tBays=0, tEv=0, ev=0),
}
PRESET_LABEL = {"ref": "Mixed-use G+8", "res": "Residential G+4", "office": "Office tower G+11",
                "podium": "Parking podium + flats", "blank": "Blank"}


# ============================================================================
#  plan generator: every area comes from this geometry
# ============================================================================
def mix_seq(w):
    tot = sum(w) or 1
    w = [x / tot for x in w]
    got = [0, 0, 0]
    seq = []
    for n in range(20):
        b, bv = 0, -1e9
        for k, x in enumerate(w):
            v = x * (n + 1) - got[k]
            if v > bv:
                bv, b = v, k
        got[b] += 1
        seq.append(b)
    return seq


def core_els(P):
    B, cw, x0 = P.B, P.cw, P.x0
    sh = min(5.5, B * 0.18)
    el = O(stairs=[], lifts=[])
    for y0 in (0, B - sh):
        el.stairs.append(O(x=r2(x0 + 0.3), y=r2(y0 + 0.3), w=r2(cw - 0.6), h=r2(sh - 0.6)))
    lw = min(2.4, (cw - 1.8) / 2)
    mid = B / 2
    for ly in (mid - lw - 0.4, mid + 0.4):
        for lx in (x0 + cw / 2 - lw - 0.3, x0 + cw / 2 + 0.3):
            el.lifts.append(O(x=r2(lx), y=r2(ly), w=r2(lw), h=r2(lw)))
    return el


def plan_floor(fn, L, B, num, mix):
    P = O(fn=fn, L=L, B=B, rooms=[], bays=[], aisles=[], rows=[])
    cw = min(9, L * 0.22)
    x0 = (L - cw) / 2
    x1 = x0 + cw
    P.core = O(x=x0, y=0, w=cw, h=B)
    P.cw, P.x0, P.x1 = cw, x0, x1
    if fn == "park":
        ys = []
        y = 0
        while y + 2 * BAY_D + AISLE <= B + 1e-6:
            ys += [y, y + BAY_D + AISLE]
            P.aisles.append(O(y=y + BAY_D, h=AISLE))
            y += 2 * BAY_D + AISLE
        if B - y >= BAY_D + AISLE:
            ys.append(y)
            P.aisles.append(O(y=y + BAY_D, h=AISLE))
            y += BAY_D + AISLE
        P.rows = ys
        for yy in ys:
            for a, b in ((0, x0), (x1, L)):
                n = int(math.floor((b - a) / BAY_W))
                st0 = a + ((b - a) - n * BAY_W) / 2
                for k in range(n):
                    P.bays.append(O(x=st0 + k * BAY_W, y=yy))
        P.usable = len(P.bays) * BAY_AREA
        P.svc = cw * B
        P.circ = L * B - P.usable - P.svc
        P.ramp = O(x=L - 12, y=P.aisles[0].y, w=12, h=AISLE) if P.aisles else None
        P.el = O(doors=[], windows=[], balconies=[])
        P.el.update(core_els(P))
        return P

    cr = 2 if fn == "res" else 3
    d = (B - cr) / 2
    P.corr = O(x=0, y=d, w=L, h=cr)
    P.d, P.cr = d, cr
    segs = [
        O(x=0, w=x0, y=0, side="N", west=1), O(x=x1, w=L - x1, y=0, side="N", east=1),
        O(x=0, w=x0, y=d + cr, side="S", west=1), O(x=x1, w=L - x1, y=d + cr, side="S", east=1),
    ]
    seq = mix_seq(mix.w)
    cnt = shop = 0
    unit = 1
    for k, sg in enumerate(segs):
        if fn == "res":
            items, cum = [], 0
            for j in range(20):
                u = UNITS[seq[(k * 3 + j) % 20]]
                wn = u.a / d
                if cum + wn > sg.w * 1.1 and items:
                    break
                items.append(O(w=wn, u=u))
                cum += wn
            f = sg.w / cum
            for it in items:
                it.w *= f
        else:
            total = int(mix.officeSpaces if fn == "office" else mix.commSpaces)
            n = max(1, total // 4 + (1 if k < total % 4 else 0))
            W = [1.3, 1, 0.8, 1.15, 0.9, 1.05]
            ws = [W[(k + j) % 6] for j in range(n)]
            sm = sum(ws)
            items = [O(w=w / sm * sg.w) for w in ws]
        x = sg.x
        for it in items:
            if fn == "res":
                name, typ, id_ = f"Unit {num}{unit:02d}", it.u.t, f"R-{num}{unit:02d}"
                unit += 1
            elif fn == "office":
                cnt += 1
                name, typ, id_ = f"Office Suite {chr(64 + ((cnt - 1) % 26) + 1)}", "Office", f"O-{num}{cnt:02d}"
            else:
                sh = SHOPS[shop % len(SHOPS)]
                shop += 1
                cnt += 1
                name, typ, id_ = sh[0], sh[1], f"C-{num}{cnt:02d}"
            P.rooms.append(O(
                id=id_, name=name, type=typ, x=x, y=sg.y, w=it.w, h=d, side=sg.side,
                west=bool(sg.west) and abs(x - sg.x) < 1e-6,
                east=bool(sg.east) and abs(x + it.w - (sg.x + sg.w)) < 1e-6,
                area=it.w * d, kind="space"))
            x += it.w
    P.usable = sum(r.area for r in P.rooms)
    P.svc = cw * B
    P.circ = L * B - P.usable - P.svc
    el = O(doors=[], windows=[], balconies=[])
    el.update(core_els(P))
    for r in P.rooms:
        north = r.side == "N"
        cy = r.y + r.h if north else r.y
        el.doors.append(O(x=r2(r.x + r.w * 0.2), y=r2(cy), len=0.9, o="h", sw=-1 if north else 1))
        wl = min(r.w * 0.6, 6)
        el.windows.append(O(x=r2(r.x + (r.w - wl) / 2), y=0 if north else B, len=r2(wl), o="h"))
        if r.west:
            hl = min(r.h * 0.5, 5)
            el.windows.append(O(x=0, y=r2(r.y + (r.h - hl) / 2), len=r2(hl), o="v"))
        if r.east:
            hl = min(r.h * 0.5, 5)
            el.windows.append(O(x=r2(L), y=r2(r.y + (r.h - hl) / 2), len=r2(hl), o="v"))
        if fn == "res" and num > 0 and r.w >= 5:
            bw = r2(min(3.5, r.w * 0.6))
            el.balconies.append(O(x=r2(r.x + (r.w - bw) / 2), y=-1.5 if north else r2(B), w=bw, h=1.5))
    if num == 0:
        el.doors.append(O(x=0, y=r2(d + (cr - 1.2) / 2), len=1.2, o="v", sw=1))
    P.el = el
    return P


def apply_edits(pl, i, id_, sig):
    pl.defEl = pl.el
    e = (i.edits or {}).get(id_)
    if e and e.sig == sig:
        pl.el = clone(e.els)
        pl.custom = True
    elif e:
        pl.staleEdit = True


# ============================================================================
#  openings checks shared by houses and buildings
# ============================================================================
def near(a, b):
    return abs(a - b) < 0.06


def has_door(r, ds):
    for x in ds:
        if x.o == "h":
            if (near(x.y, r.y) or near(x.y, r.y + r.h)) and r.x - .05 <= x.x + x.len / 2 <= r.x + r.w + .05:
                return True
        elif (near(x.x, r.x) or near(x.x, r.x + r.w)) and r.y - .05 <= x.y + x.len / 2 <= r.y + r.h + .05:
            return True
    return False


def ext_edges(r, L, B):
    return O(n=near(r.y, 0), s=near(r.y + r.h, B), w=near(r.x, 0), e=near(r.x + r.w, L))


def has_win(r, ws, L, B):
    e = ext_edges(r, L, B)
    for x in ws:
        if x.o == "h":
            if ((e.n and near(x.y, 0)) or (e.s and near(x.y, B))) and r.x - .05 <= x.x + x.len / 2 <= r.x + r.w + .05:
                return True
        elif ((e.w and near(x.x, 0)) or (e.e and near(x.x, L))) and r.y - .05 <= x.y + x.len / 2 <= r.y + r.h + .05:
            return True
    return False


def lst(a):
    return ", ".join(a[:3]) + (f" and {len(a) - 3} more" if len(a) > 3 else "")


ROOMS = {
    "living": O(l="Living room", g="pub", min=12, a=24), "dining": O(l="Dining", g="pub", min=7, a=13),
    "kitchen": O(l="Kitchen", g="pub", min=5, a=12), "pooja": O(l="Pooja room", g="pub", min=0, a=4),
    "lounge": O(l="Family lounge", g="pub", min=0, a=16), "study": O(l="Study", g="pub", min=6, a=9),
    "garage": O(l="Garage", g="pub", min=12, a=18), "master": O(l="Master bedroom", g="pri", min=12, a=18),
    "bedroom": O(l="Bedroom", g="pri", min=9.5, a=14), "guest": O(l="Guest bedroom", g="pri", min=9.5, a=13),
    "bath": O(l="Bathroom", g="pri", min=2.8, a=4.5), "toilet": O(l="Toilet", g="pri", min=1.2, a=2),
    "store": O(l="Store", g="pri", min=0, a=5), "utility": O(l="Utility", g="pri", min=0, a=4),
}
HAB = {"living", "dining", "kitchen", "study", "lounge", "master", "bedroom", "guest"}
BEDS = {"master", "bedroom", "guest"}
SMALL = {"bath", "toilet", "store", "utility", "pooja"}


def opening_checks(M):
    C, noD, noW, inner = [], [], [], []
    house = M.kind == "house"
    for l in M.levels:
        if l.zone == "parking":
            continue
        P = l.plan
        el = P.el
        for r in P.rooms:
            nm = f"{r.name} ({l.name})"
            if not has_door(r, el.doors):
                noD.append(nm)
            e = ext_edges(r, P.L, P.B)
            if e.n or e.s or e.w or e.e:
                if not has_win(r, el.windows, P.L, P.B) and r.t not in SMALL:
                    noW.append(nm)
            elif house and r.t in HAB:
                inner.append(nm)
    C.append(O(label="Doors to every room", state="review" if noD else "ok",
               note=(f"{len(noD)} room{'s have' if len(noD) > 1 else ' has'} no door: {lst(noD)}" if noD
                     else "Every room has a door on its boundary")))
    if noW:
        wn = f"{len(noW)} room{'s' if len(noW) > 1 else ''} on an outer wall without a window: {lst(noW)}"
    elif inner:
        wn = f"No outer wall for daylight: {lst(inner)}"
    else:
        wn = "Every room on an outer wall has a window"
    C.append(O(label="Windows on outer walls", state="review" if (noW or inner) else "ok", note=wn))
    if len(M.levels) > 1:
        per = [(len(l.plan.el.stairs or []), len(l.plan.el.lifts or [])) for l in M.levels]
        need = 1 if house else 2
        zero = sum(1 for p in per if p[0] == 0)
        few = sum(1 for p in per if p[0] < need)
        no_lift = sum(1 for p in per if p[1] == 0) if (not house and M.above >= 5) else 0
        stt = "ok"
        n = ("Every floor has a stair" if house else "Every level has 2 stairs") + \
            ("" if house else (" and a lift" if M.above >= 5 else ""))
        if zero:
            stt, n = "missing", f"{zero} level{'s have' if zero > 1 else ' has'} no stair"
        elif few:
            stt, n = "review", f"{few} level{'s have' if few > 1 else ' has'} fewer than {need} stairs"
        elif no_lift:
            stt, n = "review", f"{no_lift} level{'s have' if no_lift > 1 else ' has'} no lift in a building of {M.above} floors"
        C.append(O(label="Stairs and lifts", state=stt, note=n))
    return C


def wall_segs(P, outer_only=False):
    s = [O(o="h", c=0, a1=0, a2=P.L), O(o="h", c=P.B, a1=0, a2=P.L),
         O(o="v", c=0, a1=0, a2=P.B), O(o="v", c=P.L, a1=0, a2=P.B)]
    if not outer_only:
        for r in P.rooms:
            s += [O(o="h", c=r.y, a1=r.x, a2=r.x + r.w), O(o="h", c=r.y + r.h, a1=r.x, a2=r.x + r.w),
                  O(o="v", c=r.x, a1=r.y, a2=r.y + r.h), O(o="v", c=r.x + r.w, a1=r.y, a2=r.y + r.h)]
    return s


# ============================================================================
#  building model
# ============================================================================
def build(i):
    M = O(kind="building", inp=i, levels=[], spaces=[], bays=[])
    mix = O(w=list(i.mix), commSpaces=i.commSpaces, officeSpaces=i.officeSpaces)
    Lf = max(20, i.plotW - 2 * i.side)
    Bf = max(12, i.plotD - i.front - i.rear)
    Lb = max(20, i.plotW - 2 * i.base)
    Bb = max(12, i.plotD - 2 * i.base)
    M.fp = O(L=Lf, B=Bf, area=Lf * Bf)
    M.cov = pct(Lf * Bf, i.plotW * i.plotD)

    def mk(o_):
        pl = plan_floor(o_.fn, o_.L, o_.B, o_.number, mix)
        key = o_.fn
        z = FUNCS[key].zone
        sig = "|".join(str(x) for x in [key, o_.L, o_.B, ",".join(str(v) for v in i.mix), i.commSpaces, i.officeSpaces])
        apply_edits(pl, i, o_.id, sig)
        o_.update(sig=sig, plan=pl, zone=z, fnLabel=FUNCS[key].label,
                  fn="Parking + EV charging" if o_.number < 0 else FUNCS[key].label,
                  fnKey=key, fac=FAC[z], gross=o_.L * o_.B, usable=pl.usable, circ=pl.circ, svc=pl.svc)
        return o_

    for k in range(i.basements, 0, -1):
        M.levels.append(mk(O(id=f"B{k}", name="Basement" if i.basements == 1 else f"Basement {k}", fn="park",
                             L=Lb, B=Bb, number=-k, h=BASE_H, elev=-k * BASE_H)))
    e = 0
    for n, f in enumerate(i.floors):
        h = FUNCS[f].h
        M.levels.append(mk(O(id=f"L{n}", name=ord_(n), fn=f, L=Lf, B=Bf, number=n, h=h, elev=e)))
        e += h
    M.height = e

    pk = [l for l in M.levels if l.zone == "parking"]
    order = sorted([l for l in pk if l.number < 0], key=lambda l: -l.number) + \
        sorted([l for l in pk if l.number >= 0], key=lambda l: l.number)
    for l in order:
        for j, b in enumerate(l.plan.bays):
            M.bays.append(O(id=f"{l.id}-{j + 1:03d}", level=l.id, ev=False, alloc=False, b=b))
    M.nb = len(M.bays)
    M.nev = min(i.ev, M.nb)
    M.nal = jr(M.nb * i.allocPct / 100)
    for j, b in enumerate(M.bays):
        b.ev = j < M.nev
    r = rng(M.nb * 31 + 7)
    idx = list(range(M.nb))
    for j in range(M.nb - 1, 0, -1):
        k = int(math.floor(r() * (j + 1)))
        idx[j], idx[k] = idx[k], idx[j]
    for j in idx[:M.nal]:
        M.bays[j].alloc = True
    lb = {}
    for b in M.bays:
        lb.setdefault(b.level, []).append(b)
    for l in M.levels:
        if l.zone == "parking":
            bs = lb.get(l.id, [])
            ev = sum(1 for b in bs if b.ev)
            for j, pb in enumerate(l.plan.bays):
                b = bs[j] if j < len(bs) else None
                pb.ev = b.ev if b else False
                pb.al = b.alloc if b else False
            M.spaces += [
                O(id=l.id + "-CONV", name="Conventional bays", level=l.id, zone="parking", type="Car park", kind="park", area=(len(bs) - ev) * BAY_AREA),
                O(id=l.id + "-EV", name="EV charging bays", level=l.id, zone="parking", type="EV bay", kind="park", area=ev * BAY_AREA),
                O(id=l.id + "-CIR", name="Vehicle circulation", level=l.id, zone="parking", type="Circulation", kind="circ", area=l.circ),
                O(id=l.id + "-SVC", name="Lifts, stairs and plant", level=l.id, zone="parking", type="Service", kind="svc", area=l.svc)]
        else:
            for rm in l.plan.rooms:
                M.spaces.append(O(id=rm.id, name=rm.name, level=l.id, zone=l.zone, type=rm.type, kind="space", area=rm.area))
            M.spaces += [
                O(id=l.id + "-CIR", name="Common circulation", level=l.id, zone=l.zone, type="Circulation", kind="circ", area=l.circ),
                O(id=l.id + "-SVC", name="Lifts, stairs and services", level=l.id, zone=l.zone, type="Service", kind="svc", area=l.svc)]

    def sm(z, k):
        return sum(l[k] for l in M.levels if (not z or l.zone == z))

    M.zone = O()
    for z in ("commercial", "residential", "parking"):
        M.zone[z] = O(gross=sm(z, "gross"), usable=sm(z, "usable"), circ=sm(z, "circ"), svc=sm(z, "svc"),
                      floors=sum(1 for l in M.levels if l.zone == z))
    M.tot = O(gross=sm(None, "gross"), usable=sm(None, "usable"), circ=sm(None, "circ"), svc=sm(None, "svc"))
    M.bayArea = M.zone.parking.usable
    M.units = [s for s in M.spaces if s.zone == "residential" and s.kind == "space"]
    M.cspaces = [s for s in M.spaces if s.zone == "commercial" and s.kind == "space"]
    M.required = (math.ceil(M.zone.commercial.usable / 100) + len(M.units)) if (M.cspaces or M.units) else 0
    M.above = sum(1 for l in M.levels if l.number >= 0)
    M.sur = [O(name=n, af=a, at=t, sur=pct(a, t), color=c) for n, a, t, c in [
        ("Commercial Area", M.zone.commercial.usable, M.zone.commercial.gross, "commercial"),
        ("Residential Area", M.zone.residential.usable, M.zone.residential.gross, "residential"),
        ("Parking Area", M.bayArea, M.zone.parking.gross, "parking"),
        ("Circulation Area", M.tot.circ, M.tot.gross, "circ"),
        ("Service Area", M.tot.svc, M.tot.gross, "svc")]]
    M.overallSUR = pct(M.zone.commercial.usable + M.zone.residential.usable + M.bayArea, M.tot.gross)

    def tgt(t, h):
        return "na" if t <= 0 else "ok" if h >= t else "review" if h > 0 else "missing"

    M.req = [O(k=k, target=t, have=h, unit=u, state=tgt(t, h)) for k, t, h, u in [
        ("Commercial usable area", i.tComm, M.zone.commercial.usable, "m²"),
        ("Residential usable area", i.tRes, M.zone.residential.usable, "m²"),
        ("Parking bays", i.tBays, M.nb, "bays"), ("EV charging bays", i.tEv, M.nev, "bays")]]
    M.checks = run_checks(M)
    M.status = status_of(M)
    M.elements = elements_of(M)
    M.circ = circ_of(M)
    return M


def run_checks(M):
    i = M.inp
    C = []

    def mk(label, state, note):
        C.append(O(label=label, state=state, note=note))

    cf, rf = M.zone.commercial.floors, M.zone.residential.floors
    pa = sum(1 for l in M.levels if l.zone == "parking" and l.number >= 0)
    mk("Site coverage", "ok" if M.cov <= i.maxCov else "review",
       f"Footprint {m2(M.fp.area)} m² is {f1(M.cov)}% of the {m2(i.plotW * i.plotD)} m² plot (limit {i.maxCov}%)")
    mk("Basement level(s)", "ok" if i.basements > 0 else "review" if pa > 0 else "missing",
       f"{i.basements} basement level{'s' if i.basements > 1 else ''} modelled" if i.basements > 0
       else "Parking is provided in podium floors only" if pa > 0 else "No basement or podium parking")
    g0 = next((l for l in M.levels if l.number == 0), None)
    mk("Ground Floor", "ok" if g0 else "missing", f"Ground Floor is {g0.fn}" if g0 else "No Ground Floor")
    if cf > 0 or i.tComm > 0:
        q = M.req[0]
        if cf == 0:
            st_, nt = "missing", "No commercial floors"
        elif q.state == "review":
            st_, nt = "review", f"{m2(q.have)} of {m2(q.target)} m² target provided"
        else:
            st_ = "ok"
            nt = f"{cf} commercial floor{'s' if cf > 1 else ''}, {m2(M.zone.commercial.usable)} m² usable" + \
                (f" (target {m2(q.target)})" if q.target else "")
        mk("Commercial zoning", st_, nt)
    if rf > 0 or i.tRes > 0:
        q = M.req[1]
        if rf == 0:
            st_, nt = "missing", "No residential floors"
        elif q.state == "review":
            st_, nt = "review", f"{m2(q.have)} of {m2(q.target)} m² target provided"
        else:
            st_ = "ok"
            nt = f"{rf} residential floor{'s' if rf > 1 else ''}, {len(M.units)} dwellings" + \
                (f", {m2(q.have)} m² against target {m2(q.target)}" if q.target else "")
        mk("Residential configuration", st_, nt)
    need = max(M.required, i.tBays)
    ps, pn = "ok", f"{M.nb} bays against {need} needed"
    if M.nb == 0:
        ps, pn = "missing", "No parking facility in the model"
    elif M.nb < need:
        ps, pn = "review", f"{need - M.nb} bays short of the {need} needed"
    mk("Parking facilities", ps, pn)
    nev = i.tEv if i.tEv > 0 else math.ceil(M.nb * 0.1)
    if M.nb == 0 or M.nev == 0:
        es, en = "missing", ("No EV charging bays")
    elif M.nev >= nev:
        es, en = "ok", f"{M.nev} EV bays, {fp(pct(M.nev, M.nb))} of parking"
    else:
        es, en = "review", f"{M.nev} EV bays, {nev} needed"
    mk("EV charging facilities", es, en)
    return C + opening_checks(M)


VT = {"ok": "VERIFIED", "review": "NEEDS REVIEW", "missing": "NOT CONFIGURED", "na": "NOT REQUIRED"}


def status_of(M):
    by = {c.label: c.state for c in M.checks}
    allst = worst(*[c.state for c in M.checks])
    g = lambda k: by.get(k, "na")  # noqa: E731
    park = worst(g("Basement level(s)"), g("Parking facilities"))
    rows = [
        ["Building Model", "COMPLETE" if allst == "ok" else "INCOMPLETE", "ok" if allst == "ok" else allst],
        ["Commercial Zoning", VT[g("Commercial zoning")], g("Commercial zoning")],
        ["Residential Configuration", VT[g("Residential configuration")], g("Residential configuration")],
        ["Basement Parking", VT[park], park],
        ["EV Charging", VT[g("EV charging facilities")], g("EV charging facilities")]]
    return O(rows=rows, overall=allst,
             title="Design verified" if allst == "ok" else "Needs review" if allst == "review" else "Incomplete",
             count=sum(1 for c in M.checks if c.state == "ok"), total=len(M.checks))


def elements_of(M):
    L = M.levels
    house = M.kind == "house"
    nl = len(L)
    S = lambda k: sum(len(l.plan.el.get(k) or []) for l in L)  # noqa: E731
    walls = sum((12 + len(l.plan.aisles)) if l.zone == "parking" else len(l.plan.rooms) * 2 + 10 for l in L)
    bal_a = sum(b.w * b.h for l in L for b in (l.plan.el.balconies or []))
    out = [
        O(k="Walls", n=walls, d=f"{nl} levels, outer walls and partitions"),
        O(k="Floors", n=nl + 1, d=f"{nl} level slabs and 1 roof slab"),
        O(k="Doors", n=S("doors"), d="Room, entrance and service doors on the plans"),
        O(k="Windows", n=S("windows"), d="Openings on outer walls"),
        O(k="Rooms", n=len(M.spaces),
          d="Rooms, passages and stair halls" if house else f"{len(M.cspaces)} commercial, {len(M.units)} dwellings, circulation and service"),
        O(k="Stairs", n=S("stairs"), d=f"{S('stairs')} stair run{'' if S('stairs') == 1 else 's'} placed on {nl} level{'s' if nl > 1 else ''}"),
        O(k="Elevators", n=S("lifts"), d=f"{S('lifts')} lift cars placed" if S("lifts") else "None placed"),
        O(k="Balconies", n=S("balconies"), d=f"{m2(bal_a)} m² in total")]
    if not house:
        ramps = sum(1 for l in L if l.zone == "parking")
        panels = math.ceil(M.nev / 8) if M.nev > 0 else 0
        conv = max(0, M.nb - M.nev)
        out += [O(k="Parking elements", n=conv + ramps, d=f"{conv} conventional bays, {ramps} ramp{'' if ramps == 1 else 's'}"),
                O(k="EV charging elements", n=M.nev + panels, d=f"{M.nev} charging bays, {panels} distribution panels")]
    out.append(O(k="Building levels", n=nl, d=f"{M.above} above ground" + (f", {M.inp.basements} below" if M.inp.basements else "")))
    return out


def circ_of(M):
    i = M.inp
    cm, rs = M.zone.commercial.floors > 0, M.zone.residential.floors > 0
    pk, bs, nl = M.zone.parking.floors > 0, i.basements > 0, len(M.levels)
    S = lambda k: sum(len(l.plan.el.get(k) or []) for l in M.levels)  # noqa: E731
    ns, nlift = S("stairs"), S("lifts")
    gf = next((l for l in M.levels if l.number == 0), None)
    ap = max([0] + [pct(l.circ, l.gross) or 0 for l in M.levels if l.zone == "parking"])
    it = lambda k, d, s: O(k=k, d=d, s=s)  # noqa: E731
    items = [
        it("Main building entrances",
           f"Ground Floor entrance to {gf.fn.lower()}; residential entry through the central core{'' if rs else ' (not used)'}" if gf else "No Ground Floor",
           "ok" if gf else "missing"),
        it("Commercial access", f"Street frontage on {M.zone.commercial.floors} commercial floor{'s' if M.zone.commercial.floors > 1 else ''}, core lifts and stairs" if cm else "No commercial floors", "ok" if cm else "na"),
        it("Residential access", f"Core lifts and stairs to {M.zone.residential.floors} residential floors" if rs else "No residential floors", "ok" if rs else "na"),
        it("Basement vehicle access",
           f"One two-way ramp serves {i.basements} basement level{'s' if i.basements > 1 else ''}" if bs else "Podium ramp only" if pk else "No basement ramp",
           "ok" if (bs or pk) else "missing"),
        it("Pedestrian circulation", "Street frontage and Ground Floor entry", "ok" if gf else "missing"),
        it("Vertical circulation", f"{nlift} lift cars and {ns} stair runs placed" if nl > 1 else "Single level", "missing" if (nl > 1 and ns == 0) else "ok"),
        it("Staircases", f"{ns} stair runs across {nl} levels", "missing" if (nl > 1 and ns == 0) else "ok"),
        it("Elevator locations", f"{nlift} cars, positions set on each floor plan" if nlift else "None placed", "ok" if (nlift or M.above < 5) else "review"),
        it("Parking circulation", f"Aisles take {f1(ap)}% of the largest parking level" if pk else "No parking level", "ok" if pk else "missing")]
    both = cm and rs
    sep = [
        ["Commercial and residential entrances", "ok" if both else "na", "Shopfronts on the street, residential entry through the core lobby" if both else "Only one use present"],
        ["Commercial and residential vertical cores", "review" if both else "na", "One central core serves both uses, so dedicate lifts and a stair to each" if both else "Only one use present"],
        ["Pedestrian and vehicular routes", "ok" if pk else "missing", "Ramp is separate from the street frontage" if pk else "No vehicle route"],
        ["Commercial and residential at parking", "review" if (pk and both) else "na", "Both uses share the parking aisles, so allocation zones need review" if (pk and both) else "Not applicable"]]
    return O(its=items, sep=sep)


# ============================================================================
#  house model
# ============================================================================
def hrows(a):
    return [O(t=t, a=x) for t, x in a]


GROUND3 = hrows([("living", 24), ("dining", 13), ("kitchen", 12), ("guest", 14), ("bath", 5), ("pooja", 4), ("store", 5), ("utility", 5)])
UPPER3 = hrows([("master", 18), ("bedroom", 15), ("bedroom", 14), ("bath", 5), ("bath", 4.5), ("lounge", 18), ("study", 9)])
HBASE = O(name="My House", plotW=12.0, plotD=18.0, front=3.0, rear=1.5, side=1.0, maxCov=70, tArea=250, tBeds=3, tBaths=3, edits=O())


def _hpreset(**kw):
    p = clone(HBASE)
    p.update(kw)
    return p


HPRESETS = {
    "h3": _hpreset(floors=[clone(GROUND3), clone(UPPER3)]),
    "h2": _hpreset(name="2BHK Home", plotW=10.0, plotD=15.0, front=2.0, rear=1.5, side=1.0, tArea=90, tBeds=2, tBaths=2,
                   floors=[hrows([("living", 18), ("dining", 10), ("kitchen", 9), ("master", 13), ("bedroom", 11), ("bath", 3.5), ("bath", 3)])]),
    "h4": _hpreset(name="4BHK Villa", plotW=15.0, plotD=20.0, front=3.0, rear=2.0, side=1.5, tArea=330, tBeds=4, tBaths=3,
                   floors=[hrows([("living", 30), ("dining", 16), ("kitchen", 14), ("guest", 15), ("bath", 5), ("pooja", 5), ("store", 6), ("utility", 5), ("garage", 22), ("toilet", 2.5)]),
                           hrows([("master", 22), ("bedroom", 17), ("bedroom", 16), ("bath", 5.5), ("bath", 5), ("lounge", 24), ("study", 11), ("store", 6)])]),
    "hb": _hpreset(name="Starter Home", plotW=10.0, plotD=15.0, front=2.0, rear=1.5, side=1.0, tArea=0, tBeds=0, tBaths=0,
                   floors=[hrows([("living", 26), ("kitchen", 14), ("bedroom", 18), ("bath", 5), ("store", 5)])]),
}
HPRESET_LABEL = {"h3": "3BHK G+1", "h2": "2BHK single floor", "h4": "4BHK villa", "hb": "Starter home"}


def squarify(items, rect, keep):
    out = []
    r = O(rect)
    q = list(items) if keep else sorted(items, key=lambda a: -a.a)
    while q:
        side = min(r.w, r.h)
        row, sm, best = [], 0, math.inf
        while q:
            cand = row + [q[0]]
            cs = sm + q[0].a
            w_ = 0
            for it in cand:
                d_ = side * side * it.a
                if d_ > 0:
                    w_ = max(w_, d_ / (cs * cs), cs * cs / d_)
            if row and w_ > best:
                break
            row, sm, best = cand, cs, w_
            q.pop(0)
        if r.w >= r.h:
            sw = sm / r.h
            y0 = r.y
            for it in row:
                hh = it.a / sw
                n = O(it)
                n.update(x=r.x, y=y0, w=sw, h=hh)
                out.append(n)
                y0 += hh
            r = O(x=r.x + sw, y=r.y, w=r.w - sw, h=r.h)
        else:
            sh = sm / r.w
            x0 = r.x
            for it in row:
                ww = it.a / sh
                n = O(it)
                n.update(x=x0, y=r.y, w=ww, h=sh)
                out.append(n)
                x0 += ww
            r = O(x=r.x, y=r.y + sh, w=r.w, h=r.h - sh)
    return out


def best_layout(arr, rect, is_ext):
    if not arr:
        return []
    rnd = rng(len(arr) * 97 + jr(rect.w * 10) + jr(rect.h * 10))
    best, bs = None, math.inf
    for t in range(120):
        o_ = list(arr)
        if t > 0:
            for j in range(len(o_) - 1, 0, -1):
                k = int(math.floor(rnd() * (j + 1)))
                o_[j], o_[k] = o_[k], o_[j]
        else:
            o_.sort(key=lambda a: -a.a)
        pl = squarify(o_, rect, True)
        sc = 0
        for p in pl:
            asp = max(p.w / p.h, p.h / p.w)
            sc += max(0, asp - 2) * 4
            if p.t in HAB and not is_ext(p):
                sc += 100
        if sc < bs:
            bs, best = sc, pl
    return best


def share_edge(r, n):
    if near(r.x + r.w, n.x) or near(n.x + n.w, r.x):
        lo, hi = max(r.y, n.y), min(r.y + r.h, n.y + n.h)
        if hi - lo >= 1.1:
            return O(o="v", x=n.x if near(r.x + r.w, n.x) else r.x, y=(lo + hi) / 2 - .45, sw=-1 if near(r.x + r.w, n.x) else 1)
    if near(r.y + r.h, n.y) or near(n.y + n.h, r.y):
        lo, hi = max(r.x, n.x), min(r.x + r.w, n.x + n.w)
        if hi - lo >= 1.1:
            return O(o="h", y=n.y if near(r.y + r.h, n.y) else r.y, x=(lo + hi) / 2 - .45, sw=-1 if near(r.y + r.h, n.y) else 1)
    return None


def plan_house(rows, L, B, multi, num, tot, cnt):
    sw = 2.5 if multi else 0
    Lr = L - sw
    pas = 1.2
    avail = Lr * (B - pas)
    P = O(kind="house", fn="house", L=L, B=B, rooms=[], bays=[], aisles=[], extras=[], cw=sw, x0=Lr, x1=L,
          el=O(doors=[], windows=[], stairs=[], lifts=[], balconies=[]))
    items = [O(t=r.t, a=max(2, r.a), req=r.a) for r in rows]
    sm = sum(i.a for i in items)
    sc = avail / sm if sm > 0 else 1
    for i in items:
        i.a *= sc
    P.scale = sc
    pub = [i for i in items if ROOMS[i.t].g == "pub"]
    pri = [i for i in items if ROOMS[i.t].g == "pri"]
    dpub = sum(i.a for i in pub) / Lr
    dpri = sum(i.a for i in pri) / Lr
    placed = []
    eE = lambda p: near(p.x, 0) or (sw == 0 and near(p.x + p.w, Lr))  # noqa: E731
    if pri:
        placed += best_layout(pri, O(x=0, y=0, w=Lr, h=dpri), lambda p: near(p.y, 0) or eE(p))
    if pub:
        placed += best_layout(pub, O(x=0, y=dpri + pas, w=Lr, h=dpub), lambda p: near(p.y + p.h, B) or eE(p))
    for k, p in enumerate(placed):
        lab = ROOMS[p.t].l
        if tot[p.t] > 1:
            cnt[p.t] += 1
            name = f"{lab} {cnt[p.t]}"
        else:
            name = lab
        P.rooms.append(O(id=f"H{num}-{k + 1:02d}", name=name, type=lab, t=p.t, x=p.x, y=p.y, w=p.w, h=p.h,
                         area=p.w * p.h, req=p.req, kind="space"))
    P.corr = O(x=0, y=dpri, w=L, h=pas)
    P.pasY = dpri
    if multi:
        P.extras.append(O(x=Lr, y=0, w=sw, h=B))
    P.usable = sum(r.area for r in P.rooms)
    P.svc = sw * B
    P.circ = L * B - P.usable - P.svc
    R, reached, el = P.rooms, set(), P.el
    cl = lambda v, a, b: max(a, min(b, v))  # noqa: E731
    for r in R:
        if pri and near(r.y + r.h, dpri):
            el.doors.append(O(x=r2(cl(r.x + r.w / 2 - .45, r.x + .1, r.x + r.w - 1)), y=r2(dpri), len=.9, o="h", sw=-1))
            reached.add(r.id)
        elif pub and near(r.y, dpri + pas):
            el.doors.append(O(x=r2(cl(r.x + r.w / 2 - .45, r.x + .1, r.x + r.w - 1)), y=r2(dpri + pas), len=.9, o="h", sw=1))
            reached.add(r.id)
    ch = True
    while ch:
        ch = False
        for r in R:
            if r.id in reached:
                continue
            for n in R:
                if n.id not in reached:
                    continue
                s = share_edge(r, n)
                if s:
                    el.doors.append(O(x=r2(s.x), y=r2(s.y), len=.9, o=s.o, sw=s.sw))
                    reached.add(r.id)
                    ch = True
                    break
    south_c = sorted([r for r in R if near(r.y + r.h, B)], key=lambda r: -r.area)
    south = south_c[0] if south_c else None
    if south:
        el.doors.append(O(x=r2(south.x + min(.25, max(0, south.w - 1.45) / 2)), y=r2(B), len=1.2, o="h", sw=-1))
    for r in R:
        e = ext_edges(r, L, B)
        sm_ = r.t in SMALL
        if e.n or e.s:
            wl = 1 if sm_ else min(r.w * .5, 2.4)
            wx = r.x + (r.w - wl) / 2
            if e.s and r is south:
                x0 = r.x + 1.6
                av = r.x + r.w - .2 - x0
                w2 = min(2.4, av)
                if w2 >= .9:
                    wl = min(1, w2) if sm_ else w2
                    wx = x0 + (av - wl) / 2
            el.windows.append(O(x=r2(wx), y=0 if e.n else r2(B), len=r2(wl), o="h"))
        if e.w or e.e:
            hl = 1 if sm_ else min(r.h * .5, 2.2)
            el.windows.append(O(x=0 if e.w else r2(L), y=r2(r.y + (r.h - hl) / 2), len=r2(hl), o="v"))
    if multi:
        el.stairs.append(O(x=r2(Lr + .15), y=.15, w=r2(sw - .3), h=4.6))
        el.windows.append(O(x=r2(L), y=1.6, len=1.2, o="v"))
    if num > 0 and south and south.w >= 2.5:
        bw = r2(min(south.w * .7, 3.6))
        el.balconies.append(O(x=r2(south.x + (south.w - bw) / 2), y=r2(B), w=bw, h=1.4))
    return P


def build_house(h):
    M = O(kind="house", inp=h, levels=[], spaces=[], bays=[], nb=0, nev=0, nal=0, bayArea=0)
    L = max(6, h.plotW - 2 * h.side)
    B = max(8, h.plotD - h.front - h.rear)
    multi = len(h.floors) > 1
    M.fp = O(L=L, B=B, area=L * B)
    M.cov = pct(L * B, h.plotW * h.plotD)
    tot, cnt = {}, {}
    for f in h.floors:
        for r in f:
            tot[r.t] = tot.get(r.t, 0) + 1
            cnt[r.t] = 0
    e = 0
    for n, rows in enumerate(h.floors):
        pl = plan_house(rows, L, B, multi, n, tot, cnt)
        id_ = f"L{n}"
        sig = "|".join(str(x) for x in ["house", L, B, multi, repr([(r.t, r.a) for r in rows])])
        apply_edits(pl, h, id_, sig)
        lv = O(id=id_, name=ord_(n), zone="residential", fn="House floor", fnKey="house", fac="Rooms", L=L, B=B,
               number=n, h=3, elev=e, gross=L * B, usable=pl.usable, circ=pl.circ, svc=pl.svc, plan=pl, sig=sig)
        M.levels.append(lv)
        e += 3
        for r in pl.rooms:
            M.spaces.append(O(id=r.id, name=r.name, level=id_, zone="residential", type=r.type, kind="space", area=r.area))
        M.spaces += [O(id=id_ + "-CIR", name="Passage", level=id_, zone="residential", type="Circulation", kind="circ", area=pl.circ),
                     O(id=id_ + "-SVC", name="Stair hall" if multi else "Services", level=id_, zone="residential", type="Service", kind="svc", area=pl.svc)]
    M.height = e
    M.above = len(M.levels)
    sm = lambda k: sum(l[k] for l in M.levels)  # noqa: E731
    z0 = lambda: O(gross=0, usable=0, circ=0, svc=0, floors=0)  # noqa: E731
    M.zone = O(commercial=z0(), residential=O(gross=sm("gross"), usable=sm("usable"), circ=sm("circ"), svc=sm("svc"), floors=len(M.levels)), parking=z0())
    M.tot = O(gross=sm("gross"), usable=sm("usable"), circ=sm("circ"), svc=sm("svc"))
    M.units, M.cspaces = [], []
    allr = [r for l in M.levels for r in l.plan.rooms]
    M.beds = sum(1 for r in allr if r.t in BEDS)
    M.baths = sum(1 for r in allr if r.t == "bath")
    M.sur = [O(name=n, af=a, at=M.tot.gross, sur=pct(a, M.tot.gross), color=c) for n, a, c in
             [("Rooms (usable)", M.tot.usable, "residential"), ("Passages", M.tot.circ, "circ"), ("Stair hall and services", M.tot.svc, "svc")]]
    M.overallSUR = pct(M.tot.usable, M.tot.gross)
    tgt = lambda t, v: "na" if t <= 0 else "ok" if v >= t else "review" if v > 0 else "missing"  # noqa: E731
    M.req = [O(k=k, target=t, have=v, unit=u, state=tgt(t, v)) for k, t, v, u in
             [("Built-up area", h.tArea, M.tot.gross, "m²"), ("Bedrooms", h.tBeds, M.beds, ""), ("Bathrooms", h.tBaths, M.baths, "")]]
    M.checks = house_checks(M)
    M.status = house_status(M)
    M.elements = elements_of(M)
    M.circ = None
    return M


def house_checks(M):
    h = M.inp
    C = []

    def mk(label, state, note):
        C.append(O(label=label, state=state, note=note))

    rooms = [(l, r) for l in M.levels for r in l.plan.rooms]
    mk("Plot coverage", "ok" if M.cov <= h.maxCov else "review",
       f"Footprint {m2(M.fp.area)} m² is {f1(M.cov)}% of the {m2(h.plotW * h.plotD)} m² plot (limit {h.maxCov}%)")
    has = lambda t: any(r.t == t for _, r in rooms)  # noqa: E731
    miss = [m for m in [None if has("living") else "living room", None if has("kitchen") else "kitchen",
                        None if any(r.t in BEDS for _, r in rooms) else "bedroom",
                        None if (has("bath") or has("toilet")) else "bathroom"] if m]
    mk("Essential rooms", "missing" if miss else "ok",
       "Missing: " + ", ".join(miss) if miss else "Living room, kitchen, bedroom and bathroom are present")
    for q in M.req:
        if q.state == "na":
            continue
        txt = (f"{m2(q.have)} {q.unit} against a target of {m2(q.target)}" if q.state == "ok"
               else f"{m2(q.have)} {q.unit} provided, target {m2(q.target)}")
        mk(q.k, q.state, " ".join(txt.split()))
    small = [f"{r.name} ({l.name}) {f1(r.area)} m²" for l, r in rooms if ROOMS[r.t].min and r.area < ROOMS[r.t].min]
    sc = [l.plan.scale for l in M.levels]
    lo, hi = min(sc), max(sc)
    stt, n = "ok", "Rooms fit the footprint close to the sizes you asked for"
    if small:
        stt, n = "review", f"Below the working minimum: {lst(small)}"
    elif lo < .8:
        stt, n = "review", f"Rooms are squeezed to {jr(lo * 100)}% of the requested areas; enlarge the plot or remove rooms"
    elif hi > 1.5:
        stt, n = "review", f"Rooms are stretched to {jr(hi * 100)}% of the requested areas; add rooms or reduce the plot"
    mk("Room sizes", stt, n)
    g = M.levels[0] if M.levels else None
    ent = bool(g) and any((near(d.y, 0) or near(d.y, g.plan.B)) if d.o == "h" else (near(d.x, 0) or near(d.x, g.plan.L)) for d in g.plan.el.doors)
    mk("Main entrance", "ok" if ent else "missing",
       "A door opens to the outside on the Ground Floor" if ent else "No door on an outer wall of the Ground Floor")
    return C + opening_checks(M)


def house_status(M):
    by = {c.label: c.state for c in M.checks}
    allst = worst(*[c.state for c in M.checks])
    prog = worst(*[by.get(k, "ok") for k in ("Essential rooms", "Room sizes", "Built-up area", "Bedrooms", "Bathrooms")])
    opn = worst(*[by.get(k, "ok") for k in ("Doors to every room", "Windows on outer walls", "Main entrance")])
    st_ = by.get("Stairs and lifts", "na")
    rows = [["House Model", "COMPLETE" if allst == "ok" else "INCOMPLETE", "ok" if allst == "ok" else allst],
            ["Room Program", VT[prog], prog], ["Doors and Windows", VT[opn], opn], ["Stairs", VT[st_], st_],
            ["Plot Coverage", VT[by["Plot coverage"]], by["Plot coverage"]]]
    return O(rows=rows, overall=allst,
             title="Design verified" if allst == "ok" else "Needs review" if allst == "review" else "Incomplete",
             count=sum(1 for c in M.checks if c.state == "ok"), total=len(M.checks))


def fit_building(inp):
    """Fit floors and basements to the area / bay targets. Returns a message."""
    L = max(20, inp.plotW - 2 * inp.side)
    B = max(12, inp.plotD - inp.front - inp.rear)
    Lb = max(20, inp.plotW - 2 * inp.base)
    Bb = max(12, inp.plotD - 2 * inp.base)
    mx = O(w=list(inp.mix), commSpaces=inp.commSpaces, officeSpaces=inp.officeSpaces)
    uc = plan_floor(inp.commFn, L, B, 0, mx).usable
    ur = plan_floor("res", L, B, 0, mx).usable
    pb = len(plan_floor("park", Lb, Bb, 0, mx).bays)
    nc = math.ceil(inp.tComm / uc) if inp.tComm > 0 else 0
    nr = math.ceil(inp.tRes / ur) if inp.tRes > 0 else 0
    msg = []
    fl = [inp.commFn] * nc + ["res"] * nr
    if len(fl) > 30:
        fl = fl[:30]
        msg.append("Capped at 30 floors: enlarge the plot or lower the area targets.")
    if not fl:
        fl = ["res"]
        msg.append("No area target set, kept one floor.")
    inp.floors = fl
    if inp.tBays > 0 and pb > 0:
        nb = math.ceil(inp.tBays / pb)
        inp.basements = min(4, nb)
        if nb > 4:
            msg.append(f"{inp.tBays} bays need {nb} basements of {pb} bays each; capped at 4.")
    if inp.tEv > 0:
        inp.ev = inp.tEv
    return " ".join(msg) or f"Fitted: {nc} commercial and {nr} residential floors, {inp.basements} basement level{'' if inp.basements == 1 else 's'}."
# ============================================================================
#  SVG drawing (blueprint sheets, site plan, building section)
# ============================================================================
SHEET_CSS = r'''
svg.sheet{--pz-commercial:#7A4DB3;--pz-residential:#1E8C8C;--pz-parking:#5C6E7E;--pz-ev:#2F6FE4}
svg.sheet.blue{--p-bg:#133A66;--p-ink:#E8F2FF;--p-dim:#8FB7E6;--p-grid:rgba(143,183,230,.35);--pz-commercial:#C9A7FF;--pz-residential:#6FE0D8;--pz-parking:#9FB3C8;--pz-ev:#6FB1FF}
svg.sheet.paper{--p-bg:#FFFFFF;--p-ink:#15222B;--p-dim:#6A7A84;--p-grid:rgba(21,34,43,.22)}
svg.sheet .p-bg{fill:var(--p-bg)}
svg.sheet .p-wall{fill:none;stroke:var(--p-ink);stroke-width:3}
svg.sheet .p-line{fill:none;stroke:var(--p-ink);stroke-width:1.1}
svg.sheet .p-thin{fill:none;stroke:var(--p-ink);stroke-width:.6}
svg.sheet .p-dimln{fill:none;stroke:var(--p-dim);stroke-width:.7}
svg.sheet .p-grid{stroke:var(--p-grid);stroke-width:.6;stroke-dasharray:7 3 1.5 3}
svg.sheet .p-bub{fill:var(--p-bg);stroke:var(--p-dim);stroke-width:.8}
svg.sheet .p-col{fill:var(--p-ink)}
svg.sheet .p-room{stroke:var(--p-ink);stroke-width:1.1}
svg.sheet .z-commercial{fill:var(--pz-commercial);fill-opacity:.32}
svg.sheet .z-residential{fill:var(--pz-residential);fill-opacity:.28}
svg.sheet .z-parking{fill:var(--pz-parking);fill-opacity:.22}
svg.sheet .p-core{fill:var(--p-ink);fill-opacity:.1;stroke:var(--p-ink);stroke-width:1.1}
svg.sheet .p-bay{fill:none;stroke:var(--p-ink);stroke-width:.7}
svg.sheet .p-bay.al{fill:var(--pz-parking);fill-opacity:.55}
svg.sheet .p-bay.ev{fill:var(--pz-ev);fill-opacity:.55;stroke:var(--pz-ev);stroke-width:1}
svg.sheet .p-balc{fill:var(--p-ink);fill-opacity:.1;stroke:var(--p-ink);stroke-width:1}
svg.sheet .p-ramp{fill:var(--p-ink);fill-opacity:.08;stroke:var(--p-ink);stroke-width:.8}
svg.sheet .p-site{fill:var(--p-ink);fill-opacity:.14;stroke:var(--p-ink);stroke-width:2.4}
svg.sheet .p-set{fill:none;stroke:var(--p-dim);stroke-width:.9;stroke-dasharray:6 4}
svg.sheet text{fill:var(--p-ink);font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;font-size:7.5px;font-weight:500;text-anchor:middle}
svg.sheet text.a{font-size:6.5px;font-weight:400}
svg.sheet text.t{font-size:4.8px;fill:var(--p-dim)}
svg.sheet text.c{font-size:6px;fill:var(--p-ink)}
svg.sheet text.l{text-anchor:start}
svg.sheet text.d{fill:var(--p-dim);font-size:6.5px}
svg.sheet text.h{font-size:11px;font-weight:600}
svg.sheet text.s{font-size:5.2px;fill:var(--p-dim)}
'''
SHEET_CSS = ' '.join(SHEET_CSS.split())


def _n(v):
    v = round(v, 2)
    return str(int(v)) if v == int(v) else str(v)


def lbl(x, y, t, cls=None, mx=None):
    s = str(t)
    if mx and len(s) > mx:
        s = s[:max(1, mx - 1)] + "…"
    c = f' class="{cls}"' if cls else ""
    return f'<text{c} x="{_n(x)}" y="{_n(y)}">{esc(s)}</text>'


def erase_h(x, y, w):
    return f'<rect class="p-bg" x="{_n(x)}" y="{_n(y - 1.8)}" width="{_n(w)}" height="3.6"/>'


def erase_v(x, y, h):
    return f'<rect class="p-bg" x="{_n(x - 1.8)}" y="{_n(y)}" width="3.6" height="{_n(h)}"/>'


def north_arrow(x, y):
    return (f'<g transform="translate({_n(x)},{_n(y)})"><circle r="11" class="p-bub"/>'
            f'<path d="M0 -9 L4 5 L0 2 L-4 5 Z" class="p-col"/><text y="-13" class="c">N</text></g>')


def dims_svg(L, B, sc):
    w, h = round(L * sc, 1), round(B * sc, 1)
    y, x = h + 22, w + 22
    return (f'<g><line class="p-dimln" x1="0" x2="{_n(w)}" y1="{_n(y)}" y2="{_n(y)}"/>'
            f'<line class="p-dimln" x1="0" x2="0" y1="{_n(y - 4)}" y2="{_n(y + 4)}"/>'
            f'<line class="p-dimln" x1="{_n(w)}" x2="{_n(w)}" y1="{_n(y - 4)}" y2="{_n(y + 4)}"/>'
            f'<text class="d" x="{_n(w / 2)}" y="{_n(y - 3)}">{f1(L)} m</text>'
            f'<line class="p-dimln" x1="{_n(x)}" x2="{_n(x)}" y1="0" y2="{_n(h)}"/>'
            f'<line class="p-dimln" x1="{_n(x - 4)}" x2="{_n(x + 4)}" y1="0" y2="0"/>'
            f'<line class="p-dimln" x1="{_n(x - 4)}" x2="{_n(x + 4)}" y1="{_n(h)}" y2="{_n(h)}"/>'
            f'<text class="d" transform="translate({_n(x + 9)},{_n(h / 2)}) rotate(90)">{f1(B)} m</text></g>')


def title_block(W, H, mx, wpx, cells):
    y, h = H - 62, 48
    xs = [mx + f * wpx for f in (0, .4, .7, .86, 1)]
    s = f'<rect x="{_n(mx)}" y="{_n(y)}" width="{_n(wpx)}" height="{h}" class="p-line"/>'
    for x in xs[1:-1]:
        s += f'<line class="p-line" x1="{_n(x)}" x2="{_n(x)}" y1="{_n(y)}" y2="{_n(y + h)}"/>'
    for k, c in enumerate(cells):
        cx = xs[k] + 6
        s += f'<text class="s l" x="{_n(cx)}" y="{_n(y + 10)}">{esc(c[0])}</text>'
        s += lbl(cx, y + 28, c[1], "l h", int((xs[k + 1] - xs[k] - 12) // 6.2))
        if len(c) > 2 and c[2]:
            s += f'<text class="a l" x="{_n(cx)}" y="{_n(y + 41)}">{esc(c[2])}</text>'
    return s


def sheet_list(M):
    return [O(id="site", name="Site plan")] + [O(id=l.id, name=l.name) for l in M.levels]


def draw_els(el, mini, tags, sc):
    X = lambda m: round(m * sc, 1)  # noqa: E731
    g = ""
    for b in el.balconies or []:
        g += f'<rect class="p-balc" x="{_n(X(b.x))}" y="{_n(X(b.y))}" width="{_n(X(b.w))}" height="{_n(X(b.h))}"/>'
        if not mini:
            g += (f'<rect class="p-thin" x="{_n(X(b.x) + 2)}" y="{_n(X(b.y) + 2)}" width="{_n(max(0, X(b.w) - 4))}" '
                  f'height="{_n(max(0, X(b.h) - 4))}" stroke-dasharray="3 2"/>')
            if X(b.w) >= 26:
                g += f'<text class="t" x="{_n(X(b.x + b.w / 2))}" y="{_n(X(b.y + b.h / 2) + 2)}">BALCONY</text>'
    for w in el.windows or []:
        x, y, l = X(w.x), X(w.y), X(w.len)
        if w.o == "h":
            g += erase_h(x, y, l) + (f'<line class="p-thin" x1="{_n(x)}" x2="{_n(x + l)}" y1="{_n(y - 1.2)}" y2="{_n(y - 1.2)}"/>'
                                     f'<line class="p-thin" x1="{_n(x)}" x2="{_n(x + l)}" y1="{_n(y + 1.2)}" y2="{_n(y + 1.2)}"/>')
        else:
            g += erase_v(x, y, l) + (f'<line class="p-thin" x1="{_n(x - 1.2)}" x2="{_n(x - 1.2)}" y1="{_n(y)}" y2="{_n(y + l)}"/>'
                                     f'<line class="p-thin" x1="{_n(x + 1.2)}" x2="{_n(x + 1.2)}" y1="{_n(y)}" y2="{_n(y + l)}"/>')
    for d in el.doors or []:
        x, y, l = X(d.x), X(d.y), X(d.len)
        s = -1 if (d.sw or 1) < 0 else 1
        if d.o == "h":
            g += erase_h(x, y, l) + f'<path class="p-thin" d="M{_n(x)} {_n(y)} L{_n(x)} {_n(y + s * l)} M{_n(x + l)} {_n(y)} A{_n(l)} {_n(l)} 0 0 {0 if s < 0 else 1} {_n(x)} {_n(y + s * l)}"/>'
        else:
            g += erase_v(x, y, l) + f'<path class="p-thin" d="M{_n(x)} {_n(y)} L{_n(x + s * l)} {_n(y)} M{_n(x)} {_n(y + l)} A{_n(l)} {_n(l)} 0 0 {0 if s > 0 else 1} {_n(x + s * l)} {_n(y)}"/>'
    for s in el.stairs or []:
        x, y, w, h = X(s.x), X(s.y), X(s.w), X(s.h)
        g += f'<rect class="p-line" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"/>'
        run = max(w, h)
        n = int(run // 3.2)
        for k in range(1, n):
            if w >= h:
                g += f'<line class="p-thin" x1="{_n(x + k * run / n)}" x2="{_n(x + k * run / n)}" y1="{_n(y)}" y2="{_n(y + h)}"/>'
            else:
                g += f'<line class="p-thin" x1="{_n(x)}" x2="{_n(x + w)}" y1="{_n(y + k * run / n)}" y2="{_n(y + k * run / n)}"/>'
        if not mini and min(w, h) >= 22:
            g += f'<text class="t" x="{_n(x + w / 2)}" y="{_n(y + h / 2 + 2)}">STAIR</text>'
    for s in el.lifts or []:
        x, y, w, h = X(s.x), X(s.y), X(s.w), X(s.h)
        g += (f'<rect class="p-line" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"/>'
              f'<line class="p-thin" x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x + w)}" y2="{_n(y + h)}"/>'
              f'<line class="p-thin" x1="{_n(x + w)}" y1="{_n(y)}" x2="{_n(x)}" y2="{_n(y + h)}"/>')
    if tags and not mini:
        pre = {"doors": "D", "windows": "W", "stairs": "S", "lifts": "E", "balconies": "B"}
        for t, pf in pre.items():
            for i, it in enumerate(el.get(t) or []):
                rect = it.w is not None
                if rect:
                    cx, cy, ox, oy = it.x + it.w / 2, it.y + it.h / 2, 0, 3
                else:
                    flip = t == "doors" and (it.sw or 1) > 0
                    cx = it.x + it.len / 2 if it.o == "h" else it.x
                    cy = it.y if it.o == "h" else it.y + it.len / 2
                    ox = 0 if it.o == "h" else (-1 if flip else 1) * 4.5
                    oy = ((-1 if flip else 1) * -4.5 + (9 if flip else 0)) if it.o == "h" else 2.2
                g += f'<text class="c" style="fill:var(--pz-ev);font-weight:700" x="{_n(X(cx) + ox)}" y="{_n(X(cy) + oy)}">{pf}{i + 1}</text>'
    return g


def wrap_svg(inner, W, H, style, standalone=False):
    dim = f' width="{_n(W)}" height="{_n(H)}"' if standalone else ""
    return (f'<svg class="sheet {style}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(W)} {_n(H)}"{dim} role="img" '
            f'aria-label="Floor plan"><style>{SHEET_CSS}</style><rect class="p-bg" width="{_n(W)}" height="{_n(H)}"/>{inner}</svg>')


def plan_svg(lv, M, style="blue", mini=False, standalone=False):
    P = lv.plan
    L, B = P.L, P.B
    sc = min(26, max(10, 520 / L))
    X = lambda m: round(m * sc, 1)  # noqa: E731
    mx, my, bot = (4, 4, 4) if mini else (54, 46, 96)
    w, h = X(L), X(B)
    W, H = w + 2 * mx, h + my + bot
    z = "z-" + lv.zone
    house = lv.fnKey == "house"
    g = ""
    if not mini:
        nx = max(2, jr(L / (3 if house else 8)))
        ny = max(2, jr(B / (3 if house else 8)))
        for j in range(nx + 1):
            x = X(L * j / nx)
            g += (f'<line class="p-grid" x1="{_n(x)}" x2="{_n(x)}" y1="-10" y2="{_n(h + 10)}"/>'
                  f'<circle class="p-bub" cx="{_n(x)}" cy="-18" r="6"/><text class="c" x="{_n(x)}" y="-15.8">{j + 1}</text>')
        for j in range(ny + 1):
            y = X(B * j / ny)
            g += (f'<line class="p-grid" x1="-10" x2="{_n(w + 10)}" y1="{_n(y)}" y2="{_n(y)}"/>'
                  f'<circle class="p-bub" cx="-18" cy="{_n(y)}" r="6"/><text class="c" x="-18" y="{_n(y + 2.2)}">{chr(65 + (j % 26))}</text>')
    if lv.zone == "parking":
        g += f'<rect class="{z}" x="0" y="0" width="{_n(w)}" height="{_n(h)}"/>'
        for b in P.bays:
            cls = " ev" if b.ev else " al" if b.al else ""
            g += f'<rect class="p-bay{cls}" x="{_n(X(b.x))}" y="{_n(X(b.y))}" width="{_n(X(BAY_W))}" height="{_n(X(BAY_D))}"/>'
            if not mini and b.ev:
                g += f'<text class="t" style="fill:var(--p-bg);font-weight:600" x="{_n(X(b.x + BAY_W / 2))}" y="{_n(X(b.y + BAY_D / 2) + 2)}">EV</text>'
        for a in P.aisles:
            y = X(a.y + a.h / 2)
            g += (f'<line class="p-dimln" x1="{_n(X(P.x0) - 8)}" x2="8" y1="{_n(y)}" y2="{_n(y)}" stroke-dasharray="6 4"/>'
                  f'<line class="p-dimln" x1="{_n(X(P.x1) + 8)}" x2="{_n(w - 70)}" y1="{_n(y)}" y2="{_n(y)}" stroke-dasharray="6 4"/>')
            if not mini:
                g += f'<text class="t" x="{_n(X(P.x0) / 2)}" y="{_n(y - 3)}">DRIVE AISLE</text>'
        if P.ramp:
            r = P.ramp
            g += f'<rect class="p-ramp" x="{_n(X(r.x))}" y="{_n(X(r.y))}" width="{_n(X(r.w))}" height="{_n(X(r.h))}"/>'
            for k in range(1, 12):
                g += f'<line class="p-thin" x1="{_n(X(r.x + k))}" x2="{_n(X(r.x + k))}" y1="{_n(X(r.y))}" y2="{_n(X(r.y + r.h))}"/>'
            if not mini:
                g += f'<text class="a" x="{_n(X(r.x + r.w / 2))}" y="{_n(X(r.y + r.h / 2) + 2)}">RAMP</text>'
    else:
        for r in P.rooms:
            g += f'<rect class="p-room {z}" x="{_n(X(r.x))}" y="{_n(X(r.y))}" width="{_n(X(r.w))}" height="{_n(X(r.h))}"/>'
        for x in P.extras or []:
            g += f'<rect class="p-line" x="{_n(X(x.x))}" y="{_n(X(x.y))}" width="{_n(X(x.w))}" height="{_n(X(x.h))}"/>'
    if not house:
        c = P.core
        g += f'<rect class="p-core" x="{_n(X(c.x))}" y="0" width="{_n(X(c.w))}" height="{_n(h)}"/>'
    g += f'<rect class="p-wall" x="0" y="0" width="{_n(w)}" height="{_n(h)}"/>'
    g += draw_els(P.el, mini, P.custom, sc)
    if not mini:
        if house:
            for r in P.rooms:
                rw, rh = X(r.w), X(r.h)
                cx, cy = X(r.x + r.w / 2), X(r.y + r.h / 2)
                g += lbl(cx, cy - 1, r.name, "", int(rw // 4.7))
                if rh > 24:
                    g += lbl(cx, cy + 8, f"{f1(r.area)} m²", "a")
            g += f'<text class="t" x="{_n(X(P.x0) / 2)}" y="{_n(X(P.corr.y + P.corr.h / 2) + 2)}">PASSAGE</text>'
            if P.cw > 0:
                g += f'<text class="t" x="{_n(X(P.x0 + P.cw / 2))}" y="{_n(X(P.corr.y + P.corr.h / 2) + 2)}">HALL</text>'
        elif lv.zone != "parking":
            for r in P.rooms:
                north = r.side == "N"
                cy = r.y + r.h if north else r.y
                dr = -1 if north else 1
                rw = X(r.w)
                mc = int(rw // 4.7)
                if lv.zone == "residential":
                    sd = min(4.5, r.h * .3)
                    py = cy + dr * sd
                    ext = r.y if north else r.y + r.h
                    g += f'<line class="p-thin" x1="{_n(X(r.x))}" x2="{_n(X(r.x + r.w))}" y1="{_n(X(py))}" y2="{_n(X(py))}"/>' + erase_h(X(r.x + r.w * .7), X(py), 9)
                    if r.type != "1BHK":
                        g += f'<line class="p-thin" x1="{_n(X(r.x + r.w / 2))}" x2="{_n(X(r.x + r.w / 2))}" y1="{_n(X(py))}" y2="{_n(X(ext))}"/>'
                    ly = X(cy + dr * sd / 2)
                    g += lbl(X(r.x + r.w / 2), ly - 1, r.name, "", mc) + lbl(X(r.x + r.w / 2), ly + 7, f"{r.type} · {f1(r.area)} m²", "a", int(rw // 3.9))
                    if rw >= 60:
                        my2 = X(py + dr * (r.h - sd) / 2)
                        g += lbl(X(r.x + r.w * (.5 if r.type == "1BHK" else .25)), my2, "LIVING", "t")
                        if r.type != "1BHK":
                            g += lbl(X(r.x + r.w * .75), my2, "BED", "t")
                else:
                    ly = X(r.y + r.h / 2)
                    g += lbl(X(r.x + r.w / 2), ly - 1, r.name, "", mc) + lbl(X(r.x + r.w / 2), ly + 8, f"{f1(r.area)} m²", "a") + lbl(X(r.x + r.w / 2), ly + 15, r.type, "t", mc)
            cl = "CORRIDOR" if lv.zone == "residential" else "COMMON CIRCULATION"
            g += (f'<text class="t" x="{_n(X(P.x0) / 2)}" y="{_n(X(P.d + P.cr / 2) + 2)}">{cl}</text>'
                  f'<text class="t" x="{_n((X(P.x1) + w) / 2)}" y="{_n(X(P.d + P.cr / 2) + 2)}">{cl}</text>')
        if not house and not P.custom and (P.el.lifts or []):
            g += f'<text class="t" x="{_n(X(P.core.x + P.core.w / 2))}" y="{_n(X(B / 2) + 2)}">LIFTS</text>'
        if not house:
            nx, ny = max(2, jr(L / 8)), max(2, jr(B / 8))
            for a in range(nx + 1):
                for b in range(ny + 1):
                    g += f'<rect class="p-col" x="{_n(X(L * a / nx) - 2)}" y="{_n(X(B * b / ny) - 2)}" width="4" height="4"/>'
        g += dims_svg(L, B, sc)
    body = f'<g transform="translate({mx},{my})">{g}</g>'
    if mini:
        return wrap_svg(body, W, H, style, standalone)
    sh = sheet_list(M)
    idx = next((k for k, s in enumerate(sh) if s.id == lv.id), 0) + 1
    extra = north_arrow(W - 30, 26) + title_block(W, H, mx, w, [
        ("PROJECT", M.inp.name, f"G+{M.above - 1}"), ("SHEET", f"{lv.name} plan", lv.fn),
        ("AREAS", f"{m2(lv.gross)} m²", f"{m2(lv.usable)} m² usable"),
        ("SHEET NO.", f"{idx} / {len(sh)}", "custom elements" if P.custom else "schematic")])
    return wrap_svg(body + extra, W, H, style, standalone)


def site_svg(M, style="blue", standalone=False):
    i = M.inp
    s = min(11, 560 / i.plotW)
    mx, my, bot = 54, 36, 96
    pw, pd_ = i.plotW * s, i.plotD * s
    W, H = pw + 2 * mx, pd_ + my + bot + 18
    g = f'<rect class="p-line" x="0" y="0" width="{_n(pw)}" height="{_n(pd_)}"/>'
    if i.basements > 0:
        g += f'<rect class="p-set" x="{_n(i.base * s)}" y="{_n(i.base * s)}" width="{_n(pw - 2 * i.base * s)}" height="{_n(pd_ - 2 * i.base * s)}"/>'
    fx, fy, fw, fh = i.side * s, i.rear * s, M.fp.L * s, M.fp.B * s
    g += f'<rect class="p-site" x="{_n(fx)}" y="{_n(fy)}" width="{_n(fw)}" height="{_n(fh)}"/>'
    g += (f'<text class="h" x="{_n(fx + fw / 2)}" y="{_n(fy + fh / 2 - 4)}">BUILDING FOOTPRINT</text>'
          f'<text class="a" x="{_n(fx + fw / 2)}" y="{_n(fy + fh / 2 + 10)}">{f1(M.fp.L)} × {f1(M.fp.B)} m · {m2(M.fp.area)} m²</text>')
    if i.basements > 0:
        g += f'<text class="t" x="{_n(pw / 2)}" y="{_n(i.base * s - 3)}">BASEMENT OUTLINE (DASHED)</text>'
    g += (f'<text class="d" x="{_n(pw / 2)}" y="{_n(pd_ + 14)}">{f1(i.plotW)} m</text>'
          f'<text class="d" transform="translate({_n(pw + 14)},{_n(pd_ / 2)}) rotate(90)">{f1(i.plotD)} m</text>'
          f'<text class="s" x="{_n(pw / 2)}" y="{_n(fy - 4)}">REAR SETBACK {f1(i.rear)} m</text>'
          f'<text class="s" x="{_n(pw / 2)}" y="{_n(fy + fh + min(i.front * s, 14))}">FRONT SETBACK {f1(i.front)} m</text>'
          f'<text class="s" transform="translate({_n(fx - 4)},{_n(pd_ / 2)}) rotate(-90)">SIDE {f1(i.side)} m</text>'
          f'<rect class="p-site" x="0" y="{_n(pd_ + 20)}" width="{_n(pw)}" height="9" style="fill-opacity:.3;stroke-width:.8"/>'
          f'<text class="t" x="{_n(pw / 2)}" y="{_n(pd_ + 27)}" style="fill:var(--p-ink)">STREET</text>')
    body = f'<g transform="translate({mx},{my})">{g}</g>'
    sh = sheet_list(M)
    extra = north_arrow(W - 30, 26) + title_block(W, H, mx, pw, [
        ("PROJECT", i.name, f"G+{M.above - 1}"), ("SHEET", "Site plan", f"Plot {m2(i.plotW * i.plotD)} m²"),
        ("COVERAGE", fp(M.cov), f"limit {i.maxCov}%"), ("SHEET NO.", f"1 / {len(sh)}", "schematic")])
    return wrap_svg(body + extra, W, H, style, standalone)


def section_svg(M, sel="all"):
    L = list(reversed(M.levels))
    rh = max(14, min(32, 360 / len(L)))
    top, x0, wd, W = 18, 112, 222, 430
    H = top + len(L) * rh + 22
    fs = 9 if rh < 22 else 11
    s = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {_n(H)}" width="{W}" height="{_n(H)}" '
         f'font-family="IBM Plex Sans, Arial, sans-serif"><rect width="{W}" height="{_n(H)}" fill="#F7F9F9"/>')
    gy = None
    for i, lv in enumerate(L):
        y = top + i * rh
        selected = sel == lv.id
        dim = sel != "all" and not selected
        ug = lv.number < 0
        if ug and gy is None:
            gy = y
        x = x0 - 12 if ug else x0
        w = wd + 24 if ug else wd
        op = 0.35 if dim else 1
        stroke = f' stroke="{COL["ink"]}" stroke-width="2"' if selected else ""
        ty = y + rh / 2 + fs / 3
        s += (f'<g opacity="{op}"><rect x="{_n(x)}" y="{_n(y + 1.5)}" width="{_n(w)}" height="{_n(rh - 3)}" rx="2" fill="{COL[lv.zone]}"{stroke}/>'
              f'<text font-size="{fs}" x="{x0 - 18}" y="{_n(ty)}" text-anchor="end" fill="{COL["ink"]}">{esc(lv.name)}</text>'
              f'<text font-size="{fs}" x="{_n(x + 8)}" y="{_n(ty)}" fill="#FFFFFF" font-weight="600">{esc(lv.fn.upper())}</text>'
              f'<text font-size="{fs}" x="{W - 4}" y="{_n(ty)}" text-anchor="end" fill="{COL["muted"]}">{"+" if lv.elev >= 0 else "−"}{abs(lv.elev):.1f}</text></g>')
    if gy is None:
        gy = top + len(L) * rh
    s += (f'<line x1="8" x2="{W - 8}" y1="{_n(gy)}" y2="{_n(gy)}" stroke="{COL["ink"]}" stroke-dasharray="4 3"/>'
          f'<text x="{W - 8}" y="{_n(gy - 4)}" text-anchor="end" font-size="9" fill="{COL["ink"]}">GL</text></svg>')
    return s


def export_svg(svg: str) -> str:
    """Standalone SVG file content (adds the XML header)."""
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + svg


# ============================================================================
#  charts
# ============================================================================
def floor_bars_fig(M):
    lv = list(reversed(M.levels))
    names = [l.name for l in lv]
    series = [("Commercial usable", COL["commercial"], [l.usable if l.zone == "commercial" else 0 for l in lv]),
              ("Residential usable", COL["residential"], [l.usable if l.zone == "residential" else 0 for l in lv]),
              ("Parking bays", COL["parking"], [l.usable if l.zone == "parking" else 0 for l in lv]),
              ("Circulation", COL["circ"], [l.circ for l in lv]), ("Service", COL["svc"], [l.svc for l in lv])]
    fig = go.Figure()
    for n, c, v in series:
        fig.add_bar(y=names, x=[round(a, 1) for a in v], name=n, orientation="h", marker_color=c)
    fig.update_layout(barmode="stack", height=max(260, 34 * len(lv) + 110), margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="m²", yaxis=dict(autorange="reversed"), legend=dict(orientation="h", y=-0.2))
    return fig


def simple_bars(labels, values, color, unit="m²", height=None):
    fig = go.Figure(go.Bar(y=labels, x=[round(v, 1) for v in values], orientation="h", marker_color=color,
                           text=[f"{f1(v)} {unit}" for v in values], textposition="auto"))
    fig.update_layout(height=height or max(180, 40 * len(labels) + 60), margin=dict(l=10, r=10, t=10, b=10),
                      yaxis=dict(autorange="reversed"), xaxis_title=unit)
    return fig


# ============================================================================
#  3D model (Plotly)
# ============================================================================
class Mesh:
    TRI = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 5, 1), (0, 4, 5), (1, 6, 2), (1, 5, 6), (2, 7, 3), (2, 6, 7), (3, 4, 0), (3, 7, 4)]

    def __init__(self):
        self.x, self.y, self.z, self.i, self.j, self.k = [], [], [], [], [], []

    def box(self, x1, z1, y1, x2, z2, y2):
        """x, elevation, plan-y. Plan y grows southwards, so it is flipped for Plotly."""
        if x2 - x1 < 1e-4 or z2 - z1 < 1e-4 or y2 - y1 < 1e-4:
            return
        n = len(self.x)
        ya, yb = -y2, -y1
        self.x += [x1, x2, x2, x1, x1, x2, x2, x1]
        self.y += [ya, ya, yb, yb, ya, ya, yb, yb]
        self.z += [z1, z1, z1, z1, z2, z2, z2, z2]
        for a, b, c in self.TRI:
            self.i.append(n + a)
            self.j.append(n + b)
            self.k.append(n + c)

    def trace(self, color, opacity=1.0):
        if not self.x:
            return None
        return go.Mesh3d(x=self.x, y=self.y, z=self.z, i=self.i, j=self.j, k=self.k, color=color, opacity=opacity,
                         flatshading=True, hoverinfo="skip", showlegend=False,
                         lighting=dict(ambient=.62, diffuse=.85, specular=.05))


def level_offset(M, lv):
    o = M.inp
    if M.kind == "house":
        return o.side, o.rear
    if lv.number < 0:
        return o.base, o.base
    return o.side, o.rear


def model_3d(M, cut="all", view="iso"):
    levels = M.levels
    if cut != "all":
        ci = next((k for k, l in enumerate(levels) if l.id == cut), len(levels) - 1)
        shown = levels[:ci + 1]
    else:
        shown = levels
    meshes = {}

    def mesh(key):
        return meshes.setdefault(key, Mesh())

    inp = M.inp
    W, D = inp.plotW, inp.plotD
    low = min([0] + [l.elev for l in levels]) - 0.6
    mesh(("#93B67E", 1)).box(-6, low, -6, W + 6, -0.02, D + 6)
    mesh(("#3B4146", 1)).box(-6, -0.02, D + 2, W + 6, 0.0, D + 8)
    lines_x, lines_y, lines_z = [], [], []
    bay_x, bay_y, bay_z = [], [], []
    labels = [[], [], [], []]

    def seg(pts, e, off):
        for (x, y) in pts:
            lines_x.append(x + off[0])
            lines_y.append(-(y + off[1]))
            lines_z.append(e)
        lines_x.append(None)
        lines_y.append(None)
        lines_z.append(None)

    for lv in shown:
        P, e, H = lv.plan, lv.elev, lv.h
        ox, oz = level_offset(M, lv)
        Hw = H - 0.2
        park = lv.zone == "parking"
        mesh((COL[lv.zone], 1)).box(ox, e - 0.2, oz, ox + P.L, e, oz + P.B)
        t = 0.24
        ph = 1.1 if (park and lv.number >= 0) else Hw
        wk = ("#E4DFD3", 0.35)
        mesh(wk).box(ox, e, oz, ox + P.L, e + ph, oz + t)
        mesh(wk).box(ox, e, oz + P.B - t, ox + P.L, e + ph, oz + P.B)
        mesh(wk).box(ox, e, oz, ox + t, e + ph, oz + P.B)
        mesh(wk).box(ox + P.L - t, e, oz, ox + P.L, e + ph, oz + P.B)
        if not park:
            c = P.core
            mesh(("#A3A8AC", 1)).box(ox + c.x, e, oz + c.y, ox + c.x + c.w, e + Hw, oz + c.y + c.h) if M.kind != "house" else None
            for r in P.rooms:
                seg([(r.x, r.y), (r.x + r.w, r.y), (r.x + r.w, r.y + r.h), (r.x, r.y + r.h), (r.x, r.y)], e + 0.05, (ox, oz))
                mesh(wk).box(ox + r.x, e, oz + r.y, ox + r.x + r.w, e + Hw * 0.55, oz + r.y + 0.08)
            for r in P.extras or []:
                mesh(("#A3A8AC", 1)).box(ox + r.x, e, oz + r.y, ox + r.x + r.w, e + 0.03, oz + r.y + r.h)
        else:
            for b in P.bays:
                if b.ev:
                    mesh(("#2FAE6E", 1)).box(ox + b.x + .08, e, oz + b.y + .08, ox + b.x + BAY_W - .08, e + .04, oz + b.y + BAY_D - .08)
                x0_, y0_ = b.x, b.y
                for (px, py) in [(x0_, y0_), (x0_ + BAY_W, y0_), (x0_ + BAY_W, y0_ + BAY_D), (x0_, y0_ + BAY_D), (x0_, y0_)]:
                    bay_x.append(px + ox)
                    bay_y.append(-(py + oz))
                    bay_z.append(e + 0.05)
                bay_x.append(None)
                bay_y.append(None)
                bay_z.append(None)
        el = P.el or O()
        for s_ in el.get("stairs") or []:
            mesh(("#B7AE9E", 1)).box(ox + s_.x, e, oz + s_.y, ox + s_.x + s_.w, e + Hw * 0.5, oz + s_.y + s_.h)
        for s_ in el.get("lifts") or []:
            mesh(("#8A949B", 1)).box(ox + s_.x, e, oz + s_.y, ox + s_.x + s_.w, e + Hw, oz + s_.y + s_.h)
        for b in el.get("balconies") or []:
            mesh(("#CFC9BD", 1)).box(ox + b.x, e - .2, oz + b.y, ox + b.x + b.w, e, oz + b.y + b.h)
            mesh(("#9CC9E6", 0.4)).box(ox + b.x, e, oz + b.y, ox + b.x + b.w, e + 1.0, oz + b.y + b.h)
    top = shown[-1]
    if cut == "all":
        ox, oz = level_offset(M, top)
        mesh(("#8E9498", 1)).box(ox - .1, top.elev + top.h - 0.2, oz - .1, ox + top.plan.L + .1, top.elev + top.h, oz + top.plan.B + .1)
    else:
        ox, oz = level_offset(M, top)
        for r in top.plan.rooms:
            labels[0].append(ox + r.x + r.w / 2)
            labels[1].append(-(oz + r.y + r.h / 2))
            labels[2].append(top.elev + 0.4)
            labels[3].append(r.name)
    fig = go.Figure()
    for (color, op), m in meshes.items():
        tr = m.trace(color, op)
        if tr is not None:
            fig.add_trace(tr)
    if lines_x:
        fig.add_trace(go.Scatter3d(x=lines_x, y=lines_y, z=lines_z, mode="lines", line=dict(color="#12222B", width=2), hoverinfo="skip", showlegend=False))
    if bay_x:
        fig.add_trace(go.Scatter3d(x=bay_x, y=bay_y, z=bay_z, mode="lines", line=dict(color="#FFFFFF", width=1), hoverinfo="skip", showlegend=False))
    if labels[0]:
        fig.add_trace(go.Scatter3d(x=labels[0], y=labels[1], z=labels[2], mode="text", text=labels[3], textfont=dict(size=10, color="#12222B"), hoverinfo="skip", showlegend=False))
    for name, c in (("Commercial", COL["commercial"]), ("Residential", COL["residential"]), ("Parking", COL["parking"]), ("EV bay", "#2FAE6E")):
        fig.add_trace(go.Scatter3d(x=[None], y=[None], z=[None], mode="markers", marker=dict(size=8, color=c), name=name))
    cams = {"iso": dict(eye=dict(x=1.35, y=-1.55, z=0.95)), "top": dict(eye=dict(x=0.0, y=0.001, z=2.6), up=dict(x=0, y=1, z=0)),
            "front": dict(eye=dict(x=0.0, y=-2.5, z=0.35)), "side": dict(eye=dict(x=2.5, y=0.0, z=0.35))}
    fig.update_layout(scene=dict(aspectmode="data", camera=cams.get(view, cams["iso"]),
                                 xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False),
                                 bgcolor="#DCE8F2"),
                      margin=dict(l=0, r=0, t=0, b=0), height=620, legend=dict(orientation="h", y=0.02, x=0.02))
    return fig


# ============================================================================
#  filters, schedules and report data
# ============================================================================
def in_band(a, b, kind):
    cut = (80, 130) if kind == "r" else (150, 250)
    return b == "all" or (b == "s" and a < cut[0]) or (b == "m" and cut[0] <= a <= cut[1]) or (b == "l" and a > cut[1])


def match(s, flt):
    if flt.floor != "all" and s.level != flt.floor:
        return False
    if flt.zone != "all" and s.zone != flt.zone:
        return False
    if flt.type != "all" and s.type != flt.type:
        return False
    if flt.rband != "all" and s.zone == "residential" and not (s.kind == "space" and in_band(s.area, flt.rband, "r")):
        return False
    if flt.cband != "all" and s.zone == "commercial" and not (s.kind == "space" and in_band(s.area, flt.cband, "c")):
        return False
    if flt.ptype != "all" and s.zone == "parking":
        if s.kind != "park":
            return False
        if (flt.ptype == "ev") != (s.type == "EV bay"):
            return False
    return True


def lv_name(M, id_):
    l = next((x for x in M.levels if x.id == id_), None)
    return l.name if l else id_


def filter_note(M, flt):
    o = []
    if flt.floor != "all":
        o.append("Floor: " + lv_name(M, flt.floor))
    if flt.zone != "all":
        o.append("Zone: " + ZN[flt.zone])
    if flt.type != "all":
        o.append("Type: " + flt.type)
    if flt.ptype != "all":
        o.append("Parking: " + ("EV charging" if flt.ptype == "ev" else "Conventional"))
    if flt.rband != "all":
        o.append("Residential size filter")
    if flt.cband != "all":
        o.append("Commercial size filter")
    return ("Filters applied: " + " · ".join(o)) if o else ""


def sgn(l):
    return ("+" if l.elev >= 0 else "−") + f"{abs(l.elev):.1f}"


def schedules(M, flt):
    fl = list(reversed(M.levels))
    ev = [b for b in M.bays if b.ev]
    park_rows = []
    for l in reversed([l for l in M.levels if l.zone == "parking"]):
        b = [x for x in M.bays if x.level == l.id]
        park_rows.append([l.name, len(b), sum(1 for x in b if x.ev), sum(1 for x in b if x.alloc), sum(1 for x in b if not x.alloc), m2(len(b) * BAY_AREA)])
    park_rows.append(["Total", M.nb, M.nev, M.nal, M.nb - M.nal, m2(M.bayArea)])
    return {
        "rooms": O(t="Room schedule", cols=["ID", "Room", "Level", "Zone", "Type", "Area m²"],
                   rows=[[s.id, s.name, lv_name(M, s.level), ZN[s.zone], s.type, f1(s.area)] for s in M.spaces if match(s, flt)]),
        "floors": O(t="Floor-wise area schedule", cols=["Level", "Zone", "Elevation m", "Gross m²", "Usable m²", "Circulation m²", "Service m²", "SUR %"],
                    rows=[[l.name, ZN[l.zone], sgn(l), m2(l.gross), m2(l.usable), m2(l.circ), m2(l.svc), f1(pct(l.usable, l.gross))] for l in fl]),
        "elements": O(t="Element counts", cols=["Category", "Count", "Classification"], rows=[[e.k, fmt(e.n), e.d] for e in M.elements]),
        "parking": O(t="Parking schedule", cols=["Level", "Bays", "EV", "Allocated", "Available", "Area m²"], rows=park_rows),
        "ev": O(t="EV charging schedule", cols=["Bay ID", "Level", "Status", "Element"],
                rows=[[b.id, lv_name(M, b.level), "Allocated" if b.alloc else "Available", "Charging point"] for b in ev]),
        "levels": O(t="Building-level information", cols=["Level", "Elevation m", "Floor-to-floor m", "Function"], rows=[[l.name, sgn(l), f1(l.h), l.fn] for l in fl]),
        "sur": O(t="Space utilization statistics", cols=["Functional area", "A_f m²", "A_t m²", "SUR %"],
                 rows=[[r.name, m2(r.af), m2(r.at), "—" if r.sur is None else f1(r.sur)] for r in M.sur]),
        "verify": O(t="Configuration verification results", cols=["Requirement", "State", "Finding"], rows=[[c.label, ST[c.state], c.note] for c in M.checks]),
    }


SCHED_LABEL = {"rooms": "Rooms", "floors": "Floor areas", "elements": "Elements", "parking": "Parking", "ev": "EV charging",
               "levels": "Levels", "sur": "Utilization", "verify": "Verification"}


def to_csv(cols, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()


def report_data(M):
    if M.kind == "house":
        i = M.inp
        return [
            ("Project Overview", [("Project", i.name), ("Plot area", f"{m2(i.plotW * i.plotD)} m²"), ("Floors", f"G+{M.above - 1}"), ("Built-up area", f"{m2(M.tot.gross)} m²"), ("Overall status", M.status.title)]),
            ("Requirements and Provision", [(r.k, (f"target {m2(r.target)} {r.unit}, " if r.target > 0 else "no target, ") + f"provided {m2(r.have)} {r.unit} ({ST[r.state]})".replace(" )", ")")) for r in M.req]),
            ("Floor-wise Analysis", [(l.name, f"{len(l.plan.rooms)} rooms, {m2(l.gross)} m² gross, {m2(l.usable)} m² in rooms") for l in M.levels]),
            ("Room Schedule", [(f"{l.name}: {r.name}", f"{f1(r.area)} m²") for l in M.levels for r in l.plan.rooms]),
            ("Openings and Elements", [(e.k, fmt(e.n)) for e in M.elements]),
            ("Configuration Verification", [(c.label, f"{ST[c.state]}. {c.note}") for c in M.checks]),
            ("Design Status", [(r[0], r[1]) for r in M.status.rows])]
    z, i, U = M.zone, M.inp, M.sur
    return [
        ("Project Overview", [("Project", i.name), ("Plot area", f"{m2(i.plotW * i.plotD)} m²"), ("Total levels", len(M.levels)), ("Built-up area", f"{m2(M.tot.gross)} m²"), ("Overall status", M.status.title)]),
        ("Requirements and Provision", [(r.k, (f"target {m2(r.target)} {r.unit}, " if r.target > 0 else "no target, ") + f"provided {m2(r.have)} {r.unit} ({ST[r.state]})") for r in M.req]),
        ("Building Configuration", [("Basement levels", i.basements), ("Commercial floors", z.commercial.floors), ("Residential floors", z.residential.floors), ("Height above ground", f"{f1(M.height)} m")]),
        ("Floor-wise Analysis", [(l.name, f"{l.fn}, {m2(l.gross)} m² gross, {m2(l.usable)} m² usable") for l in reversed(M.levels)]),
        ("Commercial Space Summary", [("Spaces", len(M.cspaces)), ("Usable area", f"{m2(z.commercial.usable)} m²"), ("Common circulation", f"{m2(z.commercial.circ)} m²")]),
        ("Residential Space Summary", [("Floors", z.residential.floors), ("Units", len(M.units)), ("Usable area", f"{m2(z.residential.usable)} m²"), ("Circulation", f"{m2(z.residential.circ)} m²")]),
        ("Basement Parking Summary", [("Total bays", M.nb), ("Allocated", M.nal), ("Available", M.nb - M.nal), ("Parking area", f"{m2(M.bayArea)} m²"), ("Utilization", fp(pct(M.nal, M.nb)))]),
        ("EV Charging Summary", [("EV bays", M.nev), ("Share of parking", fp(pct(M.nev, M.nb))), ("Conventional bays", M.nb - M.nev)]),
        ("Space Utilization", [(r.name, f"{fp(r.sur)} ({m2(r.af)} of {m2(r.at)} m²)") for r in U]),
        ("BIM Element Summary", [(e.k, fmt(e.n)) for e in M.elements]),
        ("Configuration Verification", [(c.label, f"{ST[c.state]}. {c.note}") for c in M.checks]),
        ("Design Status", [(r[0], r[1]) for r in M.status.rows])]


def report_text(M):
    return f"# {M.inp.name}\nAutomated design report\n\n" + "\n\n".join(
        f"## {n + 1}. {s[0]}\n" + "\n".join(f"- {r[0]}: {r[1]}" for r in s[1]) for n, s in enumerate(report_data(M)))


# ============================================================================
#  Streamlit UI
# ============================================================================
def _ver():
    try:
        return tuple(int(x) for x in st.__version__.split(".")[:2])
    except Exception:  # pragma: no cover
        return (1, 0)


SW = {"width": "stretch"} if _ver() >= (1, 52) else {"use_container_width": True}

CSS = """
<style>
.block-container{padding-top:1.4rem;max-width:1300px}
.hdr h1{font-family:'Barlow Condensed','Arial Narrow',sans-serif;text-transform:uppercase;letter-spacing:.01em;margin:0;font-size:2rem;line-height:1.1}
.hdr p{margin:.15rem 0 0;opacity:.7;font-size:.9rem}
.badge{display:inline-block;border:1px solid rgba(128,128,128,.55);border-radius:4px;padding:1px 8px;font-size:.7rem;letter-spacing:.06em;opacity:.8;margin-left:8px;vertical-align:middle}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin:.4rem 0 .8rem}
.kpi{border:1px solid rgba(128,128,128,.35);border-radius:6px;padding:10px 12px;background:rgba(128,128,128,.07);display:flex;flex-direction:column;gap:2px}
.kpi .kl{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;opacity:.7}
.kpi .kv{font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;font-size:1.45rem;font-weight:500}
.kpi .kv small{font-size:.75rem;margin-left:4px;opacity:.7}
.kpi .ks{font-size:.78rem;opacity:.7}
.banner{border-left:6px solid;border-radius:6px;padding:12px 16px;margin:.2rem 0 .8rem}
.banner h2{margin:0;font-family:'Barlow Condensed','Arial Narrow',sans-serif;text-transform:uppercase;font-size:1.6rem}
.banner .bl{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;opacity:.75}
.banner p{margin:.3rem 0 0;font-size:.9rem}
.banner.ok{border-color:#1F7F46;background:rgba(31,127,70,.14)}
.banner.review{border-color:#C98A00;background:rgba(201,138,0,.15)}
.banner.missing{border-color:#B8322A;background:rgba(184,50,42,.14)}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(26px,1fr));gap:3px;margin:.2rem 0 .8rem}
.bay{height:22px;border:1px solid rgba(128,128,128,.5);border-radius:2px;font-size:9px;display:flex;align-items:center;justify-content:center;background:rgba(128,128,128,.1)}
.bay.alloc{background:#5C6E7E;color:#fff}
.bay.ev{background:#2F6FE4;color:#fff;border-color:#2F6FE4}
.bay.off{opacity:.2}
.legend span{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:.8rem}
.legend i{width:12px;height:12px;border-radius:2px;display:inline-block;background:var(--c)}
.plabel{font-weight:600;font-size:.85rem;margin:.4rem 0 .2rem}
</style>
"""

EDEF = O(
    doors=O(l="Doors", s="door", p="D", f=[("x", "X m", None), ("y", "Y m", None), ("len", "Width m", None),
                                           ("o", "Wall", [("h", "Horizontal"), ("v", "Vertical")]),
                                           ("sw", "Swings", [(-1, "Up / left"), (1, "Down / right")])]),
    windows=O(l="Windows", s="window", p="W", f=[("x", "X m", None), ("y", "Y m", None), ("len", "Width m", None),
                                                 ("o", "Wall", [("h", "Horizontal"), ("v", "Vertical")])]),
    stairs=O(l="Stairs", s="stair", p="S", f=[("x", "X m", None), ("y", "Y m", None), ("w", "Width m", None), ("h", "Depth m", None)]),
    lifts=O(l="Elevators", s="elevator", p="E", f=[("x", "X m", None), ("y", "Y m", None), ("w", "Width m", None), ("h", "Depth m", None)]),
    balconies=O(l="Balconies", s="balcony", p="B", f=[("x", "X m", None), ("y", "Y m", None), ("w", "Width m", None), ("h", "Depth m", None)]),
)

TABSET = {
    ("building", "exec"): [("summary", "Summary"), ("blueprints", "Blueprints"), ("explore", "3D model"), ("report", "Report")],
    ("building", "tech"): [("requirements", "Requirements"), ("overview", "Overview"), ("blueprints", "Blueprints"), ("explore", "3D model"),
                           ("floors", "Floors"), ("commercial", "Commercial"), ("residential", "Residential"), ("parking", "Parking & EV"),
                           ("utilization", "Utilization"), ("verification", "Verification"), ("circulation", "Circulation"),
                           ("elements", "BIM elements"), ("schedules", "Schedules"), ("report", "Report")],
    ("house", "exec"): [("summary", "Summary"), ("blueprints", "Blueprints"), ("explore", "3D model"), ("report", "Report")],
    ("house", "tech"): [("requirements", "Requirements"), ("overview", "Overview"), ("blueprints", "Blueprints"), ("explore", "3D model"),
                        ("rooms", "Rooms"), ("elements", "Doors, stairs and more"), ("verification", "Verification"), ("report", "Report")],
}


# ---------------------------------------------------------------- state ----
def init_state():
    ss = st.session_state
    if "inp" not in ss:
        ss.inp = clone(PRESETS["ref"])
    if "hinp" not in ss:
        ss.hinp = clone(HPRESETS["h3"])
    ss.setdefault("kind", None)
    ss.setdefault("mode", "tech")
    ss.setdefault("ver", 0)
    ss.setdefault("fit_msg", "")
    ss.setdefault("ed_msg", "")
    ss.setdefault("style", "blue")


def cur():
    ss = st.session_state
    return ss.hinp if ss.kind == "house" else ss.inp


def bump():
    st.session_state.ver += 1


# -------------------------------------------------------------- helpers ----
def kpi_html(items):
    out = []
    for it in items:
        l, v = it[0], it[1]
        s = it[2] if len(it) > 2 else ""
        u = it[3] if len(it) > 3 else ""
        unit = f"<small>{esc(u)}</small>" if u else ""
        sub = f'<span class="ks">{esc(s)}</span>' if s else ""
        out.append(f'<div class="kpi"><span class="kl">{esc(l)}</span><span class="kv">{v}{unit}</span>{sub}</div>')
    return '<div class="kpis">' + "".join(out) + "</div>"


def kpis(items):
    st.markdown(kpi_html(items), unsafe_allow_html=True)


def banner(state, title, text, label="Overall project status"):
    st.markdown(f'<div class="banner {state}"><div class="bl">{esc(label)}</div><h2>{esc(title)}</h2><p>{esc(text)}</p></div>',
                unsafe_allow_html=True)


def show_df(cols, rows, key=None, height=None):
    d = pd.DataFrame([[("—" if c is None else c) for c in r] for r in rows], columns=cols)
    kw = dict(hide_index=True, **SW)
    if height:
        kw["height"] = height
    st.dataframe(d, **kw)


def show_svg(svg):
    st.image(svg, **SW)


def empty(t):
    st.info(t)


def req_rows(M):
    out = []
    for r in M.req:
        tgt = (f"{m2(r.target)} {r.unit}".strip()) if r.target > 0 else "No target"
        out.append([r.k, tgt, f"{m2(r.have)} {r.unit}".strip(), chip(r.state)])
    return out


def status_panel(M):
    show_df(["Check", "Status"], [[r[0], f"{EMOJI[r[2]]} {r[1]}"] for r in M.status.rows])


def editor(key, base_df, **kw):
    """data_editor fed with a frozen base frame, so edits are always applied to the same data."""
    ss = st.session_state
    bk = "base::" + key
    if bk not in ss:
        ss[bk] = base_df
    return st.data_editor(ss[bk], key=key, hide_index=True, **SW, **kw)


def num(obj, k, label, mn, mx, step, unit="", prefix="w"):
    ss = st.session_state
    typ = float if any(isinstance(x, float) for x in (mn, mx, step)) else int
    cur_v = obj.mix[int(k[3:])] if k.startswith("mix") else obj[k]
    cur_v = min(max(typ(cur_v), typ(mn)), typ(mx))
    v = st.number_input(f"{label}{f' ({unit})' if unit else ''}", min_value=typ(mn), max_value=typ(mx), value=cur_v,
                        step=typ(step), key=f"{prefix}_{ss.kind}_{k}_{ss.ver}")
    if k.startswith("mix"):
        obj.mix[int(k[3:])] = v
    else:
        obj[k] = v
    return v


def get_flt():
    ss = st.session_state
    return O(floor=ss.get("f_floor", "all"), zone=ss.get("f_zone", "all"), type=ss.get("f_type", "all"),
             ptype=ss.get("f_ptype", "all"), rband=ss.get("f_rband", "all"), cband=ss.get("f_cband", "all"))


def reset_filters():
    for k in ("floor", "zone", "type", "ptype", "rband", "cband"):
        st.session_state[f"f_{k}"] = "all"


# ------------------------------------------------------------- landing ----
def landing():
    st.markdown('<div class="hdr"><h1>Urban Mixed-Use BIM Dashboard</h1><p>Choose House or Building to begin</p></div>', unsafe_allow_html=True)
    st.markdown("### What are you designing?")
    st.caption("Pick a project type. Every drawing and number is generated from the requirements you enter, and you can place doors, "
               "windows, stairs, elevators and balconies yourself on every floor plan.")
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown("#### 🏠 House")
        st.markdown("- Plot, setbacks and one to four floors\n- Choose every room and the area you want for it\n"
                    "- Floor plan for each floor, checked against your bedroom, bathroom and area targets")
        if st.button("Design a house", key="pick_house", type="primary"):
            st.session_state.kind = "house"
            st.rerun()
    with c2, st.container(border=True):
        st.markdown("#### 🏢 Building")
        st.markdown("- Residential, commercial, office or mixed-use, up to 30 floors\n- Basement parking, EV charging, area targets and auto-fit\n"
                    "- Blueprint for every level, with space utilization, schedules and a report")
        if st.button("Design a building", key="pick_building", type="primary"):
            st.session_state.kind = "building"
            st.rerun()


# --------------------------------------------------------- requirements ----
def snapshot(o):
    import json
    return json.dumps(o, sort_keys=True, default=str)


def live_output(M):
    s = M.status
    gaps = [c for c in M.checks if c.state != "ok"]
    banner(s.overall, s.title, ". ".join(f"{g.label}: {g.note}" for g in gaps) if gaps else "Every checked requirement is met by the plans.", "Result")
    if M.kind == "house":
        i = M.inp
        kpis([("Floors", f"G+{M.above - 1}", f"{f1(M.height)} m high"), ("Built-up area", m2(M.tot.gross), "all floors", "m²"),
              ("Footprint", m2(M.fp.area), f"{fp(M.cov)} of plot", "m²"), ("Bedrooms", str(M.beds), f"target {i.tBeds}" if i.tBeds else ""),
              ("Bathrooms", str(M.baths), f"target {i.tBaths}" if i.tBaths else ""),
              ("Rooms", str(sum(len(l.plan.rooms) for l in M.levels)), f"{m2(M.tot.usable)} m² in rooms")])
    else:
        kpis([("Floors", f"G+{M.above - 1}", f"{M.inp.basements} basement{'' if M.inp.basements == 1 else 's'} · {f1(M.height)} m high"),
              ("Built-up area", m2(M.tot.gross), "all levels", "m²"), ("Footprint", m2(M.fp.area), f"{fp(M.cov)} coverage", "m²"),
              ("Dwellings", str(len(M.units)), f"{m2(M.zone.residential.usable)} m² usable")])
    show_df(["Requirement", "Target", "Provided", "State"], req_rows(M))
    show_svg(section_svg(M))


def v_requirements_building(M):
    ss = st.session_state
    inp = ss.inp
    before = snapshot(inp)
    st.subheader("Your requirements")
    st.caption("Describe the site, the floors, what each floor is used for and how much area you need. "
               "Every other tab, including the blueprints for each floor, updates from this page.")
    cols = st.columns(len(PRESETS))
    for c, (k, lab) in zip(cols, PRESET_LABEL.items()):
        if c.button(lab, key=f"pre_{k}", **SW):
            ss.inp = clone(PRESETS[k])
            ss.fit_msg = ""
            bump()
            st.rerun()
    left, right = st.columns([1.1, 1])
    with left:
        with st.container(border=True):
            st.markdown("**1. Site** · plot and setbacks")
            inp.name = st.text_input("Project name", value=inp.name, key=f"w_name_{ss.ver}").strip() or "Untitled project"
            a, b = st.columns(2)
            with a:
                num(inp, "plotW", "Plot width", 30, 200, 1, "m")
                num(inp, "front", "Front setback", 0.0, 15.0, 0.5, "m")
                num(inp, "side", "Side setback", 0.0, 12.0, 0.5, "m")
                num(inp, "maxCov", "Max coverage", 20, 100, 1, "%")
            with b:
                num(inp, "plotD", "Plot depth", 24, 200, 1, "m")
                num(inp, "rear", "Rear setback", 0.0, 15.0, 0.5, "m")
                num(inp, "base", "Basement setback", 0.0, 6.0, 0.5, "m")
        with st.container(border=True):
            st.markdown("**2. Building stack** · floors and use")
            num(inp, "basements", "Basement levels", 0, 4, 1)
            use_labels = [FUNCS[k].label for k in FUNCS]
            l2k = {FUNCS[k].label: k for k in FUNCS}
            c1, c2, c3 = st.columns([2, 1, 2])
            add_fn = c1.selectbox("Use to add", use_labels, index=2, key=f"addfn_{ss.ver}")
            add_n = c2.number_input("Floors", 1, 30, 1, key=f"addn_{ss.ver}")
            c3.write("")
            if c3.button("Add floors on top", key="b_add"):
                n = max(1, min(30 - len(inp.floors), int(add_n)))
                inp.floors += [l2k[add_fn]] * n
                bump()
                st.rerun()
            d1, d2, d3 = st.columns([2, 2, 1])
            all_fn = d1.selectbox("Use for all floors", use_labels, index=2, key=f"allfn_{ss.ver}")
            d2.write("")
            if d2.button("Set every floor", key="b_setall"):
                inp.floors = [l2k[all_fn]] * len(inp.floors)
                bump()
                st.rerun()
            d3.write("")
            if d3.button("Remove top", key="b_rm", disabled=len(inp.floors) <= 1):
                inp.floors = inp.floors[:-1]
                bump()
                st.rerun()
            st.caption(f"{len(inp.floors)} above-ground floor{'s' if len(inp.floors) > 1 else ''} (G+{len(inp.floors) - 1}). "
                       "Choose the use of each floor below, roof first.")
            rows = [[ord_(n).replace(" Floor", ""), FUNCS[inp.floors[n]].label] for n in range(len(inp.floors) - 1, -1, -1)]
            ed = editor(f"floors_{ss.ver}", pd.DataFrame(rows, columns=["Floor", "Use"]), num_rows="fixed",
                        column_config={"Floor": st.column_config.TextColumn(disabled=True),
                                       "Use": st.column_config.SelectboxColumn(options=use_labels, required=True)})
            inp.floors = [l2k.get(u, "res") for u in list(ed["Use"])][::-1]
        with st.container(border=True):
            st.markdown("**3. Uses and mix** · layout rules")
            a, b = st.columns(2)
            with a:
                num(inp, "commSpaces", "Shops per retail floor", 4, 16, 1)
                num(inp, "mix0", "1BHK weight", 0, 100, 1, "%")
                num(inp, "mix2", "3BHK weight", 0, 100, 1, "%")
                num(inp, "ev", "EV bays to equip", 0, 200, 1)
            with b:
                num(inp, "officeSpaces", "Suites per office floor", 4, 16, 1)
                num(inp, "mix1", "2BHK weight", 0, 100, 1, "%")
                st.write("")
                num(inp, "allocPct", "Bays allocated", 0, 100, 1, "%")
            st.caption("Dwelling sizes are about 75, 112 and 165 m². The weights set how many of each type are drawn on a residential floor.")
        with st.container(border=True):
            st.markdown("**4. Area targets** · what you need")
            st.caption("Enter the usable area and bay counts you need. Use 0 for no target.")
            a, b = st.columns(2)
            with a:
                num(inp, "tComm", "Commercial usable area", 0, 60000, 100, "m²")
                num(inp, "tBays", "Parking bays needed", 0, 1500, 5)
            with b:
                num(inp, "tRes", "Residential usable area", 0, 100000, 100, "m²")
                num(inp, "tEv", "EV bays needed", 0, 300, 1)
            fn_opts = ["retail", "office"]
            inp.commFn = st.selectbox("Commercial use for auto-fit", fn_opts, index=fn_opts.index(inp.commFn) if inp.commFn in fn_opts else 0,
                                      format_func=lambda k: FUNCS[k].label, key=f"commfn_{ss.ver}")
            if st.button("Fit floors and basements to my targets", type="primary", key="b_fit"):
                ss.fit_msg = fit_building(inp)
                bump()
                st.rerun()
            if ss.fit_msg:
                st.success(ss.fit_msg)
    if snapshot(inp) != before:
        st.rerun()
    with right:
        live_output(build(inp))


def room_labels():
    return [ROOMS[k].l for k in ROOMS], {ROOMS[k].l: k for k in ROOMS}


def v_requirements_house(M):
    ss = st.session_state
    h = ss.hinp
    before = snapshot(h)
    labels, l2k = room_labels()
    st.subheader("Your house")
    st.caption("Describe the plot, the floors and the rooms you want. The floor plans and every number on the other tabs update from this page. "
               "Then open Blueprints to place doors, windows, stairs, elevators and balconies yourself.")
    cols = st.columns(len(HPRESETS))
    for c, (k, lab) in zip(cols, HPRESET_LABEL.items()):
        if c.button(lab, key=f"hpre_{k}", **SW):
            ss.hinp = clone(HPRESETS[k])
            bump()
            st.rerun()
    left, right = st.columns([1.1, 1])
    with left:
        with st.container(border=True):
            st.markdown("**1. Site** · plot and setbacks")
            h.name = st.text_input("Project name", value=h.name, key=f"w_hname_{ss.ver}").strip() or "Untitled project"
            a, b = st.columns(2)
            with a:
                num(h, "plotW", "Plot width", 6.0, 100.0, 0.5, "m")
                num(h, "front", "Front setback", 0.0, 10.0, 0.5, "m")
                num(h, "side", "Side setback", 0.0, 8.0, 0.5, "m")
            with b:
                num(h, "plotD", "Plot depth", 8.0, 100.0, 0.5, "m")
                num(h, "rear", "Rear setback", 0.0, 10.0, 0.5, "m")
                num(h, "maxCov", "Max coverage", 20, 100, 1, "%")
        with st.container(border=True):
            st.markdown("**2. Floors and rooms** · room program")
            nf = st.selectbox("Floors above ground", [1, 2, 3, 4], index=len(h.floors) - 1,
                              format_func=lambda n: "Ground floor only" if n == 1 else f"G+{n - 1}", key=f"nfl_{ss.ver}")
            if nf != len(h.floors):
                while len(h.floors) < nf:
                    h.floors.append(clone(UPPER3))
                while len(h.floors) > nf:
                    h.floors.pop()
                bump()
                st.rerun()
            st.caption("List the rooms on each floor with the area you want. Areas are scaled to fit the footprint. "
                       "The passage and stair hall are added automatically. Use the + row to add a room.")
            for f in range(len(h.floors) - 1, -1, -1):
                rows = h.floors[f]
                st.markdown(f"<div class='plabel'>{ord_(f)} · {len(rows)} rooms · {m2(sum(r.a for r in rows))} m² requested</div>", unsafe_allow_html=True)
                base = pd.DataFrame([[ROOMS[r.t].l, float(r.a)] for r in rows], columns=["Room", "Area m²"])
                ed = editor(f"hrooms_{f}_{ss.ver}", base, num_rows="dynamic",
                            column_config={"Room": st.column_config.SelectboxColumn(options=labels, required=True),
                                           "Area m²": st.column_config.NumberColumn(min_value=1.0, max_value=200.0, step=0.5, default=10.0)})
                new = []
                for _, r in ed.iterrows():
                    if isinstance(r["Room"], str) and r["Room"] in l2k:
                        a_ = float(r["Area m²"]) if pd.notna(r["Area m²"]) else ROOMS[l2k[r["Room"]]].a
                        new.append(O(t=l2k[r["Room"]], a=max(1.0, a_)))
                h.floors[f] = new
        with st.container(border=True):
            st.markdown("**3. Targets** · what you need")
            st.caption("Use 0 for no target. The dashboard compares these with what the plans provide.")
            a, b, c = st.columns(3)
            with a:
                num(h, "tArea", "Built-up area", 0, 2000, 10, "m²")
            with b:
                num(h, "tBeds", "Bedrooms", 0, 12, 1)
            with c:
                num(h, "tBaths", "Bathrooms", 0, 12, 1)
    if snapshot(h) != before:
        st.rerun()
    with right:
        live_output(build_house(h))


# ------------------------------------------------------- overview views ----
def hkpis(M):
    i = M.inp
    kpis([("Floors", f"G+{M.above - 1}", f"{f1(M.height)} m high"), ("Built-up area", m2(M.tot.gross), "all floors", "m²"),
          ("Footprint", m2(M.fp.area), f"{fp(M.cov)} of plot", "m²"), ("Bedrooms", str(M.beds), f"target {i.tBeds}" if i.tBeds else ""),
          ("Bathrooms", str(M.baths), f"target {i.tBaths}" if i.tBaths else ""),
          ("Rooms", str(sum(len(l.plan.rooms) for l in M.levels)), f"{m2(M.tot.usable)} m² in rooms")])


def v_summary(M):
    s = M.status
    gaps = [c for c in M.checks if c.state != "ok"]
    banner(s.overall, s.title,
           f"{len(gaps)} requirement{'s need' if len(gaps) > 1 else ' needs'} attention: {', '.join(g.label for g in gaps)}." if gaps
           else "Every checked requirement is met by the plans.")
    if M.kind == "house":
        st.markdown("#### House summary")
        hkpis(M)
    else:
        z = M.zone
        st.markdown("#### Decision summary")
        kpis([("Building configuration", f"G+{M.above - 1}", f"{M.inp.basements} basement(s), {z.commercial.floors} commercial, {z.residential.floors} residential floors"),
              ("Commercial area", m2(z.commercial.usable), f"usable, {len(M.cspaces)} spaces", "m²"),
              ("Residential area", m2(z.residential.usable), f"usable, {len(M.units)} dwellings", "m²"),
              ("Parking capacity", str(M.nb), f"{f1(pct(M.nal, M.nb) or 0)}% allocated", "bays"),
              ("EV charging capacity", str(M.nev), "bays with chargers", "bays"),
              ("Space utilization", fp(M.overallSUR), "usable area over built-up area"),
              ("Design verification", f"{s.count}/{s.total}", "requirements satisfied"),
              ("Built-up area", m2(M.tot.gross), "all levels", "m²")])
    a, b = st.columns(2)
    with a:
        st.markdown("#### Design status")
        status_panel(M)
    with b:
        st.markdown("#### Building section")
        show_svg(section_svg(M, get_flt().floor))


def v_overview(M):
    i = M.inp
    far = (M.tot.gross - sum(l.gross for l in M.levels if l.number < 0)) / (i.plotW * i.plotD)
    if M.kind == "house":
        st.markdown(f"### {i.name}, G+{M.above - 1}")
        st.caption(f"Plot {m2(i.plotW * i.plotD)} m² ({f1(i.plotW)} × {f1(i.plotD)} m) · footprint {f1(M.fp.L)} × {f1(M.fp.B)} m · {fp(M.cov)} coverage")
        hkpis(M)
    else:
        z = M.zone
        pa = sum(1 for l in M.levels if l.zone == "parking" and l.number >= 0)
        st.markdown(f"### {i.name}, G+{M.above - 1}")
        st.caption(f"Plot {m2(i.plotW * i.plotD)} m² ({f1(i.plotW)} × {f1(i.plotD)} m) · footprint {m2(M.fp.area)} m² ({fp(M.cov)} coverage) · above-ground FAR {far:.2f}")
        kpis([("Total building levels", str(len(M.levels)), f"{M.above} above ground · {f1(M.height)} m high"),
              ("Basement configuration", f"{i.basements} level{'s' if i.basements > 1 else ''}" if i.basements else "None",
               "Parking + EV charging" if i.basements else "Podium parking only" if pa else "Not modelled"),
              ("Commercial floors", str(z.commercial.floors), ", ".join(l.name.replace(" Floor", "") for l in M.levels if l.zone == "commercial") or "None"),
              ("Residential floors", str(z.residential.floors), f"{len(M.units)} dwellings" if z.residential.floors else "None"),
              ("Total built-up area", m2(M.tot.gross), "all levels, gross", "m²"),
              ("Commercial area", m2(z.commercial.gross), f"{m2(z.commercial.usable)} m² usable", "m²"),
              ("Residential area", m2(z.residential.gross), f"{m2(z.residential.usable)} m² usable", "m²"),
              ("Parking capacity", str(M.nb), f"{M.nb - M.nal} bays available", "bays"),
              ("EV charging capacity", str(M.nev), f"{fp(pct(M.nev, M.nb))} of parking" if M.nb else "No parking", "bays"),
              ("Overall design status", chip(M.status.overall, M.status.title), f"{M.status.count} of {M.status.total} requirements satisfied")])
        st.markdown("#### Integrated chain")
        el = sum(e.n for e in M.elements)
        kpis([("1 · Design", f"{m2(i.plotW * i.plotD)}", "m² plot"), ("2 · Model", str(len(M.levels)), "levels"), ("3 · Extract", fmt(el), "elements"),
              ("4 · Analyse", fp(M.overallSUR), "space utilization"), ("5 · Verify", f"{M.status.count}/{M.status.total}", "satisfied"),
              ("6 · Visualize", "2D + 3D", "Executive + Technical"), ("7 · Report", str(len(report_data(M))), "sections")])
    a, b = st.columns(2)
    with a:
        st.markdown("#### Design status")
        status_panel(M)
    with b:
        st.markdown("#### Building section")
        show_svg(section_svg(M, get_flt().floor))
        st.caption("Use the floor filter in the sidebar to highlight a level. Elevations in metres.")
    if M.kind == "house":
        st.markdown("#### Floor-wise area")
        st.plotly_chart(floor_bars_fig(M), key="ov_bars", **SW)


def v_floors(M):
    flt = get_flt()
    a, b = st.columns(2)
    with a:
        st.markdown("#### Building section")
        show_svg(section_svg(M, flt.floor))
        n = filter_note(M, flt)
        if n:
            st.caption(n)
    with b:
        st.markdown("#### Floor-wise area use")
        st.plotly_chart(floor_bars_fig(M), key="fl_bars", **SW)
    st.markdown("#### Level configuration")
    show_df(["Level", "Primary function", "Key facilities", "Gross m²", "Usable m²", "Usable share"],
            [[l.name, l.fn, l.fac, m2(l.gross), m2(l.usable), fp(pct(l.usable, l.gross))] for l in reversed(M.levels)])


def v_commercial(M):
    flt = get_flt()
    z = M.zone.commercial
    cl = [l for l in M.levels if l.zone == "commercial"]
    st.markdown("#### Commercial space analysis")
    if not cl:
        return empty("No commercial floors in the building stack. Add retail or office floors in Requirements.")
    sp = [s for s in M.spaces if s.zone == "commercial" and s.kind == "space" and match(s, flt)]
    kpis([("Commercial spaces", str(len(M.cspaces)), f"{len(sp)} match filters"), ("Commercial floors", str(z.floors), ", ".join(l.name.replace(" Floor", "") for l in cl)),
          ("Usable area", m2(z.usable), f"target {m2(M.inp.tComm)} m²" if M.inp.tComm else "no target", "m²"),
          ("Common circulation", m2(z.circ), "corridors and lobbies", "m²")])
    n = filter_note(M, flt)
    if n:
        st.caption(n)
    a, b = st.columns(2)
    with a:
        st.markdown("**Area by level**")
        show_df(["Level", "Use", "Spaces", "Usable m²", "Circulation m²"], [[l.name, l.fn, str(len(l.plan.rooms)), m2(l.usable), m2(l.circ)] for l in cl])
    with b:
        st.markdown("**Occupancy configuration**")
        byt = {}
        for s in sp:
            byt[s.type] = byt.get(s.type, 0) + s.area
        if byt:
            st.plotly_chart(simple_bars(list(byt), list(byt.values()), COL["commercial"]), key="com_occ", **SW)
        else:
            empty("No commercial spaces match the current filters.")
    st.markdown("**Individual space area**")
    if sp:
        tot = sum(s.area for s in sp)
        show_df(["ID", "Space", "Level", "Type", "Occupancy", "Area m²", "Share"],
                [[s.id, s.name, lv_name(M, s.level), s.type, OCC.get(s.type, "—"), f1(s.area), fp(pct(s.area, tot))] for s in sp], height=380)
    else:
        empty("No commercial spaces match the current filters.")


def v_residential(M):
    flt = get_flt()
    z = M.zone.residential
    rl = [l for l in M.levels if l.zone == "residential"]
    st.markdown("#### Residential space analysis")
    if not rl:
        return empty("No residential floors in the building stack. Add residential floors in Requirements.")
    sp = [s for s in M.units if match(s, flt)]
    kpis([("Residential floors", str(len(rl)), f"{rl[0].name} to {rl[-1].name}"), ("Residential units", str(len(M.units)), f"{len(sp)} match filters"),
          ("Residential area", m2(z.usable), f"target {m2(M.inp.tRes)} m²" if M.inp.tRes else "usable", "m²"),
          ("Residential circulation", m2(z.circ), "corridors", "m²"), ("Vertical access", "4 lifts", "central core, plus 2 fire stairs")])
    n = filter_note(M, flt)
    if n:
        st.caption(n)
    a, b = st.columns(2)
    with a:
        st.markdown("**Floor-wise residential area**")
        pf = [l for l in rl if flt.floor == "all" or l.id == flt.floor]
        if pf:
            vals = [sum(s.area for s in sp if s.level == l.id) for l in reversed(pf)]
            st.plotly_chart(simple_bars([l.name for l in reversed(pf)], vals, COL["residential"]), key="res_fl", **SW)
        else:
            empty("No residential floors match the current filters.")
    with b:
        st.markdown("**Unit mix**")
        rows = []
        for m in UNITS:
            u = [s for s in sp if s.type == m.t]
            tot = sum(s.area for s in u)
            rows.append([m.t, f1(tot / len(u)) if u else "—", str(len(u)), m2(tot)])
        show_df(["Type", "Average area m²", "Units", "Total m²"], rows)
    st.markdown("**Unit-wise area**")
    if sp:
        show_df(["Unit", "Level", "Type", "Area m²"], [[s.name, lv_name(M, s.level), s.type, f1(s.area)] for s in sp], height=380)
    else:
        empty("No dwellings match the current filters.")


def v_parking(M):
    flt = get_flt()
    pl = [l for l in M.levels if l.zone == "parking"]
    util = pct(M.nal, M.nb)
    ac = max([0] + [pct(l.circ, l.gross) or 0 for l in pl])
    st.markdown("#### Parking and EV charging")
    kpis([("Total parking spaces", str(M.nb), f"{len(pl)} parking level{'' if len(pl) == 1 else 's'}", "bays"), ("Allocated", str(M.nal), f"{fp(util)} utilization"),
          ("Available", str(M.nb - M.nal), "bays free"), ("EV charging spaces", str(M.nev), f"{fp(pct(M.nev, M.nb))} of bays" if M.nb else "—"),
          ("Conventional spaces", str(M.nb - M.nev), "bays"), ("Parking area", m2(M.bayArea), f"{BAY_AREA} m² per bay", "m²"),
          ("Vehicle circulation", m2(sum(l.circ for l in pl)), f"{fp(ac)} of the largest level" if pl else "No parking level", "m²"),
          ("Parking utilization", fp(util), "allocated over total")])
    if not pl:
        return empty("No parking level. Add basement levels or a parking podium floor in Requirements.")
    n = filter_note(M, flt)
    if n:
        st.caption(n)
    st.markdown("#### Bay map")
    html = ""
    for l in sorted(pl, key=lambda x: -x.number):
        if flt.floor != "all" and l.id != flt.floor:
            continue
        bs = [b for b in M.bays if b.level == l.id]
        html += f"<div class='plabel'>{esc(l.name)} · {len(bs)} bays · {sum(1 for b in bs if b.ev)} EV</div><div class='pgrid'>"
        for b in bs:
            on = flt.ptype == "all" or (flt.ptype == "ev") == b.ev
            cls = ("ev " if b.ev else "") + ("alloc " if b.alloc else "") + ("" if on else "off")
            tip = f"{b.id} · {'EV charging' if b.ev else 'Conventional'} · {'Allocated' if b.alloc else 'Available'}"
            html += f"<div class='bay {cls}' title='{esc(tip)}'>{'EV' if b.ev else ''}</div>"
        html += "</div>"
    st.markdown(html or "No parking level matches the floor filter.", unsafe_allow_html=True)
    st.markdown("<div class='legend'><span style='--c:#5C6E7E'><i></i>Allocated</span><span style='--c:rgba(128,128,128,.3)'><i></i>Available</span>"
                "<span style='--c:#2F6FE4'><i></i>EV charging bay</span></div>", unsafe_allow_html=True)
    st.caption("EV bays sit nearest the ground, shown first. Hover a bay for its ID and status.")


def v_utilization(M):
    st.markdown("#### Space utilization ratio")
    st.latex(r"\mathrm{SUR}=\frac{A_f}{A_t}\times 100 \qquad \text{Eq. (1)}")
    st.caption("A_f is the functional usable area and A_t the total available area. For Commercial, Residential and Parking, A_t is the gross area "
               "of that zone's levels. Circulation and Service are measured against the whole built-up area.")
    a, b = st.columns(2)
    with a:
        st.markdown("**Utilization by functional area**")
        fig = go.Figure(go.Bar(y=[r.name for r in M.sur], x=[r.sur or 0 for r in M.sur], orientation="h",
                               marker_color=[COL[r.color] for r in M.sur], text=[fp(r.sur) for r in M.sur], textposition="auto"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"), xaxis=dict(range=[0, 100], title="%"))
        st.plotly_chart(fig, key="sur_bars", **SW)
        st.caption(f"Whole building: {fp(M.overallSUR)} of the built-up area is functional usable space.")
    with b:
        st.markdown("**Values from the plans**")
        show_df(["Functional area", "A_f usable m²", "A_t available m²", "SUR"], [[r.name, m2(r.af), m2(r.at), fp(r.sur)] for r in M.sur])


def v_verification(M):
    st.markdown("#### Configuration verification")
    st.caption("Each requirement resolves to exactly one of three states: Satisfied (configured as intended), Needs Review (incomplete, check required) "
               "or Not Configured (missing from the model).")
    rows = [[c.label, "✓" if c.state == "ok" else "", "!" if c.state == "review" else "", "✕" if c.state == "missing" else "", c.note] for c in M.checks]
    show_df(["Requirement checked in the model", "Satisfied", "Needs Review", "Not Configured", "Finding"], rows)
    st.markdown("#### Requirements against the plans")
    show_df(["Requirement", "Target", "Provided", "State"], req_rows(M))
    st.markdown("#### Missing or incomplete components")
    bad = [c for c in M.checks if c.state != "ok"]
    if bad:
        show_df(["Component", "State", "Finding"], [[c.label, chip(c.state), c.note] for c in bad])
    else:
        empty("Nothing is missing or incomplete.")


def v_circulation(M):
    c = M.circ
    st.markdown("#### Circulation and accessibility")
    show_df(["Element", "Configuration", "State"], [[x.k, x.d, chip(x.s)] for x in c.its])
    st.markdown("#### Separation of circulation")
    show_df(["Circulation streams", "State", "Finding"], [[x[0], chip(x[1]), x[2]] for x in c.sep])
    st.caption("Commercial, residential, pedestrian and vehicular flows are reviewed through the BIM model.")


def v_elements(M):
    tot = sum(e.n for e in M.elements)
    title = "BIM model element analysis" if M.kind == "building" else "Doors, stairs and more"
    st.markdown(f"#### {title}")
    kpis([(e.k, fmt(e.n), e.d) for e in M.elements])
    st.caption(f"{fmt(tot)} elements counted across {len(M.elements)} categories, taken from the generated plans.")
    es = [e for e in M.elements if e.k != "Building levels"]
    st.markdown("**Elements by category**")
    st.plotly_chart(simple_bars([e.k for e in es], [e.n for e in es], COL["ev"], unit=""), key="el_bars", **SW)


def v_schedules(M):
    ss = st.session_state
    flt = get_flt()
    sx = schedules(M, flt)
    key = st.radio("Schedule", list(SCHED_LABEL), format_func=lambda k: SCHED_LABEL[k], horizontal=True, key="sched")
    s = sx[key]
    st.markdown(f"#### {s.t}  ·  {len(s.rows)} rows")
    if key == "rooms":
        n = filter_note(M, flt)
        if n:
            st.caption(n)
    if s.rows:
        rows = [[chip({"Satisfied": "ok", "Needs Review": "review", "Not Configured": "missing"}[r[1]], r[1]), ] if False else r for r in s.rows]
        if key == "verify":
            rows = [[r[0], chip({"Satisfied": "ok", "Needs Review": "review", "Not Configured": "missing"}[r[1]], r[1]), r[2]] for r in s.rows]
        show_df(s.cols, rows, height=420)
    else:
        empty("No rows match the current filters.")
    st.download_button("Download as CSV", to_csv(s.cols, s.rows), file_name=f"{key}_schedule.csv", mime="text/csv", key=f"dl_{key}")


def v_report(M):
    st.markdown("#### Automated design report")
    st.caption("Structured report for design review and project documentation.")
    st.download_button("Download report (Markdown)", report_text(M), file_name="design_report.md", mime="text/markdown", key="dl_report", type="primary")
    for n, (title, rows) in enumerate(report_data(M)):
        with st.expander(f"{n + 1}. {title}", expanded=n < 2):
            show_df(["Item", "Value"], [[r[0], str(r[1])] for r in rows])


def v_rooms(M):
    flt = get_flt()
    rooms = [(l, r) for l in M.levels for r in l.plan.rooms]
    sel = [x for x in rooms if flt.floor == "all" or x[0].id == flt.floor]
    st.markdown("#### Rooms")
    st.caption(("All floors." if flt.floor == "all" else f"Filtered to {lv_name(M, flt.floor)}.") + " Use the floor filter in the sidebar. Minimum sizes are working assumptions for early design.")
    a, b = st.columns(2)
    with a:
        st.markdown("**Area by room type**")
        byt = {}
        for l, r in sel:
            byt[r.type] = byt.get(r.type, 0) + r.area
        if byt:
            st.plotly_chart(simple_bars(list(byt), list(byt.values()), COL["residential"]), key="rm_bars", **SW)
        else:
            empty("No rooms.")
    with b:
        st.markdown("**Room schedule**")
        if sel:
            show_df(["Floor", "Room", "Requested m²", "Drawn m²", "Minimum m²", "Check"],
                    [[l.name, r.name, f1(r.req), f1(r.area), f1(ROOMS[r.t].min) if ROOMS[r.t].min else "—",
                      chip("review" if (ROOMS[r.t].min and r.area < ROOMS[r.t].min) else "ok", "Below minimum" if (ROOMS[r.t].min and r.area < ROOMS[r.t].min) else "Fits")]
                     for l, r in sel], height=380)
        else:
            empty("No rooms on this floor.")


# ------------------------------------------------------------ blueprints ----
def center_item(t, lv):
    P = lv.plan
    house = lv.fnKey == "house"
    cx, cy = P.L / 2, P.B / 2
    r1 = lambda v: round(v * 20) / 20  # noqa: E731
    if t == "doors":
        return O(x=r1(cx - .45), y=r1(cy), len=.9, o="h", sw=1)
    if t == "windows":
        return O(x=r1(cx - .9), y=0, len=1.8, o="h")
    if t == "balconies":
        return O(x=r1(cx - 1.5), y=r2(P.B), w=3, h=1.5)
    sz = (1.6 if house else 2.4) if t == "lifts" else None
    w = sz or (2.2 if house else 2.4)
    h = sz or 4.6
    return O(x=r1(cx - w / 2), y=r1(cy - h / 2), w=w, h=h)


def els_df(t, els):
    d = EDEF[t]
    rows = []
    for i, it in enumerate(els):
        row = {"Tag": f"{d.p}{i + 1}"}
        for key, label, ch in d.f:
            v = it.get(key)
            row[label] = dict(ch).get(v, ch[0][1]) if ch else v
        rows.append(row)
    return pd.DataFrame(rows, columns=["Tag"] + [f[1] for f in d.f])


def df_to_els(t, df, dflt):
    out = []
    for _, r in df.iterrows():
        it = O()
        for key, label, ch in EDEF[t].f:
            v = r.get(label)
            if ch:
                it[key] = {lab: k for k, lab in ch}.get(v, dflt[key])
            else:
                it[key] = round(float(v), 2) if pd.notna(v) else dflt[key]
                if key in ("w", "h", "len") and it[key] <= 0:
                    it[key] = dflt[key]
        out.append(it)
    return out


def norm_els(els):
    return [tuple(sorted((k, round(v, 2) if isinstance(v, (int, float)) else v) for k, v in e.items())) for e in els]


def element_editor(M, lv):
    ss = st.session_state
    o = cur()
    P = lv.plan
    st.markdown("#### Doors, windows, stairs, elevators and balconies")
    if not P.custom:
        cnt = ", ".join(f"{len(P.el.get(t) or [])} {EDEF[t].l.lower()}" for t in EDEF)
        st.caption(f"{lv.name} uses the automatic layout: {cnt}. Customize it to add, move or remove any element and set its exact location.")
        if P.staleEdit:
            st.warning("Your earlier custom elements for this floor were discarded because the plan changed.")
        if st.button("Customize this floor", type="primary", key="ed_start"):
            o.edits[lv.id] = O(sig=lv.sig, els=clone(P.defEl))
            ss.ed_msg = ""
            bump()
            st.rerun()
        return
    ed = o.edits[lv.id]
    c1, c2, c3, c4 = st.columns([1.2, 1, 1.2, 1.3])
    t = c1.selectbox("Element", list(EDEF), format_func=lambda k: EDEF[k].s.capitalize(), key="eltype")
    c2.write("")
    if c2.button("Add at centre", key="ed_add"):
        ed.els.setdefault(t, []).append(center_item(t, lv))
        ss.ed_msg = f"Added {EDEF[t].p}{len(ed.els[t])} at the centre. Set its position in the table."
        bump()
        st.rerun()
    c3.write("")
    if c3.button("Copy to similar floors", key="ed_copy"):
        n = 0
        for l in M.levels:
            if l.id != lv.id and l.sig == lv.sig:
                o.edits[l.id] = O(sig=l.sig, els=clone(ed.els))
                n += 1
        ss.ed_msg = f"Copied to {n} similar floor{'s' if n > 1 else ''}." if n else "No other floor has the same plan."
        bump()
        st.rerun()
    c4.write("")
    if c4.button("Back to automatic layout", key="ed_reset"):
        del o.edits[lv.id]
        ss.ed_msg = ""
        bump()
        st.rerun()
    st.caption("Positions are in metres from the top-left corner of the plan. Tags on the sheet (D1, W2, S1, E1, B1) match the rows below. "
               "Edit a cell, use the + row to add, or select a row and press Delete to remove it.")
    if ss.ed_msg:
        st.info(ss.ed_msg)
    changed = False
    structural = False
    groups = [["doors", "windows"], ["stairs", "lifts", "balconies"]]
    for grp in groups:
        cols = st.columns(len(grp))
        for col, tp in zip(cols, grp):
            with col:
                els = ed.els.setdefault(tp, [])
                st.markdown(f"**{EDEF[tp].l} ({len(els)})**")
                cfg = {"Tag": st.column_config.TextColumn(disabled=True)}
                for key, label, ch in EDEF[tp].f:
                    cfg[label] = st.column_config.SelectboxColumn(label, options=[x[1] for x in ch]) if ch else st.column_config.NumberColumn(label, step=0.05, format="%.2f")
                dflt = center_item(tp, lv)
                dflt.update({k: v for k, v in {"o": "h", "sw": 1}.items() if k not in dflt and any(f[0] == k for f in EDEF[tp].f)})
                res = editor(f"els_{lv.id}_{tp}_{ss.ver}", els_df(tp, els), num_rows="dynamic", column_config=cfg)
                new = df_to_els(tp, res, dflt)
                if norm_els(new) != norm_els(els):
                    if len(new) != len(els):
                        structural = True
                    ed.els[tp] = new
                    changed = True
    if changed:
        ss.ed_msg = ""
        if structural:
            bump()
        st.rerun()


def sheet_info(M, sid):
    if sid == "site":
        i = M.inp
        show_df(["Item", "Value"], [
            ["Plot", f"{f1(i.plotW)} × {f1(i.plotD)} m, {m2(i.plotW * i.plotD)} m²"],
            ["Setbacks (front / rear / side)", f"{f1(i.front)} / {f1(i.rear)} / {f1(i.side)} m"],
            ["Building footprint", f"{f1(M.fp.L)} × {f1(M.fp.B)} m, {m2(M.fp.area)} m²"],
            ["Coverage", f"{fp(M.cov)} (limit {i.maxCov}%)"],
            ["Basement outline", f"{f1(i.plotW - 2 * i.base)} × {f1(i.plotD - 2 * i.base)} m, {i.basements} level{'s' if i.basements > 1 else ''}" if i.basements else "None"],
            ["Height above ground", f"{f1(M.height)} m"]])
        return
    l = next((x for x in M.levels if x.id == sid), None)
    if not l:
        return
    el = l.plan.el
    bal = sum(b.w * b.h for b in (el.balconies or []))
    rows = [["Gross area", f"{m2(l.gross)} m²"], ["Usable area", f"{m2(l.usable)} m² ({fp(pct(l.usable, l.gross))})"], ["Circulation", f"{m2(l.circ)} m²"],
            ["Core and services", f"{m2(l.svc)} m²"], ["Floor-to-floor", f"{f1(l.h)} m"], ["Elevation", sgn(l) + " m"]]
    if l.zone == "parking":
        rows.append(["Bays", f"{len(l.plan.bays)} ({sum(1 for b in l.plan.bays if b.ev)} EV)"])
    else:
        rows.append(["Rooms" if l.fnKey == "house" else "Dwellings" if l.zone == "residential" else "Spaces", str(len(l.plan.rooms))])
    rows += [["Doors / windows", f"{len(el.doors)} / {len(el.windows)}"], ["Stairs / elevators", f"{len(el.stairs or [])} / {len(el.lifts or [])}"],
             ["Balconies", f"{len(el.balconies)} ({m2(bal)} m²)"]]
    show_df(["Item", "Value"], rows)
    if l.zone != "parking":
        show_df(["ID", "Room", "Type", "Area m²"], [[r.id, r.name, r.type, f1(r.area)] for r in l.plan.rooms], height=300)


def v_blueprints(M):
    ss = st.session_state
    sh = sheet_list(M)
    if M.kind == "house":
        sh = sh[1:]
    ids = [s.id for s in sh]
    default = next((l.id for l in M.levels if l.number == 0), ids[0])
    if ss.get("sheet") not in ids:
        ss.sheet = default
    st.markdown("#### Blueprints · floor plans")
    sid = st.radio("Sheet", ids, format_func=lambda i: next(s.name for s in sh if s.id == i), horizontal=True, key="sheet")
    c1, c2 = st.columns([1, 3])
    style = c1.radio("Drawing style", ["blue", "paper"], format_func=lambda s: "Blueprint" if s == "blue" else "White paper", horizontal=True, key="style")
    lv = next((l for l in M.levels if l.id == sid), None)
    svg = site_svg(M, style) if sid == "site" else plan_svg(lv, M, style)
    show_svg(svg)
    sv_name = "site_plan.svg" if sid == "site" else f"{sid}_plan.svg"
    full = site_svg(M, style, True) if sid == "site" else plan_svg(lv, M, style, standalone=True)
    st.download_button("Download this sheet as SVG", export_svg(full), file_name=sv_name, mime="image/svg+xml", key="dl_svg")
    st.caption("Schematic layouts generated from your requirements: each floor shows its grid, rooms, doors, windows, stairs, elevators and balconies.")
    if ss.mode == "tech" and lv is not None:
        with st.container(border=True):
            element_editor(M, lv)
    a, b = st.columns(2)
    with a:
        st.markdown("#### Site data" if sid == "site" else f"#### {lv.name} data")
        sheet_info(M, sid)
    with b:
        st.markdown("#### All sheets")
        cols = st.columns(3)
        for n, s in enumerate(sh):
            l = next((x for x in M.levels if x.id == s.id), None)
            with cols[n % 3]:
                show_svg(site_svg(M, style) if s.id == "site" else plan_svg(l, M, style, mini=True))
                st.caption(s.name + ("" if s.id == "site" else f" · {l.fn}" + (" · custom" if l.plan.custom else "")))


# --------------------------------------------------------------- explore ----
def v_explore(M):
    ss = st.session_state
    st.markdown("#### Explore · 2D plan and 3D model")
    mode = st.radio("View mode", ["2D plan", "3D model"], horizontal=True, key="xmode")
    if mode == "2D plan":
        ids = [l.id for l in M.levels]
        if ss.get("xplan") not in ids:
            ss.xplan = next((l.id for l in M.levels if l.number == 0), ids[0])
        pid = st.radio("Floor", ids, format_func=lambda i: lv_name(M, i), horizontal=True, key="xplan")
        lv = next(l for l in M.levels if l.id == pid)
        show_svg(plan_svg(lv, M, ss.style))
        st.caption("Flat plan of this floor. Switch to 3D model to orbit the whole building.")
        return
    opts = ["all"] + [l.id for l in reversed(M.levels)]
    if ss.get("xcut") not in opts:
        ss.xcut = "all"
    c1, c2 = st.columns([3, 1])
    cut = c1.radio("Cut-away", opts, format_func=lambda i: "Whole building" if i == "all" else lv_name(M, i), horizontal=True, key="xcut")
    view = c2.radio("Camera", ["iso", "top", "front", "side"], format_func=lambda v: {"iso": "Perspective", "top": "Top", "front": "Front", "side": "Side"}[v], horizontal=True, key="xview")
    st.plotly_chart(model_3d(M, cut, view), key="model3d", **SW)
    st.caption("Drag to rotate, scroll to zoom. Pick a floor to cut the building open at that level; the room names of the top visible floor are shown. "
               "Zone colours: purple commercial, teal residential, grey parking, green EV bays.")


# ------------------------------------------------------------------ main ----
VIEWS_B = {"summary": v_summary, "overview": v_overview, "blueprints": v_blueprints, "explore": v_explore, "floors": v_floors,
           "commercial": v_commercial, "residential": v_residential, "parking": v_parking, "utilization": v_utilization,
           "verification": v_verification, "circulation": v_circulation, "elements": v_elements, "schedules": v_schedules,
           "report": v_report, "requirements": v_requirements_building}
VIEWS_H = {"summary": v_summary, "overview": v_overview, "blueprints": v_blueprints, "explore": v_explore, "rooms": v_rooms,
           "elements": v_elements, "verification": v_verification, "report": v_report, "requirements": v_requirements_house}


def sidebar(kind_now, M):
    ss = st.session_state
    with st.sidebar:
        st.markdown("### Project")
        k = st.radio("Project type", ["house", "building"], index=["house", "building"].index(kind_now),
                     format_func=lambda x: x.capitalize(), horizontal=True, key="kind_sel")
        if k != kind_now:
            ss.kind = k
            ss.ed_msg = ""
            ss.pop("sheet", None)
            ss.pop("f_floor", None)
            st.rerun()
        ss.mode = "tech" if st.radio("Dashboard mode", ["Executive", "Technical"], index=1 if ss.mode == "tech" else 0, horizontal=True, key="mode_sel") == "Technical" else "exec"
        st.markdown("### Filters")
        ids = ["all"] + [l.id for l in reversed(M.levels)]
        if ss.get("f_floor") not in ids:
            ss["f_floor"] = "all"
        st.selectbox("Floor", ids, format_func=lambda i: "All floors" if i == "all" else lv_name(M, i), key="f_floor")
        if kind_now == "building":
            st.selectbox("Building zone", ["all", "commercial", "residential", "parking"], format_func=lambda z: "All zones" if z == "all" else ZN[z], key="f_zone")
            st.selectbox("Space type", ["all"] + TYPES, format_func=lambda t: "All types" if t == "all" else t, key="f_type")
            st.selectbox("Parking type", ["all", "ev", "conv"], format_func=lambda t: {"all": "All bays", "ev": "EV charging", "conv": "Conventional"}[t], key="f_ptype")
            st.selectbox("Residential area", ["all", "s", "m", "l"], format_func=lambda t: {"all": "All sizes", "s": "Under 80 m²", "m": "80–130 m²", "l": "Over 130 m²"}[t], key="f_rband")
            st.selectbox("Commercial area", ["all", "s", "m", "l"], format_func=lambda t: {"all": "All sizes", "s": "Under 150 m²", "m": "150–250 m²", "l": "Over 250 m²"}[t], key="f_cband")
        st.button("Reset filters", on_click=reset_filters, key="reset_f")
        st.divider()
        if st.button("← Back to project chooser", key="back"):
            ss.kind = None
            st.rerun()


def main():
    st.set_page_config(page_title="Urban Mixed-Use BIM Dashboard", page_icon="🏙️", layout="wide")
    init_state()
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state
    if ss.kind is None:
        landing()
        return
    kind = ss.kind
    M = build_house(ss.hinp) if kind == "house" else build(ss.inp)
    sidebar(kind, M)
    if ss.kind != kind:  # project type changed in the sidebar
        return
    o = cur()
    sub = (f"{o.name} · G+{M.above - 1} · {m2(M.tot.gross)} m² built-up" if kind == "house"
           else f"{o.name} · G+{M.above - 1} · {o.basements} basement{'' if o.basements == 1 else 's'} · {m2(M.tot.gross)} m² built-up")
    st.markdown(f'<div class="hdr"><h1>Urban Mixed-Use BIM Dashboard<span class="badge" title="Plans and areas are generated from your requirements, '
                f'not read from a live Revit file">GENERATED MODEL</span></h1><p>{esc(sub)}</p></div>', unsafe_allow_html=True)
    tabs = TABSET[(kind, ss.mode)]
    label = st.radio("Dashboard modules", [t[1] for t in tabs], horizontal=True, key=f"tab_{kind}_{ss.mode}", label_visibility="collapsed")
    tab = next(t[0] for t in tabs if t[1] == label)
    st.divider()
    (VIEWS_H if kind == "house" else VIEWS_B)[tab](M)
    st.caption("Every plan, area, unit and bay count is generated from the requirements you enter: floor plans are laid out first and the dashboard "
               "reads its numbers from them. The plans are schematic layouts for early design review, not construction drawings. In a live deployment "
               "the same dashboard reads a Revit extraction. The EV rule (10% of bays) and the parking rule (1 bay per dwelling, 1 per 100 m² of commercial "
               "area) are editable assumptions in the script, not code requirements.")


if __name__ == "__main__":
    main()
