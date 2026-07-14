/* The temperature planet — OpenThomas's page.

   A live globe wearing the world's current 2 m temperature (cold blue → hot red),
   with coastlines, country and state borders, and every weather market pinned
   where its weather is: faint for the whole book, glowing for the edges we found,
   the bets we hold, the wins and losses we've settled. One pin per place — hover
   for how many markets sit there, click to open them all.

   Canvas2D orthographic (verified against Natural Earth). The heat field is drawn
   to a small offscreen buffer and upscaled — smooth and cheap — and only redrawn
   when the view moves. There is no land polygon fill, so a zoomed-in view can
   never flood: the field is the surface, coastlines and borders are just lines. */

(function () {
  "use strict";
  var LAND = window.WORLD_LAND || [];
  var BORDERS = window.WORLD_BORDERS || { countries: [], admin: [] };
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;

  var STYLES = {
    pending: { fill: "#ffffff", ring: "#f0803a", glow: "#f0a24e", r: 5 },
    held:    { fill: "#f0803a", ring: "#8f3f0e", glow: "#ffb066", r: 6 },
    won:     { fill: "#46c882", ring: "#187048", glow: "#7ae4a8", r: 5 },
    lost:    { fill: "#e85642", ring: "#93291d", glow: "#ff8570", r: 5 },
  };
  var ORDER = { held: 3, won: 2, lost: 1, pending: 0 };

  // temperature (°C) → colour, cold blue → hot red
  var STOPS = [[-25, 46, 86, 196], [-8, 58, 150, 214], [6, 92, 196, 206],
               [16, 206, 224, 214], [24, 238, 176, 74], [32, 226, 92, 50], [44, 158, 26, 26]];
  function tcol(t, out) {
    if (t <= STOPS[0][0]) { out[0] = STOPS[0][1]; out[1] = STOPS[0][2]; out[2] = STOPS[0][3]; return; }
    for (var i = 0; i < STOPS.length - 1; i++) {
      var a = STOPS[i], b = STOPS[i + 1];
      if (t <= b[0]) { var f = (t - a[0]) / (b[0] - a[0]);
        out[0] = a[1] + (b[1] - a[1]) * f; out[1] = a[2] + (b[2] - a[2]) * f; out[2] = a[3] + (b[3] - a[3]) * f; return; }
    }
    var e = STOPS[STOPS.length - 1]; out[0] = e[1]; out[1] = e[2]; out[2] = e[3];
  }

  function Stars(canvas) {
    var ctx = canvas.getContext("2d"), dpr = Math.min(devicePixelRatio || 1, 2), stars = [];
    function seed(w, h) {
      stars = []; var n = Math.round(w * h / 1600), s = 20261;
      function rnd() { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }
      for (var i = 0; i < n; i++) stars.push([rnd() * w, rnd() * h, rnd(), rnd() < 0.12 ? 1.6 : 0.9]);
    }
    function draw() {
      var w = canvas.clientWidth, h = canvas.clientHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      if (!stars.length) seed(w, h);
      for (var i = 0; i < stars.length; i++) { var st = stars[i];
        ctx.beginPath(); ctx.arc(st[0], st[1], st[3], 0, 7);
        ctx.fillStyle = "rgba(200,214,238," + (0.25 + st[2] * 0.6).toFixed(2) + ")"; ctx.fill(); }
    }
    addEventListener("resize", function () { stars = []; draw(); }); draw();
  }

  function Globe(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    var reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    var dpr = Math.min(devicePixelRatio || 1, 2);
    var markers = [], GRID = null, gridV = 0;
    var rot = -95, tilt = 20, zoom = 1;
    var dragging = false, moved = false, lastX = 0, lastY = 0, idleAt = 0, hovered = null;
    var W = 0, H = 0, cx = 0, cy = 0, R = 0, raf = 0;
    var fld = document.createElement("canvas"), fctx = fld.getContext("2d"), fldKey = "";

    function syncSize() {
      var w = canvas.clientWidth, h = canvas.clientHeight;
      var cw = Math.round(w * dpr), ch = Math.round(h * dpr);
      if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
      W = w; H = h; cx = w / 2; cy = h / 2; R = Math.min(w, h) * 0.42 * zoom;
    }
    function project(lon, lat) {
      var l0 = rot * D2R, p0 = tilt * D2R, lam = lon * D2R, phi = lat * D2R;
      var c = Math.sin(p0) * Math.sin(phi) + Math.cos(p0) * Math.cos(phi) * Math.cos(lam - l0);
      return { x: cx + R * Math.cos(phi) * Math.sin(lam - l0),
               y: cy - R * (Math.cos(p0) * Math.sin(phi) - Math.sin(p0) * Math.cos(phi) * Math.cos(lam - l0)),
               c: c, vis: c >= -0.02 };
    }
    function sampleTemp(lon, lat) {
      if (!GRID) return null;
      var fx = (lon - GRID.lon0) / GRID.dlon, fy = (lat - GRID.lat0) / GRID.dlat;
      var x0 = Math.floor(fx), y0 = Math.floor(fy);
      if (y0 < 0) y0 = 0; if (y0 > GRID.ny - 2) y0 = GRID.ny - 2;
      var tx = fx - x0, ty = fy - y0;
      var xa = ((x0 % GRID.nx) + GRID.nx) % GRID.nx, xb = (xa + 1) % GRID.nx;
      var T = GRID.temps;
      function g(xi, yi) { var v = T[yi * GRID.nx + xi]; return v == null ? (28 - 0.6 * Math.abs(lat)) : v; }
      var t00 = g(xa, y0), t10 = g(xb, y0), t01 = g(xa, y0 + 1), t11 = g(xb, y0 + 1);
      return (t00 * (1 - tx) + t10 * tx) * (1 - ty) + (t01 * (1 - tx) + t11 * tx) * ty;
    }

    function renderField() {
      var key = rot.toFixed(2) + "," + tilt.toFixed(2) + "," + R.toFixed(1) + "," + W + "," + H + "," + gridV;
      if (key === fldKey) return;                 // view unchanged → reuse buffer
      fldKey = key;
      var FS = W * H > 900000 ? 0.4 : 0.5;         // low-res: the field is smooth
      var fw = Math.max(2, Math.round(W * FS)), fh = Math.max(2, Math.round(H * FS));
      if (fld.width !== fw || fld.height !== fh) { fld.width = fw; fld.height = fh; }
      var img = fctx.createImageData(fw, fh), data = img.data;
      var l0 = rot * D2R, p0 = tilt * D2R, sinp0 = Math.sin(p0), cosp0 = Math.cos(p0);
      var Rf = R * FS, cxf = cx * FS, cyf = cy * FS, col = [0, 0, 0];
      for (var yy = 0; yy < fh; yy++) {
        for (var xx = 0; xx < fw; xx++) {
          var dx = (xx - cxf) / Rf, dy = -(yy - cyf) / Rf, rho2 = dx * dx + dy * dy, idx = (yy * fw + xx) * 4;
          if (rho2 > 1) { data[idx + 3] = 0; continue; }
          var cc = Math.sqrt(1 - rho2);
          var phi = Math.asin(cc * sinp0 + dy * cosp0);
          var lam = l0 + Math.atan2(dx, cc * cosp0 - dy * sinp0);
          if (GRID) tcol(sampleTemp(lam * R2D, phi * R2D), col);
          else { col[0] = 30 + 26 * cc; col[1] = 74 + 60 * cc; col[2] = 128 + 70 * cc; }  // plain blue planet
          var sh = 0.58 + 0.42 * cc;
          data[idx] = col[0] * sh; data[idx + 1] = col[1] * sh; data[idx + 2] = col[2] * sh; data[idx + 3] = 255;
        }
      }
      fctx.putImageData(img, 0, 0);
    }

    function strokeLines(coll, style, width) {
      ctx.strokeStyle = style; ctx.lineWidth = width;
      for (var r = 0; r < coll.length; r++) { var ring = coll[r], first = true; ctx.beginPath();
        for (var i = 0; i < ring.length; i++) { var p = project(ring[i][0], ring[i][1]);
          if (p.vis) { first ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y); first = false; } else first = true; }
        ctx.stroke(); }
    }

    function draw(now) {
      syncSize();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      var atm = ctx.createRadialGradient(cx, cy, R * 0.94, cx, cy, R * 1.22);
      atm.addColorStop(0, "rgba(88,168,240,0)"); atm.addColorStop(0.5, "rgba(96,176,248,.26)");
      atm.addColorStop(1, "rgba(96,176,248,0)");
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.22, 0, 7); ctx.fillStyle = atm; ctx.fill();

      renderField();
      ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.clip();
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(fld, 0, 0, fld.width, fld.height, 0, 0, W, H);
      strokeLines(BORDERS.admin, "rgba(255,255,255,.16)", 1);
      strokeLines(BORDERS.countries, "rgba(255,255,255,.3)", 1);
      strokeLines(LAND, "rgba(6,14,24,.6)", 1);
      ctx.restore();
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.strokeStyle = "rgba(150,190,230,.35)"; ctx.lineWidth = 1; ctx.stroke();

      for (var i = 0; i < markers.length; i++) {
        var m = markers[i], p = project(m.lon, m.lat);
        m._sx = p.x; m._sy = p.y; m._vis = p.vis;
        if (!p.vis || m.state !== "market") continue;
        ctx.beginPath(); ctx.arc(p.x, p.y, m === hovered ? 2.7 : 1.9, 0, 7);
        ctx.fillStyle = m === hovered ? "rgba(240,248,255,.98)" : "rgba(226,240,255,.62)"; ctx.fill();
      }
      var ours = markers.filter(function (m) { return m.state !== "market"; })
        .sort(function (a, b) { return (ORDER[a.state] || 0) - (ORDER[b.state] || 0); });
      for (var k = 0; k < ours.length; k++) {
        var o = ours[k], q = project(o.lon, o.lat);
        if (!q.vis) continue;
        var st = STYLES[o.state] || STYLES.pending, rad = st.r * (0.85 + 0.55 * (o.weight || 0.3));
        if (!reduced && o.state === "held") { var t = (now % 1700) / 1700;
          ctx.beginPath(); ctx.arc(q.x, q.y, rad + t * 12, 0, 7);
          ctx.strokeStyle = "rgba(240,128,58," + (0.5 * (1 - t)).toFixed(3) + ")"; ctx.lineWidth = 1.5; ctx.stroke(); }
        ctx.save(); ctx.shadowColor = st.glow; ctx.shadowBlur = o === hovered ? 20 : 12;
        ctx.beginPath(); ctx.arc(q.x, q.y, rad, 0, 7); ctx.fillStyle = st.fill; ctx.fill(); ctx.restore();
        ctx.beginPath(); ctx.arc(q.x, q.y, rad, 0, 7); ctx.fillStyle = st.fill; ctx.fill();
        ctx.lineWidth = 1.6; ctx.strokeStyle = st.ring; ctx.stroke();
        if (o.count > 1) {                                   // a cluster: a thin outer ring
          ctx.beginPath(); ctx.arc(q.x, q.y, rad + 3.5, 0, 7);
          ctx.strokeStyle = "rgba(255,255,255,.5)"; ctx.lineWidth = 1; ctx.stroke();
        }
        if (o === hovered) { ctx.beginPath(); ctx.arc(q.x, q.y, rad + 5, 0, 7);
          ctx.strokeStyle = st.ring; ctx.lineWidth = 1; ctx.stroke(); }
      }

      if (!dragging && !hovered && !reduced && now - idleAt > 2200) rot += 0.04;
      raf = requestAnimationFrame(draw);
    }

    function hit(ex, ey) {
      var best = null, bd = 15 * 15;
      for (var i = 0; i < markers.length; i++) { var m = markers[i]; if (!m._vis) continue;
        var dx = m._sx - ex, dy = m._sy - ey, d = dx * dx + dy * dy;
        var pref = m.state === "market" ? d + 40 : d;
        if (pref < bd) { bd = pref; best = m; } }
      return best;
    }
    canvas.addEventListener("pointerdown", function (e) {
      dragging = true; moved = false; lastX = e.clientX; lastY = e.clientY; idleAt = performance.now();
      canvas.setPointerCapture(e.pointerId); canvas.style.cursor = "grabbing"; });
    canvas.addEventListener("pointermove", function (e) {
      if (dragging) {
        if (Math.abs(e.clientX - lastX) + Math.abs(e.clientY - lastY) > 3) moved = true;
        rot -= (e.clientX - lastX) * 0.32 / zoom;
        tilt = Math.max(-85, Math.min(85, tilt + (e.clientY - lastY) * 0.32 / zoom));
        lastX = e.clientX; lastY = e.clientY; idleAt = performance.now();
      } else { var r = canvas.getBoundingClientRect(), h = hit(e.clientX - r.left, e.clientY - r.top);
        if (h !== hovered) { hovered = h; canvas.style.cursor = h ? "pointer" : "grab";
          if (opts.onHover) opts.onHover(h, h ? h._sx : 0, h ? h._sy : 0); } } });
    function endDrag(e) { dragging = false; idleAt = performance.now(); canvas.style.cursor = "grab";
      if (e && e.pointerId != null && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId); }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", function () {
      if (hovered) { hovered = null; if (opts.onHover) opts.onHover(null); canvas.style.cursor = "grab"; } });
    canvas.addEventListener("wheel", function (e) { e.preventDefault();
      zoom = Math.max(0.85, Math.min(5, zoom * (1 - e.deltaY * 0.0012))); idleAt = performance.now(); }, { passive: false });
    canvas.addEventListener("click", function (e) {
      if (moved) return;
      var r = canvas.getBoundingClientRect(), h = hit(e.clientX - r.left, e.clientY - r.top);
      if (h && opts.onSelect) opts.onSelect(h); });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else if (!raf) raf = requestAnimationFrame(draw); });

    canvas.style.cursor = "grab"; syncSize(); raf = requestAnimationFrame(draw);
    return {
      focus: function (lon, lat) { rot = -lon; tilt = Math.max(-70, Math.min(70, lat)); idleAt = performance.now() + 4000; },
      setMarkers: function (list) { markers = list || []; },
      setTemps: function (grid) { GRID = grid || null; gridV++; },
    };
  }

  window.OTGlobe = {
    api: null,
    init: function (id, opts) { var c = document.getElementById(id); if (!c || !c.getContext) return;
      try { this.api = Globe(c, opts || {}); } catch (e) { var b = document.getElementById("globe"); if (b) b.classList.add("no-gl"); } },
    setMarkers: function (l) { if (this.api) this.api.setMarkers(l); },
    setTemps: function (g) { if (this.api) this.api.setTemps(g); },
    focus: function (lon, lat) { if (this.api) this.api.focus(lon, lat); },
  };
  window.OTStars = { init: function (id) { var c = document.getElementById(id); if (c && c.getContext) try { Stars(c); } catch (e) {} } };
})();
