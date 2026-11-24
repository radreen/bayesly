/* Bayesly post decorations. Shared by design/posts.html and bayesly/render.py.
 * Draws bell curves, gauge ticks, flat-prior histogram, circular badges, crop marks,
 * then shrinks [data-fit] text to fit its box. Sets <body data-ready="1"> when done
 * so the renderer knows it can take screenshots.
 */
(function () {
  var NS = "http://www.w3.org/2000/svg";

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function decorate() {
    // Logo: replace the text fallback with the outlined [b]ayesly SVG (design/logo.js).
    if (window.BAYESLY_LOGO) {
      document.querySelectorAll(".wordmark").forEach(function (el) {
        el.innerHTML = window.BAYESLY_LOGO;
        el.setAttribute("aria-label", "bayesly");
      });
    }

    // Gaussian "bell" curves: data-bell="cx,sigma,height,baseline"
    document.querySelectorAll("[data-bell]").forEach(function (el) {
      var v = el.dataset.bell.split(",").map(Number), cx = v[0], s = v[1], h = v[2], base = v[3];
      var floor = el.dataset.floor ? Number(el.dataset.floor) : base;
      var pts = [];
      for (var x = 0; x <= 1080; x += 8) {
        var y = base - h * Math.exp(-0.5 * Math.pow((x - cx) / s, 2));
        pts.push((x === 0 ? "M" : "L") + x + " " + y.toFixed(1));
      }
      var d = pts.join(" ");
      if (el.hasAttribute("data-closed")) d += " L1080 " + floor + " L0 " + floor + " Z";
      el.setAttribute("d", d);
    });

    // Bell curves sized to their own viewBox width: data-bellw="cx,sigma,height,baseline,width"
    document.querySelectorAll("[data-bellw]").forEach(function (el) {
      var v = el.dataset.bellw.split(",").map(Number), cx = v[0], s = v[1], h = v[2], base = v[3], w = v[4];
      var pts = [];
      for (var x = 0; x <= w; x += 4) pts.push((x ? "L" : "M") + x + " " + (base - h * Math.exp(-0.5 * Math.pow((x - cx) / s, 2))).toFixed(1));
      var d = pts.join(" ");
      if (el.hasAttribute("data-closed")) d += " L" + w + " " + base + " L0 " + base + " Z";
      el.setAttribute("d", d);
    });

    // Gauge ticks
    document.querySelectorAll(".gauge .ticks").forEach(function (g) {
      for (var a = 0; a <= 180; a += 10) {
        var r = a * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
        var l = document.createElementNS(NS, "line");
        l.setAttribute("class", "tick");
        l.setAttribute("x1", 260 + 178 * c); l.setAttribute("y1", 270 - 178 * s);
        l.setAttribute("x2", 260 + (a % 30 ? 166 : 156) * c); l.setAttribute("y2", 270 - (a % 30 ? 166 : 156) * s);
        g.appendChild(l);
      }
    });

    // Uniform histogram for the "flat prior"
    document.querySelectorAll(".uniform").forEach(function (g) {
      for (var i = 0; i < 26; i++) {
        var r = document.createElementNS(NS, "rect");
        r.setAttribute("x", i * 36 + 3); r.setAttribute("y", 48);
        r.setAttribute("width", 28); r.setAttribute("height", 68);
        r.setAttribute("fill", i % 2 ? "var(--accent)" : "none");
        r.setAttribute("stroke", "var(--accent)"); r.setAttribute("stroke-width", 2.5);
        g.appendChild(r);
      }
    });

    // Circular text badges
    document.querySelectorAll(".badge[data-text]").forEach(function (el, i) {
      var id = "circ" + i, txt = el.dataset.text;
      // Repeat the phrase to roughly fill the circle (~60 mono chars at this size), then fit exactly.
      var full = txt.repeat(Math.max(1, Math.round(60 / txt.length)));
      var svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 200 200");
      svg.innerHTML =
        '<defs><path id="' + id + '" d="M100 100 m-82 0 a82 82 0 1 1 164 0 a82 82 0 1 1 -164 0"/></defs>' +
        '<circle cx="100" cy="100" r="97" fill="none" stroke="currentColor" stroke-width="2"/>' +
        '<text><textPath href="#' + id + '" textLength="513" lengthAdjust="spacing">' + esc(full) + '</textPath></text>';
      el.insertBefore(svg, el.firstChild);
    });

    // (Corner crop marks removed 2026-11-24: owner found them visual noise.)
  }

  // Shrink text in [data-fit="<max height px>"] until it fits (down to 55% of its size).
  function fitText() {
    document.querySelectorAll("[data-fit]").forEach(function (el) {
      var max = Number(el.dataset.fit);
      var size = parseFloat(getComputedStyle(el).fontSize), min = size * 0.55;
      while (el.scrollHeight > max && size > min) {
        size *= 0.95;
        el.style.fontSize = size + "px";
      }
      if (el.scrollHeight > max) el.setAttribute("data-overflow", "1");
    });
  }

  function run() {
    decorate();
    // Request every face explicitly: fonts.ready alone can resolve before a face the
    // page uses has even been fetched, and fitText would then measure a fallback font.
    var faces = ["900 100px 'Inter Tight'", "800 100px 'Inter Tight'",
                 "700 40px 'Inter Tight'", "600 40px 'Inter Tight'", "500 20px 'JetBrains Mono'",
                 "700 20px 'JetBrains Mono'", "italic 400 40px 'Instrument Serif'"];
    var fonts = document.fonts
      ? Promise.all(faces.map(function (f) { return document.fonts.load(f).catch(function () {}); }))
          .then(function () { return document.fonts.ready; })
      : Promise.resolve();
    // Wait for cover photos too; a photo that fails to load is hidden so the text cover look stays clean.
    var imgs = Array.prototype.slice.call(document.images).map(function (img) {
      return (img.decode ? img.decode() : Promise.resolve()).catch(function () { img.style.display = "none"; });
    });
    Promise.all([fonts].concat(imgs)).then(function () {
      fitText();
      document.body.dataset.ready = "1";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
