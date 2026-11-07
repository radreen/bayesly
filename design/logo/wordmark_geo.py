"""Geometric "bayesly" wordmark builder.

Letters are constructed from circles, straight stems and diagonals with one stroke
weight (like Futura-style geometric sans), so letters can be merged and cut freely,
in the spirit of the "sato" / "Ardor" references.

Units: x-height = 100 (y=0 top of x-height, y=100 baseline), ascender y=-50,
descender y=150, stroke W.

Usage: python design/logo/wordmark_geo.py --out design/logo/wordmark-geo.html
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

W = 22          # stroke weight
H = W / 2
R = 50 - H      # ring centreline radius (outer radius 50)
ASC, DESC = -50, 150
INK, LIME = "#0d0d0e", "#c6ff2e"


def f(v: float) -> str:
    return f"{v:.1f}"


def stem(x: float, y0: float, y1: float) -> str:
    """Vertical stem with flat ends; x is the left ink edge."""
    return f'<rect x="{f(x)}" y="{f(y0)}" width="{W}" height="{f(y1 - y0)}"/>'


def ring(cx: float, cy: float = 50) -> str:
    return f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(R)}" fill="none" stroke="currentColor" stroke-width="{W}"/>'


def arc(cx: float, cy: float, a0: float, a1: float, rx: float = R, ry: float | None = None, sweep: int = 1) -> str:
    """Open stroked arc from angle a0 to a1 (degrees, y-down, clockwise positive)."""
    ry = rx if ry is None else ry
    p0 = (cx + rx * math.cos(math.radians(a0)), cy + ry * math.sin(math.radians(a0)))
    p1 = (cx + rx * math.cos(math.radians(a1)), cy + ry * math.sin(math.radians(a1)))
    span = (a1 - a0) % 360 if sweep else (a0 - a1) % 360
    large = 1 if span > 180 else 0
    return (f'<path d="M{f(p0[0])} {f(p0[1])} A{f(rx)} {f(ry)} 0 {large} {sweep} {f(p1[0])} {f(p1[1])}" '
            f'fill="none" stroke="currentColor" stroke-width="{W}"/>')


def diag(x0: float, y0: float, x1: float, y1: float, hw: float = 13.5) -> str:
    """Diagonal stroke with horizontal (flat) ends; x is the centreline."""
    return (f'<path d="M{f(x0 - hw)} {f(y0)} L{f(x0 + hw)} {f(y0)} L{f(x1 + hw)} {f(y1)} L{f(x1 - hw)} {f(y1)} Z"/>')


# ---------- glyphs: each returns (svg, advance) with x = left ink edge ----------
def g_b(x):
    return stem(x, ASC, 100) + ring(x + 50), 100


def g_a(x):
    return ring(x + 50) + stem(x + 100 - W, 0, 100), 100


def g_y(x, desc_x=30):
    # left arm meets the right arm at the baseline; right arm runs on to the descender
    xr_top, xr_bot = x + 86.5, x + desc_x
    meet = xr_top + (xr_bot - xr_top) * (100 / 150)
    return diag(x + 13.5, 0, meet, 100) + diag(xr_top, 0, xr_bot, DESC), 100


def g_e(x):
    return arc(x + 50, 50, 40, 360) + f'<rect x="{f(x + H)}" y="{50 - H}" width="{f(100 - W)}" height="{W}"/>', 100


def g_s(x, wdt=76):
    rx = (wdt - W) / 2
    ry = 25 - H / 2
    cx = x + wdt / 2
    top = arc(cx, 25 + H / 2, -25, 90, rx, ry, sweep=0)
    bot = arc(cx, 75 - H / 2, 270, 155, rx, ry, sweep=1)
    return top + bot, wdt


def g_yu(x):
    """Arch y: left stem + half-ring arch + right stem running down to the descender."""
    return stem(x, 0, 51) + arc(x + 50, 50, 0, 180) + stem(x + 100 - W, 0, DESC), 100


def g_ly(x):
    """l + y ligature: the l's stem drops, turns through the arch and becomes the y's tail."""
    return stem(x, ASC, 51) + arc(x + 50, 50, 0, 180) + stem(x + 100 - W, 0, DESC), 100


def g_l(x):
    return stem(x, ASC, 100), W


GAP = 9  # ink-to-ink spacing (tight, logo-like)


def word(parts):
    """parts: list of (glyph_fn, kern) -> svg body, total width."""
    x, out = 0.0, []
    for fn, kern in parts:
        x += kern
        svg, adv = fn(x)
        out.append(svg)
        x += adv + GAP
    return "".join(out), x - GAP


# ---------- variants ----------
def v_base():
    return word([(g_b, 0), (g_a, 0), (g_y, -4), (g_e, -10), (g_s, 0), (g_l, 0), (g_y, -2)])


def v_ba_share():
    """b and a share one stroke: the a's ring overlaps the b's ring."""
    body_b, _ = g_b(0)
    shift = 2 * R  # rings share the middle stroke
    body_a = ring(50 + shift) + stem(50 + shift + R - H, 0, 100)
    rest, w = word([(g_y, 0), (g_e, -10), (g_s, 0), (g_l, 0), (g_y, -2)])
    off = 50 + shift + 50 + GAP - 4
    return body_b + body_a + f'<g transform="translate({f(off)} 0)">{rest}</g>', off + w


def v_ly_merge():
    """The l's stem runs down as the y's right arm (sato-style shared stroke)."""
    head, w = word([(g_b, 0), (g_a, 0), (g_y, -4), (g_e, -10), (g_s, 0)])
    sx = w + GAP + 34  # stem left edge
    tail = stem(sx, ASC, DESC) + diag(sx - 40, 0, sx + H - 2, 100)
    return head + tail, sx + W


def v_e_bar():
    """The e's crossbar runs out to the right and becomes the s's top (Ardor-style touching)."""
    head, w = word([(g_b, 0), (g_a, 0), (g_y, -4)])
    ex = w + GAP - 10
    e_ring = arc(ex + 50, 50, 40, 360)
    bar = f'<rect x="{f(ex + H)}" y="{50 - H}" width="{f(100 - W + 30)}" height="{W}"/>'
    s_svg, s_w = g_s(ex + 100 + 2)
    rest, rw = word([(g_l, 0), (g_y, -2)])
    lx = ex + 100 + 2 + s_w + GAP
    return head + e_ring + bar + s_svg + f'<g transform="translate({f(lx)} 0)">{rest}</g>', lx + rw


def v_cut_a():
    """Sato-style cut: the a is only the lower half of a ring plus its stem, the y's arm closes it."""
    head, w = word([(g_b, 0)])
    ax = w + GAP
    a = arc(ax + 50, 50, 0, 180) + stem(ax + 100 - W, 0, 100) + f'<rect x="{f(ax)}" y="{50 - H}" width="{f(100 - W)}" height="{W}"/>'
    rest, rw = word([(g_y, 0), (g_e, -10), (g_s, 0), (g_l, 0), (g_y, -2)])
    rx = ax + 100 + GAP - 4
    return head + a + f'<g transform="translate({f(rx)} 0)">{rest}</g>', rx + rw


def v_combo():
    """ba shared stroke + ly merge."""
    body_b, _ = g_b(0)
    shift = 2 * R
    body_a = ring(50 + shift) + stem(50 + shift + R - H, 0, 100)
    mid, w = word([(g_y, 0), (g_e, -10), (g_s, 0)])
    off = 50 + shift + 50 + GAP - 4
    sx = off + w + GAP + 34
    tail = stem(sx, ASC, DESC) + diag(sx - 40, 0, sx + H - 2, 100)
    return body_b + body_a + f'<g transform="translate({f(off)} 0)">{mid}</g>' + tail, sx + W


def v_arch_y():
    """Both y's become arches (Ardor-style), everything else stays geometric."""
    return word([(g_b, 0), (g_a, 0), (g_yu, 0), (g_e, 0), (g_s, 0), (g_l, 0), (g_yu, 0)])


def v_ly_arch():
    """l flows into y through the arch: one continuous stroke (sato-style shared stroke)."""
    return word([(g_b, 0), (g_a, 0), (g_yu, 0), (g_e, 0), (g_s, 0), (g_ly, 0)])


def v_full():
    """ba shares a stroke + ly flows through the arch."""
    body_b, _ = g_b(0)
    shift = 2 * R
    body_a = ring(50 + shift) + stem(50 + shift + R - H, 0, 100)
    rest, w = word([(g_yu, 0), (g_e, 0), (g_s, 0), (g_ly, 0)])
    off = 50 + shift + 50 + GAP
    return body_b + body_a + f'<g transform="translate({f(off)} 0)">{rest}</g>', off + w


def v_full_capb():
    """Same as the full version, but the l is taller than the b: the ligature becomes the accent."""
    body_b, _ = g_b(0)
    shift = 2 * R
    body_a = ring(50 + shift) + stem(50 + shift + R - H, 0, 100)
    rest, w = word([(g_yu, 0), (g_e, 0), (g_s, 0)])
    off = 50 + shift + 50 + GAP
    lx = off + w + GAP
    tail = stem(lx, ASC - 30, 51) + arc(lx + 50, 50, 0, 180) + stem(lx + 100 - W, 0, DESC)
    return body_b + body_a + f'<g transform="translate({f(off)} 0)">{rest}</g>' + tail, lx + 100


VARIANTS = [
    ("base", "A · Geometric base", "Clean geometric letters, one stroke weight, tight spacing.", v_base),
    ("ba", "B · ba shares a stroke", "The a's bowl overlaps the b's bowl: two letters, one shared line.", v_ba_share),
    ("archy", "C · Arch y", "Both y's are arches with a straight tail (Ardor's arch idea).", v_arch_y),
    ("lyarch", "D · l flows into y", "The l drops, turns through the arch and becomes the y's tail.", v_ly_arch),
    ("full", "E · ba + l→y", "Shared stroke at the start, one continuous l→y stroke at the end.", v_full),
    ("fulltall", "F · ba + tall l→y", "As E, with the l rising above the b: the ligature is the accent.", v_full_capb),
]


def svg_of(fn) -> str:
    body, width = fn()
    pad = 14
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad} {ASC - pad} {f(width + 2 * pad)} {DESC - ASC + 2 * pad}">'
            f'<g fill="currentColor">{body}</g></svg>')


def page() -> str:
    cells = []
    for vid, title, note, fn in VARIANTS:
        s = svg_of(fn)
        cells.append(f'<section data-id="{vid}"><h2>{title}</h2><p>{note}</p>'
                     f'<div class="row"><div class="tile lime">{s}</div><div class="tile ink">{s}</div></div>'
                     f'<div class="small">{s}{s}{s}</div></section>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Bayesly geometric wordmark</title>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
body{{margin:0;padding:28px 36px 50px;background:#9d9d98;font-family:'Inter Tight',sans-serif}}
h1{{font:800 26px 'Inter Tight';letter-spacing:-.03em;margin:0 0 4px}} .note{{font:500 12px 'JetBrains Mono';opacity:.7;margin:0 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(760px,1fr));gap:26px}}
h2{{font:700 13px 'JetBrains Mono';text-transform:uppercase;letter-spacing:.08em;margin:0}}
section p{{font:500 12px 'JetBrains Mono';opacity:.7;margin:3px 0 10px}}
.row{{display:flex;gap:10px}} .tile{{flex:1;height:200px;display:grid;place-items:center}}
.tile svg{{width:78%;height:auto}} .lime{{background:{LIME};color:{INK}}} .ink{{background:{INK};color:#f5f5f0}}
.small{{display:flex;gap:26px;align-items:flex-end;background:#e4e4df;color:{INK};padding:14px 18px;margin-top:10px}}
.small svg:nth-child(1){{width:200px}} .small svg:nth-child(2){{width:110px}} .small svg:nth-child(3){{width:64px}}
</style></head><body><h1>Bayesly — geometric wordmark</h1>
<p class="note">Letters built from circles, stems and diagonals (one weight), then merged / cut like sato and Ardor.</p>
<div class="grid">{''.join(cells)}</div><script>document.body.dataset.ready="1"</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    Path(args.out).write_text(page(), encoding="utf-8")


if __name__ == "__main__":
    main()
