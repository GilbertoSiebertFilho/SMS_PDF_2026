/* Inline SVG charts.
 *
 * There are only a few, and they are specific — a comparative histogram, a
 * response curve, an elevation profile and an aspect rose — so drawing the
 * SVG by hand comes out smaller and more controllable than loading a whole
 * charting library into an app that has to open offline. */

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs = {}, parent = null) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /* Overlaid before/after histogram: it shows where the cleaning took mass out
   * of the distribution, which is what says whether it cut a tail or the middle. */
  function histogram(container, before, after, options = {}) {
    container.innerHTML = "";
    const width = container.clientWidth || 360;
    const height = options.height || 150;
    const pad = { t: 8, r: 8, b: 20, l: 34 };

    if (!before?.counts?.length && !after?.counts?.length) {
      container.innerHTML = '<div class="empty">No data for the histogram.</div>';
      return;
    }

    const svg = el("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, height }, container);
    const series = [before, after].filter((s) => s?.counts?.length);
    const xMin = Math.min(...series.map((s) => s.edges[0]));
    const xMax = Math.max(...series.map((s) => s.edges[s.edges.length - 1]));
    const yMax = Math.max(...series.map((s) => Math.max(...s.counts)));
    if (!isFinite(xMin) || !isFinite(xMax) || xMax === xMin) {
      container.innerHTML = '<div class="empty">The distribution has no spread.</div>';
      return;
    }

    const sx = (v) => pad.l + ((v - xMin) / (xMax - xMin)) * (width - pad.l - pad.r);
    const sy = (v) => height - pad.b - (v / yMax) * (height - pad.t - pad.b);

    const draw = (data, fill, opacity) => {
      if (!data?.counts?.length) return;
      for (let i = 0; i < data.counts.length; i++) {
        const x0 = sx(data.edges[i]);
        const x1 = sx(data.edges[i + 1]);
        const y = sy(data.counts[i]);
        el("rect", {
          x: x0, y, width: Math.max(0.6, x1 - x0 - 0.4),
          height: Math.max(0, height - pad.b - y), fill, opacity,
        }, svg);
      }
    };

    draw(before, css("--text-faint") || "#999", 0.5);
    draw(after, css("--accent") || "#2f7d4f", 0.75);

    el("line", {
      x1: pad.l, y1: height - pad.b, x2: width - pad.r, y2: height - pad.b,
      stroke: css("--border-strong"), "stroke-width": 1,
    }, svg);

    /* Breaks that mean something outside the data — the agronomic slope
     * classes — drawn where they fall, so the bars are read against them
     * rather than against the distribution's own shape. */
    for (const mark of options.marks || []) {
      if (!(mark.value > xMin && mark.value < xMax)) continue;
      const x = sx(mark.value);
      el("line", {
        x1: x, y1: pad.t, x2: x, y2: height - pad.b,
        stroke: css("--text-faint"), "stroke-width": 1, "stroke-dasharray": "2 3",
      }, svg);
      if (!mark.label) continue;
      const label = el("text", {
        x: x + 2, y: pad.t + 8, "font-size": 9, fill: css("--text-faint"),
      }, svg);
      label.textContent = mark.label;
    }

    const fmt = options.format || ((v) => Units.num(v, 0));
    for (const [value, anchor] of [[xMin, "start"], [xMax, "end"]]) {
      const t = el("text", {
        x: sx(value), y: height - 6, "text-anchor": anchor,
        "font-size": 10, fill: css("--text-faint"),
      }, svg);
      t.textContent = fmt(value);
    }
    const yLabel = el("text", {
      x: 4, y: pad.t + 8, "font-size": 10, fill: css("--text-faint"),
    }, svg);
    yLabel.textContent = Units.num(yMax, 0);
  }

  /* Yield curve and profit curve on the same rate axis, with the optimum
   * marked — the three things that decide the recommendation. */
  function responseCurve(container, curve, optimum, options = {}) {
    container.innerHTML = "";
    if (!curve?.length) {
      container.innerHTML = '<div class="empty">No fitted curve.</div>';
      return;
    }
    const width = container.clientWidth || 360;
    const height = options.height || 200;
    const pad = { t: 12, r: 44, b: 28, l: 46 };

    const svg = el("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, height }, container);

    // The curve arrives in internal units; the rate axis follows the chosen
    // input unit, or the chart contradicts the numbers beside it.
    const rateConv = options.rateConv || ((v) => v);
    const rates = curve.map((p) => rateConv(p.rate));
    const yields = curve.map((p) => options.yieldConv(p["yield"]));
    const profits = curve.map((p) => p.profit);

    const xMin = Math.min(...rates), xMax = Math.max(...rates);
    const yMin = Math.min(...yields), yMax = Math.max(...yields);
    const pMin = Math.min(...profits), pMax = Math.max(...profits);

    const sx = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (width - pad.l - pad.r);
    const syY = (v) => height - pad.b - ((v - yMin) / (yMax - yMin || 1)) * (height - pad.t - pad.b);
    const syP = (v) => height - pad.b - ((v - pMin) / (pMax - pMin || 1)) * (height - pad.t - pad.b);

    el("line", {
      x1: pad.l, y1: height - pad.b, x2: width - pad.r, y2: height - pad.b,
      stroke: css("--border-strong"),
    }, svg);

    const path = (values, scale, stroke, dash) => {
      const d = values.map((v, i) => `${i ? "L" : "M"}${sx(rates[i]).toFixed(1)},${scale(v).toFixed(1)}`).join(" ");
      el("path", {
        d, fill: "none", stroke, "stroke-width": 2,
        "stroke-dasharray": dash || "none", "stroke-linejoin": "round",
      }, svg);
    };

    path(yields, syY, css("--accent"));
    path(profits, syP, css("--warn"), "4 3");

    if (optimum?.optimum_rate != null) {
      const x = sx(rateConv(optimum.optimum_rate));
      el("line", {
        x1: x, y1: pad.t, x2: x, y2: height - pad.b,
        stroke: css("--text-muted"), "stroke-width": 1, "stroke-dasharray": "2 3",
      }, svg);
      const t = el("text", {
        x: Math.min(x + 4, width - pad.r - 4), y: pad.t + 9,
        "font-size": 10, fill: css("--text-muted"),
      }, svg);
      t.textContent = `optimum ${Units.num(rateConv(optimum.optimum_rate), 0)}`;
    }

    const tick = (x, y, text, anchor, color) => {
      const t = el("text", { x, y, "text-anchor": anchor, "font-size": 10, fill: color }, svg);
      t.textContent = text;
    };
    tick(pad.l, height - 8, Units.num(xMin, 0), "start", css("--text-faint"));
    tick(width - pad.r, height - 8, Units.num(xMax, 0), "end", css("--text-faint"));
    tick(4, pad.t + 8, Units.num(yMax, 0), "start", css("--accent"));
    tick(4, height - pad.b, Units.num(yMin, 0), "start", css("--accent"));
    tick(width - 4, pad.t + 8, Units.num(pMax, 0), "end", css("--warn"));
    tick(width - 4, height - pad.b, Units.num(pMin, 0), "end", css("--warn"));

    const legend = el("text", {
      x: pad.l, y: pad.t + 9, "font-size": 10, fill: css("--text-muted"),
    }, svg);
    legend.textContent =
      `— yield (${options.yieldUnit})   ╌ profit   ·   rate in ${options.rateUnit || ""}`;
  }

  /* Elevation along a line drawn on the map: height against distance, both
   * in the unit the panel beside it uses.
   *
   * Stations that fall outside the field come back null, and the path
   * breaks there rather than joining across the gap — a straight segment
   * over ground that was never measured is an invention, and it is exactly
   * where a line clipping the corner of a field would draw one. */
  function profile(container, series, options = {}) {
    container.innerHTML = "";
    const distance = series?.distance || [];
    const elevation = series?.elevation || [];
    const points = distance
      .map((d, i) => ({ d, z: elevation[i] }))
      .filter((p) => Number.isFinite(p.d));
    const measured = points.filter((p) => Number.isFinite(p.z));
    if (measured.length < 2) {
      container.innerHTML = '<div class="empty">The line crosses too little of the field to draw.</div>';
      return;
    }

    const width = container.clientWidth || 360;
    const height = options.height || 170;
    const pad = { t: 14, r: 10, b: 24, l: 44 };
    const svg = el("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, height }, container);

    const xMin = 0, xMax = Math.max(...points.map((p) => p.d)) || 1;
    let zMin = Math.min(...measured.map((p) => p.z));
    let zMax = Math.max(...measured.map((p) => p.z));
    // A profile along a contour is flat, not an error: a band around the
    // height keeps the line in the middle instead of magnifying millimetres.
    if (zMax - zMin < 1e-6) { zMin -= 0.5; zMax += 0.5; }
    const span = zMax - zMin;
    const sx = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (width - pad.l - pad.r);
    const sy = (v) => height - pad.b - ((v - zMin) / span) * (height - pad.t - pad.b);

    el("line", {
      x1: pad.l, y1: height - pad.b, x2: width - pad.r, y2: height - pad.b,
      stroke: css("--border-strong"), "stroke-width": 1,
    }, svg);

    // One path per measured run, so a gap stays a gap.
    let run = [];
    const flush = () => {
      if (run.length >= 2) {
        el("path", {
          d: run.map((p, i) => `${i ? "L" : "M"}${sx(p.d).toFixed(1)},${sy(p.z).toFixed(1)}`).join(" "),
          fill: "none", stroke: css("--accent"), "stroke-width": 2,
          "stroke-linejoin": "round", "stroke-linecap": "round",
        }, svg);
      } else if (run.length === 1) {
        el("circle", { cx: sx(run[0].d), cy: sy(run[0].z), r: 2, fill: css("--accent") }, svg);
      }
      run = [];
    };
    for (const point of points) {
      if (Number.isFinite(point.z)) run.push(point);
      else flush();
    }
    flush();

    const tick = (x, y, text, anchor, color) => {
      const node = el("text", { x, y, "text-anchor": anchor, "font-size": 10, fill: color }, svg);
      node.textContent = text;
    };
    // The unit rides on the top tick of each axis rather than on a floating
    // label, which on a line that ends high would sit over the ground it
    // describes.
    const distanceUnit = options.distanceUnit ? ` ${options.distanceUnit}` : "";
    const heightUnit = options.elevationUnit ? ` ${options.elevationUnit}` : "";
    tick(pad.l, height - 7, "0", "start", css("--text-faint"));
    tick(width - pad.r, height - 7, `${Units.num(xMax, 0)}${distanceUnit}`, "end", css("--text-faint"));
    tick(4, pad.t + 4, `${Units.num(zMax, 1)}${heightUnit}`, "start", css("--accent"));
    tick(4, height - pad.b, Units.num(zMin, 1), "start", css("--accent"));
  }

  /* The aspect rose: which way the field faces, as eight petals whose
   * length is the share of the field facing that way. Ground too flat to
   * have an aspect is named under the rose rather than drawn as a ninth
   * petal — it points nowhere, and a petal would give it a direction. */
  function rose(container, sectors, options = {}) {
    container.innerHTML = "";
    const bearings = {
      N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315,
    };
    const petals = (sectors || []).filter((s) => s.key in bearings);
    if (!petals.length) {
      container.innerHTML = '<div class="empty">No aspect to show.</div>';
      return;
    }
    const size = options.size || Math.min(container.clientWidth || 220, 240);
    const svg = el("svg", { class: "chart", viewBox: `0 0 ${size} ${size}`, height: size }, container);
    const cx = size / 2, cy = size / 2;
    const radius = size / 2 - 16;
    const max = Math.max(...petals.map((p) => p.pct || 0)) || 1;

    for (const fraction of [0.5, 1]) {
      el("circle", {
        cx, cy, r: radius * fraction, fill: "none",
        stroke: css("--border"), "stroke-width": 1, "stroke-dasharray": "2 3",
      }, svg);
    }

    const point = (bearing, r) => [
      cx + r * Math.sin((bearing * Math.PI) / 180),
      cy - r * Math.cos((bearing * Math.PI) / 180),
    ];
    for (const petal of petals) {
      const r = radius * ((petal.pct || 0) / max);
      if (r <= 0.5) continue;
      const centre = bearings[petal.key];
      const [x1, y1] = point(centre - 22.5, r);
      const [x2, y2] = point(centre + 22.5, r);
      const wedge = el("path", {
        d: `M${cx},${cy} L${x1.toFixed(1)},${y1.toFixed(1)} A${r.toFixed(1)},${r.toFixed(1)} 0 0 1 ` +
           `${x2.toFixed(1)},${y2.toFixed(1)} Z`,
        fill: css("--accent"), opacity: 0.75, stroke: css("--surface"), "stroke-width": 1,
      }, svg);
      el("title", {}, wedge).textContent = `${petal.label}: ${Units.num(petal.pct, 1)}%`;
    }

    for (const [key, bearing] of Object.entries(bearings)) {
      const [x, y] = point(bearing, radius + 9);
      const label = el("text", {
        x, y: y + 3, "text-anchor": "middle", "font-size": 9,
        fill: key === "N" ? css("--text") : css("--text-faint"),
      }, svg);
      label.textContent = key;
    }
    const scale = el("text", {
      x: cx + 3, y: cy - radius * 0.5 - 2, "font-size": 9, fill: css("--text-faint"),
    }, svg);
    scale.textContent = `${Units.num(max / 2, 0)}%`;
  }

  /* Horizontal bars for the removal counts by reason. */
  function bars(container, rows, options = {}) {
    container.innerHTML = "";
    if (!rows?.length) {
      container.innerHTML = '<div class="empty">No record was removed.</div>';
      return;
    }
    const max = Math.max(...rows.map((r) => r.value));
    const list = document.createElement("div");
    for (const row of rows) {
      const wrap = document.createElement("div");
      wrap.style.marginBottom = "7px";
      const top = document.createElement("div");
      top.style.cssText = "display:flex;justify-content:space-between;font-size:12px;gap:8px";
      top.innerHTML =
        `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${row.label}</span>` +
        `<span style="font-family:var(--mono);color:var(--text-muted);flex:none">${Units.num(row.value, 0)}${
          options.suffix ? " " + options.suffix : ""}</span>`;
      const track = document.createElement("div");
      track.className = "progress-track";
      track.style.marginTop = "3px";
      const fill = document.createElement("div");
      fill.className = "progress-fill";
      fill.style.width = `${(row.value / max) * 100}%`;
      track.appendChild(fill);
      wrap.append(top, track);
      list.appendChild(wrap);
    }
    container.appendChild(list);
  }

  return { histogram, responseCurve, profile, rose, bars };
})();
