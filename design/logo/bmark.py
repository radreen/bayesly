"""Bayesly 'b' mark (owner's design) — geometric reconstruction and refinements.

Construction (units of the stroke w = 100):
  stem      x 100..200, from the ascender down to the ring centre line
  ring      centre C = (300, 500), outer radius 2w, inner radius w (+ optical delta)
  kept      top-left quarter + right half of the ring
  flipped   the bottom-left quarter is replaced by a TOP-left quarter of a ring centred
            at C2 = (300, 700): the bowl's bottom becomes an S-wave and the two pieces
            meet corner-to-corner at K = (300, 600)

Every shape is emitted as a clockwise polygon so the NonZero fill rule unions them.
Usage: python design/logo/bmark.py  -> prints JSON {name: svg path}
"""

from __future__ import annotations

import json
import math

STEPS = 96


def cw(pts):
    s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    return pts[::-1] if s > 0 else pts


def P(pts):
    return "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in cw(pts)) + " Z"


def arc(c, r, a0, a1, steps=STEPS):
    return [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
             c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]


def sector(c, ro, ri, a0, a1):
    """Annulus sector between angles a0..a1 (degrees, y-down)."""
    return arc(c, ro, a0, a1) + arc(c, ri, a1, a0)


def arc_toward(fc, f, p0, p1, corner, steps=16):
    """Arc of the fillet circle from p0 to p1, taking the way round that passes nearer the
    corner being filled (the concave side), never the long way round."""
    b0 = math.atan2(p0[1] - fc[1], p0[0] - fc[0])
    b1 = math.atan2(p1[1] - fc[1], p1[0] - fc[0])
    d = (b1 - b0 + math.pi) % (2 * math.pi) - math.pi      # short way
    best = None
    for dd in (d, d - math.copysign(2 * math.pi, d)):
        mid = b0 + dd / 2
        m = (fc[0] + f * math.cos(mid), fc[1] + f * math.sin(mid))
        dist = math.hypot(m[0] - corner[0], m[1] - corner[1])
        if best is None or dist < best[0]:
            best = (dist, dd)
    dd = best[1]
    return [(fc[0] + f * math.cos(b0 + dd * i / steps), fc[1] + f * math.sin(b0 + dd * i / steps)) for i in range(steps + 1)]


def fillet_line_circle_outside(x_line, c, R, f, upward=True):
    """Filler for the concave corner between the vertical line x = x_line (shape on its left)
    and the OUTSIDE of circle (c, R), above the point where they cross."""
    # fillet circle centre (x_line + f, yc), externally tangent to the big circle
    yc = c[1] - math.sqrt((R + f) ** 2 - (x_line + f - c[0]) ** 2)
    t_line = (x_line, yc)
    # tangent point on the big circle: along the line from c to the fillet centre
    dx, dy = (x_line + f) - c[0], yc - c[1]
    L = math.hypot(dx, dy)
    t_circ = (c[0] + dx / L * R, c[1] + dy / L * R)
    k_y = c[1] - math.sqrt(R ** 2 - (x_line - c[0]) ** 2)  # line x circle crossing
    a_from = math.degrees(math.atan2(t_circ[1] - c[1], t_circ[0] - c[0]))
    a_to = math.degrees(math.atan2(k_y - c[1], x_line - c[0]))
    big = arc(c, R, a_from, a_to, 16)                       # t_circ -> crossing, along the circle
    fc = (x_line + f, yc)
    corner = (x_line, k_y)
    fil = arc_toward(fc, f, t_line, t_circ, corner)          # t_line -> t_circ, concave side
    return fil + big + [corner]


def fillet_line_circle_inside(x_line, c, r, f, side):
    """Filler for the corner at the bottom of a circle's inside (hole), next to the vertical
    line x = x_line passing through the circle's lowest point (side=+1: right of the line,
    the hole above) or the top point of a circle (side=-1: left of the line, the hole below)."""
    if side > 0:   # hole of circle c above, line on the left (black on the left of the line)
        fc_x = x_line + f
        yc = c[1] + math.sqrt((r - f) ** 2 - f ** 2)
        k = (x_line, c[1] + r)
    else:          # hole of circle c below, line on the right
        fc_x = x_line - f
        yc = c[1] - math.sqrt((r - f) ** 2 - f ** 2)
        k = (x_line, c[1] - r)
    fc = (fc_x, yc)
    t_line = (x_line, yc)
    dx, dy = fc[0] - c[0], fc[1] - c[1]
    L = math.hypot(dx, dy)
    t_circ = (c[0] + dx / L * r, c[1] + dy / L * r)
    a_k = math.degrees(math.atan2(k[1] - c[1], k[0] - c[0]))
    a_t = math.degrees(math.atan2(t_circ[1] - c[1], t_circ[0] - c[0]))
    big = arc(c, r, a_t, a_k, 16)
    fil = arc_toward(fc, f, t_line, t_circ, k)
    return fil + big[1:] + [k]


def build(asc=200, delta=0, top_fillet=0, neck=0, gap=0):
    """asc: stem height above the ring top; delta: optical extra ring weight (outer +delta/2,
    inner -delta/2); top_fillet: radius at the stem/bowl join; neck: fillet radius fusing the
    kiss point; gap: horizontal clearance pulling the flipped quarter left of the kiss point."""
    C, C2 = (300, 500), (300, 700)
    # Optical weight goes INWARD only, so the outer contour stays flush with the stem's left edge.
    Ro, Ri = 200, 100 - delta
    parts = []
    top = 500 - Ro - asc
    parts.append(P([(100, top), (200, top), (200, 500), (100, 500)]))             # stem
    parts.append(P(sector(C, Ro, Ri, 180, 270)))                                     # top-left quarter
    parts.append(P(sector(C, Ro, Ri, 270, 450)))                                     # right half
    q = sector((C2[0] - gap, C2[1]), Ro, Ri, 180, 270)                               # flipped quarter
    parts.append(P(q))
    if top_fillet:
        parts.append(P(fillet_line_circle_outside(200, C, Ro, top_fillet)))
    if neck and not gap:
        parts.append(P(fillet_line_circle_inside(300, C, Ri, neck, +1)))
        parts.append(P(fillet_line_circle_inside(300, C2, Ri, neck, -1)))
    return " ".join(parts), {"top": top, "bottom": 700 + delta / 2 - 0}


VARIANTS = {
    "original": dict(asc=200, top_fillet=22),
    "r1_fused": dict(asc=200, delta=8, top_fillet=22, neck=16),
    "r2_split": dict(asc=200, delta=8, top_fillet=22, gap=14),
    "r3_matched": dict(asc=165, delta=8, top_fillet=22, neck=16),
    "r4_matched_split": dict(asc=165, delta=8, top_fillet=22, gap=14),
}

if __name__ == "__main__":
    out = {}
    for name, kw in VARIANTS.items():
        path, box = build(**kw)
        out[name] = {"path": path, "top": box["top"]}
    print(json.dumps(out))
