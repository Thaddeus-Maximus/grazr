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

  function baseMap(el) {
    const cfg = JSON.parse(el.dataset.config);
    const map = L.map(el, {zoomSnap: 0.5}).setView(cfg.center, cfg.zoom);
    L.tileLayer("/tiles/{z}/{x}/{y}", {
      minZoom: cfg.min_zoom, maxZoom: 22, maxNativeZoom: cfg.max_zoom,
      attribution: "Imagery: USGS",
    }).addTo(map);
    return {map, cfg};
  }

  const pageUrl = (id) => "/p/" + encodeURIComponent(id);

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
            p.paddocks.map((id) => `<a href="${pageUrl(id)}">${esc(id)}</a>`).join(", ") +
            (p.herds.length ? `<br>herds: ${p.herds.map(esc).join(", ")}` : ""));
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
            l.bindTooltip(p.herds.map(esc).join(", "), {permanent: true, direction: "center", className: "herd-label"});
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

  /* Draw or reshape a paddock; the perimeter goes into a hidden [lat, lon] list. */
  function drawMap(el, input, existing, others) {
    const {map} = baseMap(el);
    const ghost = L.featureGroup().addTo(map);
    for (const o of others || []) {
      L.polygon(o.pts, {color: "#fff", weight: 1, fill: false, interactive: false}).bindTooltip(o.id).addTo(ghost);
    }
    let poly = null;
    const sync = () => {
      input.value = poly ? JSON.stringify(poly.getLatLngs()[0].map((p) => [p.lat, p.lng])) : "";
    };
    const attach = (layer) => {
      if (poly && poly !== layer) poly.remove();
      poly = layer;
      poly.on("pm:edit", sync);
      sync();
    };
    map.pm.addControls({position: "topleft", drawMarker: false, drawCircleMarker: false, drawPolyline: false,
      drawCircle: false, drawText: false, cutPolygon: false, rotateMode: false, dragMode: false});
    map.pm.setGlobalOptions({snappable: true, snapDistance: 15});
    map.on("pm:create", (e) => attach(e.layer));
    map.on("pm:remove", () => { poly = null; sync(); });
    if (existing && existing.length) {
      attach(L.polygon(existing, {color: "#ffeb3b"}).addTo(map));
      map.fitBounds(poly.getBounds(), {padding: [40, 40], maxZoom: 19});
    } else if (ghost.getLayers().length) {
      map.fitBounds(ghost.getBounds(), {padding: [20, 20], maxZoom: 18});
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

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("textarea[data-editor]").forEach(editor);
    document.querySelectorAll("[data-check-all]").forEach((box) => {
      box.addEventListener("change", () => {
        document.querySelectorAll(box.dataset.checkAll).forEach((c) => { c.checked = box.checked; });
      });
    });
  });

  return {restMap, drawMap, restColor, fmtDays};
})();
