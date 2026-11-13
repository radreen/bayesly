# Bayesly logo — [b]ayesly

The owner's **b** (stem + ring, the bottom-left quarter flipped so the bowl ends in an S-wave)
is the first letter; **ayesly** is **Host Grotesk SemiBold**, tracking −20 (−0.02 em).
Both are outlined in the SVGs, so the logo needs no font installed.

How it fits the type:
- Stroke: the b's stem is 0.25 × x-height — the same as Host Grotesk SemiBold's stems.
- Height: the bowl spans baseline → round overshoot of the font; the stem top meets the top of the **l**.
- Spacing: b-to-a ink gap = 0.07 × x-height (the font's own b-to-a gap at −0.02 em).
- Refinements over the original drawing (variant "R1"): ring +8 % optical weight (inward, so the
  outer contour stays flush with the stem), one concave fillet at the stem/bowl join, and a fused
  neck where the two ring pieces meet, so the joint does not clog at small sizes.

## Files

| File | Use |
|---|---|
| `bayesly-logo-ink.svg` | Main logo on lime or light backgrounds |
| `bayesly-logo-paper.svg` | On black |
| `bayesly-logo-lime.svg` | On black only |
| `bayesly-b-ink.svg` / `-paper` / `-lime` | The b on its own (icon, favicon, stickers) |
| `bayesly-avatar-lime.png` / `.svg` | Threads / X profile picture (1000×1000, platforms crop to a circle) |
| `bayesly-avatar-ink.png` / `.svg` | Alternative profile picture |
| `board-lime.png`, `board-ink.png`, `board-avatars.png` | Presentation boards |

Source: `design/logo/bmark.py` (geometry, variant `final_host`) + `design/logo/compose_logo.py`
(lockup, avatars and `design/logo.js`, which the post templates inline). Re-run compose_logo.py after
changing the geometry.

## Rules

- Colours: `#0D0D0E`, `#C6FF2E`, `#F5F5F0`. One colour per logo, no effects.
- Clear space: at least the b's stem width on every side.
- Don't retype "ayesly" in another font, re-space it, or use the b with a normal "bayesly" next to it.
- Below ~80 px wide use the b on its own.
