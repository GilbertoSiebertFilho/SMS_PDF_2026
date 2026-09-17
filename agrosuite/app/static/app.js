/* AgroSuite — user interface.
 *
 * A five-step flow, one per tab: load the data, clean it, analyse it, lay out
 * a trial, export. State lives in a single object; each tab redraws the right
 * panel from it. */

const App = {
  state: {
    catalog: null,
    units: null,
    datasets: [],
    selectedId: null,
    selected: null,
    colorColumn: "value",
    tab: "dados",
    design: null,
    reports: {},
    drawing: null,
  },

  /* ---------------------------------------------------------------- API */

  async api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
      ...options,
      body: options.body instanceof FormData ? options.body
        : options.body ? JSON.stringify(options.body) : undefined,
    });
    let payload = null;
    try { payload = await response.json(); } catch { /* resposta sem corpo */ }
    if (!response.ok) {
      throw new Error(payload?.detail || `${response.status} ${response.statusText}`);
    }
    return payload;
  },

  /* ------------------------------------------------------------- toasts */

  toast(title, message = "", kind = "") {
    const box = document.getElementById("toasts");
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.innerHTML = `<div class="t"></div><div class="m"></div>`;
    node.querySelector(".t").textContent = title;
    node.querySelector(".m").textContent = message;
    box.appendChild(node);
    setTimeout(() => node.remove(), kind === "error" ? 11000 : 5000);
  },

  async busy(node, fn) {
    node?.classList.add("busy");
    try { return await fn(); }
    catch (err) { this.toast("That did not work", err.message, "error"); return null; }
    finally { node?.classList.remove("busy"); }
  },

  /* ---------------------------------------------------------------- init */

  async init() {
    MapView.init("map", "point-canvas");

    try {
      this.state.catalog = await this.api("/api/catalog");
      this.state.units = this.state.catalog.units;
      Units.init(this.state.units, this.state.units.default_preset);
    } catch (err) {
      this.toast("Server unavailable", err.message, "error");
      return;
    }

    this.buildUnitPreset();
    this.bindTopbar();
    this.bindImport();
    this.bindDialogs();
    this.renderTab();
    this.refreshDatasets().then(() => this.refreshProject());
  },

  bindTopbar() {
    document.getElementById("steps").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-tab]");
      if (!button) return;
      this.state.tab = button.dataset.tab;
      for (const b of document.querySelectorAll("#steps button")) {
        b.setAttribute("aria-selected", String(b === button));
      }
      this.renderTab();
    });

    document.getElementById("theme-toggle").addEventListener("click", () => {
      const root = document.documentElement;
      const current = root.getAttribute("data-theme");
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.setAttribute("data-theme", current ? (current === "dark" ? "light" : "dark")
        : (dark ? "light" : "dark"));
      MapView.draw();
      this.renderTab();
    });

    document.getElementById("unit-details").addEventListener("click", () => this.openUnitsDialog());

    document.getElementById("color-column").addEventListener("change", (event) => {
      this.state.colorColumn = event.target.value;
      this.loadMap();
    });
    document.getElementById("show-basemap").addEventListener("change", (event) => {
      MapView.setBasemap(event.target.checked);
    });
    document.getElementById("btn-fit").addEventListener("click", () => {
      if (this.state.selected?.bounds) MapView.fit(this.state.selected.bounds);
      else MapView.fitOverlays();
    });
  },

  buildUnitPreset() {
    const select = document.getElementById("unit-preset");
    select.innerHTML = "";
    for (const [key, preset] of Object.entries(this.state.units.presets)) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = preset.label;
      option.title = preset.description;
      select.appendChild(option);
    }
    select.value = Units.get().preset;
    select.addEventListener("change", (event) => {
      Units.applyPreset(event.target.value);
      this.toast("Units", this.state.units.presets[event.target.value].description);
      this.loadMap();
      this.renderTab();
    });
  },

  /* ----------------------------------------------------------- datasets */

  async refreshDatasets() {
    const payload = await this.api("/api/datasets").catch(() => null);
    if (!payload) return;
    this.state.datasets = payload.datasets;
    this.renderDatasetList();
    this.refreshProject();
  },

  renderDatasetList() {
    const box = document.getElementById("dataset-list");
    if (!this.state.datasets.length) {
      box.innerHTML = '<div class="empty">Nada carregado ainda.</div>';
      return;
    }
    box.innerHTML = "";
    for (const item of this.state.datasets) {
      const card = document.createElement("div");
      card.className = "card";
      card.setAttribute("aria-selected", String(item.id === this.state.selectedId));
      const origin = { clean: "clean", clean_removed: "removed", units: "converted",
                       demo: "demo", upload: "uploaded", path: "disk" }[item.origin] || item.origin;
      card.innerHTML = `
        <div class="title">${this.escape(item.label)}</div>
        <div class="meta">
          <span class="tag ${item.origin === "clean" ? "accent" : ""}">${origin}</span>
          <span>${Units.num(item.rows, 0)} rec.</span>
          <span>${this.escape(item.meta.brand_label)}</span>
        </div>`;
      card.addEventListener("click", () => this.selectDataset(item.id));
      box.appendChild(card);
    }
  },

  async selectDataset(id) {
    this.state.selectedId = id;
    const detail = await this.api(`/api/datasets/${id}`).catch((err) => {
      this.toast("That did not work", err.message, "error");
      return null;
    });
    if (!detail) return;
    this.state.selected = detail;

    const columns = detail.numeric_columns?.length ? detail.numeric_columns : ["value"];
    this.state.colorColumn = columns.includes(this.state.colorColumn)
      ? this.state.colorColumn : columns[0];

    this.renderDatasetList();
    this.buildColorColumns(columns);
    await this.loadMap();
    if (detail.bounds) MapView.fit(detail.bounds);
    this.renderTab();
  },

  buildColorColumns(columns) {
    const select = document.getElementById("color-column");
    select.innerHTML = "";
    for (const column of columns) {
      const option = document.createElement("option");
      option.value = column;
      option.textContent = this.state.catalog.columns[column] || column;
      select.appendChild(option);
    }
    select.value = this.state.colorColumn;
  },

  async loadMap() {
    if (!this.state.selectedId) return;
    const column = this.state.colorColumn;
    const payload = await this.api(
      `/api/datasets/${this.state.selectedId}/map?column=${encodeURIComponent(column)}`
    ).catch(() => null);
    if (!payload) return;

    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const scale = MapView.setPoints(payload, conv);
    if (payload.polygons?.length) MapView.setPolygons(payload.polygons);
    else if (this.state.tab !== "ensaio") MapView.clearOverlays();

    const status = document.getElementById("map-status");
    status.textContent = payload.sampled
      ? `${Units.num(payload.count, 0)} of ${Units.num(payload.total, 0)} points (sampled)`
      : `${Units.num(payload.count, 0)} points`;

    const legend = document.getElementById("legend");
    if (scale) {
      legend.hidden = false;
      document.getElementById("legend-title").textContent =
        `${this.state.catalog.columns[column] || column}${unit ? ` (${unit})` : ""}`;
      document.getElementById("legend-ramp").style.background = MapView.rampCss();
      document.getElementById("legend-min").textContent = Units.num(scale.low);
      document.getElementById("legend-max").textContent = Units.num(scale.high);
    } else {
      legend.hidden = true;
    }
  },

  /* ------------------------------------------------------------ helpers */

  escape(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  },

  field(label, inputHtml, hint) {
    return `<label class="field"><span>${label}</span>${inputHtml}` +
      (hint ? `<span class="hint tight" style="margin-top:3px;display:block">${hint}</span>` : "") +
      `</label>`;
  },

  numberInput(id, value, step = "any", min = null) {
    return `<input type="number" id="${id}" value="${value ?? ""}" step="${step}"` +
      (min != null ? ` min="${min}"` : "") + `>`;
  },

  selectInput(id, options, selected) {
    const items = options.map(([value, label]) =>
      `<option value="${this.escape(value)}"${value === selected ? " selected" : ""}>${
        this.escape(label)}</option>`).join("");
    return `<select id="${id}">${items}</select>`;
  },

  /* Round to the nearest "round number" in the same order of magnitude:
   * 44.8 becomes 50; 4.48 becomes 5; 448 becomes 450. */
  roundStep(value) {
    if (!isFinite(value) || value <= 0) return 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
    const normalized = value / magnitude;
    const nice = normalized <= 1.5 ? 1 : normalized <= 3.5 ? 2.5 : normalized <= 7.5 ? 5 : 10;
    return Math.round(nice * magnitude * 100) / 100;
  },

  value(id) { return document.getElementById(id)?.value; },
  number(id, fallback = null) {
    const raw = document.getElementById(id)?.value;
    if (raw === "" || raw == null) return fallback;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
  },
  checked(id) { return document.getElementById(id)?.checked ?? false; },

  columnOptions(extra = []) {
    const columns = this.state.selected?.numeric_columns || [];
    const labels = this.state.catalog.columns;
    return [...extra, ...columns.map((c) => [c, labels[c] || c])];
  },

  /* ------------------------------------------------ right panel rendering */

  renderTab() {
    const panel = document.getElementById("right-panel");
    const renderers = {
      dados: () => this.tabDados(panel),
      limpeza: () => this.tabLimpeza(panel),
      difm: () => this.tabDifm(panel),
      ensaio: () => this.tabEnsaio(panel),
      exportar: () => this.tabExportar(panel),
    };
    (renderers[this.state.tab] || renderers.dados)();
  },
};

/* ======================================================================
 * Aba 1 — Dados
 * ==================================================================== */

Object.assign(App, {
  tabDados(panel) {
    const d = this.state.selected;
    if (!d) {
      panel.innerHTML = `<div class="panel"><h3>Data</h3>
        <div class="empty">Load a file on the left, or start from one of the demo
        datasets.</div></div>`;
      return;
    }

    const meta = d.meta;
    const stats = d.stats || {};
    const areaUnit = Units.label.area();
    const confidence = meta.extra?.brand_confidence;

    panel.innerHTML = `
      ${this.preflightPanel(d.reports_data?.preflight)}
      <div class="panel">
        <h3>Source</h3>
        <div class="stat-grid">
          <div class="stat"><div class="k">Monitor identified</div>
            <div class="v" style="font-size:13px">${this.escape(meta.brand_label)}</div>
            <div class="d">${confidence != null
              ? `${Math.round(confidence * 100)}% confidence` : "declared"}</div></div>
          <div class="stat"><div class="k">Operation</div>
            <div class="v" style="font-size:13px">${this.escape(meta.operation_label)}</div>
            <div class="d">${this.escape(meta.source_format)}</div></div>
          <div class="stat"><div class="k">Records</div>
            <div class="v">${Units.num(d.rows, 0)}</div>
            <div class="d">${d.metric_crs || ""}</div></div>
          <div class="stat"><div class="k">Area worked</div>
            <div class="v">${Units.num(Units.convert.area(d.area_ha))}</div>
            <div class="d">${areaUnit}</div></div>
        </div>
        <div class="row tight" style="margin-top:10px">
          <button class="small" id="btn-declare-units">File units</button>
          ${meta.brand === "augmenta"
            ? '<button class="small" id="btn-augmenta">Vigour vs rate</button>' : ""}
        </div>
      </div>

      <div class="panel">
        <h3>Main variable · ${this.escape(
          this.state.catalog.columns[this.state.colorColumn] || this.state.colorColumn)}</h3>
        ${this.statsBlock(stats, this.state.colorColumn)}
      </div>

      ${meta.extra?.jd_card ? this.cardPanel(meta.extra.jd_card, meta.extra.jd_card_layers) : ""}

      ${meta.notes?.length ? `<div class="panel"><h3>Notes from reading the file</h3>
        ${meta.notes.map((n) => `<div class="note">${this.escape(n)}</div>`).join("")}</div>` : ""}

      ${Object.keys(meta.extra?.column_mapping || {}).length ? `
      <div class="panel"><h3>Columns recognized</h3>
        <details class="fold"><summary>${
          Object.keys(meta.extra.column_mapping).length} columns mapped</summary>
          <div class="inner scroll-x"><table class="data">
            <tr><th>In the file</th><th>Read as</th></tr>
            ${Object.entries(meta.extra.column_mapping).map(([raw, canonical]) =>
              `<tr><td>${this.escape(raw)}</td><td>${this.escape(
                this.state.catalog.columns[canonical] || canonical)}</td></tr>`).join("")}
          </table></div>
        </details>
      </div>` : ""}

      <div class="panel"><h3>First rows</h3>
        <div class="scroll-x" id="preview"></div>
      </div>

      <div class="panel">
        <button class="wide" id="btn-remove-dataset">Remove from session</button>
      </div>`;

    this.renderPreview(document.getElementById("preview"), d.preview);

    document.getElementById("btn-apply-units")?.addEventListener("click", () => {
      const proposed = d.reports_data?.preflight?.proposed_units;
      if (proposed) this.applyProposedUnits(proposed);
    });
    document.getElementById("btn-preflight-next")?.addEventListener("click", () => {
      const step = d.reports_data?.preflight?.next_step?.step;
      if (step === "units") this.openImportUnitsDialog();
      else this.goToStep(step || "clean");
    });
    document.getElementById("btn-declare-units")
      ?.addEventListener("click", () => this.openImportUnitsDialog());
    document.getElementById("btn-augmenta")
      ?.addEventListener("click", () => this.runAugmenta());
    document.getElementById("btn-remove-dataset").addEventListener("click", async () => {
      await this.api(`/api/datasets/${this.state.selectedId}`, { method: "DELETE" });
      this.state.selectedId = null;
      this.state.selected = null;
      MapView.clearPoints();
      MapView.clearOverlays();
      document.getElementById("legend").hidden = true;
      await this.refreshDatasets();
      this.renderTab();
    });
  },

  /* The preliminary pass, shown the moment a file opens rather than on
   * request: the user should not have to ask whether what they just loaded is
   * usable. Alerts come first because they block everything downstream. */
  preflightPanel(report) {
    if (!report) return "";
    const proposed = report.proposed_units;
    const noteClass = { ok: "ok", warning: "warning", alert: "alert" }[report.verdict] || "";
    const icon = { ok: "✓", warning: "!", alert: "✕" };
    const order = { alert: 0, warning: 1, ok: 2 };
    const findings = [...report.findings].sort(
      (a, b) => order[a.level] - order[b.level]);

    return `
      <div class="panel">
        <h3>First look at this file</h3>
        <div class="note ${noteClass}">${this.escape(report.summary)}</div>
        <div style="margin-top:8px">
          ${findings.map((f) => `
            <div class="req ${f.level === "ok" ? "done" : "todo"}" style="align-items:flex-start">
              <span class="mark" style="color:${
                f.level === "ok" ? "var(--ok)"
                : f.level === "warning" ? "var(--warn)" : "var(--danger)"
              }">${icon[f.level]}</span>
              <span class="txt">
                <b>${this.escape(f.title)}</b>
                <br><span class="d">${this.escape(f.detail)}</span>
                ${f.action ? `<br><span class="d" style="color:var(--text)">${
                  this.escape(f.action)}</span>` : ""}
              </span>
            </div>`).join("")}
        </div>
        <div class="row tight" style="margin-top:10px">
          ${proposed ? `<button class="primary" id="btn-apply-units">
            Apply ${this.escape(Object.entries(proposed)
              .filter(([k]) => k !== "crop").map(([, v]) => v).join(" · "))}
          </button>` : ""}
          <button class="${report.verdict === "alert" && !proposed ? "primary" : ""}"
                  id="btn-preflight-next">
            ${this.escape(report.next_step.label)}
          </button>
        </div>
        <p class="hint tight">${this.escape(report.next_step.why)}</p>
      </div>`;
  },

  /* John Deere card inventory: what can be read and what is proprietary.
   * Knowing a file exists but is closed is different from thinking the card
   * came up empty. */
  cardPanel(card, layers) {
    const roleLabels = {
      boundary: "Boundary", guidance: "AB lines",
      prescription: "Prescription", data: "Operation data",
    };
    return `
      <div class="panel">
        <h3>John Deere card</h3>
        <div class="note">${this.escape(card.summary)}</div>
        ${layers?.length ? `<h4>Layers the app can open</h4>
          <div class="scroll-x"><table class="data">
            <tr><th>File</th><th>Role</th></tr>
            ${layers.map((l) => `<tr>
              <td style="font-family:var(--mono);font-size:11px">${this.escape(l.relative)}</td>
              <td>${this.escape(roleLabels[l.role] || l.role)}</td></tr>`).join("")}
          </table></div>` : ""}
        ${card.proprietary?.length ? `<details class="fold" style="margin-top:8px">
          <summary>${card.proprietary.length} file(s) in a proprietary format</summary>
          <div class="inner">
            ${card.proprietary.map((f) => `<p class="hint tight">
              <span style="font-family:var(--mono)">${this.escape(f.relative)}</span> — ${
                this.escape(f.kind)}</p>`).join("")}
            <p class="hint tight" style="margin-top:6px">These files only open in John
            Deere's own software or on the display itself. To bring them into AgroSuite,
            export again from SMS choosing shapefile instead of GreenStar.</p>
          </div></details>` : ""}
      </div>`;
  },

  statsBlock(stats, column) {
    if (!stats || !stats.n) return '<div class="empty">No statistics.</div>';
    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const cell = (label, value, decimals) =>
      `<div class="stat"><div class="k">${label}</div>
        <div class="v">${Units.num(conv(value), decimals)}</div>
        <div class="d">${unit}</div></div>`;
    return `<div class="stat-grid">
      ${cell("Mean", stats.mean)}
      ${cell("Median", stats.median)}
      ${cell("Std deviation", stats.std)}
      <div class="stat"><div class="k">Coeff. of variation</div>
        <div class="v">${Units.num(stats.cv, 1)}</div><div class="d">%</div></div>
      ${cell("Minimum", stats.min)}
      ${cell("Maximum", stats.max)}
      ${cell("5th percentile", stats.p05)}
      ${cell("95th percentile", stats.p95)}
    </div>`;
  },

  renderPreview(node, preview) {
    if (!node || !preview?.rows?.length) {
      if (node) node.innerHTML = '<div class="empty">No rows.</div>';
      return;
    }
    const columns = preview.columns.slice(0, 10);
    const head = columns.map((c) =>
      `<th>${this.escape(this.state.catalog.columns[c] || c)}</th>`).join("");
    const body = preview.rows.slice(0, 12).map((row) =>
      `<tr>${columns.map((c) => {
        const value = row[c];
        const text = typeof value === "number" ? Units.num(value) : (value ?? "—");
        return `<td class="${typeof value === "number" ? "num" : ""}">${this.escape(text)}</td>`;
      }).join("")}</tr>`).join("");
    node.innerHTML = `<table class="data"><tr>${head}</tr>${body}</table>` +
      (preview.columns.length > 10
        ? `<p class="hint">Showing 10 of ${preview.columns.length} columns.</p>` : "");
  },

  async runAugmenta() {
    const report = await this.busy(document.getElementById("right-panel"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/augmenta`, { method: "POST" }));
    if (!report) return;
    if (!report.available) { this.toast("Augmenta", report.reason, "warn"); return; }

    const rows = report.classes.map((c) =>
      `<tr><td>${this.escape(c["class"])}</td><td class="num">${Units.num(c.n, 0)}</td>
       <td class="num">${Units.num(c.mean_rate)}</td>
       <td class="num">${Units.num(c.sd)}</td></tr>`).join("");
    const panel = document.getElementById("right-panel");
    const block = document.createElement("div");
    block.className = "panel";
    block.innerHTML = `<h3>Augmenta · vigour vs rate</h3>
      <div class="note ${report.relative_spread_pct < 5 ? "warning" : "ok"}">${
        this.escape(report.reading)}</div>
      <div class="stat-grid" style="margin:8px 0">
        <div class="stat"><div class="k">Correlation</div>
          <div class="v">${Units.num(report.correlation, 2)}</div>
          <div class="d">vigour vs rate</div></div>
        <div class="stat"><div class="k">Rate spread</div>
          <div class="v">${Units.num(report.relative_spread_pct, 1)}</div><div class="d">%</div></div>
      </div>
      <div class="scroll-x"><table class="data">
        <tr><th>Vigour class</th><th>n</th><th>Mean rate</th><th>SD</th></tr>${rows}
      </table></div>`;
    panel.insertBefore(block, panel.children[1]);
  },
});

/* ======================================================================
 * Aba 2 — Limpeza
 * ==================================================================== */

Object.assign(App, {
  tabLimpeza(panel) {
    const d = this.state.selected;
    if (!d) {
      panel.innerHTML = `<div class="panel"><h3>Cleaning</h3>
        <div class="empty">Pick a dataset first.</div></div>`;
      return;
    }

    const presets = this.state.catalog.presets;
    const suggested = presets[d.meta.operation] ? d.meta.operation : "minimal";
    const current = this.state.cleanPreset || suggested;
    const config = presets[current];

    panel.innerHTML = `
      <div class="panel">
        <h3>Data cleaning</h3>
        ${this.field("Profile", this.selectInput("clean-preset",
          Object.entries(presets).map(([k, v]) => [k, v.label]), current))}
        <div class="note">${this.escape(config.description)}</div>
        ${this.field("Variable to clean", this.selectInput("clean-column",
          this.columnOptions(), this.state.colorColumn))}
        ${this.field("Sensor flow delay (s)",
          this.numberInput("clean-delay", config.corrections.flow_delay_s ?? 0, "0.5", "0"),
          "Seconds between the cut and the sensor reading. Zero turns the correction off.")}
      </div>

      <div class="panel">
        <h3>Filters</h3>
        <div id="clean-steps"></div>
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-clean">Run cleaning</button>
        <p class="hint tight">The original data is kept: cleaning creates new
        datasets, "clean" and "removed".</p>
      </div>

      <div id="clean-report"></div>`;

    this.renderCleanSteps(config.steps);

    document.getElementById("clean-preset").addEventListener("change", (event) => {
      this.state.cleanPreset = event.target.value;
      this.renderTab();
    });
    document.getElementById("btn-run-clean").addEventListener("click", () => this.runClean());

    const existing = this.state.reports[`${this.state.selectedId}:clean`];
    if (existing) this.renderCleanReport(existing);
  },

  renderCleanSteps(stepConfig) {
    const box = document.getElementById("clean-steps");
    box.innerHTML = "";
    const lengthUnit = Units.label.length();
    const speedUnit = Units.label.speed();
    const { unit: rateUnit, kind: rateKind } =
      Units.forColumn("value", this.state.selected?.meta?.operation);

    /* Parameters that are physical quantities show up in the user's chosen unit
     * and go back to metric on the way out. */
    const PARAM_META = {
      min_fraction: { label: "Minimum fraction of full width", step: "0.05", suffix: "0-1" },
      max_overlap_pct: { label: "Overlap tolerated", step: "5", suffix: "%" },
      buffer_m: { label: "Field edge", step: "1", suffix: lengthUnit, kind: "length" },
      start_m: { label: "Drop at pass start", step: "1", suffix: lengthUnit, kind: "length" },
      end_m: { label: "Drop at pass end", step: "1", suffix: lengthUnit, kind: "length" },
      min_points: { label: "Minimum records", step: "1", suffix: "rec." },
      max_change_pct: { label: "Maximum change", step: "5", suffix: "%" },
      max_jump_m: { label: "Maximum jump", step: "1", suffix: lengthUnit, kind: "length" },
      k: { label: "Standard deviations (k)", step: "0.25", suffix: "sd" },
      lower_pct: { label: "Lower percentile", step: "0.5", suffix: "%" },
      upper_pct: { label: "Upper percentile", step: "0.5", suffix: "%" },
      k_neighbors: { label: "Neighbours", step: "1", suffix: "points" },
      threshold: { label: "Deviation limit", step: "0.5", suffix: "MAD" },
      method: { label: "Method", choices: [["std", "Standard deviation"], ["percentile", "Percentile"]] },
      drop_zero: { label: "Drop zeros", bool: true },
      drop_negative: { label: "Drop negatives", bool: true },
      drop_duplicates: { label: "Drop duplicates", bool: true },
    };

    for (const step of this.state.catalog.steps) {
      const settings = stepConfig[step.key] || {};
      const enabled = settings.enabled ?? false;

      const unitFor = (key) => {
        if (key === "min" || key === "max") {
          if (step.key === "speed_range") return { suffix: speedUnit, kind: "speed" };
          if (step.key === "moisture") return { suffix: "%" };
          return { suffix: rateUnit, kind: rateKind };
        }
        return PARAM_META[key] || { label: key };
      };

      const params = Object.keys(step.defaults).map((key) => {
        const raw = settings[key] !== undefined ? settings[key] : step.defaults[key];
        const meta = { ...(PARAM_META[key] || {}), ...unitFor(key) };
        const label = meta.label || (key === "min" ? "Minimum" : key === "max" ? "Maximum" : key);
        const id = `p-${step.key}-${key}`;

        if (meta.bool) {
          return `<label class="inline" style="margin-bottom:7px">
            <input type="checkbox" id="${id}"${raw ? " checked" : ""}>
            <span style="font-size:12px">${label}</span></label>`;
        }
        if (meta.choices) {
          return this.field(label, this.selectInput(id, meta.choices, raw));
        }
        const shown = meta.kind && raw != null
          ? Number(Units.convert[meta.kind](raw).toFixed(3)) : raw;
        return this.field(
          `${label}${meta.suffix ? ` <span style="color:var(--text-faint)">(${meta.suffix})</span>` : ""}`,
          `<input type="number" id="${id}" value="${shown ?? ""}" step="${meta.step || "any"}"
             data-kind="${meta.kind || ""}">`);
      }).join("");

      const node = document.createElement("details");
      node.className = "fold";
      node.innerHTML = `
        <summary>
          <label class="inline" style="display:inline-flex" onclick="event.stopPropagation()">
            <input type="checkbox" id="en-${step.key}"${enabled ? " checked" : ""}>
            <span>${this.escape(step.label)}</span>
          </label>
        </summary>
        <div class="inner">
          <p class="hint tight">${this.escape(step.description)}</p>
          ${params || '<p class="hint tight">No parameters.</p>'}
        </div>`;
      box.appendChild(node);
    }
  },

  collectCleanConfig() {
    const steps = {};
    for (const step of this.state.catalog.steps) {
      const entry = { enabled: this.checked(`en-${step.key}`) };
      for (const key of Object.keys(step.defaults)) {
        const node = document.getElementById(`p-${step.key}-${key}`);
        if (!node) continue;
        if (node.type === "checkbox") { entry[key] = node.checked; continue; }
        if (node.tagName === "SELECT") { entry[key] = node.value; continue; }
        if (node.value === "") { entry[key] = null; continue; }
        let value = Number(node.value);
        const kind = node.dataset.kind;
        if (kind && Units.toInternal[kind]) value = Units.toInternal[kind](value);
        entry[key] = value;
      }
      steps[step.key] = entry;
    }
    return {
      value_column: this.value("clean-column"),
      corrections: { flow_delay_s: this.number("clean-delay", 0) },
      steps,
    };
  },

  async runClean() {
    const body = this.collectCleanConfig();
    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/clean`, { method: "POST", body }));
    if (!result) return;

    this.state.reports[`${this.state.selectedId}:clean`] = result;
    await this.refreshDatasets();
    this.renderCleanReport(result);
    this.toast("Cleaning done",
      `${Units.num(result.report.totals.removed, 0)} records removed ` +
      `(${Units.num(result.report.totals.removed_pct, 1)}%).`);
  },

  renderCleanReport(result) {
    const box = document.getElementById("clean-report");
    if (!box) return;
    const report = result.report;
    const totals = report.totals;
    const column = report.value_column;
    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const before = report.statistics.before;
    const after = report.statistics.after;

    const stepRows = report.steps.filter((s) => !s.skipped).map((s) =>
      `<tr${s.removed ? ` class="clickable" data-reason="${this.escape(s.label)}"` : ""}>
       <td title="${this.escape(s.detail)}">${this.escape(s.label)}</td>
       <td class="num">${Units.num(s.removed, 0)}</td>
       <td class="num">${Units.num(s.removed / totals.input * 100, 1)}%</td>
       <td class="num">${Units.num(s.remaining, 0)}</td></tr>`).join("");

    const skipped = report.steps.filter((s) => s.skipped);

    const compare = (label, b, a, decimals, convert) => {
      const cb = convert ? convert(b) : b;
      const ca = convert ? convert(a) : a;
      let deltaText = "—";
      if (cb) {
        const delta = ((ca - cb) / Math.abs(cb)) * 100;
        const arrow = Math.abs(delta) < 0.05 ? "" : (delta > 0 ? "▲" : "▼");
        deltaText = `${arrow} ${Units.num(delta, 1)}%`;
      }
      return `<tr><td>${label}</td>
        <td class="num">${Units.num(cb, decimals)}</td>
        <td class="num">${Units.num(ca, decimals)}</td>
        <td class="num" style="color:var(--text-muted)">${deltaText}</td></tr>`;
    };

    box.innerHTML = `
      <div class="panel">
        <h3>Cleaning report</h3>
        ${report.findings.map((f) =>
          `<div class="note ${f.level}">${this.escape(f.text)}</div>`).join("")}
        <div class="stat-grid" style="margin-top:10px">
          <div class="stat"><div class="k">Came in</div>
            <div class="v">${Units.num(totals.input, 0)}</div><div class="d">records</div></div>
          <div class="stat"><div class="k">Kept</div>
            <div class="v">${Units.num(totals.kept, 0)}</div><div class="d">records</div></div>
          <div class="stat"><div class="k">Removed</div>
            <div class="v">${Units.num(totals.removed_pct, 1)}</div><div class="d">%</div></div>
          <div class="stat"><div class="k">Area worked</div>
            <div class="v">${Units.num(Units.convert.area(totals.area_ha_after))}</div>
            <div class="d">${Units.label.area()}</div></div>
        </div>
        ${report.corrections.length ? report.corrections.map((c) =>
          `<div class="note" style="margin-top:8px">${this.escape(c)}</div>`).join("") : ""}
      </div>

      <div class="panel">
        <h3>Before and after</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Statistic</th><th>Before</th><th>After</th><th>Δ</th></tr>
          ${compare(`Mean (${unit})`, before.mean, after.mean, null, conv)}
          ${compare(`Median (${unit})`, before.median, after.median, null, conv)}
          ${compare(`SD (${unit})`, before.std, after.std, null, conv)}
          ${compare("Coeff. of variation (%)", before.cv, after.cv, 1)}
          ${compare(`Minimum (${unit})`, before.min, after.min, null, conv)}
          ${compare(`Maximum (${unit})`, before.max, after.max, null, conv)}
        </table></div>
        <h4>Distribution</h4>
        <div id="clean-hist"></div>
        <p class="hint tight">Grey: before. Green: after.</p>
      </div>

      <div class="panel">
        <h3>What each filter removed</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Filter</th><th>Removed</th><th>% of total</th><th>Left</th></tr>
          ${stepRows || '<tr><td colspan="4">No filter was enabled.</td></tr>'}
        </table></div>
        ${skipped.length ? `<details class="fold" style="margin-top:8px">
          <summary>${skipped.length} filter(s) could not run</summary>
          <div class="inner">${skipped.map((s) =>
            `<p class="hint tight"><b>${this.escape(s.label)}</b>: ${this.escape(s.detail)}</p>`
          ).join("")}</div></details>` : ""}
        <p class="hint tight">Click a filter to see on the map exactly which records
        it removed. Ten percent removed means nothing until you can see it was the
        headland and the overlap, and not a strip through the middle of the field.</p>
        <h4>Removals by reason</h4>
        <div id="clean-bars"></div>
        <div class="row tight" style="margin-top:8px">
          <button class="small" id="btn-show-all-removed">Show everything removed</button>
          <button class="small" id="btn-hide-removed">Clear the overlay</button>
        </div>
        <div id="removed-legend"></div>
      </div>

      <div class="panel">
        <h3>Datasets produced</h3>
        <div class="row tight">
          <button id="btn-goto-clean">View clean data</button>
          ${result.removed ? '<button id="btn-goto-removed">View removed</button>' : ""}
        </div>
      </div>`;

    Charts.histogram(
      document.getElementById("clean-hist"),
      this.scaleHistogram(report.histogram.before, conv),
      this.scaleHistogram(report.histogram.after, conv),
    );
    Charts.bars(document.getElementById("clean-bars"),
      report.by_reason.map((r) => ({ label: r.reason, value: r.records })));

    if (result.removed) {
      for (const row of box.querySelectorAll("tr.clickable")) {
        row.style.cursor = "pointer";
        row.addEventListener("click",
          () => this.showRemoved(result.removed.id, row.dataset.reason));
      }
      document.getElementById("btn-show-all-removed")?.addEventListener("click",
        () => this.showRemoved(result.removed.id, null));
      document.getElementById("btn-hide-removed")?.addEventListener("click", () => {
        MapView.clearOverlay();
        document.getElementById("removed-legend").innerHTML = "";
      });
    }

    document.getElementById("btn-goto-clean")?.addEventListener("click",
      () => this.selectDataset(result.clean.id));
    document.getElementById("btn-goto-removed")?.addEventListener("click",
      () => this.selectDataset(result.removed.id));
  },

  /* Draw the removed records over the clean ones, coloured by the filter that
   * caught them. Seeing that the removals sit on the headland and the overlap
   * is the difference between trusting a cleaning and hoping. */
  async showRemoved(removedDatasetId, onlyReason) {
    const payload = await this.api(
      `/api/datasets/${removedDatasetId}/map?group_column=removal_reason`
    ).catch(() => null);
    if (!payload?.lon?.length) {
      this.toast("Nothing to show", "No removed record carries a position.", "warn");
      return;
    }

    let filtered = payload;
    if (onlyReason) {
      const keep = [];
      for (let i = 0; i < payload.groups.length; i++) {
        if (payload.groups[i] === onlyReason) keep.push(i);
      }
      filtered = {
        lon: keep.map((i) => payload.lon[i]),
        lat: keep.map((i) => payload.lat[i]),
        groups: keep.map(() => onlyReason),
      };
    }

    // One colour per filter, taken from the same ramp the value map uses so the
    // two never look like different applications.
    const reasons = [...new Set(filtered.groups.filter(Boolean))];
    const palette = {};
    reasons.forEach((reason, index) => {
      const c = MapView.rampColor(reasons.length === 1 ? 0.82
        : index / Math.max(1, reasons.length - 1));
      palette[reason] = `rgb(${c.join(",")})`;
    });

    MapView.setOverlay(filtered, palette);
    document.getElementById("removed-legend").innerHTML = `
      <p class="hint tight" style="margin-top:8px">
        ${Units.num(filtered.lon.length, 0)} removed record(s) on the map${
          onlyReason ? ` — ${this.escape(onlyReason)}` : ""}.</p>
      ${reasons.map((reason) => `
        <div class="inline" style="margin-bottom:3px">
          <span style="width:11px;height:11px;border-radius:50%;flex:none;
                background:${palette[reason]};border:1px solid rgba(255,255,255,.7)"></span>
          <span style="font-size:11px">${this.escape(reason)}</span>
        </div>`).join("")}`;
  },

  /* The histogram arrives in internal units; the bin edges have to follow the
   * chosen unit, or the axis would read kg/ha on a chart labelled bu/ac. */
  scaleHistogram(hist, convert) {
    if (!hist?.edges?.length) return hist;
    return { counts: hist.counts, edges: hist.edges.map((e) => convert(e)) };
  },
});

/* ======================================================================
 * Aba 3 — Análise DIFM
 * ==================================================================== */

Object.assign(App, {
  tabDifm(panel) {
    const d = this.state.selected;
    if (!d) {
      panel.innerHTML = `<div class="panel"><h3>DIFM analysis</h3>
        <div class="empty">Pick a dataset carrying an applied rate and a yield.</div></div>`;
      return;
    }

    const columns = d.numeric_columns || [];
    const guessRate = ["applied_rate", "target_rate", "rate"].find((c) => columns.includes(c))
      || columns[0];
    const guessValue = columns.includes("value") ? "value" : columns[0];
    const zoneOptions = [["", "— no zones —"],
      ...(d.columns || []).filter((c) => !["lon", "lat", "x", "y"].includes(c))
        .map((c) => [c, this.state.catalog.columns[c] || c])];

    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();
    const yieldNum = Units.rateNumerator(yieldUnit);
    const inputNum = Units.rateNumerator(inputUnit);
    const symbol = Units.currencySymbol();
    const lengthUnit = Units.label.length();

    // Prices are set once on the project and reused here, so the same numbers
    // drive the panel on the left and the analysis on the right.
    const prices = this.state.project?.prices || {};
    const projectCrop = prices.crop_price
      ? Number(Units.priceFromInternal(prices.crop_price, yieldUnit).toFixed(2)) : 0;
    const projectInput = prices.input_cost
      ? Number(Units.priceFromInternal(prices.input_cost, inputUnit).toFixed(2)) : 0;

    panel.innerHTML = `
      <div class="panel">
        <h3>Trial</h3>
        ${this.field("Applied rate column",
          this.selectInput("difm-rate", this.columnOptions(), guessRate))}
        ${this.field("Yield column",
          this.selectInput("difm-value", this.columnOptions(), guessValue))}
        ${this.field("Zone column (optional)",
          this.selectInput("difm-zone", zoneOptions, ""),
          "With zones, the app compares a single rate against variable rate.")}
      </div>

      <div class="panel">
        <h3>Prices</h3>
        ${this.field(`Crop price (${symbol}/${yieldNum})`,
          this.numberInput("difm-price", projectCrop, "0.01", "0"))}
        ${this.field(`Input cost (${symbol}/${inputNum})`,
          this.numberInput("difm-cost", projectInput, "0.01", "0"),
          "The optimum rate is where the next unit of input stops paying for itself.")}
      </div>

      <div class="panel">
        <h3>Aggregation</h3>
        <div class="row tight">
          ${this.field(`Cell (${lengthUnit})`,
            this.numberInput("difm-cell", Math.round(Units.convert.length(20)), "1", "1"))}
          ${this.field(`Edge margin (${lengthUnit})`,
            this.numberInput("difm-edge", Math.round(Units.convert.length(6)), "1", "0"))}
        </div>
        <p class="hint tight">The margin drops the transition between strips, where the
        rates mix. It has to exceed half the pass spacing to have any effect.</p>
        ${this.field("Models to try", `<select id="difm-models" multiple size="4">${
          Object.entries(this.state.catalog.response_models).map(([k, v]) =>
            `<option value="${k}" selected>${this.escape(v)}</option>`).join("")}</select>`,
          "The one with the highest R-squared is chosen automatically.")}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-difm">Analyse the response</button>
      </div>

      <div id="difm-report"></div>`;

    document.getElementById("btn-run-difm").addEventListener("click", () => this.runDifm());
    const existing = this.state.reports[`${this.state.selectedId}:difm`];
    if (existing) this.renderDifmReport(existing);
  },

  async runDifm() {
    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();

    /* Prices come in per selling unit — dollars per bushel, per pound — and have
     * to become price per internal unit before going to the server. */
    const body = {
      rate_column: this.value("difm-rate"),
      value_column: this.value("difm-value"),
      zone_column: this.value("difm-zone") || null,
      crop_price: Units.priceToInternal(this.number("difm-price", 0), yieldUnit),
      input_cost: Units.priceToInternal(this.number("difm-cost", 0), inputUnit),
      cell_m: Units.toInternal.length(this.number("difm-cell", 20)),
      edge_margin_m: Units.toInternal.length(this.number("difm-edge", 6)),
      models: Array.from(document.getElementById("difm-models").selectedOptions)
        .map((o) => o.value),
    };
    if (!body.crop_price) {
      this.toast("Enter a price", "Without a crop price there is no economic optimum.", "warn");
      return;
    }

    const report = await this.busy(document.getElementById("right-panel"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/difm`, { method: "POST", body }));
    if (!report) return;
    this.state.reports[`${this.state.selectedId}:difm`] = report;
    this.renderDifmReport(report);
  },

  renderDifmReport(report) {
    const box = document.getElementById("difm-report");
    if (!box) return;

    const model = report.chosen_model;
    const economy = report.economics || {};
    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();
    const symbol = Units.currencySymbol();
    const areaUnit = Units.label.area();

    /* The rate arrives in the internal mass-per-area unit; on screen it shows in
     * the input rate unit — lb/ac under the Canadian default. */
    const rate = (v) => Units.num(Units.convert.inputRate(v));
    const yld = (v) => Units.num(Units.convert.yield(v));
    /* Internal profit is per hectare; convert it to the chosen area unit. */
    const perArea = (v) => Units.money(v * Units.factor("area", Units.get().area_unit));

    const rateRows = (report.by_rate || []).map((r) =>
      `<tr><td class="num">${rate(r.rate)}</td>
       <td class="num">${Units.num(r.n, 0)}</td>
       <td class="num">${yld(r.mean_yield)}</td>
       <td class="num">${r.mean_profit != null ? perArea(r.mean_profit) : "—"}</td></tr>`).join("");

    const zones = report.zones;
    const zoneRows = (zones?.by_zone || []).map((z) => z.error
      ? `<tr><td>${this.escape(z.zone)}</td><td colspan="4">${this.escape(z.error)}</td></tr>`
      : `<tr><td>${this.escape(z.zone)}</td>
         <td class="num">${Units.num(z.cells, 0)}</td>
         <td class="num">${Units.num(z.r2, 3)}</td>
         <td class="num">${rate(z.optimum_rate)}</td>
         <td class="num">${perArea(z.profit_at_optimum)}</td></tr>`).join("");

    box.innerHTML = `
      <div class="panel">
        <h3>Recommendation</h3>
        <div class="stat-grid">
          <div class="stat"><div class="k">Economic optimum rate</div>
            <div class="v">${rate(economy.optimum_rate)}</div>
            <div class="d">${inputUnit}</div></div>
          <div class="stat"><div class="k">Expected yield</div>
            <div class="v">${yld(economy.yield_at_optimum)}</div>
            <div class="d">${yieldUnit}</div></div>
          <div class="stat"><div class="k">Margin at the optimum</div>
            <div class="v" style="font-size:13px">${perArea(economy.profit_at_optimum)}</div>
            <div class="d">per ${areaUnit}</div></div>
          <div class="stat"><div class="k">Agronomic maximum</div>
            <div class="v">${economy.agronomic_maximum != null
              ? rate(economy.agronomic_maximum) : "—"}</div>
            <div class="d">${inputUnit}</div></div>
        </div>
        ${economy.at_range_limit ? `<div class="note alert" style="margin-top:8px">
          The optimum landed at the edge of the tested range — the trial never showed
          the point of diminishing returns. Read it as "at least this much".</div>` : ""}
        ${(report.notes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${report.parameters ? `<p class="hint tight" style="margin-top:8px">
          ${Units.num(Units.convert.length(report.parameters.cell_m), 0)}
          ${Units.label.length()} cells · ${Units.num(
            Units.convert.length(report.parameters.edge_margin_m), 0)}
          ${Units.label.length()} edge margin.</p>` : ""}
      </div>

      <div class="panel">
        <h3>Response curve</h3>
        <div id="difm-curve"></div>
        <div class="stat-grid" style="margin-top:10px">
          <div class="stat"><div class="k">Model</div>
            <div class="v" style="font-size:13px">${this.escape(model.label)}</div>
            <div class="d">${model.n} cells</div></div>
          <div class="stat"><div class="k">R²</div>
            <div class="v">${Units.num(model.r2, 3)}</div>
            <div class="d">RMSE ${yld(model.rmse)} ${yieldUnit}</div></div>
        </div>
        ${model.message ? `<div class="note warning" style="margin-top:8px">${
          this.escape(model.message)}</div>` : ""}
        <details class="fold" style="margin-top:8px">
          <summary>All models tried</summary>
          <div class="inner scroll-x"><table class="data">
            <tr><th>Model</th><th>R²</th><th>RMSE</th></tr>
            ${(report.models_evaluated || []).map((m) =>
              `<tr><td>${this.escape(m.label)}</td>
               <td class="num">${Units.num(m.r2, 3)}</td>
               <td class="num">${yld(m.rmse)}</td></tr>`).join("")}
          </table></div>
        </details>
      </div>

      <div class="panel">
        <h3>Response by rate</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Rate (${inputUnit})</th><th>Cells</th>
              <th>Yield (${yieldUnit})</th><th>Margin/${areaUnit}</th></tr>
          ${rateRows}
        </table></div>
      </div>

      ${zones ? `<div class="panel">
        <h3>By zone · ${this.escape(zones.zone_column)}</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Zone</th><th>Cells</th><th>R²</th>
              <th>Optimum (${inputUnit})</th><th>Margin/${areaUnit}</th></tr>
          ${zoneRows}
        </table></div>
        ${zones.comparison?.reading ? `
          <div class="note ${zones.comparison.gain_per_ha > 0 ? "ok" : "warning"}"
               style="margin-top:10px">${this.escape(zones.comparison.reading)}</div>
          <div class="stat-grid" style="margin-top:8px">
            <div class="stat"><div class="k">Best single rate</div>
              <div class="v">${rate(zones.comparison.best_uniform_rate)}</div>
              <div class="d">${inputUnit}</div></div>
            <div class="stat"><div class="k">Variable rate gain</div>
              <div class="v" style="font-size:13px">${perArea(zones.comparison.gain_per_ha)}</div>
              <div class="d">per ${areaUnit}</div></div>
          </div>` : ""}
      </div>` : ""}

      <div class="panel">
        <button class="wide" id="btn-difm-to-rx">Build a prescription from this analysis</button>
      </div>`;

    Charts.responseCurve(document.getElementById("difm-curve"), report.curve, economy, {
      yieldConv: (v) => Units.convert.yield(v),
      rateConv: (v) => Units.convert.inputRate(v),
      yieldUnit,
      rateUnit: inputUnit,
    });

    document.getElementById("btn-difm-to-rx").addEventListener("click", () =>
      this.prescriptionFromDifm(report));
  },

  /* Turn the analysis result into a prescription: the optimum per zone when
   * zones exist, or a single rate when they do not. */
  prescriptionFromDifm(report) {
    const zones = report.zones?.by_zone?.filter((z) => !z.error && z.optimum_rate != null);
    if (!zones?.length) {
      const rate = report.economics?.optimum_rate;
      if (rate == null) { this.toast("No rate", "The analysis produced no optimum rate.", "warn"); return; }
      this.state.pendingRx = { uniform: rate };
      this.toast("Rate carried to export",
        `Single rate of ${Units.num(Units.convert.inputRate(rate))} ${Units.label.inputRate()}. ` +
        "Go to the Export tab.");
    } else {
      this.state.pendingRx = {
        byZone: Object.fromEntries(zones.map((z) => [z.zone, z.optimum_rate])),
        zoneColumn: report.zones.zone_column,
      };
      this.toast("Zone rates carried to export",
        zones.map((z) => `${z.zone}: ${Units.num(Units.convert.inputRate(z.optimum_rate))}`).join(" · "));
    }
    this.state.tab = "exportar";
    for (const b of document.querySelectorAll("#steps button")) {
      b.setAttribute("aria-selected", String(b.dataset.tab === "exportar"));
    }
    this.renderTab();
  },
});

/* ======================================================================
 * Aba 4 — Desenhar ensaio
 * ==================================================================== */

Object.assign(App, {
  tabEnsaio(panel) {
    const lengthUnit = Units.label.length();
    const inputUnit = Units.label.inputRate();
    const areaUnit = Units.label.area();
    const datasets = this.state.datasets.map((d) => [d.id, d.label]);
    /* Largura usual do implemento na unidade escolhida: 60 pés é a barra e o
     * semeador padrão das Pradarias; 12 metros, o equivalente métrico comum. */
    const defaultWidth = Units.label.length() === "ft" ? 60
      : Math.round(Units.convert.length(12));

    /* Suggested rates around a typical one, in the user's unit — a useful trial
     * has to reach well below and well above current practice. The step is
     * rounded to a round number in the chosen unit: nobody programs a monitor
     * at 44.8 lb/ac. */
    const typical = Units.convert.inputRate(100);
    const step = this.roundStep(typical / 2);
    const suggested = [0, 1, 2, 3, 4].map((i) => i * step).join(", ");

    panel.innerHTML = `
      <div class="panel">
        <h3>Field boundary</h3>
        ${this.field("From a loaded dataset", this.selectInput("design-source",
          [["", "— choose —"], ...datasets], this.state.selectedId || ""),
          "Use a boundary shapefile, or a yield map (the app falls back to the hull of the points).")}
        <div class="row tight">
          <button class="small" id="btn-draw-boundary">Draw on the map</button>
          <button class="small" id="btn-clear-boundary">Clear drawing</button>
        </div>
        <p class="hint tight" id="draw-status"></p>
      </div>

      <div class="panel">
        <h3>Treatments</h3>
        ${this.field(`Rates to test (${inputUnit}, comma separated)`,
          `<input type="text" id="design-rates" value="${suggested}">`,
          "At least three, and it is worth including an unapplied strip as a check.")}
        <div class="row tight">
          ${this.field(`Implement width (${lengthUnit})`,
            this.numberInput("design-width", defaultWidth, "1", "1"))}
          ${this.field("Passes per strip",
            this.numberInput("design-passes", 2, "1", "1"))}
        </div>
        <div class="row tight">
          ${this.field("Blocks (replicates)", this.numberInput("design-blocks", 4, "1", "1"))}
          ${this.field(`Headland setback (${lengthUnit})`,
            this.numberInput("design-buffer", Math.round(Units.convert.length(15)), "1", "0"))}
        </div>
        ${this.field("Strip direction (degrees, empty = longest side)",
          `<input type="number" id="design-angle" step="1" placeholder="automatic">`)}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-design">Generate layout</button>
        <p class="hint tight">Two passes per strip leave the centre pass free of the
        neighbouring rates — that is what lets the analysis drop the edges without
        losing the whole treatment.</p>
      </div>

      <div id="design-report"></div>`;

    document.getElementById("btn-run-design").addEventListener("click", () => this.runDesign());
    document.getElementById("btn-draw-boundary").addEventListener("click", () => this.startDrawing());
    document.getElementById("btn-clear-boundary").addEventListener("click", () => {
      this.state.drawing = null;
      MapView.clearOverlays();
      document.getElementById("draw-status").textContent = "";
    });

    if (this.state.design) this.renderDesignReport(this.state.design);
  },

  startDrawing() {
    this.state.drawing = [];
    const status = document.getElementById("draw-status");
    status.textContent = "Click the field's corners, then press Generate layout.";

    const handler = (event) => {
      if (!this.state.drawing) { MapView.offClick(handler); return; }
      this.state.drawing.push([event.latlng.lng, event.latlng.lat]);
      MapView.setPolygons([this.state.drawing.concat([this.state.drawing[0]])], { fill: true });
      status.textContent =
        `${this.state.drawing.length} corner(s). At least 3 are needed to close the field.`;
    };
    MapView.onClick(handler);
  },

  async runDesign() {
    const inputUnit = Units.label.inputRate();
    const rates = (this.value("design-rates") || "")
      .split(/[,;]/).map((s) => Number(s.trim().replace(",", ".")))
      .filter((n) => Number.isFinite(n))
      .map((n) => Units.toInternal.inputRate(n));

    if (rates.length < 3) {
      this.toast("Not enough rates",
        `Enter at least three rates in ${inputUnit}.`, "warn");
      return;
    }

    const body = {
      rates,
      implement_width_m: Units.toInternal.length(this.number("design-width", 12)),
      passes_per_strip: this.number("design-passes", 2),
      blocks: this.number("design-blocks", 4),
      buffer_m: Units.toInternal.length(this.number("design-buffer", 0)),
      angle_deg: this.number("design-angle", null),
    };
    if (this.state.drawing?.length >= 3) body.boundary = this.state.drawing;
    else if (this.value("design-source")) body.boundary_dataset_id = this.value("design-source");
    else { this.toast("No boundary", "Pick a dataset or draw the field.", "warn"); return; }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/design", { method: "POST", body }));
    if (!result) return;

    this.state.design = result;
    MapView.setFeatures(result.features, "rate");
    MapView.fitOverlays();
    this.renderDesignReport(result);
    this.toast("Trial laid out",
      `${result.summary.strips} strips in ${result.summary.blocks} block(s).`);
  },

  renderDesignReport(result) {
    const box = document.getElementById("design-report");
    if (!box) return;
    const s = result.summary;
    const inputUnit = Units.label.inputRate();
    const lengthUnit = Units.label.length();
    const areaUnit = Units.label.area();

    const reps = Object.entries(s.reps_per_rate).map(([rateValue, count]) =>
      `<tr><td class="num">${Units.num(Units.convert.inputRate(Number(rateValue)))}</td>
       <td class="num">${count}</td></tr>`).join("");

    box.innerHTML = `
      <div class="panel">
        <h3>Layout generated</h3>
        ${(result.warnings || []).map((w) =>
          `<div class="note warning">${this.escape(w)}</div>`).join("")}
        <div class="stat-grid" style="margin-top:8px">
          <div class="stat"><div class="k">Strips</div>
            <div class="v">${s.strips}</div><div class="d">${s.blocks} block(s)</div></div>
          <div class="stat"><div class="k">Strip width</div>
            <div class="v">${Units.num(Units.convert.length(s.strip_width_m), 0)}</div>
            <div class="d">${lengthUnit} · ${s.passes_per_strip} pass(es)</div></div>
          <div class="stat"><div class="k">Trial area</div>
            <div class="v">${Units.num(Units.convert.area(s.total_area_ha))}</div>
            <div class="d">${areaUnit}</div></div>
          <div class="stat"><div class="k">Direction</div>
            <div class="v">${Units.num(s.direction_deg, 0)}°</div>
            <div class="d">azimuth</div></div>
        </div>
        <h4>Replicates per rate</h4>
        <table class="data">
          <tr><th>Rate (${inputUnit})</th><th>Strips</th></tr>${reps}
        </table>
      </div>
      <div class="panel">
        <h3>Guidance line</h3>
        <p class="hint tight">The AB line has to follow the same direction as the
        strips; if the operator enters at another angle, the passes cut across the
        treatments and the trial is lost.</p>
        <button class="wide" id="btn-make-ab" style="margin-bottom:6px">
          Build an AB line along the trial direction
        </button>
        <div id="ab-status"></div>
      </div>
      <div class="panel">
        <button class="primary wide" id="btn-design-export">Export this trial</button>
      </div>`;

    document.getElementById("btn-make-ab").addEventListener("click", () => this.makeGuidance());
    if (this.state.guidance?.length) this.renderGuidanceStatus();

    document.getElementById("btn-design-export").addEventListener("click", () => {
      this.state.tab = "exportar";
      for (const b of document.querySelectorAll("#steps button")) {
        b.setAttribute("aria-selected", String(b.dataset.tab === "exportar"));
      }
      this.renderTab();
    });
  },
});

Object.assign(App, {
  async makeGuidance() {
    if (!this.state.design) {
      this.toast("No trial yet", "Generate the layout before building the AB line.", "warn");
      return;
    }
    const body = {
      angle_deg: this.state.design.summary.direction_deg,
      name: "Trial AB line",
    };
    if (this.state.drawing?.length >= 3) body.boundary = this.state.drawing;
    else body.boundary_dataset_id = this.value("design-source") || this.state.selectedId;

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/guidance", { method: "POST", body }));
    if (!result) return;

    this.state.guidance = [result.line];
    this.renderGuidanceStatus();
    this.drawDesignWithGuidance();
    this.toast("AB line created", `Compass bearing ${Units.num(result.line.heading, 1)}°.`);
  },

  renderGuidanceStatus() {
    const box = document.getElementById("ab-status");
    if (!box) return;
    box.innerHTML = (this.state.guidance || []).map((line) => `
      <div class="note ok">
        <b>${this.escape(line.name)}</b> — bearing ${Units.num(line.heading, 1)}°<br>
        <span style="font:11px var(--mono)">
          A ${line.a[1].toFixed(6)}, ${line.a[0].toFixed(6)}<br>
          B ${line.b[1].toFixed(6)}, ${line.b[0].toFixed(6)}
        </span>
      </div>`).join("");
  },

  /* Draw the strips and the AB line on top — the visual check that the two are
   * pointing the same way. */
  drawDesignWithGuidance() {
    if (!this.state.design) return;
    MapView.setFeatures(this.state.design.features, "rate");
    for (const line of this.state.guidance || []) {
      MapView.addLine([line.a, line.b], line.name);
    }
  },
});

/* ======================================================================
 * Aba 5 — Exportar
 * ==================================================================== */

Object.assign(App, {
  tabExportar(panel) {
    const hasDesign = !!this.state.design;
    const hasDataset = !!this.state.selectedId;
    const mode = this.state.exportMode || "package";
    const inputUnit = Units.label.inputRate();

    panel.innerHTML = `
      <div class="panel">
        <h3>How to export</h3>
        ${this.field("Mode", this.selectInput("exp-mode", [
          ["package", "Ready-to-load package for the monitor"],
          ["files", "Individual files"],
        ], mode), mode === "package"
          ? "Builds the USB folder in the layout the monitor looks for, with boundary, AB lines and prescription."
          : "Generates only the chosen files, with no folder structure.")}
        ${!hasDesign && !hasDataset ? '<div class="empty">Nothing to export yet.</div>' : ""}
      </div>
      <div id="export-form"></div>
      <div id="export-report"></div>`;

    document.getElementById("exp-mode").addEventListener("change", (event) => {
      this.state.exportMode = event.target.value;
      this.renderTab();
    });

    if (mode === "package") this.renderPackageForm();
    else this.renderFilesForm();
  },

  /* ---------------------------------------------------- package mode --- */

  renderPackageForm() {
    const box = document.getElementById("export-form");
    const monitors = this.state.catalog.monitors;
    const chosen = this.state.exportMonitor || "john_deere";
    const lengthUnit = Units.label.length();
    const hasDesign = !!this.state.design;
    const hasGuidance = !!this.state.guidance?.length;
    const datasets = this.state.datasets.map((d) => [d.id, d.label]);

    box.innerHTML = `
      <div class="panel">
        <h3>Target monitor</h3>
        ${this.field("Platform", this.selectInput("pkg-monitor",
          monitors.map((m) => [m.key, m.label]), chosen))}
        <div id="pkg-accepts"></div>
      </div>

      <div class="panel">
        <h3>What goes in the package</h3>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-rx"${hasDesign ? " checked" : ""}
            ${hasDesign ? "" : "disabled"}>
          <span>Prescription${hasDesign
            ? ` — ${this.state.design.summary.strips} trial strips`
            : " (lay the trial out first)"}</span></label>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-boundary" checked>
          <span>Field boundary</span></label>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-guidance"${hasGuidance ? " checked" : ""}
            ${hasGuidance ? "" : "disabled"}>
          <span>AB lines${hasGuidance
            ? ` — ${this.state.guidance.length} line(s)`
            : " (build them on the Trial tab)"}</span></label>
        ${this.field("Boundary from", this.selectInput("pkg-boundary-src",
          [["", "— current drawing / trial —"], ...datasets],
          this.state.selectedId || ""),
          "A boundary imported from the monitor is truer than the hull of the points.")}
      </div>

      <div class="panel">
        <h3>Rate</h3>
        ${this.field("Kind", this.selectInput("pkg-kind",
          Object.entries(this.state.catalog.rate_kinds), "mass"))}
        ${this.field("Unit written to the file", this.selectInput("pkg-unit",
          this.state.units.groups.rate_mass.units.map((u) => [u.key, u.label]),
          Units.label.inputRate()))}
        ${this.field("Crop (for bushel units)", this.selectInput("pkg-crop",
          this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}
        ${this.field(`ISOXML grid cell (${lengthUnit})`,
          this.numberInput("pkg-cell", Math.round(Units.convert.length(10)), "1", "1"),
          "A smaller cell makes a bigger file; 10 m suits nearly every terminal.")}
      </div>

      <div class="panel">
        <h3>Naming</h3>
        <div class="row tight">
          ${this.field("Field", `<input type="text" id="pkg-field" value="Field">`)}
          ${this.field("Task", `<input type="text" id="pkg-task" value="Prescription">`)}
        </div>
        <div class="row tight">
          ${this.field("Client", `<input type="text" id="pkg-customer" value="AgroSuite">`)}
          ${this.field("Farm", `<input type="text" id="pkg-farm" value="Farm">`)}
        </div>
        ${this.field("Product", `<input type="text" id="pkg-product" value="Product">`)}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-package">Build the package</button>
      </div>

      ${this.qgisPanel()}`;

    const showAccepts = () => {
      const monitor = monitors.find((m) => m.key === this.value("pkg-monitor"));
      const node = document.getElementById("pkg-accepts");
      if (!monitor) { node.innerHTML = ""; return; }
      const labels = this.state.catalog.artifact_labels;
      node.innerHTML = `
        <p class="hint tight">Accepts: ${monitor.accepts.map((a) => labels[a] || a).join(" · ")}</p>
        <p class="hint tight">Rate field in the shapefile: <b>${monitor.rate_field}</b></p>
        <details class="fold" style="margin-top:6px">
          <summary>How to load it on the ${this.escape(monitor.label)}</summary>
          <div class="inner">${monitor.instructions.map((i) =>
            `<p class="hint tight">• ${this.escape(i)}</p>`).join("")}</div>
        </details>`;
      /* Disable what the platform cannot take, rather than letting the user tick
       * it and only find out afterwards that nothing was generated. */
      for (const [id, artifact] of [["pkg-rx", "prescription"],
                                    ["pkg-boundary", "boundary"],
                                    ["pkg-guidance", "guidance"]]) {
        const input = document.getElementById(id);
        const supported = monitor.accepts.includes(artifact);
        if (!supported) { input.checked = false; input.disabled = true; }
        input.parentElement.style.opacity = supported ? "1" : "0.45";
      }
    };

    document.getElementById("pkg-monitor").addEventListener("change", (event) => {
      this.state.exportMonitor = event.target.value;
      showAccepts();
    });
    document.getElementById("pkg-kind").addEventListener("change", () => {
      const kind = this.value("pkg-kind");
      const group = kind === "volume" ? "rate_volume" : kind === "count" ? "rate_count" : "rate_mass";
      const select = document.getElementById("pkg-unit");
      select.innerHTML = this.state.units.groups[group].units.map((u) =>
        `<option value="${u.key}">${this.escape(u.label)}</option>`).join("");
    });
    document.getElementById("btn-run-package").addEventListener("click", () => this.runPackage());
    this.bindQgisPanel();
    showAccepts();
  },

  /* QGIS is where the layers get looked at properly — against imagery, against
   * soil maps, with zones drawn by hand. The GeoPackage is the reliable half of
   * the bridge; the project file just saves adding each layer by hand. */
  qgisPanel() {
    return `
      <div class="panel">
        <h3>QGIS</h3>
        <div class="row tight">
          <button id="btn-qgis-export">Write layers for QGIS</button>
          <button id="btn-qgis-import">Open a QGIS project</button>
        </div>
        <p class="hint tight">Export writes one GeoPackage with every loaded layer,
        plus a .qgs project that opens them all at once. Import reads a .qgs or .qgz
        and brings its layers in — which is how zones drawn in QGIS reach the monitor.</p>
        <div id="qgis-result"></div>
      </div>`;
  },

  bindQgisPanel() {
    document.getElementById("btn-qgis-export")?.addEventListener("click", async () => {
      const result = await this.busy(document.getElementById("qgis-result"), () =>
        this.api("/api/qgis/export", {
          method: "POST",
          body: { name: this.state.project?.name || "agrosuite" },
        }));
      if (!result) return;
      document.getElementById("qgis-result").innerHTML = `
        <div class="note ok" style="margin-top:8px">
          <b>${result.geopackage.layers.length} layer(s) written.</b><br>
          <span style="font:11px var(--mono)">${this.escape(result.folder)}</span>
        </div>
        <div class="scroll-x" style="margin-top:8px"><table class="data">
          <tr><th>Layer</th><th>Features</th><th>Type</th></tr>
          ${result.geopackage.layers.map((l) => `<tr>
            <td>${this.escape(l.layer)}</td>
            <td class="num">${Units.num(l.features, 0)}</td>
            <td>${this.escape(l.geometry)}</td></tr>`).join("")}
        </table></div>
        <p class="hint tight">${this.escape(result.note)}</p>
        <a class="btn wide" style="display:block;text-decoration:none;margin-top:8px"
           href="${result.download_url}" download>Download the GeoPackage</a>`;
      this.toast("Written for QGIS", `${result.geopackage.layers.length} layer(s).`);
    });

    document.getElementById("btn-qgis-import")?.addEventListener("click", () => {
      this.state.qgisImport = true;
      document.getElementById("dlg-path").showModal();
      document.getElementById("path-input").value = "";
      this.browse("");
      this.toast("Pick a project", "Choose a .qgs or .qgz file, then press Open.");
    });
  },

  async importQgisProject(path) {
    const project = await this.api(
      `/api/qgis/project?path=${encodeURIComponent(path)}`
    ).catch((err) => { this.toast("That did not work", err.message, "error"); return null; });
    if (!project) return;

    const importable = project.layers.filter((l) => l.importable);
    if (!importable.length) {
      this.toast("Nothing to import",
        "None of the project's layers are file-based layers on this machine.", "warn");
      return;
    }
    const result = await this.busy(document.querySelector("aside.left"), () =>
      this.api("/api/qgis/import", { method: "POST", body: { path } }));
    if (!result) return;

    await this.refreshDatasets();
    await this.selectDataset(result.imported[0].id);
    this.toast(
      `Imported from ${result.project}`,
      result.imported.map((d) => d.label).join(", ") +
      (result.skipped.length ? `\nSkipped: ${result.skipped.map((s) =>
        `${s.name} (${s.reason})`).join(", ")}` : ""),
    );
  },

  async runPackage() {
    const body = {
      monitor: this.value("pkg-monitor"),
      rate_property: "rate",
      rate_kind: this.value("pkg-kind"),
      rate_unit: this.value("pkg-unit"),
      crop: this.value("pkg-crop"),
      cell_m: Units.toInternal.length(this.number("pkg-cell", 10)),
      field_name: this.value("pkg-field") || "Talhao",
      task_name: this.value("pkg-task") || "Prescricao",
      product_name: this.value("pkg-product") || "Produto",
      customer_name: this.value("pkg-customer") || "AgroSuite",
      farm_name: this.value("pkg-farm") || "Fazenda",
    };

    if (this.checked("pkg-rx") && this.state.design) {
      body.features = this.state.design.features;
    }
    if (this.checked("pkg-guidance") && this.state.guidance?.length) {
      body.guidance_lines = this.state.guidance;
    }
    if (this.checked("pkg-boundary")) {
      const source = this.value("pkg-boundary-src");
      if (this.state.drawing?.length >= 3) body.boundary = this.state.drawing;
      else if (source) body.boundary_dataset_id = source;
      else if (this.state.design) body.features = body.features || this.state.design.features;
    }
    if (!body.features && !body.boundary && !body.boundary_dataset_id &&
        !body.guidance_lines?.length) {
      this.toast("Empty package", "Tick at least one item to include.", "warn");
      return;
    }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/export/package", { method: "POST", body }));
    if (!result) return;
    this.renderPackageReport(result);
    this.bindUsbPanel(result.folder);
    this.toast("Package built", `${result.contents.length} item(s) for the ${result.monitor_label}.`);
  },

  renderPackageReport(result) {
    const box = document.getElementById("export-report");
    const labels = this.state.catalog.artifact_labels;

    box.innerHTML = `
      <div class="panel">
        <h3>Package for the ${this.escape(result.monitor_label)}</h3>
        <a class="btn primary wide" style="display:block;text-decoration:none"
           href="${result.bundle.download_url}" download>
          Download ${this.escape(result.bundle.filename)}
        </a>
        <p class="hint tight">Written to:<br>
          <code style="font-size:11px">${this.escape(result.folder)}</code></p>
        <div class="scroll-x" style="margin-top:10px"><table class="data">
          <tr><th>File</th><th>Contents</th></tr>
          ${result.contents.map((c) => `<tr>
            <td style="font-family:var(--mono);font-size:11px">${this.escape(c.path)}</td>
            <td style="text-align:left">${this.escape(
              c.artifact === "isoxml" ? "ISOXML" : (labels[c.artifact] || c.artifact))}
              — ${this.escape(c.detail)}</td></tr>`).join("")}
        </table></div>
        ${(result.notes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${(result.skipped || []).map((s) =>
          `<div class="note warning" style="margin-top:6px">${this.escape(s)}</div>`).join("")}
      </div>
      ${this.verificationPanel(result.verification)}
      ${this.usbPanel(result)}
      <div class="panel">
        <h3>How to load it</h3>
        ${result.instructions.map((step, i) =>
          `<p class="hint tight">${i + 1}. ${this.escape(step)}</p>`).join("")}
        <p class="hint tight" style="margin-top:8px">The <b>README.txt</b> inside the
        package repeats these instructions, to read in the cab.</p>
      </div>`;
  },

  /* Copying to the stick is the last step of every job, and the step where
   * the mistakes happen — the folder ends up one level deep, or last season's
   * prescription is still sitting there. */
  usbPanel(result) {
    return `
      <div class="panel">
        <h3>Write to a USB drive</h3>
        <div class="row tight">
          <select id="usb-drive"><option value="">— refresh to list drives —</option></select>
          <button class="small" id="btn-usb-refresh" style="flex:none">Refresh</button>
        </div>
        <div id="usb-detail"></div>
        <button class="wide" id="btn-usb-write" style="margin-top:8px" disabled>
          Copy the package to the drive
        </button>
        <p class="hint tight">The package goes to the root of the drive, which is where
        the terminal looks. Anything already there is left alone unless you say
        otherwise.</p>
      </div>`;
  },

  bindUsbPanel(folder) {
    const select = document.getElementById("usb-drive");
    const detail = document.getElementById("usb-detail");
    const writeButton = document.getElementById("btn-usb-write");
    if (!select) return;

    const refresh = async () => {
      const payload = await this.api("/api/usb").catch(() => null);
      const drives = payload?.drives || [];
      select.innerHTML = drives.length
        ? drives.map((d) => `<option value="${this.escape(d.path)}">${
            this.escape(d.label)} — ${this.escape(d.path)}${
            d.removable ? "" : " (fixed disk)"}</option>`).join("")
        : '<option value="">no drive found</option>';
      select.dispatchEvent(new Event("change"));
    };

    const preview = async () => {
      const drive = select.value;
      if (!drive) { detail.innerHTML = ""; writeButton.disabled = true; return; }
      const plan = await this.api("/api/usb/plan", {
        method: "POST", body: { folder, drive },
      }).catch(() => null);
      if (!plan) { detail.innerHTML = ""; writeButton.disabled = true; return; }

      this.state.usbPlan = plan;
      writeButton.disabled = !plan.fits;
      detail.innerHTML = `
        <p class="hint tight">${plan.total_mb} MB to copy${
          plan.free_mb != null ? `, ${Units.num(plan.free_mb, 0)} MB free` : ""}.</p>
        ${plan.conflicts.length ? `
          <div class="note warning">
            Already on this drive: ${plan.conflicts.map((c) =>
              `<b>${this.escape(c.name)}</b>`).join(", ")}.
            <label class="inline" style="margin-top:6px">
              <input type="checkbox" id="usb-replace">
              <span>Replace ${plan.conflicts.length === 1 ? "it" : "them"}</span>
            </label>
          </div>` : ""}
        ${!plan.fits ? '<div class="note alert">Not enough room on the drive.</div>' : ""}`;
    };

    document.getElementById("btn-usb-refresh").addEventListener("click", refresh);
    select.addEventListener("change", preview);
    writeButton.addEventListener("click", async () => {
      const plan = this.state.usbPlan;
      const replaceAll = this.checked("usb-replace");
      if (plan?.conflicts.length && !replaceAll) {
        this.toast("Already on the drive",
          "Tick the replace box, or the existing folders stay as they are.", "warn");
      }
      const result = await this.busy(document.getElementById("export-report"), () =>
        this.api("/api/usb/write", {
          method: "POST",
          body: {
            folder, drive: select.value,
            replace: replaceAll ? plan.conflicts.map((c) => c.name) : [],
          },
        }));
      if (!result) return;
      detail.innerHTML += `<div class="note ok" style="margin-top:8px">${
        this.escape(result.message)}${result.skipped.length
          ? `<br>Left alone: ${result.skipped.map((s) =>
              this.escape(s.name)).join(", ")}.` : ""}</div>`;
      this.toast("Copied to the drive", result.message);
      this.refreshProject();
    });

    refresh();
  },

  /* Verification runs over the files already written. Red is a real blocker;
   * amber depends on the display's firmware. */
  verificationPanel(check) {
    if (!check) return "";
    const noteClass = { ok: "ok", warning: "warning", fail: "alert" }[check.verdict] || "";
    const icon = { ok: "✓", warning: "!", fail: "✕" };

    const groups = (check.groups || []).map((group) => {
      const failed = group.checks.filter((c) => c.status !== "ok");
      const rows = group.checks.map((c) => `
        <div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="flex:none;width:14px;text-align:center;font-weight:700;color:${
            c.status === "ok" ? "var(--ok)"
            : c.status === "warning" ? "var(--warn)" : "var(--danger)"
          }">${icon[c.status]}</span>
          <span style="min-width:0">
            <b style="font-size:12px">${this.escape(c.item)}</b>
            <span style="font-size:12px;color:var(--text-muted)"> — ${this.escape(c.message)}</span>
            ${c.fix ? `<br><span class="hint tight">${this.escape(c.fix)}</span>` : ""}
          </span>
        </div>`).join("");
      return `<details class="fold"${failed.length ? " open" : ""}>
        <summary>${this.escape(group.file)} · ${this.escape(group.kind)}${
          failed.length ? ` — ${failed.length} point(s) to look at` : " — all checked"}</summary>
        <div class="inner">${rows}</div>
      </details>`;
    }).join("");

    return `
      <div class="panel">
        <h3>Check before you take it out</h3>
        <div class="note ${noteClass}">${this.escape(check.summary)}</div>
        <div class="stat-grid" style="margin:10px 0">
          <div class="stat"><div class="k">Checked</div>
            <div class="v" style="color:var(--ok)">${check.totals.ok}</div>
            <div class="d">no reservations</div></div>
          <div class="stat"><div class="k">To confirm</div>
            <div class="v" style="color:var(--warn)">${check.totals.warning}</div>
            <div class="d">on the monitor screen</div></div>
          <div class="stat"><div class="k">Blockers</div>
            <div class="v" style="color:var(--danger)">${check.totals.fail}</div>
            <div class="d">the monitor would refuse</div></div>
          <div class="stat"><div class="k">Target</div>
            <div class="v" style="font-size:12px">${this.escape(check.monitor_label)}</div>
            <div class="d">profile applied</div></div>
        </div>
        ${groups}
        <p class="hint tight" style="margin-top:10px">${this.escape(check.caveat)}</p>
      </div>`;
  },

  /* -------------------------------------------- individual files mode -- */

  renderFilesForm() {
    const box = document.getElementById("export-form");
    const hasDesign = !!this.state.design;
    const hasDataset = !!this.state.selectedId;
    const brands = this.state.catalog.brands.filter((b) => b.export_formats.length);
    const source = this.state.exportSource || (hasDesign ? "design" : "dataset");
    const lengthUnit = Units.label.length();

    box.innerHTML = `
      <div class="panel">
        <h3>Source</h3>
        ${this.field("What to export", this.selectInput("exp-source", [
          ...(hasDesign ? [["design", "Trial layout (prescription)"]] : []),
          ...(hasDataset ? [["dataset", `Data: ${this.state.selected?.label ?? ""}`]] : []),
        ], source))}
      </div>
      <div class="panel">
        <h3>Formats</h3>
        <label class="inline" style="margin-bottom:6px">
          <input type="checkbox" id="fmt-shapefile" checked><span>Shapefile (.shp)</span></label>
        <label class="inline" style="margin-bottom:6px">
          <input type="checkbox" id="fmt-isoxml"><span>ISOXML / TASKDATA</span></label>
        <label class="inline" style="margin-bottom:6px">
          <input type="checkbox" id="fmt-csv"><span>CSV</span></label>
        <label class="inline" style="margin-bottom:6px">
          <input type="checkbox" id="fmt-geojson"><span>GeoJSON</span></label>
      </div>
      <div class="panel">
        <h3>Settings</h3>
        ${this.field("Name the rate field for", this.selectInput("exp-brand",
          brands.map((b) => [b.key, b.label]), "john_deere"))}
        ${this.field("Rate kind", this.selectInput("exp-kind",
          Object.entries(this.state.catalog.rate_kinds), "mass"))}
        ${this.field("Unit written to the file", this.selectInput("exp-unit",
          this.state.units.groups.rate_mass.units.map((u) => [u.key, u.label]),
          Units.label.inputRate()))}
        ${this.field("Crop", this.selectInput("exp-crop",
          this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}
        <div class="row tight">
          ${this.field(`ISOXML cell (${lengthUnit})`,
            this.numberInput("exp-cell", Math.round(Units.convert.length(10)), "1", "1"))}
          ${this.field("Name", `<input type="text" id="exp-task" value="Prescription">`)}
        </div>
        <input type="hidden" id="exp-field" value="Field">
        <input type="hidden" id="exp-product" value="Product">
      </div>
      <div class="panel">
        <button class="primary wide" id="btn-run-export">Generate files</button>
      </div>

      ${this.qgisPanel()}`;

    document.getElementById("exp-source").addEventListener("change", (e) => {
      this.state.exportSource = e.target.value;
    });
    document.getElementById("exp-kind").addEventListener("change", () => {
      const kind = this.value("exp-kind");
      const group = kind === "volume" ? "rate_volume" : kind === "count" ? "rate_count" : "rate_mass";
      const select = document.getElementById("exp-unit");
      select.innerHTML = this.state.units.groups[group].units.map((u) =>
        `<option value="${u.key}">${this.escape(u.label)}</option>`).join("");
    });
    document.getElementById("btn-run-export").addEventListener("click", () => this.runExport());
    this.bindQgisPanel();
  },

  async runExport() {
    const formats = [
      ["shapefile", this.checked("fmt-shapefile")],
      ["isoxml", this.checked("fmt-isoxml")],
      ["csv", this.checked("fmt-csv")],
      ["geojson", this.checked("fmt-geojson")],
    ].filter(([, on]) => on).map(([f]) => f);

    if (!formats.length) { this.toast("Choose a format", "", "warn"); return; }

    const body = {
      formats,
      brand: this.value("exp-brand"),
      rate_property: "rate",
      rate_kind: this.value("exp-kind"),
      rate_unit: this.value("exp-unit"),
      crop: this.value("exp-crop"),
      cell_m: Units.toInternal.length(this.number("exp-cell", 10)),
      task_name: this.value("exp-task") || "Prescricao",
      field_name: this.value("exp-field") || "Talhao",
      product_name: this.value("exp-product") || "Produto",
    };

    if (this.value("exp-source") === "design") {
      if (!this.state.design) { this.toast("No trial", "Lay the trial out first.", "warn"); return; }
      body.features = this.state.design.features;
    } else {
      if (!this.state.selectedId) { this.toast("No data", "Pick a dataset.", "warn"); return; }
      body.dataset_id = this.state.selectedId;
      if (formats.includes("isoxml")) {
        this.toast("ISOXML needs polygons",
          "Use the package built from the trial layout to generate ISOXML.", "warn");
        return;
      }
    }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/export", { method: "POST", body }));
    if (!result) return;
    this.renderExportReport(result);
    this.toast("Files generated", `${result.bundle.entries.length} file(s) in the bundle.`);
  },

  renderExportReport(result) {
    const box = document.getElementById("export-report");
    if (!box) return;
    const rows = result.outputs.map((o) => {
      const detail = o.rate_field ? `rate field: ${o.rate_field}`
        : o.cols ? `${o.rows} x ${o.cols} grid, ${Units.num(o.cells_with_rate, 0)} cells with a rate`
        : o.features != null ? `${Units.num(o.features, 0)} features`
        : o.rows != null ? `${Units.num(o.rows, 0)} rows` : "";
      return `<tr><td>${this.escape(o.format)}</td><td>${this.escape(detail)}</td></tr>`;
    }).join("");
    const warnings = result.outputs.flatMap((o) => o.warnings || []);

    box.innerHTML = `
      <div class="panel">
        <h3>Files generated</h3>
        <a class="btn primary wide" style="display:block;text-decoration:none"
           href="${result.bundle.download_url}" download>
          Download ${this.escape(result.bundle.filename)}
        </a>
        <p class="hint tight">Written to:<br>
          <code style="font-size:11px">${this.escape(result.folder)}</code></p>
        <div class="scroll-x" style="margin-top:10px"><table class="data">
          <tr><th>Format</th><th>Detail</th></tr>${rows}
        </table></div>
        ${(result.notes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${warnings.map((w) =>
          `<div class="note warning" style="margin-top:6px">${this.escape(w)}</div>`).join("")}
      </div>`;
  },
});

/* ======================================================================
 * Import and dialogs
 * ==================================================================== */

Object.assign(App, {
  bindImport() {
    const input = document.getElementById("file-input");
    document.getElementById("btn-open-file").addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const result = await this.busy(document.querySelector("aside.left"), () =>
        this.api("/api/import/upload", { method: "POST", body: form }));
      input.value = "";
      if (!result) return;
      await this.refreshDatasets();
      await this.selectDataset(result.id);
      this.toast("File loaded",
        `${result.meta.brand_label} · ${Units.num(result.rows, 0)} records.`);
    });

    document.getElementById("btn-open-path").addEventListener("click", () => {
      document.getElementById("dlg-path").showModal();
      this.browse("");
    });

    for (const [id, kind] of [["btn-demo-harvest", "harvest"], ["btn-demo-trial", "trial"]]) {
      document.getElementById(id).addEventListener("click", async () => {
        const result = await this.busy(document.querySelector("aside.left"), () =>
          this.api("/api/import/demo", { method: "POST", body: { kind } }));
        if (!result) return;
        await this.refreshDatasets();
        await this.selectDataset(result.id);
      });
    }

    /* Drag and drop anywhere on the window. */
    const dropZone = document.getElementById("app");
    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); });
    dropZone.addEventListener("drop", async (e) => {
      e.preventDefault();
      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const result = await this.busy(dropZone, () =>
        this.api("/api/import/upload", { method: "POST", body: form }));
      if (!result) return;
      await this.refreshDatasets();
      await this.selectDataset(result.id);
    });
  },

  bindDialogs() {
    for (const button of document.querySelectorAll("dialog [data-close]")) {
      button.addEventListener("click", () => button.closest("dialog").close());
    }
    document.getElementById("btn-path-list").addEventListener("click",
      () => this.browse(this.value("path-input")));
    document.getElementById("btn-path-up").addEventListener("click",
      () => this.browse(this.state.browseParent || ""));
    document.getElementById("btn-path-open").addEventListener("click", async () => {
      const path = this.value("path-input");
      if (!path) return;
      // A .qgs or .qgz is a project, not a data file: it names layers rather
      // than holding them.
      if (this.state.qgisImport || /\.(qgs|qgz)$/i.test(path)) {
        this.state.qgisImport = false;
        document.getElementById("dlg-path").close();
        await this.importQgisProject(path);
        return;
      }
      const result = await this.busy(document.getElementById("dlg-path"), () =>
        this.api("/api/import/path", { method: "POST", body: { path } }));
      if (!result) return;
      document.getElementById("dlg-path").close();
      await this.refreshDatasets();
      await this.selectDataset(result.id);
      this.toast("Loaded",
        `${result.meta.brand_label} · ${Units.num(result.rows, 0)} records.`);
    });
    document.getElementById("btn-import-units-apply")
      .addEventListener("click", () => this.applyImportUnits());
  },

  async browse(path) {
    const payload = await this.api(`/api/browse?path=${encodeURIComponent(path || "")}`)
      .catch((err) => { this.toast("That did not work", err.message, "error"); return null; });
    if (!payload) return;

    this.state.browseParent = payload.parent;
    document.getElementById("path-current").textContent = payload.path;
    const box = document.getElementById("path-browser");
    box.innerHTML = "";

    if (!payload.entries.length) {
      box.innerHTML = '<div class="empty">Nothing the app can open in this folder.</div>';
      return;
    }
    for (const entry of payload.entries) {
      const node = document.createElement("div");
      node.className = "item";
      node.innerHTML = `<span class="icon">${entry.is_dir ? "📁" : "📄"}</span>
        <span class="name">${this.escape(entry.name)}</span>
        <span class="size">${entry.size != null ? this.fileSize(entry.size) : ""}</span>`;
      node.addEventListener("click", () => {
        document.getElementById("path-input").value = entry.path;
        if (entry.is_dir) this.browse(entry.path);
      });
      node.addEventListener("dblclick", () => {
        document.getElementById("path-input").value = entry.path;
        document.getElementById("btn-path-open").click();
      });
      box.appendChild(node);
    }
  },

  fileSize(bytes) {
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${Units.num(value, i === 0 ? 0 : 1)} ${units[i]}`;
  },

  openUnitsDialog() {
    const prefs = Units.get();
    const groups = this.state.units.groups;
    const options = (group) => groups[group].units.map((u) => [u.key, u.label]);

    document.getElementById("units-body").innerHTML = `
      <p class="hint">Data is always stored in metric; these choices only change how
      the numbers appear and which unit files are written in.</p>
      ${this.field("Yield", this.selectInput("u-yield", options("rate_mass"), prefs.yield_unit))}
      ${this.field("Input rate", this.selectInput("u-input", options("rate_mass"), prefs.input_rate_unit))}
      ${this.field("Area", this.selectInput("u-area", options("area"), prefs.area_unit))}
      ${this.field("Length and width", this.selectInput("u-length", options("length"), prefs.length_unit))}
      ${this.field("Speed", this.selectInput("u-speed", options("speed"), prefs.speed_unit))}
      ${this.field("Mass", this.selectInput("u-mass", options("mass"), prefs.mass_unit))}
      ${this.field("Crop (bushel weight)", this.selectInput("u-crop",
        this.state.units.crops.map((c) => [c.key, `${c.label} — ${c.bushel_kg.toFixed(2)} kg/bu`]),
        prefs.crop))}
      ${this.field("Currency", this.selectInput("u-currency",
        this.state.units.currencies.map((c) => [c.key, c.label]), prefs.currency))}`;

    const apply = () => {
      Units.set("yield_unit", this.value("u-yield"));
      Units.set("input_rate_unit", this.value("u-input"));
      Units.set("area_unit", this.value("u-area"));
      Units.set("length_unit", this.value("u-length"));
      Units.set("speed_unit", this.value("u-speed"));
      Units.set("mass_unit", this.value("u-mass"));
      Units.set("crop", this.value("u-crop"));
      Units.set("currency", this.value("u-currency"));
      this.loadMap();
      this.renderTab();
    };
    for (const id of ["u-yield", "u-input", "u-area", "u-length", "u-speed", "u-mass",
                      "u-crop", "u-currency"]) {
      document.getElementById(id).addEventListener("change", apply);
    }
    document.getElementById("dlg-units").showModal();
  },

  openImportUnitsDialog() {
    const groups = this.state.units.groups;
    const options = (group) => [["", "— leave as it is —"],
      ...groups[group].units.map((u) => [u.key, u.label])];

    document.getElementById("import-units-body").innerHTML = `
      ${this.field("Yield / rate in the file",
        this.selectInput("iu-rate", options("rate_mass"), ""))}
      ${this.field("Speed in the file", this.selectInput("iu-speed", options("speed"), ""))}
      ${this.field("Width, distance and altitude in the file",
        this.selectInput("iu-length", options("length"), ""))}
      ${this.field("Crop (for bushels)", this.selectInput("iu-crop",
        this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}`;
    document.getElementById("dlg-import-units").showModal();
  },

  /* One click instead of a dialog with three pickers. The app already worked
   * the units out from the file; asking the user to re-enter them would be
   * friction for its own sake. */
  async applyProposedUnits(proposed) {
    const source_units = {};
    if (proposed.rate) {
      source_units.value = proposed.rate;
      source_units.target_rate = proposed.rate;
      source_units.applied_rate = proposed.rate;
    }
    if (proposed.speed) source_units.speed_kmh = proposed.speed;
    if (proposed.length) {
      /* A monitor writes every length in the one system it was set to: the
       * GPS altitude is in feet whenever the width is, and the terrain
       * analyser reads it as metres unless told otherwise. */
      source_units.swath_m = proposed.length;
      source_units.distance_m = proposed.length;
      source_units.elev_m = proposed.length;
    }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/units`, {
        method: "POST",
        body: { source_units, crop: proposed.crop || Units.get().crop },
      }));
    if (!result) return;

    await this.refreshDatasets();
    await this.selectDataset(result.dataset.id);
    this.toast("Units applied", result.conversions.join("\n"));
  },

  async applyImportUnits() {
    const source_units = {};
    const rate = this.value("iu-rate");
    if (rate) {
      source_units.value = rate;
      source_units.target_rate = rate;
      source_units.applied_rate = rate;
    }
    if (this.value("iu-speed")) source_units.speed_kmh = this.value("iu-speed");
    if (this.value("iu-length")) {
      source_units.swath_m = this.value("iu-length");
      source_units.distance_m = this.value("iu-length");
      source_units.elev_m = this.value("iu-length");
    }
    if (!Object.keys(source_units).length) {
      this.toast("Nothing to convert", "Choose at least one unit.", "warn");
      return;
    }

    const result = await this.busy(document.getElementById("dlg-import-units"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/units`, {
        method: "POST",
        body: { source_units, crop: this.value("iu-crop") },
      }));
    if (!result) return;
    document.getElementById("dlg-import-units").close();
    await this.refreshDatasets();
    await this.selectDataset(result.dataset.id);
    this.toast("Units converted", result.conversions.join("\n"));
  },
});

document.addEventListener("DOMContentLoaded", () => App.init());

/* ======================================================================
 * The guided workflow
 *
 * Five stages, always visible, with what is missing and the single next
 * thing to do. The stage is derived on the server from what has actually
 * happened, so it can never disagree with the state of the session.
 * ==================================================================== */

Object.assign(App, {
  async refreshProject() {
    const project = await this.api("/api/project").catch(() => null);
    if (!project) return;
    this.state.project = project;
    this.renderStageStrip(project);
    this.renderProjectPanel(project);
  },

  renderStageStrip(project) {
    const strip = document.getElementById("stage-strip");
    if (!strip) return;
    const stages = this.state.catalog.workflow.stages;
    const currentIndex = stages.findIndex((s) => s.key === project.stage);
    strip.hidden = false;

    strip.innerHTML = stages.map((stage, index) => {
      const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "";
      const mark = index < currentIndex ? "✓" : String(index + 1);
      return `<div class="stage ${state}" title="${this.escape(stage.description)}">
        <span class="dot">${mark}</span><span>${this.escape(stage.label)}</span>
      </div>`;
    }).join("") + `
      <div class="next">
        <span class="what">Next: ${this.escape(project.next_action.label)}</span>
        <button class="small" id="btn-next-action">Go</button>
      </div>`;

    document.getElementById("btn-next-action").addEventListener("click",
      () => this.goToStep(project.next_action.step));
    document.getElementById("btn-next-action").title = project.next_action.why;
  },

  /* Each workflow step maps to the tab that carries it out. */
  goToStep(step) {
    const tab = {
      load: "dados", review: "dados", units: "dados", columns: "dados",
      clean: "limpeza", analyse: "difm", augmenta: "dados", export: "exportar",
    }[step] || "dados";
    this.state.tab = tab;
    for (const b of document.querySelectorAll("#steps button")) {
      b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    }
    this.renderTab();
  },

  renderProjectPanel(project) {
    const box = document.getElementById("project-panel");
    if (!box) return;
    const goals = this.state.catalog.workflow.goals;
    const evaluation = project.evaluation;

    const layers = project.layers.length ? project.layers.map((layer) => `
      <div class="layer-row">
        <span class="name" title="${this.escape(layer.label)}">${this.escape(layer.label)}</span>
        <select data-role-for="${layer.dataset_id}">
          ${Object.entries(project.role_options).map(([key, label]) =>
            `<option value="${key}"${key === layer.role ? " selected" : ""}>${
              this.escape(label)}</option>`).join("")}
        </select>
      </div>`).join("")
      : '<div class="empty" style="padding:12px">No file has a role yet.</div>';

    const requirement = (r) => `
      <div class="req ${r.satisfied ? "done" : "todo"}">
        <span class="mark">${r.satisfied ? "✓" : "○"}</span>
        <span class="txt"><b>${this.escape(r.label)}</b>
          ${r.satisfied ? "" : `<br><span class="d">${this.escape(r.detail)}</span>`}</span>
      </div>`;

    box.innerHTML = `
      ${this.field("Goal", this.selectInput("project-goal",
        Object.entries(goals).map(([k, v]) => [k, v.label]), project.goal))}
      <h4 style="margin:10px 0 6px">Files and their roles</h4>
      ${layers}
      ${project.goal === "difm" ? this.priceFields(project) : ""}
      <h4 style="margin:12px 0 4px">What this needs</h4>
      ${evaluation.requirements.map(requirement).join("")}
      ${evaluation.optional.length ? `<details class="fold" style="margin-top:8px">
        <summary>Optional, but worth having</summary>
        <div class="inner">${evaluation.optional.map(requirement).join("")}</div>
      </details>` : ""}
      ${evaluation.ready && project.roles_present.length > 1 ? `
        <button class="wide" id="btn-join-layers" style="margin-top:10px">
          Join the layers for analysis
        </button>
        <p class="hint tight">One file per layer, three passes over the same ground.
        The join puts them on a shared grid so the response can be measured.</p>` : ""}`;

    for (const id of ["price-crop", "price-value", "price-cost"]) {
      document.getElementById(id)?.addEventListener("change", () => this.savePrices());
    }

    document.getElementById("project-goal").addEventListener("change", async (event) => {
      await this.api("/api/project", { method: "POST", body: { goal: event.target.value } });
      this.refreshProject();
      this.renderTab();
    });
    for (const select of box.querySelectorAll("[data-role-for]")) {
      select.addEventListener("change", async (event) => {
        await this.busy(box, () => this.api("/api/project/role", {
          method: "POST",
          body: { dataset_id: event.target.dataset.roleFor, role: event.target.value },
        }));
        await this.refreshDatasets();
        this.refreshProject();
      });
    }
    document.getElementById("btn-join-layers")?.addEventListener("click", () => this.joinLayers());
  },

  /* Prices belong to the project, not to one analysis: the same crop price
   * decides the optimum, the profit curve and the value of the whole trial. */
  priceFields(project) {
    const prices = project.prices || {};
    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();
    const symbol = Units.currencySymbol();
    const crop = prices.crop || Units.get().crop;

    // Stored per internal unit (per kg); shown per selling unit (per bushel,
    // per pound), which is how prices are actually quoted.
    const shownCrop = prices.crop_price
      ? Units.priceFromInternal(prices.crop_price, yieldUnit) : "";
    const shownInput = prices.input_cost
      ? Units.priceFromInternal(prices.input_cost, inputUnit) : "";

    return `
      <h4 style="margin:12px 0 6px">Prices</h4>
      ${this.field("Crop", this.selectInput("price-crop",
        this.state.units.crops.map((c) => [c.key, c.label]), crop))}
      <div class="row tight">
        ${this.field(`${symbol}/${Units.rateNumerator(yieldUnit)}`,
          `<input type="number" id="price-value" step="0.01" min="0"
             value="${shownCrop === "" ? "" : Number(shownCrop).toFixed(2)}"
             placeholder="crop">`)}
        ${this.field(`${symbol}/${Units.rateNumerator(inputUnit)}`,
          `<input type="number" id="price-cost" step="0.01" min="0"
             value="${shownInput === "" ? "" : Number(shownInput).toFixed(2)}"
             placeholder="input">`)}
      </div>`;
  },

  async savePrices() {
    const crop = this.value("price-crop");
    if (crop) Units.set("crop", crop);
    const body = {
      crop_price: Units.priceToInternal(this.number("price-value", 0), Units.label.yield()),
      input_cost: Units.priceToInternal(this.number("price-cost", 0), Units.label.inputRate()),
      currency: Units.get().currency,
      crop,
    };
    await this.api("/api/project/prices", { method: "POST", body }).catch(() => null);
    this.refreshProject();
  },

  async joinLayers() {
    /* The zone column is whatever the yield layer carries beyond the canonical
     * columns — it is what lets the analysis split the field instead of
     * pooling two different responses into one curve. */
    const carry = [];
    const yieldLayer = this.state.project?.layers.find((l) => l.role === "yield");
    if (yieldLayer) {
      const detail = await this.api(`/api/datasets/${yieldLayer.dataset_id}`).catch(() => null);
      for (const column of detail?.columns || []) {
        if (this.state.catalog.columns[column] || ["x", "y"].includes(column)) continue;
        carry.push(column);
      }
    }

    const result = await this.busy(document.querySelector("aside.left"), () =>
      this.api("/api/project/join", { method: "POST", body: { carry, min_purity: 0.8 } }));
    if (!result) return;

    await this.refreshDatasets();
    await this.selectDataset(result.dataset.id);
    this.refreshProject();
    this.state.joinReport = result.report;

    const lengthUnit = Units.label.length();
    this.toast(
      "Layers joined",
      `${result.report.cells} cells at ${Units.num(Units.convert.length(result.cell_m), 0)} ` +
      `${lengthUnit}. ${result.report.notes[0] || ""}`,
    );
    this.goToStep("analyse");
  },
});
