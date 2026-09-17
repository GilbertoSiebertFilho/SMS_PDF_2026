/* Map and point layer.
 *
 * The points do not become Leaflet markers: a yield map has tens of thousands
 * of them, and one DOM element per point locks up the browser. Instead a canvas
 * laid over the map is redrawn on every move, projecting the coordinates with
 * Leaflet's own maths. */

const MapView = (() => {
  let map = null;
  let canvas = null;
  let ctx = null;
  let basemap = null;
  let overlayGroup = null;
  let data = null;      // { lon: [], lat: [], values: [], scale: {} }
  let colorScale = null;
  let pointSize = 3;

  /* A cool-to-warm ramp, legible in both light and dark themes and readable by
   * people who do not see green and red as separate colours: luminance rises
   * monotonically from the minimum to the maximum. */
  const RAMP = [
    [ 49,  54, 149],
    [ 69, 117, 180],
    [116, 173, 209],
    [171, 217, 233],
    [254, 224, 144],
    [253, 174,  97],
    [244, 109,  67],
    [215,  48,  39],
  ];

  function rampColor(t) {
    const clamped = Math.max(0, Math.min(1, t));
    const scaled = clamped * (RAMP.length - 1);
    const i = Math.min(RAMP.length - 2, Math.floor(scaled));
    const f = scaled - i;
    const a = RAMP[i], b = RAMP[i + 1];
    return [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f),
    ];
  }

  function rampCss() {
    const stops = RAMP.map((c, i) =>
      `rgb(${c.join(",")}) ${((i / (RAMP.length - 1)) * 100).toFixed(0)}%`);
    return `linear-gradient(90deg, ${stops.join(", ")})`;
  }

  function init(mapId, canvasId) {
    map = L.map(mapId, { preferCanvas: true, zoomControl: true, attributionControl: false })
      .setView([-23.5, -51.2], 15);

    basemap = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 21, maxNativeZoom: 19 },
    );
    basemap.addTo(map);
    overlayGroup = L.layerGroup().addTo(map);

    canvas = document.getElementById(canvasId);
    ctx = canvas.getContext("2d");

    map.on("move zoom viewreset resize zoomend moveend", draw);
    window.addEventListener("resize", () => { resize(); draw(); });
    resize();
    return map;
  }

  function setBasemap(on) {
    if (!basemap) return;
    if (on && !map.hasLayer(basemap)) basemap.addTo(map);
    if (!on && map.hasLayer(basemap)) map.removeLayer(basemap);
  }

  function resize() {
    if (!canvas || !map) return;
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = size.x * ratio;
    canvas.height = size.y * ratio;
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  /* Feed the point layer. `convert` translates the internal value into the
   * chosen unit — the colour scale is computed in the displayed unit, so the
   * legend and the colours tell the same story. */
  function setPoints(payload, convert) {
    data = payload;
    const scale = payload?.scale;
    if (scale) {
      const low = convert ? convert(scale.low) : scale.low;
      const high = convert ? convert(scale.high) : scale.high;
      colorScale = { low, high, span: (high - low) || 1 };
    } else {
      colorScale = null;
    }
    data._convert = convert || ((v) => v);
    resize();
    draw();
    return colorScale;
  }

  function clearPoints() { data = null; colorScale = null; draw(); }

  function draw() {
    if (!ctx || !canvas) return;
    const size = map.getSize();
    ctx.clearRect(0, 0, size.x, size.y);
    if (!data || !data.lon?.length) return;

    const bounds = map.getBounds().pad(0.05);
    const west = bounds.getWest(), east = bounds.getEast();
    const south = bounds.getSouth(), north = bounds.getNorth();

    const zoom = map.getZoom();
    const radius = Math.max(1.4, Math.min(7, pointSize * (zoom / 15)));
    const hasValues = data.values?.length === data.lon.length;
    const convert = data._convert || ((v) => v);

    const neutral = getComputedStyle(document.documentElement)
      .getPropertyValue("--accent").trim() || "#2f7d4f";

    ctx.globalAlpha = 0.85;
    for (let i = 0; i < data.lon.length; i++) {
      const lon = data.lon[i], lat = data.lat[i];
      if (lon < west || lon > east || lat < south || lat > north) continue;

      const p = map.latLngToContainerPoint([lat, lon]);
      if (hasValues && colorScale) {
        const raw = data.values[i];
        if (raw == null) continue;
        const t = (convert(raw) - colorScale.low) / colorScale.span;
        const c = rampColor(t);
        ctx.fillStyle = `rgb(${c[0]},${c[1]},${c[2]})`;
      } else {
        ctx.fillStyle = neutral;
      }
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, 6.283185307179586);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  /* Polygons: boundary, trial strips, prescription grid. */
  function setPolygons(rings, options = {}) {
    if (!overlayGroup) return;
    overlayGroup.clearLayers();
    if (!rings?.length) return;
    const style = {
      color: options.color || getComputedStyle(document.documentElement)
        .getPropertyValue("--accent").trim(),
      weight: options.weight ?? 1.5,
      fill: options.fill ?? false,
      fillOpacity: options.fillOpacity ?? 0.15,
    };
    for (const ring of rings) {
      const latlngs = ring.map(([lon, lat]) => [lat, lon]);
      L.polygon(latlngs, style).addTo(overlayGroup);
    }
  }

  /* Trial strips, coloured by rate, labelled on click. */
  function setFeatures(collection, rateProperty = "rate") {
    if (!overlayGroup) return;
    overlayGroup.clearLayers();
    if (!collection?.features?.length) return;

    const rates = collection.features
      .map((f) => f.properties?.[rateProperty])
      .filter((v) => v != null && isFinite(v));
    const min = Math.min(...rates), max = Math.max(...rates);
    const span = (max - min) || 1;

    L.geoJSON(collection, {
      style: (feature) => {
        const value = feature.properties?.[rateProperty];
        const c = rampColor(value == null ? 0.5 : (value - min) / span);
        return {
          color: `rgb(${c.join(",")})`, weight: 1.5,
          fillColor: `rgb(${c.join(",")})`, fillOpacity: 0.55,
        };
      },
      onEachFeature: (feature, layer) => {
        const p = feature.properties || {};
        const lines = Object.entries(p)
          .map(([k, v]) => `<b>${k}</b>: ${typeof v === "number" ? Units.num(v) : v}`);
        layer.bindPopup(lines.join("<br>"), { className: "strip-popup" });
      },
    }).addTo(overlayGroup);
    return { min, max };
  }

  /* An AB line over whatever is already drawn, with A and B markers: the visual
   * check that the line's direction matches the strips'. */
  function addLine(points, label) {
    if (!overlayGroup || !points?.length) return;
    const latlngs = points.map(([lon, lat]) => [lat, lon]);
    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue("--text").trim();

    L.polyline(latlngs, { color: accent, weight: 3, opacity: 0.9, dashArray: "10 6" })
      .bindTooltip(label || "AB line", { permanent: false })
      .addTo(overlayGroup);

    for (const [index, name] of [[0, "A"], [latlngs.length - 1, "B"]]) {
      L.marker(latlngs[index], {
        icon: L.divIcon({
          className: "",
          html: `<div style="background:${accent};color:var(--surface);
                 border-radius:50%;width:20px;height:20px;display:flex;
                 align-items:center;justify-content:center;font:600 11px system-ui;
                 box-shadow:0 1px 4px rgba(0,0,0,.4)">${name}</div>`,
          iconSize: [20, 20], iconAnchor: [10, 10],
        }),
      }).addTo(overlayGroup);
    }
  }

  function clearOverlays() { overlayGroup?.clearLayers(); }

  function fit(bounds) {
    if (!bounds || bounds.length !== 4) return;
    const [west, south, east, north] = bounds;
    if (![west, south, east, north].every(Number.isFinite)) return;
    map.fitBounds([[south, west], [north, east]], { padding: [30, 30], maxZoom: 19 });
  }

  function fitOverlays() {
    if (!overlayGroup) return;
    const layers = overlayGroup.getLayers();
    if (!layers.length) return;
    const group = L.featureGroup(layers);
    map.fitBounds(group.getBounds(), { padding: [30, 30], maxZoom: 19 });
  }

  function onClick(handler) { map.on("click", handler); }
  function offClick(handler) { map.off("click", handler); }
  function instance() { return map; }

  return {
    init, setPoints, clearPoints, setPolygons, setFeatures, addLine, clearOverlays,
    fit, fitOverlays, setBasemap, rampCss, rampColor, draw, onClick, offClick, instance,
  };
})();
