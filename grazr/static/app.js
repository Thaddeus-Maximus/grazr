/* grazr front end: maps (Leaflet), paddock drawing (Geoman), editor helpers. */
"use strict";

const grazr = (() => {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  /* Signed rest days: red at -occFull (occupied that long), orange at 0, green at +full. */
  const ORANGE = 30, GREEN = 120;
  function restColor(days, full, occFull) {
    if (days === null || days === undefined) return "#9e9e9e";
    if (days < 0) {
      const t = Math.min(1, -days / occFull);
      return `hsl(${Math.round(ORANGE * (1 - t))}, 80%, 47%)`;
    }
    const t = Math.min(1, days / full);
    return `hsl(${Math.round(ORANGE + (GREEN - ORANGE) * t)}, ${80 - 20 * t}%, ${47 - 9 * t}%)`;
  }

  const span = (d) => `${d.toFixed(2)} days`;
  function fmtDays(d) {
    if (d === null || d === undefined) return "never grazed";
    return d < 0 ? `occupied ${span(-d)}` : `rested ${span(d)}`;
  }

  function baseMap(el, mapOpts = {}) {
    const cfg = JSON.parse(el.dataset.config);
    const map = L.map(el, {zoomSnap: 0.5, ...mapOpts}).setView(cfg.center, cfg.zoom);
    L.tileLayer("/tiles/{z}/{x}/{y}", {
      minZoom: cfg.min_zoom, maxZoom: 22, maxNativeZoom: cfg.max_zoom,
      attribution: "Imagery: USGS",
    }).addTo(map);
    map.grazrLocate = locate(map);
    return {map, cfg};
  }

  /* Phone/browser location: needs a secure page (https, or localhost). */
  const canLocate = () => "geolocation" in navigator;
  const LOCATE_KEY = "grazr.locate";
  const geoError = (err) => !window.isSecureContext
    ? "Location only works on the https address (Tailscale), not plain http."
    : err && err.code === 1 ? "Location is blocked for this site. Allow it in the browser's site settings."
    : err && err.code === 3 ? "No GPS fix yet. Try again in the open."
    : "Couldn't get your location.";
  const feetOf = (m) => `${Math.round(m / 0.3048)} ft`;

  /* A live "you are here" dot with its accuracy circle, toggled by a button under the
     zoom buttons. The first fix after turning it on centres the map; tapping again while
     the dot is off screen re-centres, otherwise turns it off. Remembered per browser. */
  function locate(map) {
    let watch = null, dot = null, ring = null, last = null, center = false, wake = null;
    const api = {keepAwake: false, get last() { return last; }, on: () => watch !== null};
    const btn = L.control({position: "topleft"});
    let a;
    btn.onAdd = () => {
      const d = L.DomUtil.create("div", "leaflet-bar locate");
      a = L.DomUtil.create("a", "", d);
      a.href = "#"; a.role = "button"; a.title = "Show my location";
      a.innerHTML = `<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2" fill="currentColor"/><path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4"/></svg>`;
      L.DomEvent.disableClickPropagation(d);
      L.DomEvent.on(a, "click", (e) => {
        L.DomEvent.preventDefault(e);
        if (watch === null) start(true);
        else if (last && !map.getBounds().contains(last.ll)) map.setView(last.ll, Math.max(map.getZoom(), 17));
        else stop();
      });
      return d;
    };
    if (!canLocate()) return api;
    btn.addTo(map);
    /* the readout, in a bar under the map: accuracy, how old the fix is, which paddock you're in */
    const box = document.createElement("div");
    box.className = "locate-info";
    box.hidden = true;
    map.getContainer().after(box);
    const ago = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`; };
    const show = () => {
      if (watch === null) { box.hidden = true; return; }
      box.hidden = false;
      if (!last) { box.className = "locate-info"; box.innerHTML = "Finding your location…"; return; }
      const acc = last.acc, age = Date.now() - last.at;
      const q = acc <= 10 ? "good" : acc <= 30 ? "fair" : acc <= 300 ? "poor" : "coarse";
      const where = api.where && acc <= 100 ? api.where(last.ll) : "";
      box.className = "locate-info " + q;
      box.innerHTML = `You are here to within <b>±${acc < 1609 ? feetOf(acc) : (acc / 1609.344).toFixed(1) + " mi"}</b>` +
        ` <span class="muted">(${ago(age)})</span>` + (where ? ` · in ${where}` : "") +
        (q === "coarse" ? `<div class="tip">Approximate location only. In Android settings, allow this browser <b>Precise</b> location, and turn on GPS.</div>`
          : q === "poor" ? `<div class="tip">Not a GPS fix yet. Wait in the open, or check that GPS is on.</div>` : "") +
        (age > 20000 ? `<div class="tip">No new reading lately.</div>` : "");
    };
    setInterval(show, 1000);
    const state = (s) => { a.className = s; show(); };
    const save = (on) => { try { localStorage.setItem(LOCATE_KEY, on ? "1" : ""); } catch (e) { /* private mode */ } };
    const awake = async () => {
      if (!api.keepAwake || wake || document.visibilityState !== "visible" || !navigator.wakeLock) return;
      try { wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => { wake = null; }); }
      catch (e) { /* not allowed */ }
    };
    document.addEventListener("visibilitychange", () => { if (watch !== null) awake(); });
    const fix = (p) => {
      const ll = L.latLng(p.coords.latitude, p.coords.longitude), acc = p.coords.accuracy;
      last = {ll, acc, at: p.timestamp};
      if (!dot) {
        ring = L.circle(ll, {radius: acc, color: "#1a73e8", weight: 1, fillOpacity: 0.12,
          interactive: false, pmIgnore: true, snapIgnore: true}).addTo(map);
        dot = L.circleMarker(ll, {radius: 8, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1,
          pmIgnore: true, snapIgnore: true}).addTo(map);
      }
      ring.setLatLng(ll).setRadius(acc);
      dot.setLatLng(ll).bindTooltip(`You · ±${feetOf(acc)}`, {direction: "top", offset: [0, -8]});
      state("on");
      if (center) { center = false; map.setView(ll, Math.max(map.getZoom(), 17)); }
    };
    function start(recenter) {
      if (!window.isSecureContext) { alert(geoError()); return; }
      center = recenter;
      state("wait");
      watch = navigator.geolocation.watchPosition(fix, (err) => {
        if (err.code === 1) { stop(); alert(geoError(err)); }
        else state(last ? "on" : "wait");
      }, {enableHighAccuracy: true, maximumAge: 1000, timeout: 30000});
      save(true);
      awake();
    }
    function stop() {
      if (watch !== null) navigator.geolocation.clearWatch(watch);
      watch = null; last = null;
      if (dot) { dot.remove(); ring.remove(); dot = ring = null; }
      if (wake) { wake.release(); wake = null; }
      state("");
      save(false);
    }
    api.start = () => { if (watch === null) start(false); };
    api.refresh = show;
    api.awake = awake;
    let saved = "";
    try { saved = localStorage.getItem(LOCATE_KEY); } catch (e) { /* private mode */ }
    // turned on last time: resume, but don't prompt for permission out of the blue
    if (saved && window.isSecureContext && navigator.permissions) {
      navigator.permissions.query({name: "geolocation"}).then((p) => { if (p.state === "granted") start(false); }, () => {});
    }
    return api;
  }

  /* Average a few seconds of GPS readings while standing still. Readings much worse than
     the best are dropped, the rest weighted by 1/accuracy². `tick(seconds left, best
     accuracy)` reports progress. → {ll, acc, n}, or throws a message. */
  function sampleFix(tick, {seconds = 8, enough = 6, good = 5, worst = 30} = {}) {
    return new Promise((resolve, reject) => {
      if (!window.isSecureContext || !canLocate()) { reject(geoError()); return; }
      const got = [], t0 = Date.now();
      let id = null, timer = null, done = false;
      const finish = (err) => {
        if (done) return;
        done = true;
        navigator.geolocation.clearWatch(id);
        clearInterval(timer);
        if (err && !got.length) { reject(geoError(err)); return; }
        const best = Math.min(...got.map((g) => g.acc));
        if (!got.length || best > worst) {
          reject(got.length ? `GPS is too rough here (±${feetOf(best)}). Wait a bit or move into the open.` : geoError());
          return;
        }
        const use = got.filter((g) => g.acc <= Math.max(2 * best, good));
        let w = 0, lat = 0, lng = 0;
        for (const g of use) { const k = 1 / (g.acc * g.acc); w += k; lat += k * g.lat; lng += k * g.lng; }
        resolve({ll: L.latLng(lat / w, lng / w), acc: best, n: use.length});
      };
      id = navigator.geolocation.watchPosition((p) => {
        got.push({lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy});
        const secs = (Date.now() - t0) / 1000;
        if (secs >= 3 && got.filter((g) => g.acc <= good).length >= enough) finish();
      }, (err) => { if (err.code === 1) finish(err); },
      {enableHighAccuracy: true, maximumAge: 0, timeout: seconds * 1000});
      const tock = () => {
        const left = Math.ceil(seconds - (Date.now() - t0) / 1000);
        if (left <= 0) { finish({code: 3}); return; }
        tick(left, got.length ? Math.min(...got.map((g) => g.acc)) : null);
      };
      tock();
      timer = setInterval(tock, 500);
    });
  }

  const pageUrl = (id) => "/p/" + encodeURIComponent(id);
  const herdLinks = (ids) => ids.map((h) => `<a class="link herds" href="/h/${encodeURIComponent(h)}">${esc(h)}</a>`).join(", ");

  /* Point in a [lon, lat] ring (ray casting), and the ring's rough size, for ordering. */
  function inRing([x, y], ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  const ringSize = (ring) => Math.abs(ring.reduce((s, [x, y], i) => {
    const [x2, y2] = ring[(i + 1) % ring.length];
    return s + x * y2 - x2 * y;
  }, 0));

  /* The rest map: faces coloured by days rested, parents as outlines (§8.3). */
  async function restMap(el, opts = {}) {
    const {map, cfg} = baseMap(el);
    let layer = null, padLayer = null;
    const legend = L.control({position: "bottomright"});
    legend.onAdd = () => {
      const d = L.DomUtil.create("div", "legend");
      const occ = cfg.occupied_full_days, full = cfg.rest_full_days;
      const mid = (100 * occ / (occ + full)).toFixed(1);
      const stops = [-occ, -occ / 2, 0, full / 3, 2 * full / 3, full]
        .map((x) => `${restColor(x, full, occ)} ${(100 * (x + occ) / (occ + full)).toFixed(1)}%`).join(", ");
      d.innerHTML = `<div>days occupied · rested</div>
        <div class="bar" style="background:linear-gradient(90deg, ${stops})"></div>
        <div style="position:relative;height:1.2em">
          <span style="position:absolute;left:0">${occ}+</span>
          <span style="position:absolute;left:${mid}%;transform:translateX(-50%)">0</span>
          <span style="position:absolute;right:0">${full}+</span></div>
        <div><span class="sw" style="background:#9e9e9e"></span> never grazed</div>`;
      return d;
    };
    legend.addTo(map);

    async function load(asOf) {
      const res = await fetch("/api/map" + (asOf ? "?as_of=" + asOf : ""));
      const data = await res.json();
      if (layer) layer.remove();
      const full = data.rest_full_days || 60, occFull = data.occupied_full_days || 10;
      const focus = opts.focus;
      const faces = data.features.filter((f) => f.properties.kind === "face");
      const pads = data.features.filter((f) => f.properties.kind === "paddock");
      layer = L.featureGroup().addTo(map);
      L.geoJSON({type: "FeatureCollection", features: faces}, {
        snapIgnore: true,   // measuring snaps to the paddock outlines, which share these edges
        style: (f) => ({
          color: f.properties.herds.length ? "#fff" : "#000", weight: f.properties.herds.length ? 3 : 0.5,
          fillColor: restColor(f.properties.rest, full, occFull), fillOpacity: focus ? 0.25 : 0.55,
        }),
        onEachFeature: (f, l) => {
          const p = f.properties;
          l.bindPopup(`<b>${fmtDays(p.rest)}</b> · ${p.acres.toFixed(2)} ac<br>` +
            p.paddocks.map((id) => `<a class="link paddocks" href="${pageUrl(id)}">${esc(id)}</a>`).join(", ") +
            (p.herds.length ? `<br>herds: ${herdLinks(p.herds)}` : ""));
          if (opts.onPick) l.on("click", () => opts.onPick(p.paddocks));
        },
      }).addTo(layer);
      padLayer = L.geoJSON({type: "FeatureCollection", features: pads}, {
        style: (f) => ({
          color: f.properties.id === focus ? "#ffeb3b" : f.properties.parent ? "#ffffff" : "#222",
          weight: f.properties.id === focus ? 4 : f.properties.parent ? 2.5 : 1,
          dashArray: f.properties.parent ? "6 4" : null, fill: false, interactive: false,
        }),
        onEachFeature: (f, l) => {
          const p = f.properties;
          if (p.herds.length) {
            // plain labels; clicks go through to the paddock, whose popup links the herds
            l.bindTooltip(p.herds.map(esc).join(", "), {permanent: true, direction: "center", className: "herd-label"});
          }
        },
      }).addTo(layer);
      map.grazrLocate.where = (ll) => {
        const hit = pads.filter((f) => f.geometry.type === "Polygon" && inRing([ll.lng, ll.lat], f.geometry.coordinates[0]))
          .map((f) => ({id: f.properties.id, size: ringSize(f.geometry.coordinates[0])}))
          .sort((p, q) => p.size - q.size);
        return hit.map((h, i) => i ? esc(h.id) : `<a class="link paddocks" href="${pageUrl(h.id)}">${esc(h.id)}</a>`).join(" · ");
      };
      map.grazrLocate.refresh && map.grazrLocate.refresh();
      const target = focus ? pads.filter((f) => f.properties.id === focus) : pads;
      if (target.length && !opts.keepView) {
        map.fitBounds(L.geoJSON({type: "FeatureCollection", features: target}).getBounds(), {padding: [20, 20], maxZoom: 19});
      }
      opts.keepView = true;
      return data;
    }
    await load(opts.asOf);
    return {map, cfg, load, outlines: () => (padLayer ? padLayer.getLayers() : [])};
  }

  /* A snappable square grid in feet, anchored at a fixed point so it doesn't shift as
     the map pans. Lines run north-south and east-west. Geoman snaps to the nearest line
     and prefers that segment's end points, so every point worth hitting is made a vertex:
     grid crossings, and where grid lines cross the outlines `rings()` returns (those polygons
     get the same points added, so either line snaps there). Only the lines in view are
     drawn, and none when they'd be too dense to use. `onChange` runs after each redraw. */
  const GRID_FEET = [0, 10, 20, 50, 100, 200, 500, 1000];
  /* Paddock edges beat grid lines: Geoman snaps to the nearest line, which near a
     paddock corner is often a grid line, so the corner would be missed. When any
     non-grid line is within snapping range, only those are considered. */
  let gridSnapPatched = false;
  function preferPaddockSnaps() {
    if (gridSnapPatched) return;
    gridSnapPatched = true;
    for (const C of [L.PM.Draw.Polygon, L.PM.Draw.Line, L.PM.Edit.Line, L.PM.Edit.Marker]) {
      const orig = C.prototype._calcClosestLayer;
      C.prototype._calcClosestLayer = function (latlng, layers) {
        const real = layers.filter((l) => !l.options.grazrGrid);
        if (real.length < layers.length && real.length) {
          const r = orig.call(this, latlng, real);
          if (r && r.layer && r.distance < this.options.snapDistance) return r;
        }
        return orig.call(this, latlng, layers);
      };
    }
  }
  function feetGrid(map, origin, rings, onChange) {
    preferPaddockSnaps();
    const layer = L.featureGroup().addTo(map);
    const mLat = 111320, mLon = 111320 * Math.cos(origin[0] * Math.PI / 180);
    let feet = 0, tooFine = false;
    let touched = [];
    const restore = () => { for (const r of touched) r.layer.setLatLngs(r.pts); touched = []; };
    const redraw = () => {
      layer.clearLayers();
      tooFine = false;
      restore();
      if (!feet) { onChange(); return; }
      const m = feet * 0.3048, dLat = m / mLat, dLon = m / mLon;
      const b = map.getBounds().pad(0.25);
      const i0 = Math.floor((b.getSouth() - origin[0]) / dLat), i1 = Math.ceil((b.getNorth() - origin[0]) / dLat);
      const j0 = Math.floor((b.getWest() - origin[1]) / dLon), j1 = Math.ceil((b.getEast() - origin[1]) / dLon);
      if (i1 - i0 > 120 || j1 - j0 > 120) { tooFine = true; onChange(); return; }
      const lat = (i) => origin[0] + i * dLat, lon = (j) => origin[1] + j * dLon;
      // extra vertices per grid line: lat line i gets lons, lon line j gets lats
      const onLat = new Map(), onLon = new Map();
      const add = (mp, k, v) => { if (!mp.has(k)) mp.set(k, []); mp.get(k).push(v); };
      touched = rings();
      for (const r of touched) {
        const pts = r.pts.map((p) => L.latLng(p)), out = [];
        for (let k = 0; k < pts.length; k++) {
          const a = pts[k], c = pts[(k + 1) % pts.length], cuts = [];
          out.push(a);
          if (a.equals(c)) continue;
          // crossings with lat lines (constant latitude) and lon lines, as fractions along a→c
          const lo = Math.ceil((Math.min(a.lat, c.lat) - origin[0]) / dLat), hi = Math.floor((Math.max(a.lat, c.lat) - origin[0]) / dLat);
          for (let i = Math.max(lo, i0); i <= Math.min(hi, i1) && a.lat !== c.lat; i++) {
            const t = (lat(i) - a.lat) / (c.lat - a.lat), x = a.lng + t * (c.lng - a.lng);
            cuts.push([t, L.latLng(lat(i), x)]);
            add(onLat, i, x);
          }
          const lo2 = Math.ceil((Math.min(a.lng, c.lng) - origin[1]) / dLon), hi2 = Math.floor((Math.max(a.lng, c.lng) - origin[1]) / dLon);
          for (let j = Math.max(lo2, j0); j <= Math.min(hi2, j1) && a.lng !== c.lng; j++) {
            const t = (lon(j) - a.lng) / (c.lng - a.lng), y = a.lat + t * (c.lat - a.lat);
            cuts.push([t, L.latLng(y, lon(j))]);
            add(onLon, j, y);
          }
          cuts.sort((p, q) => p[0] - q[0]).forEach(([t, p]) => { if (t > 0 && t < 1) out.push(p); });
        }
        r.layer.setLatLngs(out);
      }
      const style = {color: "#fff", opacity: 0.45, weight: 1, interactive: false, pmIgnore: true, snapIgnore: false,
        grazrGrid: true};
      const lons = [], lats = [];
      for (let j = j0; j <= j1; j++) lons.push(lon(j));
      for (let i = i0; i <= i1; i++) lats.push(lat(i));
      for (let i = i0; i <= i1; i++) {
        const xs = lons.concat(onLat.get(i) || []).sort((p, q) => p - q);
        L.polyline(xs.map((x) => [lat(i), x]), style).addTo(layer);
      }
      for (let j = j0; j <= j1; j++) {
        const ys = lats.concat(onLon.get(j) || []).sort((p, q) => p - q);
        L.polyline(ys.map((y) => [y, lon(j)]), style).addTo(layer);
      }
      layer.bringToBack();
      onChange();
    };
    map.on("moveend", redraw);
    return {
      get feet() { return feet; },
      get tooFine() { return tooFine; },
      /* one grid square on screen, in pixels at the current zoom */
      cellPx() {
        if (!feet) return 0;
        const c = map.getCenter(), dLat = feet * 0.3048 / mLat;
        return Math.abs(map.latLngToContainerPoint([c.lat + dLat, c.lng]).y - map.latLngToContainerPoint(c).y);
      },
      set(f) { feet = GRID_FEET.includes(f) ? f : 0; redraw(); },
      cycle(step) {
        const i = GRID_FEET.indexOf(feet);
        feet = GRID_FEET[(i + step + GRID_FEET.length) % GRID_FEET.length];
        redraw();
        return feet;
      },
    };
  }

  const saveGrid = (f) => { try { localStorage.setItem("grazr.grid", f); } catch (e) { /* private mode */ } };
  const savedGrid = () => { try { return +localStorage.getItem("grazr.grid") || 0; } catch (e) { return 0; } };

  /* The grid size on the map: the size, and a bar one grid square long. Clicking it
     changes the size like G. → a function that redraws it. */
  function gridChip(map, grid) {
    const chip = L.control({position: "topright"});
    chip.onAdd = () => {
      const d = L.DomUtil.create("div", "legend grid-chip");
      d.title = "Grid size: press G (Shift+G for smaller), or click";
      L.DomEvent.disableClickPropagation(d);
      d.addEventListener("click", (e) => { saveGrid(grid.cycle(e.shiftKey ? -1 : 1)); });
      return d;
    };
    chip.addTo(map);
    const show = () => {
      const px = grid.cellPx();
      chip.getContainer().innerHTML = !grid.feet ? `Grid <b>off</b> <kbd>G</kbd>`
        : `Grid <b>${grid.feet} ft</b>` + (grid.tooFine ? `<div class="muted">zoom in to see it</div>`
          : px >= 4 && px <= 200 ? `<div class="cell" style="width:${px.toFixed(1)}px"></div>` : "");
    };
    show.remove = () => chip.remove();
    return show;
  }

  const kbd = (k, what) => `<span><kbd>${k}</kbd> ${what}</span>`;
  const typing = () => {
    const t = document.activeElement;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable;
  };

  /* Draw or reshape ONE paddock; the perimeter goes into a hidden [lat, lon] list.
     Other paddocks are shown for snapping but can't be edited (pmIgnore). No Geoman
     toolbar: its global edit/delete modes act on every layer. The status and every key
     and mouse action are listed in a bar under the map. */
  function drawMap(el, input, existing, others, buttons) {
    const {map, cfg} = baseMap(el, {doubleClickZoom: false});
    map.pm.setLang("grazr", {tooltips: {
      firstVertex: "Click to place the first corner",
      continueLine: "Click to add a corner",
      finishPoly: "Click the first corner, or press Enter, to close the shape",
    }}, "en");
    const ghost = L.featureGroup().addTo(map), rings = [];
    for (const o of others || []) {
      const layer = L.polygon(o.pts, {color: "#fff", weight: 1.5, dashArray: "4 3", fill: false,
        interactive: false, pmIgnore: true, snapIgnore: false})
        .bindTooltip(o.id, {permanent: true, direction: "center", className: "herd-label", opacity: 0.6})
        .addTo(ghost);
      rings.push({layer, pts: o.pts});
    }

    const grid = feetGrid(map, cfg.center, () => rings, () => hints());
    const showChip = gridChip(map, grid);
    const setGrid = saveGrid;
    const bar = document.createElement("div");
    bar.className = "map-keys";
    el.classList.add("with-keys");
    el.after(bar);
    let status = "";
    const key = (k, what) => `<span><kbd>${k}</kbd> ${what}</span>`;
    const drawing = () => map.pm.Draw.Polygon._enabled;
    const hints = () => {
      const g = grid.feet ? `${grid.feet} ft${grid.tooFine ? " (zoom in to see it)" : ""}` : "off";
      const keys = drawing()
        ? [key("Click", "add a corner"), key("Enter", "finish (or click the first corner)"),
           key("Backspace", "undo corner"), key("Esc", "start over")]
        : [key("Drag", "a corner to move it"), key("Drag", "a midpoint to add a corner"),
           key("Right-click", "a corner to delete it")];
      keys.push(key("G", `grid: <b>${g}</b>`), key("Shift+G", "smaller grid"),
        key("hold Alt", "don't snap"), key("Scroll", "zoom"), key("Drag", "the map to pan"));
      if (grid.feet) keys.splice(keys.length - 3, 0, `<span>snaps to grid crossings and where grid lines cross paddock edges</span>`);
      bar.innerHTML = `<div class="status">${status}${measured ? `<div>${measured}</div>` : ""}</div>` +
        `<div class="keys">${keys.join("")}</div>`;
      showChip();
    };
    const say = (html) => { status = html; hints(); if (buttons.status) buttons.status.innerHTML = html; };
    /* Live measurements: while drawing, the path so far (to the cursor) and the area as
       if closed, like the measure tool; once it's a shape, its perimeter and area. */
    let measured = "";
    const measure = () => {
      let pts, closed = false;
      if (poly) { pts = poly.getLatLngs()[0].slice(); closed = true; }
      else if (drawing()) {
        const d = map.pm.Draw.Polygon;
        pts = d._layer.getLatLngs().slice();
        if (d._hintMarker && pts.length) pts.push(d._hintMarker.getLatLng());
      } else pts = [];
      measured = pts.length < 2 ? "" : closed
        ? `Perimeter <b>${fmtFeet(pathFeet(pts.concat(pts.slice(0, 1))))}</b> · Area <b>${fmtArea(closedArea(pts))}</b>`
        : `Length <b>${fmtFeet(pathFeet(pts))}</b>` +
          (pts.length >= 3 ? ` · Area <b>${fmtArea(closedArea(pts))}</b> <span class="muted">(closing the shape)</span>` : "");
      hints();
    };
    try { grid.set(+localStorage.getItem("grazr.grid") || 0); } catch (e) { /* private mode */ }

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
    const EDIT = {allowSelfIntersection: false, removeVertexOn: "contextmenu",
      snappable: true, snapDistance: 12, draggable: false, preventMarkerRemoval: false};
    const edit = (layer) => {
      poly = layer;
      poly.pm.enable(EDIT);
      for (const ev of ["pm:edit", "pm:vertexadded", "pm:vertexremoved", "pm:markerdragend"]) {
        poly.on(ev, () => { sync(); measure(); setTimeout(vertexTips, 0); });
      }
      poly.on("pm:markerdrag", measure);
      poly.on("pm:intersect", () => say("<b>Edges can't cross.</b> Move the corner somewhere else."));
      vertexTips();
      sync();
      say("<b>Adjust the shape,</b> then press <b>Save paddock</b>.");
      measure();
    };
    const draw = () => {
      if (poly) { poly.remove(); poly = null; }
      map.pm.enableDraw("Polygon", {snappable: true, snapDistance: 12, allowSelfIntersection: false,
        templineStyle: {color: "#ffeb3b"}, hintlineStyle: {color: "#ffeb3b", dashArray: "5 5"},
        pathOptions: {color: "#ffeb3b"}});
      map.pm.Draw.Polygon._hintMarker.on("move", measure);
      map.pm.Draw.Polygon._layer.on("pm:vertexadded pm:vertexremoved", measure);
      measured = "";
      sync();
      say("<b>Draw the paddock:</b> click each corner in turn. Corners snap to other paddocks' edges" +
          " and, when it's on, to the grid.");
    };
    map.on("pm:create", (e) => { map.pm.disableDraw(); edit(e.layer); });

    /* A corner where you're standing: while drawing it's the next corner; on a finished
       shape it goes into the edge it lengthens least (then drag it if that's wrong). */
    const addCorner = (ll) => {
      if (drawing()) {
        const d = map.pm.Draw.Polygon;
        d._hintMarker._snapped = false;
        d._hintMarker.setLatLng(ll);
        d._createVertex({latlng: ll});
        measure();
        return true;
      }
      if (!poly) return false;
      const ring = poly.getLatLngs()[0];
      let at = 0, cost = Infinity;
      ring.forEach((p, i) => {
        const q = ring[(i + 1) % ring.length], c = p.distanceTo(ll) + ll.distanceTo(q) - p.distanceTo(q);
        if (c < cost) { cost = c; at = i + 1; }
      });
      poly.pm.disable();
      poly.setLatLngs([ring.slice(0, at).concat([ll], ring.slice(at))]);
      poly.pm.enable(EDIT);
      setTimeout(vertexTips, 0);
      sync(); measure();
      return true;
    };
    if (buttons.gps && canLocate()) {
      const gps = buttons.gps, label = gps.textContent;
      gps.hidden = false;
      map.grazrLocate.keepAwake = true;
      gps.onclick = async () => {
        if (gps.disabled) return;
        map.grazrLocate.start();
        map.grazrLocate.awake();
        gps.disabled = true;
        try {
          const f = await sampleFix((left, best) => {
            gps.textContent = `Hold still… ${left}`;
            say(`<b>Getting your position</b>: stand still on the corner.` +
              (best ? ` Best so far ±${feetOf(best)}.` : " Waiting for GPS…"));
          });
          if (!map.getBounds().pad(-0.1).contains(f.ll)) map.panTo(f.ll);
          if (addCorner(f.ll)) {
            say(`<b>Corner placed</b> from ${f.n} GPS reading${f.n === 1 ? "" : "s"}, good to about ±${feetOf(f.acc)}.` +
              (drawing() ? " Walk to the next corner and tap <b>Corner here</b> again." : " Drag it if it's in the wrong place."));
          }
        } catch (msg) {
          say(`<b>${esc(String(msg))}</b>`);
        } finally {
          gps.disabled = false;
          gps.textContent = label;
        }
      };
    }

    buttons.restart.onclick = draw;
    buttons.undo.onclick = () => { if (drawing()) { map.pm.Draw.Polygon._removeLastVertex(); measure(); } };
    buttons.finish.onclick = () => {
      const d = map.pm.Draw.Polygon;
      if (d._enabled && d._layer.getLatLngs().length >= 3) d._finishShape();
      else say("<b>Need at least 3 corners</b> before finishing.");
    };
    document.addEventListener("keydown", (e) => {
      const t = document.activeElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable || e.ctrlKey || e.metaKey) return;
      if (e.key === "g" || e.key === "G") {
        setGrid(grid.cycle(e.shiftKey ? -1 : 1));
        return;
      }
      if (!drawing()) return;
      if (e.key === "Backspace") { e.preventDefault(); buttons.undo.onclick(); }
      else if (e.key === "Enter") { e.preventDefault(); buttons.finish.onclick(); }
      else if (e.key === "Escape") draw();
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

  /* Length along a path of LatLngs, in feet; area of the path closed back to its start,
     in square metres (spherical excess, as Leaflet.draw does it). */
  const pathFeet = (pts) => pts.slice(1).reduce((s, p, i) => s + pts[i].distanceTo(p), 0) / 0.3048;
  function closedArea(pts) {
    if (pts.length < 3) return 0;
    const R = 6378137, rad = Math.PI / 180;
    let a = 0;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = pts[(i + 1) % pts.length];
      a += (q.lng - p.lng) * rad * (2 + Math.sin(p.lat * rad) + Math.sin(q.lat * rad));
    }
    return Math.abs(a * R * R / 2);
  }
  const fmtFeet = (ft) => `${Math.round(ft).toLocaleString()} ft` + (ft >= 5280 ? ` (${(ft / 5280).toFixed(2)} mi)` : "");
  const fmtArea = (m2) => `${(m2 / 4046.8564224).toFixed(2)} ac` +
    (m2 < 4046.8564224 ? ` (${Math.round(m2 / 0.09290304).toLocaleString()} sq ft)` : "");

  /* Measure on the rest map (M or the Measure button): click points, which snap like the
     paddock editor (paddock edges, the G grid, and where they cross). Shows the path's
     length as drawn and the area as if it were closed, live as the cursor moves. */
  function measureTool(map, cfg, outlines, button) {
    const bar = document.createElement("div");
    bar.className = "map-keys measure-keys";
    bar.hidden = true;
    map.getContainer().after(bar);
    const rings = () => outlines().filter((l) => {
      const ll = l.getLatLngs();
      return ll.length === 1 && !Array.isArray(ll[0][0]);   // simple polygons only
    }).map((l) => {
      l._grazrPts = l._grazrPts || l.getLatLngs()[0].map((p) => L.latLng(p.lat, p.lng));
      return {layer: l, pts: l._grazrPts};
    });
    let on = false, done = null, doneAt = 0, readout = "", chip = null;
    const grid = feetGrid(map, cfg.center, rings, () => { if (on) hints(); });
    const line = () => map.pm.Draw.Line;
    const drawing = () => line()._enabled;
    const show = (pts, final) => {
      readout = pts.length < 2 ? "Click to place the first point." :
        `Length <b>${fmtFeet(pathFeet(pts))}</b>` +
        (pts.length >= 3 ? ` · Area <b>${fmtArea(closedArea(pts))}</b> <span class="muted">(closing the shape)</span>` : "") +
        (final ? ` <span class="muted">· click the map or press M to measure again</span>` : "");
      hints();
    };
    const hints = () => {
      const g = grid.feet ? `${grid.feet} ft${grid.tooFine ? " (zoom in to see it)" : ""}` : "off";
      const keys = drawing()
        ? [kbd("Click", "add a point"), kbd("Enter", "finish (or click the last point)"), kbd("Backspace", "undo point")]
        : [kbd("Click", "start a new measurement")];
      keys.push(kbd("G", `grid: <b>${g}</b>`), kbd("Shift+G", "smaller grid"), kbd("hold Alt", "don't snap"),
        kbd("Esc", "stop measuring"));
      bar.innerHTML = `<div class="status">${readout}</div><div class="keys">${keys.join("")}</div>`;
      if (chip) chip();
    };
    const live = () => {
      const d = line();
      const pts = d._layer.getLatLngs().slice();
      if (d._hintMarker && pts.length) pts.push(d._hintMarker.getLatLng());
      show(pts, false);
    };
    const start = () => {
      if (done) { done.remove(); done = null; }
      map.pm.enableDraw("Line", {snappable: true, snapDistance: 12, finishOn: "dblclick",
        templineStyle: {color: "#ffeb3b"}, hintlineStyle: {color: "#ffeb3b", dashArray: "5 5"},
        pathOptions: {color: "#ffeb3b", weight: 3}});
      line()._hintMarker.on("move", live);
      line()._layer.on("pm:vertexadded", live);
      show([], false);
    };
    const begin = () => {
      on = true;
      map.closePopup();
      map.doubleClickZoom.disable();
      map.getContainer().classList.add("measuring");
      bar.hidden = false;
      if (button) button.classList.add("on");
      chip = gridChip(map, grid);
      grid.set(savedGrid());
      start();
    };
    const end = () => {
      on = false;
      map.pm.disableDraw();
      if (done) { done.remove(); done = null; }
      grid.set(0);
      if (chip) { chip.remove(); chip = null; }
      map.doubleClickZoom.enable();
      map.getContainer().classList.remove("measuring");
      bar.hidden = true;
      if (button) button.classList.remove("on");
    };
    map.on("pm:create", (e) => {
      if (!on || e.shape !== "Line") return;
      done = e.layer;
      doneAt = Date.now();
      done.options.pmIgnore = true;
      show(done.getLatLngs(), true);
    });
    // a click after a finished measurement starts another (not the click that finished it)
    map.on("click", () => { if (on && !drawing() && Date.now() - doneAt > 400) start(); });
    if (button) button.addEventListener("click", () => (on ? end() : begin()));
    document.addEventListener("keydown", (e) => {
      if (typing() || e.ctrlKey || e.metaKey) return;
      if (e.key === "m" || e.key === "M") { on ? (drawing() ? null : start()) : begin(); return; }
      if (!on) return;
      if (e.key === "Escape") end();
      else if (e.key === "g" || e.key === "G") saveGrid(grid.cycle(e.shiftKey ? -1 : 1));
      else if (drawing() && e.key === "Backspace") { e.preventDefault(); line()._removeLastVertex(); live(); }
      else if (drawing() && e.key === "Enter") {
        e.preventDefault();
        if (line()._layer.getLatLngs().length >= 2) line()._finishShape();
      }
    });
    return {begin, end};
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

  /* The ✎ by a file's name renames that file everywhere: the server renames it and
     rewrites every link to it. Unsaved text in the raw editor is saved first so its own
     links are rewritten too. The page then reloads under the new name. */
  function renamer(btn) {
    const path = btn.dataset.rename;
    const ta = document.getElementById("text");
    const original = ta ? ta.value : null;
    btn.addEventListener("click", async () => {
      const stem = path.split("/").pop().replace(/\.md$/i, "");
      const neu = prompt(`Rename ${stem} (and every link to it) to:`, stem);
      if (!neu || !neu.trim() || neu.trim() === stem) return;
      const body = new URLSearchParams({path, new: neu.trim()});
      if (ta && ta.value !== original) {
        if (!confirm("Your edits to this file will be saved as part of the rename. Go ahead?")) return;
        body.set("text", ta.value);
      }
      const r = await fetch("/api/rename", {method: "POST", body});
      const j = await r.json().catch(() => ({error: `rename failed (${r.status})`}));
      if (j.error) { alert(j.error); return; }
      try {
        sessionStorage.setItem("grazr.renamed", `Renamed ${j.old} → ${j.new}: ${j.links} link${j.links === 1 ? "" : "s"}` +
          ` in ${j.files} file${j.files === 1 ? "" : "s"} rewritten. Unlinked mentions in prose are unchanged.`);
      } catch (e) { /* private mode */ }
      location.href = ta ? "/n/" + j.path.split("/").map(encodeURIComponent).join("/") + "?edit=1" : j.url;
    });
  }

  function renamedFlash() {
    let msg = null;
    try { msg = sessionStorage.getItem("grazr.renamed"); sessionStorage.removeItem("grazr.renamed"); } catch (e) { /* ignore */ }
    const el = document.getElementById("renamed");
    if (msg && el) { el.textContent = msg; el.hidden = false; }
  }

  /* Click a heading (one with data-key) to sort the table by it; again to reverse.
     A cell's data-sort, else its text, decides: blanks last, then numbers ("1,029 ft"),
     dates (2025-04-19 or 24MAR2024), then text in natural order (orange-9 < orange-10).
     The choice lives in the page's `sort` input and URL, so filters and reloads keep it. */
  const MONTHS = {jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12};
  function sortValue(td) {
    const s = (td.dataset.sort !== undefined ? td.dataset.sort : td.textContent).trim();
    if (!s) return null;
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return {t: 1, v: s};
    const d = s.match(/^(\d{1,2})\s*([A-Za-z]{3})[A-Za-z]*\s*(\d{4})$/);
    if (d && MONTHS[d[2].toLowerCase()]) {
      return {t: 1, v: `${d[3]}-${String(MONTHS[d[2].toLowerCase()]).padStart(2, "0")}-${d[1].padStart(2, "0")}`};
    }
    const n = s.match(/^[~≈]?(-?[\d,]*\.?\d+)(?![\d-])/);
    if (n) return {t: 0, v: parseFloat(n[1].replace(/,/g, ""))};
    return {t: 2, v: s};
  }
  function sortableTable(table) {
    const heads = [...table.tHead.rows[0].cells];
    const body = table.tBodies[0];
    const input = document.querySelector("input[name=sort]");
    const apply = (spec) => {
      const desc = spec.startsWith("-"), key = desc ? spec.slice(1) : spec;
      const col = heads.findIndex((th) => th.dataset.key === key);
      heads.forEach((th) => th.classList.remove("asc", "desc"));
      if (col < 0) return;
      heads[col].classList.add(desc ? "desc" : "asc");
      const rows = [...body.rows].map((tr, i) => ({tr, i, k: sortValue(tr.cells[col])}));
      rows.sort((a, b) => {
        if (!a.k || !b.k) return a.k ? -1 : b.k ? 1 : a.i - b.i;    // blanks last, either way
        const c = a.k.t - b.k.t || (a.k.t === 0 ? a.k.v - b.k.v
          : String(a.k.v).localeCompare(String(b.k.v), undefined, {numeric: true, sensitivity: "base"}));
        return (desc ? -c : c) || a.i - b.i;
      });
      rows.forEach((r) => body.appendChild(r.tr));
    };
    heads.forEach((th) => {
      if (!th.dataset.key) return;
      th.classList.add("sortable");
      th.title = "Sort by " + th.textContent.trim();
      th.addEventListener("click", () => {
        const cur = input ? input.value : "";
        const spec = cur === th.dataset.key ? "-" + th.dataset.key : th.dataset.key;
        if (input) input.value = spec;
        const url = new URL(location.href);
        url.searchParams.set("sort", spec);
        history.replaceState(null, "", url);
        apply(spec);
      });
    });
    if (input && input.value) apply(input.value);
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

  // ← / → on a detail page step through the list it was opened from, as filtered and
  // sorted then; opened some other way, through the page's own list (data-pager)
  const NAV_KEY = "grazr.nav";
  const pathOf = (href) => new URL(href, location.href).pathname;

  function rememberList(table) {
    table.addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (!a || !table.tBodies[0] || !table.tBodies[0].contains(a)) return;
      const items = [...table.tBodies[0].rows].filter((tr) => !tr.hidden).map((tr) => {
        const l = tr.querySelector("td a[href]");
        return l && {href: l.getAttribute("href"), label: decodeURIComponent(pathOf(l.getAttribute("href")).split("/").pop())};
      }).filter(Boolean);
      try { sessionStorage.setItem(NAV_KEY, JSON.stringify({from: location.pathname + location.search, items})); } catch (err) { /* ignore */ }
    });
  }

  function pager(el) {
    const here = location.pathname;
    let items = JSON.parse(el.dataset.pager || "[]"), from = "";
    try {
      const s = JSON.parse(sessionStorage.getItem(NAV_KEY) || "null");
      if (s && s.items.some((x) => pathOf(x.href) === here)) { items = s.items; from = s.from; }
    } catch (e) { /* ignore */ }
    const i = items.findIndex((x) => pathOf(x.href) === here);
    if (i < 0 || items.length < 2) return;
    const prev = items[(i - 1 + items.length) % items.length], next = items[(i + 1) % items.length];
    const count = `${i + 1} of ${items.length}`;
    el.innerHTML = `<a class="btn" href="${esc(prev.href)}" title="${esc(prev.label)} (←)">‹</a>` +
      (from ? `<a class="muted small" href="${esc(from)}" title="back to the list">${count}</a>` : `<span class="muted small">${count}</span>`) +
      `<a class="btn" href="${esc(next.href)}" title="${esc(next.label)} (→)">›</a>`;
    document.addEventListener("keydown", (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
      if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "ArrowLeft") location.href = prev.href;
      else if (e.key === "ArrowRight") location.href = next.href;
    });
    // phones: swipe left for the next one, right for the previous (not on maps, tables or text)
    let t0 = null;
    document.addEventListener("touchstart", (e) => {
      const ok = e.touches.length === 1 && !e.target.closest(".map, .leaflet-container, table, input, textarea, select, pre");
      t0 = ok ? {x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now()} : null;
    }, {passive: true});
    document.addEventListener("touchend", (e) => {
      if (!t0) return;
      const dx = e.changedTouches[0].clientX - t0.x, dy = e.changedTouches[0].clientY - t0.y;
      if (Math.abs(dx) > 80 && Math.abs(dx) > 2.5 * Math.abs(dy) && Date.now() - t0.t < 600) {
        location.href = dx < 0 ? next.href : prev.href;
      }
      t0 = null;
    }, {passive: true});
  }

  // the header's and the phone tab bar's links: the one for this page is `.on`
  function markNav() {
    const here = location.pathname;
    document.querySelectorAll("[data-match]").forEach((a) => {
      const on = a.dataset.match.split(" ").some((m) => (m === "/" ? here === "/" : here === m || here.startsWith(m.endsWith("/") ? m : m + "/")));
      a.classList.toggle("on", on);
    });
    const more = document.querySelector(".tabbar details.more");
    if (more) {
      if (more.querySelector(".on")) more.querySelector("summary").classList.add("on");
      document.addEventListener("click", (e) => { if (more.open && !more.contains(e.target)) more.open = false; });
    }
  }

  // buttons that type into a textarea at the cursor (phones: `[[` is awkward to reach);
  // `[[` then opens the link completion as if typed
  function insertChips(bar) {
    const ta = document.getElementById(bar.dataset.insertFor);
    if (!ta) return;
    bar.addEventListener("pointerdown", (e) => { if (e.target.closest("[data-ins]")) e.preventDefault(); });  // keep the keyboard up
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-ins]");
      if (!b) return;
      const cur = ta.selectionStart, atStart = b.hasAttribute("data-at-start");
      let pos = cur, ins = b.dataset.ins;
      if (atStart) {                                     // `@yesterday` goes at the start of the line
        pos = cur ? ta.value.lastIndexOf("\n", cur - 1) + 1 : 0;
      } else if (pos && !/\s/.test(ta.value[pos - 1])) {
        ins = " " + ins;
      }
      ta.value = ta.value.slice(0, pos) + ins + ta.value.slice(pos);
      const end = (atStart ? cur : pos) + ins.length;
      ta.focus();
      ta.setSelectionRange(end, end);
      ta.dispatchEvent(new Event("input"));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".tl-filter").forEach(timelineFilter);
    document.querySelectorAll("table[data-sortable]").forEach(sortableTable);
    document.querySelectorAll("table[data-sortable]").forEach(rememberList);
    document.querySelectorAll("[data-pager]").forEach(pager);
    document.querySelectorAll("[data-insert-for]").forEach(insertChips);
    markNav();
    document.querySelectorAll("textarea[data-editor]").forEach(editor);
    document.querySelectorAll("button[data-rename]").forEach(renamer);
    document.querySelectorAll("form[data-confirm]").forEach((f) => {
      f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
    });
    renamedFlash();
    document.querySelectorAll("[data-check-all]").forEach((box) => {
      box.addEventListener("change", () => {
        document.querySelectorAll(box.dataset.checkAll).forEach((c) => { c.checked = box.checked; });
      });
    });
  });

  /* Pounds like the server's `lb` filter: `400 lb (20 buckets)`. */
  function fmtLb(v, sizes) {
    let out = Math.abs(v) >= 10 ? `${Math.round(v).toLocaleString()} lb` : `${v.toFixed(1)} lb`;
    const fits = Object.entries(sizes || {}).filter(([, w]) => w > 0 && Math.abs(v) >= w).sort((p, q) => q[1] - p[1]);
    if (fits.length) {
      const [u, w] = fits[0], n = v / w;
      out += ` (${n.toFixed(1).replace(/\.0$/, "")} ${u}${Math.abs(n) === 1 ? "" : "s"})`;
    }
    return out;
  }
  const DAY = 864e5;
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const shortDate = (t, year) => { const d = new Date(t); return `${d.getDate()} ${MON[d.getMonth()]}${year ? " " + d.getFullYear() : ""}`; };
  function niceStep(span, n) {
    const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw);
  }

  /* A feed's amount on hand over time, as steps: it changes at each feeding, purchase
     and count. Purchases and counts are marked; a dashed line runs from today to empty
     at the current rate. Tap or hover to read the amount on a day. */
  function onHandChart(el) {
    const d = JSON.parse(el.dataset.chart);
    const pts = d.points.map(([ts, lb, kind, daily]) => ({t: new Date(ts).getTime(), lb, kind, daily}));
    const now = new Date(d.now).getTime(), last = pts[pts.length - 1];
    const NS = "http://www.w3.org/2000/svg";
    const draw = () => {
      const W = Math.max(260, el.clientWidth), H = W < 500 ? 230 : 240;
      const fs = parseFloat(getComputedStyle(document.documentElement).fontSize);
      const m = {l: 8, r: 10, t: fs * 1.4, b: fs * 1.5};
      let t0 = pts[0].t, t1 = Math.max(now, last.t);
      // the projection: to empty at the current rate, shown for at most half the history (min 2 weeks)
      let proj = null;
      if (d.per_day > 0 && last.lb > 0) {
        const empty = t1 + last.lb / d.per_day * DAY, cap = t1 + Math.max(14 * DAY, (t1 - t0) / 2);
        proj = {t: Math.min(empty, cap), lb: empty <= cap ? 0 : last.lb - d.per_day * (cap - t1) / DAY, empty};
      }
      const tEnd = proj ? proj.t : t1;
      if (tEnd - t0 < DAY) t0 = tEnd - DAY;
      const vals = pts.map((p) => p.lb);
      const lo = Math.min(0, ...vals), step = niceStep(Math.max(...vals, 1) - lo, 4);
      const yMax = Math.ceil(Math.max(...vals, 1) / step) * step, yMin = Math.floor(lo / step) * step;
      const yTicks = [];
      for (let v = yMin; v <= yMax + step / 2; v += step) yTicks.push(v);
      const px = fs * 0.7;   // .chart .tick
      const tickLb = (v) => `${Math.round(v).toLocaleString()} lb`;
      const tickW = Math.max(...yTicks.map((v) => tickLb(v).length)) * px * 0.6 + 6;
      m.l = Math.max(m.l, tickW);
      const X = (t) => m.l + (t - t0) / (tEnd - t0) * (W - m.l - m.r);
      const Y = (v) => m.t + (yMax - v) / (yMax - yMin) * (H - m.t - m.b);

      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("width", W); svg.setAttribute("height", H);
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.classList.add("chart");
      const add = (tag, attrs, parent = svg) => {
        const e = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
        parent.appendChild(e);
        return e;
      };
      for (const v of yTicks) {
        add("line", {x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: v === 0 ? "zero" : "grid"});
        add("text", {x: m.l - 6, y: Y(v) + px * 0.35, "text-anchor": "end", class: "tick"}).textContent = tickLb(v);
      }
      // dates: about one per 90 px, on whole days
      const span = tEnd - t0, fit = Math.max(2, Math.floor((W - m.l) / (px * 5.5)));
      const every = [1, 2, 3, 7, 14, 30, 61, 91, 182, 365].find((n) => span / DAY / n <= fit) || 365;
      const years = new Date(t0).getFullYear() !== new Date(tEnd).getFullYear();
      const first = new Date(t0); first.setHours(0, 0, 0, 0);
      const ticks = [];
      if (every >= 30) {      // the 1st of every month, or every few months
        const step = Math.round(every / 30.4);
        for (const d = new Date(first.getFullYear(), first.getMonth() + 1, 1); d.getTime() < tEnd; d.setMonth(d.getMonth() + step)) ticks.push(d.getTime());
      } else for (let t = first.getTime() + DAY; t < tEnd; t += every * DAY) ticks.push(t);
      for (const t of ticks) {
        if (X(t) < m.l + px * 1.5 || X(t) > W - m.r - px * 2) continue;
        add("text", {x: X(t), y: H - fs * 0.4, "text-anchor": "middle", class: "tick"}).textContent = shortDate(t, years && every > 20);
      }
      // the steps, from the first known amount to today
      let path = `M${X(pts[0].t)},${Y(pts[0].lb)}`;
      for (let i = 1; i < pts.length; i++) path += `H${X(pts[i].t)}V${Y(pts[i].lb)}`;
      path += `H${X(t1)}`;
      add("path", {d: `${path}V${Y(Math.max(0, yMin))}H${X(pts[0].t)}Z`, class: "area"});
      add("path", {d: path, class: "line"});
      if (proj) {
        add("line", {x1: X(t1), y1: Y(last.lb), x2: X(proj.t), y2: Y(proj.lb), class: "proj"});
        if (proj.lb === 0) {
          add("text", {x: Math.min(X(proj.t), W - m.r), y: Y(0) - 6, "text-anchor": "end", class: "tick"})
            .textContent = `empty ~${shortDate(proj.empty)}`;
        }
      }
      add("line", {x1: X(now), x2: X(now), y1: m.t - 4, y2: H - m.b, class: "today"});
      add("text", {x: X(now), y: m.t - fs * 0.4, "text-anchor": "middle", class: "tick"}).textContent = "today";
      for (const p of pts) {
        if (p.kind !== "buy" && p.kind !== "count") continue;
        const c = add("circle", {cx: X(p.t), cy: Y(p.lb), r: 4.5, class: p.kind});
        add("title", {}, c).textContent = `${p.kind === "buy" ? "Bought, now" : "Counted"} ${fmtLb(p.lb, d.sizes)} · ${shortDate(p.t, true)}`;
      }
      // reading a day: a cursor line and the amount then
      const cur = add("line", {y1: m.t, y2: H - m.b, class: "cursor", visibility: "hidden"});
      const dotC = add("circle", {r: 4, class: "cursor-dot", visibility: "hidden"});

      const at = (t) => { let v = null; for (const p of pts) { if (p.t <= t) v = p.lb; else break; } return v; };
      const idle = `Now <b>${fmtLb(last.lb, d.sizes)}</b>` + (proj ? ` · empty about <b>${shortDate(proj.empty, true)}</b> at ${fmtLb(d.per_day)} a day` : "") +
        ` <span class="muted">· tap or hover to read a day</span>`;
      const read = (ev) => {
        const r = svg.getBoundingClientRect(), x = ev.clientX - r.left;
        const t = t0 + (x - m.l) / (W - m.l - m.r) * (tEnd - t0);
        let v = t <= t1 ? at(t) : proj ? last.lb - d.per_day * (t - t1) / DAY : null;
        if (v === null || t < pts[0].t || t > tEnd) { hide(); return; }
        if (t > t1) v = Math.max(0, v);
        for (const e of [cur, dotC]) e.setAttribute("visibility", "visible");
        cur.setAttribute("x1", x); cur.setAttribute("x2", x);
        dotC.setAttribute("cx", x); dotC.setAttribute("cy", Y(v));
        readout.innerHTML = `${shortDate(t, true)}: <b>${fmtLb(v, d.sizes)}</b>${t > t1 ? " <span class=\"muted\">(expected)</span>" : ""}`;
      };
      const hide = () => { for (const e of [cur, dotC]) e.setAttribute("visibility", "hidden"); readout.innerHTML = idle; };
      svg.addEventListener("pointermove", read);
      svg.addEventListener("pointerdown", read);
      svg.addEventListener("pointerleave", hide);
      const readout = document.createElement("div");
      readout.className = "chart-read small";
      readout.innerHTML = idle;
      el.replaceChildren(readout, svg);
    };
    draw();
    let w = el.clientWidth;
    new ResizeObserver(() => { if (el.clientWidth !== w) { w = el.clientWidth; draw(); } }).observe(el);
  }

  return {restMap, drawMap, measureTool, restColor, fmtDays, onHandChart};
})();
