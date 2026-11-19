"""Render the profile kit (design/profile/kit.html) to PNG/JPEG files in design/profile/export/.

    python design/profile/render_profile.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bayesly.render import _launch  # noqa: E402  (same browser fallback as the post renderer)

KIT = Path(__file__).with_name("kit.html")
OUT = Path(__file__).with_name("export")
# element id -> (png, also jpeg?)
TARGETS = {
    "x-header": True,
    "intro-01": True, "intro-02": True, "intro-03": True, "intro-04": True,
    "highlight-about": False, "highlight-updates": False, "highlight-tested": False,
}


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(exist_ok=True)
    warnings = []
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
            page.goto(KIT.as_uri(), timeout=30000)
            page.wait_for_selector('body[data-ready="1"]', timeout=30000)
            if not page.evaluate("document.fonts.check('800 40px \"Inter Tight\"')"):
                warnings.append("brand fonts did not load")
            if page.locator("[data-overflow]").count():
                warnings.append("some text overflowed")
            for el_id, jpeg in TARGETS.items():
                el = page.locator(f"#{el_id}")
                el.screenshot(path=str(OUT / f"{el_id}.png"))
                if jpeg:
                    el.screenshot(path=str(OUT / f"{el_id}.jpg"), type="jpeg", quality=92)
                print(f"  {el_id}")
        finally:
            browser.close()
    for w in warnings:
        print(f"WARNING: {w}")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
