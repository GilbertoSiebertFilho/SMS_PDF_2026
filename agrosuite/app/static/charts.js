/* Gráficos em SVG inline.
 *
 * São poucos e específicos — histograma comparativo e curva de resposta —
 * então desenhar o SVG à mão sai menor e mais controlável do que carregar
 * uma biblioteca de gráficos inteira num app que precisa abrir offline. */

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

  /* Histograma antes/depois sobreposto: mostra de onde a limpeza tirou massa
   * da distribuição — é a leitura que diz se ela cortou uma cauda ou o meio. */
  function histogram(container, before, after, options = {}) {
    container.innerHTML = "";
    const width = container.clientWidth || 360;
    const height = options.height || 150;
    const pad = { t: 8, r: 8, b: 20, l: 34 };

    if (!before?.counts?.length && !after?.counts?.length) {
      container.innerHTML = '<div class="empty">Sem dados para o histograma.</div>';
      return;
    }

    const svg = el("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, height }, container);
    const series = [before, after].filter((s) => s?.counts?.length);
    const xMin = Math.min(...series.map((s) => s.edges[0]));
    const xMax = Math.max(...series.map((s) => s.edges[s.edges.length - 1]));
    const yMax = Math.max(...series.map((s) => Math.max(...s.counts)));
    if (!isFinite(xMin) || !isFinite(xMax) || xMax === xMin) {
      container.innerHTML = '<div class="empty">Distribuição sem amplitude.</div>';
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

  /* Curva de rendimento e curva de lucro no mesmo eixo de dose, com a dose
   * ótima marcada — as três coisas que decidem a recomendação. */
  function responseCurve(container, curve, optimum, options = {}) {
    container.innerHTML = "";
    if (!curve?.length) {
      container.innerHTML = '<div class="empty">Sem curva ajustada.</div>';
      return;
    }
    const width = container.clientWidth || 360;
    const height = options.height || 200;
    const pad = { t: 12, r: 44, b: 28, l: 46 };

    const svg = el("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, height }, container);

    // A curva vem em unidade interna; o eixo da dose acompanha a unidade de
    // insumo escolhida, senão o gráfico contradiz os números ao lado dele.
    const rateConv = options.rateConv || ((v) => v);
    const rates = curve.map((p) => rateConv(p.dose));
    const yields = curve.map((p) => options.yieldConv(p.rendimento));
    const profits = curve.map((p) => p.lucro);

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

    if (optimum?.dose_otima != null) {
      const x = sx(rateConv(optimum.dose_otima));
      el("line", {
        x1: x, y1: pad.t, x2: x, y2: height - pad.b,
        stroke: css("--text-muted"), "stroke-width": 1, "stroke-dasharray": "2 3",
      }, svg);
      const t = el("text", {
        x: Math.min(x + 4, width - pad.r - 4), y: pad.t + 9,
        "font-size": 10, fill: css("--text-muted"),
      }, svg);
      t.textContent = `ótima ${Units.num(optimum.dose_otima, 0)}`;
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
      `— rendimento (${options.yieldUnit})   ╌ lucro   ·   dose em ${options.rateUnit || ""}`;
  }

  /* Barras horizontais para a contagem de remoções por motivo. */
  function bars(container, rows, options = {}) {
    container.innerHTML = "";
    if (!rows?.length) {
      container.innerHTML = '<div class="empty">Nenhum registro removido.</div>';
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

  return { histogram, responseCurve, bars };
})();
