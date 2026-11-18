"""Render a draft into carousel images (1080x1350 PNG) using design/ templates.

build_slides()      pure: card -> list of slide HTML strings (tested)
build_page()        wraps slides into a full HTML page that loads design/bayesly.css/.js
render_record()     writes <base>.slides.html and screenshots each slide with a headless
                    browser (Playwright). Uses the installed Edge/Chrome when available,
                    so no browser download is needed on Windows.
"""

from __future__ import annotations

import json
import logging
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


def slide_cover(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    published = (record["source"].get("published") or record.get("created_at") or "")[:10]
    try:
        date = datetime.fromisoformat(published).strftime("%d.%m.%Y")
    except ValueError:
        date = ""
    meta = [f"ID {record['id'][:6]}", date, f"Src: {source_label(record)}"]
    return f"""
<article class="post ink cover" data-kind="cover">
  <svg class="art" viewBox="0 0 1080 1350" aria-hidden="true">
    <path class="bell-prior" data-bell="400,290,250,1192" style="color:var(--white)"/>
    <path class="bell-fill" data-bell="760,150,440,1192" data-closed/>
    <path class="bell-dots" data-bell="760,150,440,1192" data-closed/>
    <g class="anno" style="color:var(--white)"><line x1="232" y1="1010" x2="232" y2="924"/><circle cx="232" cy="1010" r="6"/><text x="246" y="934">Prior · what we assumed</text></g>
    <g class="anno" style="color:var(--white)"><line x1="912" y1="900" x2="984" y2="900"/><circle cx="912" cy="900" r="6"/><text x="912" y="874">Posterior</text></g>
  </svg>
  <div class="meta">{"".join(f"<span>{e(m)}</span>" for m in meta if m)}</div>
  <div class="content z">
    <h2 class="h-xl" data-fit="470">{_headline_html(card)}</h2>
    <p class="lede"><span class="serif">What changed — and how sure we are.</span></p>
  </div>
  <div class="arrow">{ARROW}</div>
  {_chrome("cover", idx, total, card.category, domain(record["source"]["url"]))}
</article>"""


def slide_update(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    return f"""
<article class="post paper grid-bg update" data-kind="update">
  <div class="vlabel">Fig. {idx:02d} — Belief update</div>
  <div class="content">
    <div class="before"><span class="pill">Before</span><p data-fit="140">{e(card.before)}</p></div>
    <div class="delta"><div class="stem"></div><span><b>Δ</b>New evidence</span></div>
    <div class="now"><div class="halftone"></div><span class="pill fill">Now</span><p data-fit="300">{e(card.now)}</p></div>
    <div class="conf"><span>Confidence</span>{_dots(card.confidence)}<span>{e(card.confidence.title())}</span></div>
  </div>
  {_chrome("update", idx, total, "The update", domain(record["source"]["url"]))}
</article>"""


def slide_number(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    num = card.number
    assert num is not None
    size = min(560, int(1000 / (0.62 * max(len(num.value), 1))))
    style = f'style="font-size:{size}px"'
    take = (
        f'<div class="take"><p class="kicker">Our take — opinion</p><p class="quote" data-fit="200">“{e(card.opinion)}”</p></div>'
        if card.opinion else ""
    )
    return f"""
<article class="post acc number" data-kind="number">
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


def slide_confidence(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    import math

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
<article class="post ink confidence" data-kind="confidence">
  <div class="content">
    <h2 class="h-xl">How sure<br>are we?</h2>
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


def slide_closing(card: Card, record: dict[str, Any], idx: int, total: int, show_opinion: bool) -> str:
    if card.confidence == "high":
        title = 'What to<br><span class="serif">watch.</span>'
    else:
        title = 'We don\'t<br>know <span class="serif">yet.</span>'
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
<article class="post ink unknown" data-kind="closing">
  <div class="content">
    <h2 class="h-xxl">{title}</h2>
    {prior}{opinion}{questions}
  </div>
  {_chrome("closing", idx, total, "Open question" if card.confidence != "high" else "Next", domain(record["source"]["url"]), "Follow for the update")}
</article>"""


def plan_slides(card: Card) -> list[str]:
    kinds = ["cover"]
    if card.before and card.now:
        kinds.append("update")
    if card.number:
        kinds.append("number")
    kinds.append("confidence")
    if card.open_questions or (card.opinion and not card.number):
        kinds.append("closing")
    return kinds


def build_slides(card: Card, record: dict[str, Any]) -> list[str]:
    kinds = plan_slides(card)
    total = len(kinds)
    out = []
    for i, kind in enumerate(kinds, start=1):
        if kind == "cover":
            out.append(slide_cover(card, record, i, total))
        elif kind == "update":
            out.append(slide_update(card, record, i, total))
        elif kind == "number":
            out.append(slide_number(card, record, i, total))
        elif kind == "confidence":
            out.append(slide_confidence(card, record, i, total))
        else:
            out.append(slide_closing(card, record, i, total, show_opinion=card.number is None))
    return out


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
    base = json_path.with_suffix("")
    page_path = base.with_name(base.name + ".slides.html")
    page_path.write_text(build_page(build_slides(card, record), palette), encoding="utf-8")
    images, warnings, jpegs = screenshot(page_path, base)

    record["images"] = [str(p) for p in images]
    record["images_jpg"] = [str(p) for p in jpegs]
    record["image_warnings"] = card.warnings + warnings
    record["palette"] = palette
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    json_path.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8")
    log.info("rendered", extra={"path": str(json_path), "images": len(images), "warnings": len(record["image_warnings"])})
    return images
