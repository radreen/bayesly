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
import re
from html import escape, unescape
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
    """Headline with the highlight phrase coloured (no box): readable at feed size."""
    if not card.highlight:
        return e(card.headline)
    pre, _, post = card.headline.partition(card.highlight)
    return f'{e(pre)}<span class="key">{e(card.highlight)}</span>{e(post)}'


# ---------------------------------------------------------------------------------------------
# Simple design (docs/competitor-analysis.md): what the niche proved works, plus our difference.
#   cover    source photo on top, big headline below, confidence chip  (photo-less fallback: text cover)
#   facts    "What changed": numbered Before / Now / The number / Catch
#   take     our opinion + one question with two sides, to start the comments
# ---------------------------------------------------------------------------------------------
CONF_LABEL = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence"}
SOURCE_LABEL = {"primary": "Primary source", "press": "Press report", "research": "Research",
                "preprint": "Preprint · not peer reviewed", "other": "Source"}
DEFAULT_QUESTION = {
    "high": "Does this change how you work, or is it noise?",
    "medium": "Would you trust this before independent tests?",
    "low": "Real shift or vendor hype? What would convince you?",
}


def _foot(left: str, right: str) -> str:
    return f'<div class="s-foot"><span>{e(left)}</span><span>{e(right)}</span></div>'


def _brand_row() -> str:
    return '<div class="s-brand"><div class="wordmark">bayesly<i></i></div><span>@bayesly.ai</span></div>'


def _chip(card: Card, record: dict[str, Any]) -> str:
    kind = SOURCE_LABEL.get(source_label(record), "Source")
    return (f'<div class="s-chip conf-{e(card.confidence)}"><b>●</b> '
            f'{e(CONF_LABEL.get(card.confidence, "Confidence: ?"))} · {e(kind)}</div>')


def slide_cover(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    src = record["source"]
    photo = record.get("cover_image")
    right = "Swipe →" if total > 1 else "@bayesly.ai"
    if photo:
        credit = f"Image: {src.get('name') or domain(src['url'])}"
        return f"""
<article class="post s-cover" data-kind="cover" data-layout="photo">
  <img class="s-img" src="{e(photo)}" alt="">
  <div class="s-fade"></div>
  <div class="s-top"><div class="wordmark">bayesly<i></i></div><span class="pill">{e(card.category)}</span></div>
  <div class="s-credit">{e(credit)}</div>
  {_chip(card, record)}
  <h2 class="s-head" data-fit="390">{_headline_html(card)}</h2>
  {_foot("Source: " + domain(src["url"]), right)}
</article>"""
    # No usable photo (text banners, papers, release notes): the headline is the picture.
    return f"""
<article class="post s-cover s-textcover" data-kind="cover" data-layout="text">
  <div class="s-mark" aria-hidden="true"></div>
  <div class="s-top"><div class="wordmark">bayesly<i></i></div><span class="pill">{e(card.category)}</span></div>
  <p class="s-kicker">{e(src.get("name") or domain(src["url"]))}</p>
  <h2 class="s-head" data-fit="620">{_headline_html(card)}</h2>
  {_chip(card, record)}
  {_foot("Source: " + domain(src["url"]), right)}
</article>"""


def _facts(card: Card) -> list[tuple[str, str]]:
    rows = [("Before", card.before), ("Now", card.now)]
    if card.number:
        rows.append(("The number", f"{card.number.value} — {card.number.caption}"))
    if card.evidence_against:
        rows.append(("Catch", card.evidence_against[0]))
    elif card.open_questions:
        rows.append(("Still unknown", card.open_questions[0]))
    return [(k, v) for k, v in rows if v]


def slide_facts(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    items = "".join(
        f'<li><span class="n">{i}</span><div><p class="k">{e(k)}</p><p class="v">{e(v)}</p></div></li>'
        for i, (k, v) in enumerate(_facts(card), start=1)
    )
    footnote = f'<p class="s-note">{e(card.number.footnote)}</p>' if card.number and card.number.footnote else ""
    return f"""
<article class="post s-text" data-kind="facts">
  {_brand_row()}
  <h3 class="s-h3">What changed</h3>
  <ol class="s-list" data-fit="820">{items}</ol>
  {footnote}
  {_foot("Source: " + domain(record["source"]["url"]), f"{idx} / {total}")}
</article>"""


def _opinion(card: Card) -> str:
    text = (card.opinion or "").strip()
    for prefix in ("Our take:", "Our take -", "Our take —", "Opinion:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text


def slide_take(card: Card, record: dict[str, Any], idx: int, total: int) -> str:
    question = ((record.get("draft") or {}).get("question") or "").strip() or DEFAULT_QUESTION.get(
        card.confidence, DEFAULT_QUESTION["medium"])
    opinion = _opinion(card) or card.confidence_reason
    return f"""
<article class="post s-take" data-kind="take">
  {_brand_row()}
  <p class="s-kicker">Our take — opinion</p>
  <p class="s-quote" data-fit="560">{e(opinion)}</p>
  <div class="s-ask"><p class="s-kicker">Your call</p><p class="q" data-fit="200">{e(question)}</p>
    <p class="cta">Tell us in the comments ↓</p></div>
  {_foot("Follow @bayesly.ai", f"{idx} / {total}")}
</article>"""


def plan_slides(card: Card) -> list[str]:
    kinds = ["cover"]
    if len(_facts(card)) >= 2:
        kinds.append("facts")
    kinds.append("take")
    return kinds


def build_slides(card: Card, record: dict[str, Any]) -> list[str]:
    kinds = plan_slides(card)
    total = len(kinds)
    build = {"cover": slide_cover, "facts": slide_facts, "take": slide_take}
    return [build[k](card, record, i, total) for i, k in enumerate(kinds, start=1)]


# ---- cover photo: the source's own og:image ------------------------------------------------
_OG_RE = [
    re.compile(r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE),
]


def find_cover_image(url: str, *, timeout: float = 15, session: Any = None) -> str | None:
    """og:image of the source page, or None. Network errors mean "no photo", never a failure."""
    from bayesly import net

    try:
        html = net.request("GET", url, timeout=timeout, max_retries=1, session=session).text[:300_000]
    except Exception:  # noqa: BLE001 - a missing photo falls back to the text cover
        return None
    for rx in _OG_RE:
        m = rx.search(html)
        if m and m.group(1).startswith("https://"):
            return unescape(m.group(1))
    return None


def photo_allowed(source_id: str) -> bool:
    """Per-source switch in config/sources.yaml (cover_image: false for text-banner og:images)."""
    try:
        from bayesly.config import load_settings, load_sources
        for s in load_sources(load_settings().sources_path):
            if s.id == source_id:
                return s.cover_image
    except Exception:  # noqa: BLE001
        pass
    return not source_id.startswith(("arxiv", "gh-"))


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
    if "cover_image" not in record:          # looked up once, then kept in the record
        src = record["source"]
        record["cover_image"] = find_cover_image(src["url"]) if photo_allowed(src.get("id", "")) else None
    base = json_path.with_suffix("")
    page_path = base.with_name(base.name + ".slides.html")
    page_path.write_text(build_page(build_slides(card, record), palette), encoding="utf-8")
    images, warnings, jpegs = screenshot(page_path, base)

    record["images"] = [str(p) for p in images]
    record["images_jpg"] = [str(p) for p in jpegs]
    record["image_warnings"] = card.warnings + warnings
    record["palette"] = palette
    record["layout"] = "photo" if record.get("cover_image") else "text"
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    json_path.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8")
    log.info("rendered", extra={"path": str(json_path), "images": len(images), "warnings": len(record["image_warnings"])})
    return images
