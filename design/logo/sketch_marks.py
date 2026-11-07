"""Procedural logo-mark sketches for Bayesly.

Combines a base shape with technical details (slits, bites, pin holes, split-and-shift,
notch rows, rays, nubs) to explore many abstract marks quickly. Output is an HTML sheet
for review, not final artwork: chosen marks get redrawn and refined by hand.

Usage:
    python design/logo/sketch_marks.py --count 60 --seed 7 --out design/logo/sketches.html
    python design/logo/sketch_marks.py --pick 3,8,15 --seed 7 --out design/logo/picked.html
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

INK = "#0d0d0e"
LIME = "#c6ff2e"


def f(x: float) -> str:
    return f"{x:.1f}"


# ---------- base shapes (filled white inside a mask, or drawn) ----------
def base_shape(kind: str) -> str:
    if kind == "disc":
        return '<circle cx="50" cy="50" r="48"/>'
    if kind == "square":
        return '<rect x="4" y="4" width="92" height="92" rx="14"/>'
    if kind == "squircle":
        return '<rect x="4" y="4" width="92" height="92" rx="34"/>'
    if kind == "dshape":  # flat left, round right
        return '<path d="M8 4 H52 A46 46 0 0 1 52 96 H8 Z"/>'
    if kind == "pill":
        return '<rect x="22" y="2" width="56" height="96" rx="28"/>'
    if kind == "quarter":  # big quarter disc + square block
        return '<path d="M4 96 V4 A92 92 0 0 1 96 96 Z"/>'
    if kind == "arch":
        return '<path d="M6 96 V50 A44 44 0 0 1 94 50 V96 Z"/>'
    if kind == "drop":  # pointed top-left corner, round elsewhere
        return '<path d="M4 4 H52 A44 44 0 0 1 96 48 A48 48 0 0 1 48 96 A44 44 0 0 1 4 52 Z"/>'
    raise ValueError(kind)


BASES = ["disc", "square", "squircle", "dshape", "pill", "quarter", "arch", "drop"]
ANGLES = [0, 45, 90, 135, 180, 225, 270, 315, 30, 60, 120, 150, 210, 240, 300, 330]


def edge_point(kind: str, deg: float) -> tuple[float, float]:
    """Approximate point on the outline in direction deg from the centre."""
    r = 48 if kind in ("disc", "squircle", "drop") else 46
    if kind == "pill":
        r = 30 if deg % 180 in (0,) else 46
    return 50 + r * math.cos(math.radians(deg)), 50 + r * math.sin(math.radians(deg))


# ---------- cuts (drawn black inside the mask) ----------
def slit(rng, kind):
    a = rng.choice(ANGLES)
    w = rng.choice([5, 6, 8])
    start = rng.choice([0, 10, 18])
    return f'<rect x="{start}" y="{-w/2}" width="70" height="{w}" transform="translate(50 50) rotate({a})"/>'


def bite(rng, kind):
    a = rng.choice(ANGLES)
    x, y = edge_point(kind, a)
    r = rng.choice([10, 14, 18, 22])
    return f'<circle cx="{f(x)}" cy="{f(y)}" r="{r}"/>'


def pin(rng, kind):
    a = rng.choice(ANGLES)
    d = rng.choice([0, 12, 18, 24])
    r = rng.choice([5, 7, 9])
    return f'<circle cx="{f(50 + d*math.cos(math.radians(a)))}" cy="{f(50 + d*math.sin(math.radians(a)))}" r="{r}"/>'


def notch_row(rng, kind):
    a0 = rng.choice(ANGLES)
    n = rng.choice([3, 4, 5])
    step = rng.choice([22, 28, 34])
    r = rng.choice([5, 6, 7])
    out = []
    for i in range(n):
        x, y = edge_point(kind, a0 + i * step)
        out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{r}"/>')
    return "".join(out)


def ring_cut(rng, kind):
    r = rng.choice([18, 24, 30])
    a0 = rng.choice(ANGLES)
    sweep = rng.choice([90, 180, 270])
    w = rng.choice([5, 6])
    x0, y0 = 50 + r * math.cos(math.radians(a0)), 50 + r * math.sin(math.radians(a0))
    x1, y1 = 50 + r * math.cos(math.radians(a0 + sweep)), 50 + r * math.sin(math.radians(a0 + sweep))
    large = 1 if sweep > 180 else 0
    return f'<path d="M{f(x0)} {f(y0)} A{r} {r} 0 {large} 1 {f(x1)} {f(y1)}" fill="none" stroke="#000" stroke-width="{w}"/>'


CUTS = [slit, bite, pin, notch_row, ring_cut]


# ---------- additions (drawn in ink outside the mask) ----------
def rays(rng, kind):
    a0 = rng.choice(ANGLES)
    n = rng.choice([2, 3])
    out = []
    for i in range(n):
        a = a0 + i * rng.choice([30, 40, 45])
        L = rng.choice([14, 18, 22])
        out.append(f'<rect x="54" y="-5" width="{L}" height="10" transform="translate(50 50) rotate({a})"/>')
    return "".join(out)


def nubs(rng, kind):
    a0 = rng.choice(ANGLES)
    out = []
    for i in range(rng.choice([3, 4])):
        x, y = edge_point(kind, a0 + i * 30)
        out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="6.5"/>')
    return "".join(out)


def satellite(rng, kind):
    a = rng.choice(ANGLES)
    return f'<circle cx="{f(50 + 60*math.cos(math.radians(a)))}" cy="{f(50 + 60*math.sin(math.radians(a)))}" r="{rng.choice([8, 10, 12])}"/>'


ADDS = [rays, nubs, satellite]


def make_mark(idx: int, rng: random.Random) -> str:
    kind = rng.choice(BASES)
    cuts = "".join(rng.choice(CUTS)(rng, kind) for _ in range(rng.choice([1, 2, 2, 3])))
    add = rng.choice(ADDS)(rng, kind) if rng.random() < 0.35 else ""
    mid = f"mk{idx}"
    body = f'<g mask="url(#{mid})">{base_shape(kind)}</g>'

    # Split-and-shift: cut the whole shape along a line and nudge one half.
    if rng.random() < 0.3:
        a = rng.choice([0, 45, 90, 135])
        gap = rng.choice([4, 6])
        shift = rng.choice([6, 8, 10])
        nx, ny = math.cos(math.radians(a + 90)), math.sin(math.radians(a + 90))
        tx, ty = math.cos(math.radians(a)) * shift, math.sin(math.radians(a)) * shift
        half = lambda s: (f'<clipPath id="{mid}{s}"><rect x="-100" y="{gap/2 if s=="a" else -100-gap/2}" width="200" height="100" '
                          f'transform="translate(50 50) rotate({a})"/></clipPath>')
        defs = half("a") + half("b")
        body = (f'<g clip-path="url(#{mid}a)">{body}</g>'
                f'<g clip-path="url(#{mid}b)" transform="translate({f(tx)} {f(ty)})">{body}</g>')
    else:
        defs = ""
    return (f'<svg viewBox="-14 -14 128 128" xmlns="http://www.w3.org/2000/svg"><defs>{defs}'
            f'<mask id="{mid}" maskUnits="userSpaceOnUse" x="-20" y="-20" width="140" height="140">'
            f'<rect x="-20" y="-20" width="140" height="140" fill="#fff"/><g fill="#000">{cuts}</g></mask></defs>'
            f'<g fill="currentColor">{body}{add}</g></svg>')


def sheet(marks: list[tuple[int, str]], title: str, lockups: bool) -> str:
    cells = []
    for n, svg in marks:
        lock = (f'<div class="lock">{svg}<span>bayesly</span></div>' if lockups else "")
        cells.append(f'<figure><div class="tile">{svg}</div>{lock}<figcaption>{n}</figcaption></figure>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@700&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
<style>
body{{margin:0;padding:28px 32px;background:#9d9d98;font-family:'Inter Tight',sans-serif}}
h1{{font:800 24px 'Inter Tight';letter-spacing:-.03em;margin:0 0 18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax({240 if lockups else 150}px,1fr));gap:12px}}
figure{{margin:0;background:{LIME};color:{INK};padding:14px;position:relative}}
.tile{{display:grid;place-items:center}} .tile svg{{width:{110 if lockups else 112}px;height:{110 if lockups else 112}px}}
.lock{{display:flex;align-items:center;gap:8px;justify-content:center;margin-top:10px;font:700 30px 'Inter Tight';letter-spacing:-.045em}}
.lock svg{{width:30px;height:30px}}
figcaption{{position:absolute;left:10px;top:8px;font:700 12px 'JetBrains Mono',monospace;opacity:.55}}
</style></head><body><h1>{title}</h1><div class="grid">{''.join(cells)}</div>
<script>document.fonts.ready.then(()=>document.body.dataset.ready="1")</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pick", default="", help="comma-separated numbers to show with lockups")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    marks = [(i + 1, make_mark(i + 1, rng)) for i in range(args.count)]
    if args.pick:
        wanted = {int(x) for x in args.pick.split(",")}
        marks = [m for m in marks if m[0] in wanted]
        html = sheet(marks, "Bayesly — picked sketches", lockups=True)
    else:
        html = sheet(marks, f"Bayesly — {args.count} sketch marks (seed {args.seed})", lockups=False)
    Path(args.out).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
