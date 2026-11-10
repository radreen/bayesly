"""Build design/logo/bmark-board.html (comparison + lockups) and SVG files for the b mark."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
data = json.loads(subprocess.check_output([sys.executable, str(HERE / "bmark.py")]))
INK, LIME, PAPER = "#0d0d0e", "#c6ff2e", "#f5f5f0"
LABELS = {
    "original": "Original (reconstructed)",
    "r1_fused": "R1 · optical weight + fused neck",
    "r2_split": "R2 · optical weight + clean split",
    "r3_matched": "R3 · matched to Funnel Display + fused neck",
    "r4_matched_split": "R4 · matched to Funnel Display + clean split",
}

def svg(name, color, cls=""):
    v = data[name]; top = v["top"]
    # tight box: x 100..500, y top..700 (ink bounds)
    return (f'<svg class="mk {cls}" data-top="{top}" viewBox="100 {top} 400 {700 - top}" xmlns="http://www.w3.org/2000/svg">'
            f'<path fill="{color}" d="{v["path"]}"/></svg>')

out = HERE / "bmark"
out.mkdir(exist_ok=True)
for name in data:
    for cname, col in (("ink", INK), ("lime", LIME), ("paper", PAPER)):
        (out / f"bayesly-b-{name}-{cname}.svg").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n' + svg(name, col).replace(' class="mk "', ""), encoding="utf-8")

cards = "".join(
    f'<figure><div class="big">{svg(n, INK)}</div><figcaption>{LABELS[n]}</figcaption>'
    f'<div class="sizes">' + "".join(f'<i style="width:{s}px;height:{s}px">{svg(n, LIME)}</i>' for s in (96, 48, 32, 20)) + "</div></figure>"
    for n in data)

def lockup(name, word_color, mark_color, bg, integrated=True, weight=700):
    """integrated: the mark IS the first letter -> [b]ayesly. Otherwise mark + full word."""
    word = "ayesly" if integrated else "bayesly"
    cls = "lk int" if integrated else "lk"
    return (f'<div class="lock" style="background:{bg}"><div class="row" data-mark="{name}">'
            f'{svg(name, mark_color, cls)}<span class="word" style="color:{word_color};font-weight:{weight}">{word}</span></div></div>')

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Bayesly b mark</title>
<link href="https://fonts.googleapis.com/css2?family=Funnel+Display:wght@600;700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
body{{margin:0;padding:32px 40px 60px;background:#9d9d98;font-family:'JetBrains Mono',monospace}}
h1{{font:700 14px 'JetBrains Mono';text-transform:uppercase;letter-spacing:.1em;margin:0 0 16px}}
h2{{font:700 13px 'JetBrains Mono';text-transform:uppercase;letter-spacing:.1em;margin:34px 0 12px}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
figure{{margin:0;background:{LIME};padding:22px 18px 14px}}
.big{{height:330px;display:grid;place-items:center}} .big svg{{height:300px;width:auto}}
figcaption{{font:700 11px 'JetBrains Mono';text-transform:uppercase;letter-spacing:.06em;margin:14px 0 10px;min-height:30px}}
.sizes{{display:flex;gap:10px;align-items:flex-end}}
.sizes i{{background:{INK};border-radius:50%;display:grid;place-items:center}}
.sizes i svg{{height:58%;width:auto}}
.locks{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.lock{{height:300px;display:grid;place-items:center}}
.row{{display:flex;align-items:flex-end;gap:0}}
.word{{font-family:'Funnel Display';font-weight:700;font-size:150px;letter-spacing:-0.035em;line-height:1}}
.lk{{width:auto;flex:none}}
</style></head><body>
<h1>Bayesly — your b, reconstructed and refined</h1>
<div class="grid">{cards}</div>
<h2>Lockups · [b]ayesly with Funnel Display Bold 700 (top) · with SemiBold 600, matched to the b's weight (middle) · mark + word (bottom)</h2>
<div class="locks">{lockup("r4_matched_split", INK, INK, LIME)}{lockup("r4_matched_split", PAPER, PAPER, INK)}
{lockup("r4_matched_split", INK, INK, LIME, weight=600)}{lockup("r4_matched_split", PAPER, PAPER, INK, weight=600)}
{lockup("r4_matched_split", INK, INK, LIME, integrated=False)}{lockup("r4_matched_split", PAPER, LIME, INK, integrated=False)}</div>
<script>
// Size each lockup mark so its ring (y 300..700 = 400 units) equals the font's x-height and its
// bottom sits on the baseline; gap = 0.18 x-height.
Promise.all([document.fonts.load("700 150px 'Funnel Display'"), document.fonts.load("600 150px 'Funnel Display'")]).then(function () {{
  var ctx = document.createElement("canvas").getContext("2d");
  ctx.font = "700 150px 'Funnel Display'";
  var xh = ctx.measureText("x").actualBoundingBoxAscent;
  var m = ctx.measureText("bayesly"), fa = m.fontBoundingBoxAscent, fd = m.fontBoundingBoxDescent;
  var baselineInSpan = (150 - (fa + fd)) / 2 + fa;      // line-height:1
  var below = 150 - baselineInSpan;                        // span bottom below the baseline
  document.querySelectorAll(".lk").forEach(function (s) {{
    var top = Number(s.dataset.top), unit = xh / 400;      // px per mark unit
    s.style.height = ((700 - top) * unit) + "px";
    s.style.marginBottom = below + "px";
    // as a letter: the font's own b-to-a spacing (about 0.04 x-height after -35 tracking); as a symbol: wider
    s.style.marginRight = (s.classList.contains("int") ? xh * 0.045 : xh * 0.22) + "px";
  }});
  document.body.dataset.ready = "1";
}});
</script></body></html>"""
(HERE / "bmark-board.html").write_text(html, encoding="utf-8")
print("ok", list(data))
