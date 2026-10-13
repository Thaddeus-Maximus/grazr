/* grazr front end: maps (Leaflet), paddock drawing (Geoman), editor helpers. */
"use strict";

const grazr = (() => {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  function restColor(days, full) {
    if (days === null || days === undefined) return "#9e9e9e";
    const t = Math.max(0, Math.min(1, days / full));
    return `hsl(${Math.round(120 * t)}, ${75 - 15 * t}%, ${45 - 7 * t}%)`;
  }

  function fmtDays(d) {
    if (d === null || d === undefined) return "never grazed";
    if (d === 0) return "occupied";
    return d >= 2 ? `${Math.round(d)} days` : `${Math.round(d * 24)} hours`;
  }

  function baseMap(el, mapOpts = {}) {
    const cfg = JSON.parse(el.dataset.config);
    const map = L.map(el, {zoomSnap: 0.5, ...mapOpts}).setView(cfg.center, cfg.zoom);
    L.tileLayer("/tiles/{z}/{x}/{y}", {
      minZoom: cfg.min_zoom, maxZoom: 22, maxNativeZoom: cfg.max_zoom,
      attribution: "Imagery: USGS",
    }).addTo(map);
    return {map, cfg};
  }

  const pageUrl = (id) => "/p/" + encodeURIComponent(id);
  const herdLinks = (ids) => ids.map((h) => `<a class="link herds" href="/h/${encodeURIComponent(h)}">${esc(h)}</a>`).join(", ");

  /* The rest map: faces coloured by days rested, parents as outlines (§8.3). */
  async function restMap(el, opts = {}) {
    const {map, cfg} = baseMap(el);
    let layer = null;
    const legend = L.control({position: "bottomright"});
    legend.onAdd = () => {
      const d = L.DomUtil.create("div", "legend");
      d.innerHTML = `<div>days rested</div><div class="bar"></div>
        <div style="display:flex;justify-content:space-between"><span>0</span><span>${cfg.rest_full_days}+</span></div>
        <div><span class="sw" style="background:#9e9e9e"></span> never grazed</div>`;
      return d;
    };
    legend.addTo(map);

    async function load(asOf) {
      const res = await fetch("/api/map" + (asOf ? "?as_of=" + asOf : ""));
      const data = await res.json();
      if (layer) layer.remove();
      const full = data.rest_full_days || 60;
      const focus = opts.focus;
      const faces = data.features.filter((f) => f.properties.kind === "face");
      const pads = data.features.filter((f) => f.properties.kind === "paddock");
      layer = L.featureGroup().addTo(map);
      L.geoJSON({type: "FeatureCollection", features: faces}, {
        style: (f) => ({
          color: f.properties.herds.length ? "#fff" : "#000", weight: f.properties.herds.length ? 3 : 0.5,
          fillColor: restColor(f.properties.rest, full), fillOpacity: focus ? 0.25 : 0.55,
        }),
        onEachFeature: (f, l) => {
          const p = f.properties;
          l.bindPopup(`<b>${fmtDays(p.rest)}</b> · ${p.acres.toFixed(2)} ac<br>` +
            p.paddocks.map((id) => `<a class="link paddocks" href="${pageUrl(id)}">${esc(id)}</a>`).join(", ") +
            (p.herds.length ? `<br>herds: ${herdLinks(p.herds)}` : ""));
          if (opts.onPick) l.on("click", () => opts.onPick(p.paddocks));
        },
      }).addTo(layer);
      L.geoJSON({type: "FeatureCollection", features: pads}, {
        style: (f) => ({
          color: f.properties.id === focus ? "#ffeb3b" : f.properties.parent ? "#ffffff" : "#222",
          weight: f.properties.id === focus ? 4 : f.properties.parent ? 2.5 : 1,
          dashArray: f.properties.parent ? "6 4" : null, fill: false, interactive: false,
        }),
        onEachFeature: (f, l) => {
          const p = f.properties;
          if (p.herds.length) {
            l.bindTooltip(herdLinks(p.herds), {permanent: true, direction: "center", className: "herd-label",
              interactive: true});
          }
        },
      }).addTo(layer);
      const target = focus ? pads.filter((f) => f.properties.id === focus) : pads;
      if (target.length && !opts.keepView) {
        map.fitBounds(L.geoJSON({type: "FeatureCollection", features: target}).getBounds(), {padding: [20, 20], maxZoom: 19});
      }
      opts.keepView = true;
      return data;
    }
    await load(opts.asOf);
    return {map, load};
  }

  /* Draw or reshape ONE paddock; the perimeter goes into a hidden [lat, lon] list.
     Other paddocks are shown for snapping but can't be edited (pmIgnore). No Geoman
     toolbar: its global edit/delete modes act on every layer. */
  function drawMap(el, input, existing, others, buttons) {
    const {map} = baseMap(el, {doubleClickZoom: false});
    map.pm.setLang("grazr", {tooltips: {
      firstVertex: "Click to place the first corner",
      continueLine: "Click to add a corner · Backspace undoes the last one",
      finishPoly: "Click the first corner, or press Finish, to close the shape",
    }}, "en");
    const ghost = L.featureGroup().addTo(map);
    for (const o of others || []) {
      L.polygon(o.pts, {color: "#fff", weight: 1.5, dashArray: "4 3", fill: false,
        interactive: false, pmIgnore: true, snapIgnore: false})
        .bindTooltip(o.id, {permanent: true, direction: "center", className: "herd-label", opacity: 0.6})
        .addTo(ghost);
    }
    const help = L.control({position: "topright"});
    help.onAdd = () => L.DomUtil.create("div", "legend draw-help");
    help.addTo(map);
    const say = (html) => { help.getContainer().innerHTML = html; };

    let poly = null;
    const sync = () => {
      input.value = poly ? JSON.stringify(poly.getLatLngs()[0].map((p) => [p.lat, p.lng])) : "";
      buttons.finish.hidden = !!poly;
      buttons.undo.hidden = !!poly;
    };
    const vertexTips = () => {
      if (!poly || !poly.pm._markerGroup) return;
      poly.pm._markerGroup.eachLayer((m) => {
        if (!(m instanceof L.Marker) || m.getTooltip()) return;
        const middle = (m.options.icon.options.className || "").includes("marker-icon-middle");
        m.bindTooltip(middle ? "Drag to add a corner here" : "Drag to move · Right-click to delete this corner",
          {direction: "top", offset: [0, -8]});
      });
    };
    const edit = (layer) => {
      poly = layer;
      poly.pm.enable({allowSelfIntersection: false, removeVertexOn: "contextmenu",
        snappable: true, snapDistance: 12, draggable: false, preventMarkerRemoval: false});
      for (const ev of ["pm:edit", "pm:vertexadded", "pm:vertexremoved", "pm:markerdragend"]) {
        poly.on(ev, () => { sync(); setTimeout(vertexTips, 0); });
      }
      poly.on("pm:intersect", () => say("<b>Edges can't cross.</b> Move the corner somewhere else."));
      vertexTips();
      sync();
      say("<b>Adjust the shape</b><br>Drag a corner to move it<br>Drag a small midpoint to add a corner<br>" +
          "<b>Right-click</b> a corner to delete it<br>Then press <b>Save paddock</b>");
    };
    const draw = () => {
      if (poly) { poly.remove(); poly = null; }
      sync();
      map.pm.enableDraw("Polygon", {snappable: true, snapDistance: 12, allowSelfIntersection: false,
        templineStyle: {color: "#ffeb3b"}, hintlineStyle: {color: "#ffeb3b", dashArray: "5 5"},
        pathOptions: {color: "#ffeb3b"}});
      say("<b>Draw the paddock</b><br>Click each corner in turn<br>Corners snap to other paddocks' edges<br>" +
          "Click the first corner to close it");
    };
    map.on("pm:create", (e) => { map.pm.disableDraw(); edit(e.layer); });

    buttons.restart.onclick = draw;
    buttons.undo.onclick = () => { if (map.pm.Draw.Polygon._enabled) map.pm.Draw.Polygon._removeLastVertex(); };
    buttons.finish.onclick = () => {
      const d = map.pm.Draw.Polygon;
      if (d._enabled && d._layer.getLatLngs().length >= 3) d._finishShape();
      else say("<b>Need at least 3 corners</b> before finishing");
    };
    document.addEventListener("keydown", (e) => {
      if (!map.pm.Draw.Polygon._enabled || ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
      if (e.key === "Backspace") { e.preventDefault(); buttons.undo.onclick(); }
      if (e.key === "Escape") draw();
    });

    if (existing && existing.length) {
      const layer = L.polygon(existing, {color: "#ffeb3b", weight: 3}).addTo(map);
      map.fitBounds(layer.getBounds(), {padding: [40, 40], maxZoom: 19});
      edit(layer);
    } else {
      if (ghost.getLayers().length) map.fitBounds(ghost.getBounds(), {padding: [20, 20], maxZoom: 18});
      draw();
    }
    return map;
  }

  /* [[link and #tag completion, paste/drop image upload. */
  function editor(ta) {
    const box = document.createElement("div");
    box.className = "complete";
    box.hidden = true;
    ta.parentNode.style.position = "relative";
    ta.parentNode.appendChild(box);
    let items = [], sel = 0, ctx = null;

    function context() {
      const before = ta.value.slice(0, ta.selectionStart);
      let m = before.match(/\[\[([^\]\[\n|#]*)$/);
      if (m) return {kind: "link", q: m[1], start: ta.selectionStart - m[1].length};
      m = before.match(/(?:^|\s)#([^\s#]*)$/);
      if (m) return {kind: "tag", q: m[1], start: ta.selectionStart - m[1].length};
      return null;
    }
    function render() {
      box.innerHTML = items.map((t, i) => `<div class="${i === sel ? "on" : ""}" data-i="${i}">${esc(t)}</div>`).join("");
      box.hidden = !items.length;
      box.style.left = "8px";
      box.style.top = Math.min(ta.offsetHeight, 40 + (ta.value.slice(0, ta.selectionStart).split("\n").length) * 20) + "px";
    }
    function choose(i) {
      if (!ctx || !items[i]) return;
      const after = ta.value.slice(ta.selectionStart);
      let ins = items[i];
      let tail = after;
      if (ctx.kind === "link") {
        ins += "]]";
        tail = after.replace(/^[^\]\n]*\]\]/, "");
      } else {
        ins += " ";
      }
      ta.value = ta.value.slice(0, ctx.start) + ins + tail;
      const pos = ctx.start + ins.length;
      ta.setSelectionRange(pos, pos);
      items = []; ctx = null; render(); ta.focus();
    }
    let timer = null;
    ta.addEventListener("input", () => {
      ctx = context();
      clearTimeout(timer);
      if (!ctx) { items = []; render(); return; }
      timer = setTimeout(async () => {
        const r = await fetch(`/api/complete?kind=${ctx.kind}&q=${encodeURIComponent(ctx.q)}`);
        items = await r.json(); sel = 0; render();
      }, 80);
    });
    ta.addEventListener("keydown", (e) => {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { sel = (sel + 1) % items.length; render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = (sel - 1 + items.length) % items.length; render(); e.preventDefault(); }
      else if (e.key === "Enter" || e.key === "Tab") { choose(sel); e.preventDefault(); }
      else if (e.key === "Escape") { items = []; render(); }
    });
    box.addEventListener("mousedown", (e) => { const d = e.target.closest("[data-i]"); if (d) { e.preventDefault(); choose(+d.dataset.i); } });
    ta.addEventListener("blur", () => setTimeout(() => { items = []; render(); }, 150));

    async function upload(file) {
      const fd = new FormData();
      fd.append("file", file, file.name || "image.png");
      const r = await fetch("/upload", {method: "POST", body: fd});
      const j = await r.json();
      if (j.error) { alert(j.error); return; }
      const pos = ta.selectionStart;
      const ins = (pos && ta.value[pos - 1] !== "\n" ? " " : "") + j.embed;
      ta.value = ta.value.slice(0, pos) + ins + ta.value.slice(pos);
      ta.setSelectionRange(pos + ins.length, pos + ins.length);
    }
    ta.addEventListener("paste", (e) => {
      for (const it of e.clipboardData.items) {
        if (it.type.startsWith("image/")) { e.preventDefault(); upload(it.getAsFile()); }
      }
    });
    ta.addEventListener("dragover", (e) => e.preventDefault());
    ta.addEventListener("drop", (e) => {
      const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
      if (files.length) { e.preventDefault(); files.forEach(upload); }
    });
    const pick = document.querySelector(`[data-upload-for="${ta.id}"]`);
    if (pick) pick.addEventListener("change", () => { [...pick.files].forEach(upload); pick.value = ""; });
  }

  /* Quick filter over a timeline: via-herd/child entries, moves, tags, text.
     Choices are remembered per kind of page. */
  function timelineFilter(bar) {
    const list = document.getElementById(bar.dataset.for);
    const items = [...list.children];
    const viaLabel = bar.dataset.viaLabel;
    const key = "grazr.tl." + viaLabel;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) { /* private mode */ }
    const tagCounts = {};
    for (const li of items) {
      for (const t of li.dataset.tags.split(" ").filter(Boolean)) tagCounts[t] = (tagCounts[t] || 0) + 1;
    }
    const nVia = items.filter((li) => li.dataset.via).length;
    const nMove = items.filter((li) => li.dataset.move).length;
    const tags = Object.keys(tagCounts).sort((a, b) => tagCounts[b] - tagCounts[a] || a.localeCompare(b));
    const check = (name, label, on) =>
      `<label class="chip"><input type="checkbox" name="${name}" ${on ? "checked" : ""}> ${label}</label>`;
    bar.innerHTML =
      `<input type="search" name="q" placeholder="filter…" size="12">` +
      (nVia ? check("via", `${esc(viaLabel)} (${nVia})`, saved.via !== false) : "") +
      (nMove ? check("moves", `moves (${nMove})`, saved.moves !== false) : "") +
      (tags.length ? `<span class="muted small">tags:</span>` +
        tags.map((t) => check("tag:" + t, `#${esc(t)} (${tagCounts[t]})`, (saved.tags || []).includes(t))).join("") : "") +
      `<span class="muted small count"></span>`;
    const apply = () => {
      const val = (n) => { const i = bar.querySelector(`input[name="${n}"]`); return i ? i.checked : true; };
      const showVia = val("via"), showMoves = val("moves");
      const want = tags.filter((t) => bar.querySelector(`input[name="tag:${CSS.escape(t)}"]`).checked);
      const q = bar.querySelector("input[name=q]").value.trim().toLowerCase();
      let shown = 0;
      for (const li of items) {
        const liTags = li.dataset.tags.split(" ");
        const ok = (showVia || !li.dataset.via) && (showMoves || !li.dataset.move) &&
          (!want.length || want.some((t) => liTags.includes(t))) &&
          (!q || li.textContent.toLowerCase().includes(q));
        li.hidden = !ok;
        shown += ok;
      }
      bar.querySelector(".count").textContent = shown === items.length ? "" : `${shown} of ${items.length}`;
      try { localStorage.setItem(key, JSON.stringify({via: showVia, moves: showMoves, tags: want})); } catch (e) { /* ignore */ }
    };
    bar.addEventListener("input", apply);
    apply();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".tl-filter").forEach(timelineFilter);
    document.querySelectorAll("textarea[data-editor]").forEach(editor);
    document.querySelectorAll("[data-check-all]").forEach((box) => {
      box.addEventListener("change", () => {
        document.querySelectorAll(box.dataset.checkAll).forEach((c) => { c.checked = box.checked; });
      });
    });
  });

  return {restMap, drawMap, restColor, fmtDays};
})();
