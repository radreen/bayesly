"""Render a draft into carousel images (1080x1350 PNG) using design/ templates.

build_slides()      pure: card -> list of slide HTML strings (tested)
build_page()        wraps slides into a full HTML page that loads design/bayesly.css/.js
render_record()     writes <base>.slides.html and screenshots each slide with a headless
                    browser (Playwright). Uses the installed Edge/Chrome when available,
                    so no browser download is needed on Windows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bayesly.cards import Card, build_card
from bayesly.config import ROOT
from bayesly.review import render_markdown

log = logging.getLogger(__name__)

DESIGN_DIR = ROOT / "design"
FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@500;600;700;800;900"
    "&family=JetBrains+Mono:wght@500;700&family=Instrument+Serif:ital@0;1&display=swap"
)
PALETTES = {"lime", "blue"}
ARROW = '<svg viewBox="0 0 100 100"><path d="M24 24 L76 76"/><path d="M76 34 L76 76 L34 76"/></svg>'
STEP_LABELS = {
    "cover": "What happened",
    "update": "Before → Now",
    "number": "The number",
    "confidence": "How sure",
    "closing": "What's next",
}
# Gauge: zone index and needle angle (degrees) per confidence level.
GAUGE = {"low": (0, 150), "medium": (1, 80), "high": (2, 28)}


class RenderError(RuntimeError):
    pass


def e(text: Any) -> str:
    return escape(str(text), quote=True)


def source_label(record: dict[str, Any]) -> str:
    src = record["source"]
    if src["id"].startswith("arxiv"):
        return "preprint"
    return {"official_blog": "primary", "release_notes": "primary", "research": "research", "press": "press"}.get(
        src.get("kind", ""), "other"
    )


def domain(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.") or url


def _headline_html(card: Card) -> str:
    if not card.highlight:
        return e(card.headline)
    pre, _, post = card.headline.partition(card.highlight)
    return f'{e(pre)}<span class="hl">{e(card.highlight)}</span>{e(post)}'


def _dots(level: str) -> str:
    n = {"low": 1, "medium": 2, "high": 3}[level]
    return '<span class="dots">' + "".join(
        f'<span class="on"></span>' if i < n else "<span></span>" for i in range(3)
    ) + "</span>"


def _chrome(kind: str, idx: int, total: int, tag: str, source: str, final_note: str = "") -> str:
    """Top bar + ruler + bottom bar shared by carousel slides."""
    right = "Swipe →" if idx == 1 and total > 1 else (final_note or f"{idx:02d} / {total:02d}")
    return (
        f'<div class="top"><div class="wordmark">bayesly<i></i></div><span class="pill">{e(tag)}</span></div>'
        f'<div class="ruler"{_ruler_style(kind)}><div class="t1"></div><div class="t2"></div><div class="dot"></div>'
        f'<div class="lbl">{idx:02d} · {e(STEP_LABELS[kind])}</div></div>'
        f'<div class="bottom"><span>Source: {e(source)}</span><span>{e(right)}</span></div>'
    )


def _ruler_style(kind: str) -> str:
    return ' style="color:var(--white)"' if kind == "number" else ""


# ---------------------------------------------------------------------------------------------
# Variation: one brand, many layouts. Each post gets a Style derived from its id (stable across
# re-renders) and its content (confidence shapes the curves; a number can take over the cover).
# The cover layout avoids the layouts of the two previous posts so the feed never looks cloned.
# ---------------------------------------------------------------------------------------------
COVERS = ("curve", "split", "editorial", "stamp", "figure")
COVER_BG = {"curve": "ink", "split": "acc", "editorial": "paper", "stamp": "ink", "figure": "acc"}
# kind -> (background of variant "a", background of variant "b")
INNER_BG = {"update": ("paper", "ink"), "number": ("acc", "paper"), "confidence": ("ink", "paper"), "closing": ("ink", "acc")}
CONF_TITLES = (
    "How sure<br>are we?",
    'Weighing the<br><span class="serif">evidence.</span>',
    "Confidence<br>check.",
)
CLOSE_TITLES = {
    "high": ('What to<br><span class="serif">watch.</span>', 'Next<br><span class="serif">to watch.</span>'),
    "other": ("We don't<br>know <span class=\"serif\">yet.</span>", 'Still<br><span class="serif">open.</span>',
              "What we<br><span class=\"serif\">can't say.</span>"),
}
LEDES = (
    "What changed — and how sure we are.",
    "Update your priors.",
    "The evidence, sized honestly.",
    "Before → now. Read the evidence.",
)
# posterior (sigma, height) per confidence: sure = narrow and tall, unsure = wide and flat
SHAPE = {"high": (105, 470), "medium": (150, 410), "low": (230, 290)}


@dataclass(frozen=True)
class Style:
    cover: str = "curve"
    seed: int = 0
    variants: dict[str, str] = field(default_factory=dict)
    lede: str = LEDES[0]
    flip: bool = False


def post_seed(record: dict[str, Any]) -> int:
    return int(hashlib.sha1(str(record.get("id", "")).encode()).hexdigest()[:12], 16)


def choose_style(card: Card, record: dict[str, Any], recent: tuple[str, ...] | list[str] = ()) -> Style:
    """Pick a cover layout + inner-slide variants. Deterministic for a given post and history."""
    seed = post_seed(record)
    cover = record.get("layout") if record.get("layout") in COVERS else None
    if cover == "figure" and not card.number:
        cover = None
    if cover is None:
        options = ["curve", "split", "editorial", "stamp"]
        if card.number:
            options = ["figure", "figure"] + options   # a real number is the strongest cover we have
        fresh = [o for o in options if o not in list(recent)[:2]] or options
        cover = fresh[seed % len(fresh)]
    variants: dict[str, str] = {}
    prev_bg = COVER_BG[cover]
    for i, kind in enumerate(plan_slides(card, cover)[1:]):
        pick = "a" if (seed >> (8 + i)) & 1 else "b"
        if INNER_BG[kind][pick == "b"] == prev_bg:      # neighbours never share a background
            pick = "b" if pick == "a" else "a"
        variants[kind] = pick
        prev_bg = INNER_BG[kind][pick == "b"]
    return Style(cover=cover, seed=seed, variants=variants, lede=LEDES[(seed >> 5) % len(LEDES)],
                 flip=bool((seed >> 3) & 1))


def _bell_y(x: float, cx: float, s: float, h: float, base: float) -> float:
    return base - h * math.exp(-0.5 * ((x - cx) / s) ** 2)


def _meta(record: dict[str, Any]) -> str:
    published = (record["source"].get("published") or record.get("created_at") or "")[:10]
    try:
        date = datetime.fromisoformat(published).strftime("%d.%m.%Y")
    except ValueError:
        date = ""
    meta = [f"ID {record['id'][:6]}", date, f"Src: {source_label(record)}"]
    return f'<div class="meta">{"".join(f"<span>{e(m)}</span>" for m in meta if m)}</div>'


def _curves(card: Card, style: Style, base: float = 1192, label_color: str = "var(--white)") -> tuple[str, float, float]:
    """Prior (dashed) + posterior (filled) bells shaped by confidence and placed by the seed."""
    sd = style.seed
    s, h = SHAPE.get(card.confidence, SHAPE["medium"])
    post_cx = 640 + (sd >> 12) % 181
    prior_cx = 300 + (sd >> 20) % 161
    ps, ph = 290, 250
    if style.flip:
        post_cx, prior_cx = 1080 - post_cx, 1080 - prior_cx
    # prior annotation: a point on the outer slope of the prior
    ax = prior_cx - 0.75 * ps if not style.flip else prior_cx + 0.75 * ps
    ay = _bell_y(ax, prior_cx, ps, ph, base)
    # posterior annotation: a point on the outer slope of the posterior
    bx = post_cx + 1.0 * s if not style.flip else post_cx - 1.0 * s
    by = _bell_y(bx, post_cx, s, h, base)
    tx_end = 1080 - 72
    if not style.flip:
        prior_txt = f'<text x="{ax + 14:.0f}" y="{ay - 76:.0f}">Prior · what we assumed</text>'
        post_txt = f'<line x1="{bx:.0f}" y1="{by:.0f}" x2="{min(bx + 72, tx_end):.0f}" y2="{by:.0f}"/><text x="{bx:.0f}" y="{by - 26:.0f}">Posterior</text>'
    else:
        prior_txt = f'<text x="{ax - 14:.0f}" y="{ay - 76:.0f}" text-anchor="end">Prior · what we assumed</text>'
        post_txt = f'<line x1="{bx:.0f}" y1="{by:.0f}" x2="{max(bx - 72, 72):.0f}" y2="{by:.0f}"/><text x="{bx:.0f}" y="{by - 26:.0f}" text-anchor="end">Posterior</text>'
    svg = f"""
    <path class="bell-prior" data-bell="{prior_cx},{ps},{ph},{base:.0f}" style="color:{label_color}"/>
    <path class="bell-fill" data-bell="{post_cx},{s},{h},{base:.0f}" data-closed/>
    <path class="bell-dots" data-bell="{post_cx},{s},{h},{base:.0f}" data-closed/>
    <g class="anno" style="color:{label_color}"><line x1="{ax:.0f}" y1="{ay:.0f}" x2="{ax:.0f}" y2="{ay - 70:.0f}"/><circle cx="{ax:.0f}" cy="{ay:.0f}" r="6"/>{prior_txt}</g>
    <g class="anno" style="color:{label_color}"><circle cx="{bx:.0f}" cy="{by:.0f}" r="6"/>{post_txt}</g>"""
    return svg, post_cx, base - h * 0.36


def slide_cover(card: Card, record: dict[str, Any], idx: int, total: int, style: Style | None = None) -> str:
    style = style or Style()
    builder = {"curve": _cover_curve, "split": _cover_split, "editorial": _cover_editorial,
               "stamp": _cover_stamp, "figure": _cover_figure}[style.cover]
    return builder(card, record, idx, total, style)


def _cover_curve(card: Card, record: dict[str, Any], idx: int, total: int, style: Style) -> str:
    curves, ax, ay = _curves(card, style)
    return f"""
<article class="post ink cover" data-kind="cover" data-layout="curve">
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true">{curves}</svg>
  {_meta(record)}
  <div class="content z">
    <h2 class="h-xl" data-fit="470">{_headline_html(card)}</h2>
    <p class="lede"><span class="serif">{e(style.lede)}</span></p>
  </div>
  <div class="arrow" style="left:{ax - 64:.0f}px;top:{ay - 64:.0f}px">{ARROW}</div>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def _cover_split(card: Card, record: dict[str, Any], idx: int, total: int, style: Style) -> str:
    s, h = SHAPE.get(card.confidence, SHAPE["medium"])
    k = 0.42                                    # mini diagram scale
    left, right = (300, 780) if not style.flip else (780, 300)
    prior = f'<path class="bell-prior" data-bell="{left},{290 * k:.0f},{250 * k:.0f},1150" style="color:var(--white)"/>'
    post = (f'<path class="bell-fill" data-bell="{right},{s * k * 1.4:.0f},{h * k * 1.2:.0f},1150" data-closed/>'
            f'<path class="bell-dots" data-bell="{right},{s * k * 1.4:.0f},{h * k * 1.2:.0f},1150" data-closed/>')
    a1, a2 = (left + 150, right - 150) if not style.flip else (left - 150, right + 150)
    head = "M{0} 1080 L{1} 1080".format(a1, a2)
    tip = (f"M{a2 - 18} 1066 L{a2} 1080 L{a2 - 18} 1094" if not style.flip else f"M{a2 + 18} 1066 L{a2} 1080 L{a2 + 18} 1094")
    return f"""
<article class="post acc split" data-kind="cover" data-layout="split">
  <div class="panel"></div>
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true">
    {prior}{post}
    <g class="shift"><path d="{head}"/><path d="{tip}"/></g>
    <g class="anno" style="color:var(--white)">
      <text x="{left}" y="1186" text-anchor="middle">Before</text>
      <text x="{right}" y="1186" text-anchor="middle">Now · {e(card.confidence)} confidence</text>
      <text x="{(left + right) // 2}" y="1058" text-anchor="middle">Δ new evidence</text>
    </g>
  </svg>
  {_meta(record)}
  <div class="content z">
    <h2 class="h-xl" data-fit="520">{_headline_html(card)}</h2>
    <p class="lede"><span class="serif">{e(style.lede)}</span></p>
  </div>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def _cover_editorial(card: Card, record: dict[str, Any], idx: int, total: int, style: Style) -> str:
    s, h = SHAPE.get(card.confidence, SHAPE["medium"])
    k = 0.36
    cx = 190 + (style.seed >> 14) % 40
    word = card.category.upper()
    return f"""
<article class="post paper grid-bg editorial" data-kind="cover" data-layout="editorial">
  <div class="ghost" aria-hidden="true">{e(word)}</div>
  {_meta(record)}
  <div class="content z">
    <p class="kicker">Fig. 01 — the update</p>
    <h2 class="h-xl" data-fit="520">{_headline_html(card)}</h2>
    <p class="lede"><span class="serif">{e(style.lede)}</span></p>
  </div>
  <figure class="fig z">
    <svg viewBox="0 0 400 230" aria-hidden="true">
      <path class="bell-prior" data-bellw="{cx - 60},{290 * k:.0f},{250 * k:.0f},200,400" style="color:var(--ink)"/>
      <path class="bell-fill" data-bellw="{cx + 50},{s * k:.0f},{h * k:.0f},200,400" data-closed/>
      <line x1="0" y1="200" x2="400" y2="200" stroke="var(--ink)" stroke-width="2"/>
    </svg>
    <figcaption>Posterior · {e(card.confidence)} confidence {_dots(card.confidence)}</figcaption>
  </figure>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def _cover_stamp(card: Card, record: dict[str, Any], idx: int, total: int, style: Style) -> str:
    rng = random.Random(style.seed)
    spread = {"high": 70, "medium": 120, "low": 190}.get(card.confidence, 120)
    cx, cy = (720, 1000) if not style.flip else (360, 1000)
    dots = []
    for _ in range(170):
        x, y = rng.gauss(cx, spread * 1.5), rng.gauss(cy, spread)
        if 0 < x < 1080 and 760 < y < 1170:
            r = rng.choice((3, 3, 4, 6))
            cls = "o" if rng.random() < 0.25 else "f"
            dots.append(f'<circle class="{cls}" cx="{x:.0f}" cy="{y:.0f}" r="{r}"/>')
    level = {"low": "LOW", "medium": "MED", "high": "HIGH"}.get(card.confidence, "MED")
    side = "right:72px" if not style.flip else "left:72px"
    return f"""
<article class="post ink stamp" data-kind="cover" data-layout="stamp">
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true"><g class="cloud">{"".join(dots)}</g></svg>
  {_meta(record)}
  <div class="content z">
    <h2 class="h-xl" data-fit="470">{_headline_html(card)}</h2>
    <p class="lede"><span class="serif">{e(style.lede)}</span></p>
  </div>
  <div class="seal" style="{side}"><div class="badge" data-text="Confidence · {e(card.confidence)} · bayesly · "><div class="center"><div class="disc">{level}</div></div></div></div>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def _cover_figure(card: Card, record: dict[str, Any], idx: int, total: int, style: Style) -> str:
    num = card.number
    assert num is not None
    size = min(420, int(960 / (0.62 * max(len(num.value), 1))))
    return f"""
<article class="post acc figure" data-kind="cover" data-layout="figure">
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true">
    <path class="bell-ink" data-bell="{860 if not style.flip else 220},150,120,1050" data-closed data-floor="1350"/>
    <rect class="bell-ink" x="0" y="1049" width="1080" height="301"/>
  </svg>
  {_meta(record)}
  <div class="content z">
    <h2 class="h-l" data-fit="250">{e(card.headline)}</h2>
    <div class="bigwrap" style="height:{int(size * 0.84)}px"><div class="big echo" style="font-size:{size}px">{e(num.value)}</div><div class="big" style="font-size:{size}px">{e(num.value)}<sup class="fn">1</sup></div></div>
    <p class="h-m cap" data-fit="120">{e(num.caption)}</p>
  </div>
  <div class="footnote">¹ {e(num.footnote)}</div>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def slide_update(card: Card, record: dict[str, Any], idx: int, total: int, variant: str = "a") -> str:
    bg = "paper grid-bg" if variant == "a" else "ink"
    return f"""
<article class="post {bg} update" data-kind="update">
  <div class="vlabel">Fig. {idx:02d} — Belief update</div>
  <div class="content">
    <div class="before"><span class="pill">Before</span><p data-fit="140">{e(card.before)}</p></div>
    <div class="delta"><div class="stem"></div><span><b>Δ</b>New evidence</span></div>
    <div class="now"><div class="halftone"></div><span class="pill fill">Now</span><p data-fit="300">{e(card.now)}</p></div>
    <div class="conf"><span>Confidence</span>{_dots(card.confidence)}<span>{e(card.confidence.title())}</span></div>
  </div>
  {_chrome("update", idx, total, "The update", domain(record["source"]["url"]))}
</article>"""


def slide_number(card: Card, record: dict[str, Any], idx: int, total: int, variant: str = "a") -> str:
    num = card.number
    assert num is not None
    size = min(560, int(1000 / (0.62 * max(len(num.value), 1))))
    style = f'style="font-size:{size}px"'
    take = (
        f'<div class="take"><p class="kicker">Our take — opinion</p><p class="quote" data-fit="200">“{e(card.opinion)}”</p></div>'
        if card.opinion else ""
    )
    return f"""
<article class="post {'acc' if variant == 'a' else 'paper'} number" data-kind="number">
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true">
    <path class="bell-ink" d="M0 850 C 300 830, 560 760, 760 800 S 1000 860, 1080 820 L1080 1350 L0 1350 Z"/>
  </svg>
  <div class="content z">
    <div class="bigwrap" style="height:{int(size * 0.84)}px"><div class="big echo" {style}>{e(num.value)}</div><div class="big" {style}>{e(num.value)}<sup class="fn">1</sup></div></div>
    <p class="h-m cap" data-fit="125">{e(num.caption)}</p>
  </div>
  {take}
  <div class="footnote">¹ {e(num.footnote)}</div>
  {_chrome("number", idx, total, "By the numbers", domain(record["source"]["url"]))}
</article>"""


def slide_confidence(card: Card, record: dict[str, Any], idx: int, total: int, variant: str = "a",
                     seed: int = 0) -> str:

    zone, angle = GAUGE[card.confidence]
    rad = math.radians(angle)
    nx, ny = 260 + 155 * math.cos(rad), 270 - 155 * math.sin(rad)
    zones = [
        "M40 270 A220 220 0 0 1 150 79.5",
        "M154 77.3 A220 220 0 0 1 366 77.3",
        "M370 79.5 A220 220 0 0 1 480 270",
    ]
    zone_html = "".join(
        f'<path class="zone {"on" if i == zone else "off"}" d="{d}"/>' for i, d in enumerate(zones)
    )
    rows = [("for", "+", t, "For") for t in card.evidence_for] + [
        ("against", "−", t, "Against") for t in card.evidence_against
    ]
    rows_html = "".join(
        f'<div class="row {cls}"><div class="sign">{sign}</div><div class="bar">{e(text)}<small>{tag}</small></div></div>'
        for cls, sign, text, tag in rows[:3]
    )
    return f"""
<article class="post {'ink' if variant == 'a' else 'paper'} confidence" data-kind="confidence">
  <div class="content">
    <h2 class="h-xl">{CONF_TITLES[(seed >> 24) % len(CONF_TITLES)]}</h2>
    <div class="gauge-row">
      <svg class="gauge" viewBox="0 0 520 300" aria-hidden="true">
        {zone_html}<g class="ticks"></g>
        <line class="needle" x1="260" y1="270" x2="{nx:.1f}" y2="{ny:.1f}"/><circle class="hub" cx="260" cy="270" r="14"/>
        <text x="40" y="298">LOW</text><text x="260" y="22" text-anchor="middle">MEDIUM</text><text x="480" y="298" text-anchor="end">HIGH</text>
      </svg>
      <div class="readout"><div class="k">Reading</div><div class="v">{e(card.confidence.title())}</div>
        <div class="r" data-fit="130">{e(card.confidence_reason)}</div></div>
    </div>
    <div class="rows">{rows_html}</div>
  </div>
  {_chrome("confidence", idx, total, "Confidence", domain(record["source"]["url"]))}
</article>"""


def slide_closing(card: Card, record: dict[str, Any], idx: int, total: int, show_opinion: bool,
                  variant: str = "a", seed: int = 0) -> str:
    titles = CLOSE_TITLES["high" if card.confidence == "high" else "other"]
    title = titles[(seed >> 28) % len(titles)]
    prior = ""
    if card.confidence != "high" and not show_opinion:
        prior = """<div class="flat"><svg viewBox="0 0 936 120" aria-hidden="true"><g class="uniform"></g>
          <line x1="0" y1="118" x2="936" y2="118" stroke="var(--white)" stroke-width="2" opacity=".6"/>
          <text x="0" y="22" style="font:700 17px var(--mono);letter-spacing:1.6px;fill:var(--accent)">FLAT PRIOR · EVERY OUTCOME EQUALLY LIKELY</text></svg></div>"""
    questions = ""
    if card.open_questions:
        items = "".join(f"<li>{e(q)}</li>" for q in card.open_questions)
        questions = f'<div class="q"><p class="kicker">What would change our mind</p><ul>{items}</ul></div>'
    opinion = ""
    if show_opinion and card.opinion:
        opinion = (
            f'<div class="take-block"><p class="kicker">Our take — opinion</p>'
            f'<p class="quote" data-fit="190">“{e(card.opinion)}”</p></div>'
        )
    return f"""
<article class="post {'ink' if variant == 'a' else 'acc'} unknown" data-kind="closing">
  <div class="content">
    <h2 class="h-xxl">{title}</h2>
    {prior}{opinion}{questions}
  </div>
  {_chrome("closing", idx, total, "Open question" if card.confidence != "high" else "Next", domain(record["source"]["url"]), "Follow for the update")}
</article>"""


def plan_slides(card: Card, cover: str = "curve") -> list[str]:
    number_slide = bool(card.number) and cover != "figure"   # a "figure" cover already shows the number
    kinds = ["cover"]
    if card.before and card.now:
        kinds.append("update")
    if number_slide:
        kinds.append("number")
    kinds.append("confidence")
    if card.open_questions or (card.opinion and not number_slide):
        kinds.append("closing")
    return kinds


def build_slides(card: Card, record: dict[str, Any], style: Style | None = None) -> list[str]:
    style = style or choose_style(card, record)
    kinds = plan_slides(card, style.cover)
    total = len(kinds)
    out = []
    for i, kind in enumerate(kinds, start=1):
        v = style.variants.get(kind, "a")
        if kind == "cover":
            out.append(slide_cover(card, record, i, total, style))
        elif kind == "update":
            out.append(slide_update(card, record, i, total, v))
        elif kind == "number":
            out.append(slide_number(card, record, i, total, v))
        elif kind == "confidence":
            out.append(slide_confidence(card, record, i, total, v, style.seed))
        else:
            out.append(slide_closing(card, record, i, total, show_opinion="number" not in kinds, variant=v, seed=style.seed))
    return out


def recent_layouts(json_path: Path, created_at: str, limit: int = 2) -> list[str]:
    """Cover layouts of the posts drafted just before this one (same output folder tree)."""
    rows = []
    for p in json_path.parent.parent.rglob("*.json"):
        if p == json_path:
            continue
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("layout") and (r.get("created_at") or "") <= created_at:
            rows.append((r.get("created_at") or "", r["layout"]))
    return [layout for _, layout in sorted(rows, reverse=True)[:limit]]


def build_page(slides: list[str], palette: str = "lime") -> str:
    if palette not in PALETTES:
        raise RenderError(f"unknown palette {palette!r}; use one of {sorted(PALETTES)}")
    css = (DESIGN_DIR / "bayesly.css").as_uri()
    js = (DESIGN_DIR / "bayesly.js").as_uri()
    logo_js = (DESIGN_DIR / "logo.js").as_uri()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Bayesly slides</title>
<link rel="stylesheet" href="{FONTS_URL}"><link rel="stylesheet" href="{css}">
<style>body{{background:transparent;margin:0}} .post{{margin:0}}</style></head>
<body data-palette="{palette}">
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<pattern id="dots-ink" width="14" height="14" patternUnits="userSpaceOnUse"><circle cx="7" cy="7" r="2.6" fill="#0d0d0e"/></pattern>
</defs></svg>
{"".join(slides)}
<script src="{logo_js}"></script>
<script src="{js}"></script>
</body></html>"""


def _launch(playwright):
    errors = []
    for channel in ("msedge", "chrome", None):
        try:
            return playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - try the next browser
            errors.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
    raise RenderError("no browser for rendering. Install Edge/Chrome or run `playwright install chromium`.\n"
                      + "\n".join(errors))


def screenshot(page_path: Path, out_base: Path, timeout_ms: int = 30000) -> tuple[list[Path], list[str], list[Path]]:
    """Returns (png paths, warnings, jpeg paths). JPEGs are for Instagram (JPEG only) and X."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError('Playwright is not installed. Run: pip install -e ".[render]"') from exc

    paths: list[Path] = []
    jpegs: list[Path] = []
    warnings: list[str] = []
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            page.goto(page_path.as_uri(), timeout=timeout_ms)
            page.wait_for_selector('body[data-ready="1"]', timeout=timeout_ms)
            if not page.evaluate("document.fonts.check('900 40px \"Inter Tight\"')"):
                warnings.append("Brand fonts did not load (offline?). Images use fallback fonts.")
            if page.locator("[data-overflow]").count():
                warnings.append("Some text was too long to fit a slide even after shrinking. Shorten it.")
            for i, post in enumerate(page.locator(".post").all(), start=1):
                path = out_base.with_name(f"{out_base.name}-{i:02d}.png")
                # Scroll first and let the compositor paint: capturing an off-screen slide right
                # away can return unpainted (white) tiles on pages with several big slides.
                post.scroll_into_view_if_needed(timeout=timeout_ms)
                page.wait_for_timeout(150)
                post.screenshot(path=str(path), timeout=timeout_ms)
                paths.append(path)
                jpg = path.with_suffix(".jpg")
                post.screenshot(path=str(jpg), type="jpeg", quality=92, timeout=timeout_ms)
                jpegs.append(jpg)
        finally:
            browser.close()
    return paths, warnings, jpegs


def render_record(json_path: Path, palette: str = "lime", force: bool = False) -> list[Path]:
    """Render one draft JSON into PNGs next to it. Updates the JSON and .md review files."""
    record = json.loads(json_path.read_text(encoding="utf-8"))
    if not record.get("draft"):
        raise RenderError(f"{json_path.name}: no parsed draft to render (was it --skip-llm?)")
    have = record.get("images") and record.get("images_jpg")
    if have and not force and all(Path(p).exists() for p in record["images"] + record["images_jpg"]):
        log.info("render skipped (images exist)", extra={"path": str(json_path)})
        return [Path(p) for p in record["images"]]

    card = build_card(record)
    style = choose_style(card, record, recent_layouts(json_path, record.get("created_at") or ""))
    base = json_path.with_suffix("")
    page_path = base.with_name(base.name + ".slides.html")
    page_path.write_text(build_page(build_slides(card, record, style), palette), encoding="utf-8")
    images, warnings, jpegs = screenshot(page_path, base)

    record["images"] = [str(p) for p in images]
    record["images_jpg"] = [str(p) for p in jpegs]
    record["image_warnings"] = card.warnings + warnings
    record["palette"] = palette
    record["layout"] = style.cover
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    json_path.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8")
    log.info("rendered", extra={"path": str(json_path), "images": len(images), "warnings": len(record["image_warnings"])})
    return images
