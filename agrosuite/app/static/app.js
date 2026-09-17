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
    projectFile: null,   // the .agrosuite file open or last saved, as the server reports it
    projectOpen: false,  // the path dialog is picking a project, not data
    saving: false,       // a project save is on its way
    recent: null,
    machinePicked: {},   // the machine picked on each tab, by name: a redraw must not forget it
    machinePlaced: {},   // what the app put in a physical field, with its exact metric number
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
    try { payload = await response.json(); } catch { /* no body */ }
    if (!response.ok) {
      // A 422 from the request model is a list of {loc, msg}; read out as
      // "passes_per_strip: Input should be a valid integer" it can be acted
      // on, where the list itself would show as "[object Object]".
      const detail = Array.isArray(payload?.detail)
        ? payload.detail.map((e) => `${(e.loc || []).filter((part) => part !== "body")
            .join(".")}: ${e.msg}`).join(" ")
        : payload?.detail;
      const error = new Error(detail || `${response.status} ${response.statusText}`);
      // A 409 is a question ("replace it?"), not a failure; callers need the
      // status to tell the two apart.
      error.status = response.status;
      throw error;
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
    this.bindMachineDialog();
    this.bindProjectFiles();
    this.renderTab();
    this.refreshDatasets().then(async () => {
      await this.refreshProject();
      // A reload keeps the server's session but not the page's selection,
      // and a full list over an empty map reads as if the data were gone.
      // The first dataset is selected, as it is when a project is reopened.
      if (this.state.datasets.length && !this.state.selectedId) {
        await this.selectDataset(this.state.datasets[0].id);
      }
    });
    this.refreshRecent();
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
      this.state.compare?.view.draw();
      this.renderTab();
    });

    document.getElementById("unit-details").addEventListener("click", () => this.openUnitsDialog());

    document.getElementById("color-column").addEventListener("change", (event) => {
      this.state.colorColumn = event.target.value;
      this.loadMap();
    });
    document.getElementById("show-basemap").addEventListener("change", (event) => {
      MapView.setBasemap(event.target.checked);
      this.state.compare?.view.setBasemap(event.target.checked);
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
    this.forgetGoneReports();
    // The selected dataset's detail is fetched once, when it is picked; a
    // cleaning or an analysis run afterwards flips the flags the tabs read
    // to decide whether there is a stored report to fetch. A tab redrawn
    // after its cached result was pruned would otherwise believe there is
    // nothing to fetch. The list carries the same summary, fresh.
    const current = payload.datasets.find((d) => d.id === this.state.selectedId);
    if (current && this.state.selected) Object.assign(this.state.selected, current);
    this.renderDatasetList();
    this.refreshProject();
  },

  /* Nothing selected: the map, the overlays and the legend that belonged to
   * the selection go with it. */
  clearSelection() {
    this.state.selectedId = null;
    this.state.selected = null;
    MapView.clearPoints();
    MapView.clearOverlays();
    this.clearRemovedOverlay();
    document.getElementById("legend").hidden = true;
  },

  /* A cached cleaning result names the clean copy and the removed records it
   * produced; once either is deleted those ids point at nothing, and the
   * print and "view" buttons drawn from the cache would ask the server for a
   * dataset it no longer has. Dropping the entry makes the tab fetch the
   * stored report again and rebuild the shape from the datasets that are
   * still there — the original keeps its copy of the report, so printing
   * falls back to it, as it does after a reload. */
  forgetGoneReports() {
    const alive = new Set(this.state.datasets.map((d) => d.id));
    for (const [key, cached] of Object.entries(this.state.reports)) {
      const ids = [key.split(":")[0], cached?.clean?.id, cached?.removed?.id].filter(Boolean);
      if (ids.some((id) => !alive.has(id))) delete this.state.reports[key];
    }
  },

  renderDatasetList() {
    const box = document.getElementById("dataset-list");
    if (!this.state.datasets.length) {
      box.innerHTML = '<div class="empty">Nothing loaded yet.</div>';
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
    // The comparison and the removed-records overlay belong to one cleaning;
    // a new selection means a new map. Left in place, the overlay's dots would
    // sit on the next dataset and the tooltip would call its records removed.
    if (this.state.compare) this.exitCompare({ reload: false });
    this.clearRemovedOverlay();
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
    // In compare mode the two panes are painted together, on one scale.
    if (this.state.compare) return this.paintCompare();
    const column = this.state.colorColumn;
    const payload = await this.api(
      `/api/datasets/${this.state.selectedId}/map?column=${encodeURIComponent(column)}`
    ).catch(() => null);
    if (!payload) return;

    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const scale = MapView.setPoints(payload, conv, { unit });
    if (payload.polygons?.length) MapView.setPolygons(payload.polygons);
    else if (this.state.tab !== "ensaio") MapView.clearOverlays();

    document.getElementById("map-status").textContent = this.pointsStatus(payload);
    this.renderLegend(column, unit, scale);
  },

  /* Past MAP_POINT_LIMIT the server sends a systematic sample, and `count`
   * is the size of the sample, not of the dataset. Any count shown to the
   * user has to say so, or two datasets on either side of the limit read
   * as if the smaller one had more points. */
  pointsStatus(payload) {
    return payload.sampled
      ? `${Units.num(payload.count, 0)} of ${Units.num(payload.total, 0)} points (sampled)`
      : `${Units.num(payload.count, 0)} points`;
  },

  /* The legend reads in the displayed unit, like the colours: `scale` is the
   * one setPoints returned, already converted. */
  renderLegend(column, unit, scale) {
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

  /* Safe as text and inside a quoted attribute alike. textContent alone
   * leaves quotes as they are, and a name such as 'S780 "40 ft"' put in a
   * value="…" would end at its first quote. */
  escape(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
    // The comparison only makes sense next to its cleaning report.
    if (this.state.compare && this.state.tab !== "limpeza") this.exitCompare();
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
 * Tab 1 — Data
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
    document.getElementById("btn-print-preflight")?.addEventListener("click", (event) =>
      this.printReport(this.state.selectedId, event.currentTarget));
    document.getElementById("btn-remove-dataset").addEventListener("click", async () => {
      await this.api(`/api/datasets/${this.state.selectedId}`, { method: "DELETE" });
      this.clearSelection();
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
        ${this.printableHead("First look at this file", "btn-print-preflight")}
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
 * Tab 2 — Cleaning
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
        ${this.machinePicker("clean")}
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
    this.bindMachinePicker("clean");

    document.getElementById("clean-preset").addEventListener("change", (event) => {
      this.state.cleanPreset = event.target.value;
      this.renderTab();
    });
    document.getElementById("btn-run-clean").addEventListener("click", () => this.runClean());

    const existing = this.state.reports[`${this.state.selectedId}:clean`];
    if (existing) this.renderCleanReport(existing);
    else if (d.has_clean_report) this.restoreCleanReport(d);
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
    // A new cleaning replaces the clean dataset; the old comparison is stale.
    if (this.state.compare) this.exitCompare();
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
        ${this.printableHead("Cleaning report", "btn-print-clean")}
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
        <button class="wide${this.state.compare ? "" : " primary"}" id="btn-compare">
          ${this.state.compare ? "Back to a single map" : "Compare before and after on the map"}
        </button>
        <p class="hint tight">Two maps side by side, on one colour scale, moving
        together. Hover a point to read its value.</p>
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
          ${result.clean ? '<button id="btn-goto-clean">View clean data</button>' : ""}
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
      document.getElementById("btn-hide-removed")?.addEventListener("click",
        () => this.clearRemovedOverlay());
    }

    document.getElementById("btn-goto-clean")?.addEventListener("click",
      () => this.selectDataset(result.clean.id));
    document.getElementById("btn-goto-removed")?.addEventListener("click",
      () => this.selectDataset(result.removed.id));
    // The clean copy is the dataset that carries this report; the server keeps
    // a copy on the original too, which serves once the clean copy is gone.
    document.getElementById("btn-print-clean")?.addEventListener("click", (event) =>
      this.printReport(result.clean?.id || this.state.selectedId, event.currentTarget));
    document.getElementById("btn-compare")?.addEventListener("click", () => {
      if (this.state.compare) this.exitCompare();
      else this.enterCompare(result);
    });
  },

  /* Draw the removed records over the clean ones, coloured by the filter that
   * caught them. Seeing that the removals sit on the headland and the overlap
   * is the difference between trusting a cleaning and hoping. */
  async showRemoved(removedDatasetId, onlyReason) {
    // The overlay is drawn on the single map. Painted over the "Before" pane
    // it would hide the values the comparison exists to show, and the
    // tooltip there would read a removal reason where the legend reads yield.
    if (this.state.compare) this.exitCompare();
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

  /* The overlay and the legend that explains its colours go together: one
   * without the other is either unexplained dots or a legend for nothing. */
  clearRemovedOverlay() {
    MapView.clearOverlay();
    const legend = document.getElementById("removed-legend");
    if (legend) legend.innerHTML = "";
  },

  /* The histogram arrives in internal units; the bin edges have to follow the
   * chosen unit, or the axis would read kg/ha on a chart labelled bu/ac. */
  scaleHistogram(hist, convert) {
    if (!hist?.edges?.length) return hist;
    return { counts: hist.counts, edges: hist.edges.map((e) => convert(e)) };
  },
});

/* ======================================================================
 * Before and after, side by side
 * ==================================================================== */

/* A second map pane beside the main one: the original on the left, the
 * clean copy on the right, locked together and on one colour scale. The
 * histogram says how much came out of the distribution; this says where. */
Object.assign(App, {
  async enterCompare(result) {
    if (this.state.compare) return;
    if (!result?.clean?.id) {
      this.toast("Nothing to compare", "Run the cleaning first.", "warn");
      return;
    }
    // A report can outlive the clean copy it describes — removed from the
    // session, or dropped by a reopen — and the server would answer 404 for
    // the "After" pane. Better to say so before building a pane for it.
    if (!this.state.datasets.some((d) => d.id === result.clean.id)) {
      this.toast("Nothing to compare",
        "The clean copy of this dataset is no longer loaded. Run the cleaning again.", "warn");
      return;
    }
    // The panes show the value on one scale; the removed-records overlay
    // would hide the "Before" values and contradict the legend.
    this.clearRemovedOverlay();

    const panes = document.getElementById("map-panes");
    const paneA = document.getElementById("map-pane");
    const paneB = document.createElement("div");
    paneB.className = "map-pane";
    paneB.id = "map-pane-b";
    paneB.innerHTML = `<div id="map-b" class="map"></div>
      <canvas id="point-canvas-b" class="point-canvas"></canvas>
      <div class="map-caption">After</div>`;
    panes.appendChild(paneB);
    panes.classList.add("compare");
    const captionA = document.createElement("div");
    captionA.className = "map-caption";
    captionA.textContent = "Before";
    paneA.appendChild(captionA);

    const view = createMapView("map-b", "point-canvas-b");
    view.init();
    view.setBasemap(this.checked("show-basemap"));
    // Both containers changed width: Leaflet has to re-measure before any
    // projection, or the right pane draws against the old, full-width size.
    MapView.invalidateSize();
    view.invalidateSize();
    const main = MapView.instance();
    view.instance().setView(main.getCenter(), main.getZoom(), { animate: false });
    const unlink = linkMapViews(MapView, view);

    this.state.compare = {
      originalId: this.state.selectedId,
      cleanId: result.clean.id,
      column: result.report.value_column,
      view, unlink,
    };
    // The panes are coloured by the cleaned variable, whatever the picker says.
    document.getElementById("color-column").disabled = true;
    this.syncCompareButton();
    await this.paintCompare();
  },

  /* The toggle is updated in place: re-rendering the tab would reset every
   * filter parameter the user has adjusted in the form above it. */
  syncCompareButton() {
    const button = document.getElementById("btn-compare");
    if (!button) return;
    const on = !!this.state.compare;
    button.textContent = on ? "Back to a single map" : "Compare before and after on the map";
    button.classList.toggle("primary", !on);
  },

  async paintCompare() {
    const c = this.state.compare;
    if (!c) return;
    const fetchMap = (id) =>
      this.api(`/api/datasets/${id}/map?column=${encodeURIComponent(c.column)}`);
    const [before, after] = await Promise.all([fetchMap(c.originalId), fetchMap(c.cleanId)])
      .catch((err) => { this.toast("That did not work", err.message, "error"); return []; });
    // The user may have left the comparison while the request was in flight.
    if (this.state.compare !== c) return;
    // Without both maps there is nothing to compare: an empty "After" pane
    // beside a disabled colour picker would look like a broken app rather
    // than a failed request, so the single map comes back.
    if (!before || !after) {
      this.exitCompare();
      return;
    }

    const { conv, unit } = Units.forColumn(c.column, this.state.selected?.meta?.operation);
    /* One scale for both, taken from the original: on separate scales the clean
     * map would simply look re-stretched, and the eye could not tell what was
     * removed from what was rescaled. */
    const scale = MapView.setPoints(before, conv, { unit });
    c.view.setPoints(after, conv, { unit, scale });
    this.renderLegend(c.column, unit, scale);
    document.getElementById("map-status").textContent =
      `Before: ${this.pointsStatus(before)} · After: ${this.pointsStatus(after)}`;
  },

  exitCompare({ reload = true } = {}) {
    const c = this.state.compare;
    if (!c) return;
    this.state.compare = null;
    c.unlink();
    c.view.destroy();
    document.getElementById("map-pane-b")?.remove();
    document.getElementById("map-panes").classList.remove("compare");
    document.querySelector("#map-pane > .map-caption")?.remove();
    document.getElementById("color-column").disabled = false;
    MapView.invalidateSize();
    this.syncCompareButton();
    if (reload) this.loadMap();
  },
});

/* ======================================================================
 * Tab 3 — DIFM analysis
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
    else if (d.has_difm_report) this.restoreDifmReport(d);
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
        ${this.printableHead("Recommendation", "btn-print-difm")}
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
    document.getElementById("btn-print-difm").addEventListener("click", (event) =>
      this.printReport(this.state.selectedId, event.currentTarget));
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
 * Tab 4 — Trial layout
 * ==================================================================== */

Object.assign(App, {
  tabEnsaio(panel) {
    const lengthUnit = Units.label.length();
    const inputUnit = Units.label.inputRate();
    const areaUnit = Units.label.area();
    const datasets = this.state.datasets.map((d) => [d.id, d.label]);
    /* The usual implement width in the chosen unit: 60 ft is the standard
     * drill and boom on the Prairies; 12 m, the common metric equivalent. */
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
        ${this.machinePicker("design")}
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

    this.bindMachinePicker("design");
    document.getElementById("btn-run-design").addEventListener("click", () => this.runDesign());
    document.getElementById("btn-draw-boundary").addEventListener("click", () => this.startDrawing());
    document.getElementById("btn-clear-boundary").addEventListener("click", () => {
      this.state.drawing = null;
      MapView.clearOverlays();
      document.getElementById("draw-status").textContent = "";
    });

    if (this.state.design) {
      this.renderDesignReport(this.state.design);
      // The strips go back on the map with the report: after a reopened
      // project, or a visit to a tab that painted its own overlays, the
      // report alone would describe a layout nobody can see. A drawing in
      // progress is left alone — it is the boundary of the next layout.
      if (!this.state.drawing) this.drawDesignWithGuidance();
    }
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
 * Tab 5 — Export
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
        ${this.machinePicker("package")}
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
    this.bindMachinePicker("package");
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
      field_name: this.value("pkg-field") || "Field",
      task_name: this.value("pkg-task") || "Prescription",
      product_name: this.value("pkg-product") || "Product",
      customer_name: this.value("pkg-customer") || "AgroSuite",
      farm_name: this.value("pkg-farm") || "Farm",
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
      task_name: this.value("exp-task") || "Prescription",
      field_name: this.value("exp-field") || "Field",
      product_name: this.value("exp-product") || "Product",
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
    const folderInput = document.getElementById("folder-input");
    document.getElementById("btn-open-file").addEventListener("click", () => input.click());
    document.getElementById("btn-open-folder").addEventListener("click", () => folderInput.click());

    // Both pickers hand over File objects; the folder picker also knows each
    // file's place inside the chosen folder, which is what keeps a shapefile's
    // parts and a TASKDATA tree together on the server.
    for (const picker of [input, folderInput]) {
      picker.addEventListener("change", async () => {
        const files = Array.from(picker.files || []);
        picker.value = "";
        const file = files[0];
        if (!file) {
          // The file picker only comes back empty on cancel. The folder
          // picker also does on an empty folder, and the person who chose
          // it needs to hear that it was seen.
          if (picker === folderInput) this.toast("Nothing to import", "The folder holds no files.", "warn");
          return;
        }
        if (files.length === 1 && this.isProjectFile(file.name)) {
          await this.uploadProject(file);
          return;
        }
        await this.importFiles(files.map((f) => ({ file: f, path: f.webkitRelativePath || f.name })));
      });
    }

    document.getElementById("btn-open-path").addEventListener("click", () => {
      this.setPathDialogMode(false);
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

    /* Drag and drop anywhere on the window: files, a shapefile's parts, or a
     * whole folder.
     *
     * The listeners sit on the document, not on #app. While an import runs,
     * #app is dimmed with pointer events off, and a drag then lands on the
     * body — where, with nothing to cancel it, the browser's own answer to
     * a dropped file is to open it in place of the app, upload and all. The
     * same happens over an open dialog, which is not inside #app. Wherever a
     * file drag lands, its default is cancelled here; a drop that cannot be
     * taken right now is refused with a word rather than left to the browser.
     *
     * Only a file drag is ours. Text dragged from a table towards an input
     * keeps the browser's own drop, and does not light the outline that
     * promises an import. dragenter/dragleave fire for every element the
     * pointer crosses, so a depth count tells a real leave from a hop
     * between panels. */
    const dropZone = document.getElementById("app");
    const carriesFiles = (e) => {
      const types = Array.from(e.dataTransfer?.types || []);
      // A drag carrying nothing at all is neither text nor a link: an empty
      // folder from some file managers, or a synthetic event. Cancelling it
      // costs nothing, and the drop then gets its answer below.
      return !types.length || types.includes("Files");
    };
    // A drop is taken from the moment it lands. Reading a big folder's
    // entries takes a while, and the .busy class only went on once the
    // upload started: a second drop meanwhile passed the check below and
    // raced the first for the dataset list. The flag holds for the whole
    // drop; the class dims the window for the same span, and is the one
    // the pickers and every other request share.
    let reading = false;
    // Why a drop has to wait, or "" when it can be taken now.
    const blocked = () => {
      if (reading || document.querySelector(".busy")) {
        return "Something is still running. Wait for it to finish, then drop again.";
      }
      if (document.querySelector("dialog[open]")) return "Close the dialog first, then drop again.";
      return "";
    };
    let depth = 0;
    document.addEventListener("dragenter", (e) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      if (depth++ === 0 && !blocked()) dropZone.classList.add("dropping");
    });
    document.addEventListener("dragleave", (e) => {
      if (!carriesFiles(e)) return;
      if (--depth <= 0) { depth = 0; dropZone.classList.remove("dropping"); }
    });
    document.addEventListener("dragover", (e) => { if (carriesFiles(e)) e.preventDefault(); });
    document.addEventListener("drop", async (e) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      depth = 0;
      dropZone.classList.remove("dropping");
      const why = blocked();
      if (why) { this.toast("Not now", why, "warn"); return; }
      reading = true;
      dropZone.classList.add("busy");
      try {
        const entries = await this.collectDropped(e.dataTransfer).catch((err) => {
          this.toast("Could not read the drop", `${err.message || err}\nTry "Open a folder" instead.`, "error");
          return [];
        });
        const file = entries[0]?.file;
        if (!file) {
          // An empty folder drops without a file in it; the person who dropped
          // it is otherwise left wondering whether the drop was seen.
          this.toast("Nothing to import", "The drop holds no files: an empty folder, or one " +
            "holding only hidden files.", "warn");
          return;
        }
        if (entries.length === 1 && this.isProjectFile(file.name)) { await this.uploadProject(file); return; }
        await this.importFiles(entries);
      } finally {
        reading = false;
        dropZone.classList.remove("busy");
      }
    });
  },

  /* The files in a drop, each with its path inside what was dropped.
   *
   * A folder only comes through DataTransferItem.webkitGetAsEntry, and that
   * call has to be made for every item before this function first awaits:
   * the item list is emptied the moment the drop event handler yields.
   * Without the entry API (or for an item that has none) the plain file list
   * is used, which carries loose files but no folders. */
  async collectDropped(transfer) {
    const items = Array.from(transfer?.items || []).filter((item) => item.kind === "file");
    const roots = items.map((item) => item.webkitGetAsEntry?.() ?? null);
    if (!roots.length || roots.some((entry) => !entry)) {
      return Array.from(transfer?.files || []).map((file) => ({ file, path: file.name }));
    }
    const found = [];
    const visit = async (entry) => {
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        found.push({ file, path: entry.fullPath.replace(/^\/+/, "") });
        return;
      }
      if (!entry.isDirectory) return;
      // readEntries hands out a folder in batches (a hundred at a time in
      // Chromium) and signals the end with an empty one.
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) await visit(child);
      }
    };
    for (const root of roots) await visit(root);
    return found;
  },

  /* Everything picked or dropped goes up in one request, tree included: the
   * server can only read a shapefile's four files, or a TASKDATA folder, as
   * one thing when it sees them together. `entries` is [{file, path}] with
   * the path relative to what was dropped, forward slashes. */
  async importFiles(entries) {
    // The fold under Load data describes the last drop, and this is now the
    // last drop: a refusal below must not leave an older drop's list
    // standing as if it were this one's.
    this.renderDropReport([]);
    const maxFiles = 2000, maxBytes = 2 * 1024 ** 3;
    const bytes = entries.reduce((sum, entry) => sum + (entry.file.size || 0), 0);
    if (entries.length > maxFiles || bytes > maxBytes) {
      this.toast("Too much at once",
        `${Units.num(entries.length, 0)} file(s), ${this.fileSize(bytes)}. One drop takes up to ` +
        `${Units.num(maxFiles, 0)} files or 2 GB: drop one field's folder at a time, or zip it.`, "warn");
      return;
    }
    const form = new FormData();
    for (const { file, path } of entries) {
      form.append("files", file, file.name);
      form.append("paths", path);
    }
    // The whole window dims: a folder can take a while, and a second drop
    // meanwhile would only race the first for the dataset list.
    const result = await this.busy(document.getElementById("app"), () =>
      this.api("/api/import/files", { method: "POST", body: form }));
    if (!result) return;

    await this.refreshDatasets();
    await this.selectDataset(result.imported[0].id);
    this.renderDropReport(result.skipped);

    const skipped = result.skipped || [];
    // Thumbs.db, .DS_Store and their kind travel with the folder and the
    // server never walks them; named here, the file total still adds up.
    const ignored = result.ignored || 0;
    const lines = [`${Units.num(entries.length, 0)} file(s) → ${Units.num(result.imported.length, 0)} dataset(s)` +
      (ignored ? ` (${Units.num(ignored, 0)} system file(s) ignored)` : "") + "."];
    if (result.imported.length === 1) {
      const only = result.imported[0];
      lines[0] += ` ${only.meta.brand_label} · ${Units.num(only.rows, 0)} records.`;
    }
    for (const item of skipped.slice(0, 3)) lines.push(`Skipped ${item.name}: ${item.reason}`);
    if (skipped.length > 3) {
      lines.push(`…and ${skipped.length - 3} more, listed under "Load data".`);
    }
    this.toast(skipped.length ? "Imported, with files left out" : "Imported",
      lines.join("\n"), skipped.length ? "warn" : "");
  },

  /* What the last drop left out stays on the Load data panel until the next
   * one, or until the session is replaced: a toast is gone in five seconds,
   * and a missing layer would otherwise be looked for on the map. */
  renderDropReport(skipped) {
    const box = document.getElementById("drop-report");
    if (!skipped?.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <details class="fold" open style="margin-top:8px">
        <summary>${skipped.length} file(s) skipped in the last drop</summary>
        <div class="inner">${skipped.map((item) =>
          `<p class="hint tight"><b style="font-family:var(--mono);font-size:11px">${
            this.escape(item.name)}</b><br>${this.escape(item.reason)}</p>`).join("")}
        </div>
      </details>`;
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
      // An .agrosuite file is a whole session, not one more layer: it goes to
      // "open", which replaces what is loaded, and never to import.
      if (this.state.projectOpen || this.isProjectFile(path)) {
        // Looked at before the picker closes: a refused path — a folder,
        // which a click in the browser puts in the box — leaves the picker
        // open on it, with the file inside still there to choose.
        const peek = await this.peekProject(path);
        if (!peek) return;
        document.getElementById("dlg-path").close();
        await this.openProject(path, peek);
        return;
      }
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
      ${this.field("Width and distance in the file",
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
      source_units.swath_m = proposed.length;
      source_units.distance_m = proposed.length;
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

/* ======================================================================
 * Machine profiles
 *
 * A header width, a flow delay and a working speed belong to the machine,
 * not to the field, yet three tabs ask for them. A profile is entered once,
 * kept by the server in profiles.json, and picked from one small row on each
 * tab: choosing a machine fills the fields that tab needs, "Save as
 * machine" reads them back, "Update machine" edits the one picked. The
 * server owns the list, so it is fetched on every render; the client keeps
 * only which machine each tab picked, so a tab switch does not forget it.
 * Every number crosses the wire in metric and is shown in the units the
 * user chose.
 * ==================================================================== */

Object.assign(App, {
  /* What each tab takes from a profile and gives back to one. `fill` puts
   * the profile's values on the form and says what it did, in the user's
   * units; `collect` reads the form back, in metric, leaving out what the
   * form does not have so the dialog can ask for it. */
  machineScope(scope) {
    const shown = (kind, value) => Number(Units.convert[kind](value).toFixed(3));
    const said = (value) => String(Number(Number(value).toFixed(2)));
    const put = (id, value) => {
      const node = document.getElementById(id);
      if (node) node.value = value;
    };
    const metric = (kind, id) => this.machineFieldMetric(kind, id) ?? undefined;
    const kindFromOperation = (d) => ({
      harvest: "combine", planting: "seeder", application: "sprayer",
    })[d?.meta?.operation] || null;

    const scopes = {
      clean: {
        hint: "A saved machine fills the flow delay and the speed range.",
        kind: (d) => kindFromOperation(d) || "other",
        fill: (p) => {
          const speedUnit = Units.label.speed();
          const low = shown("speed", p.speed_min_kmh);
          const high = shown("speed", p.speed_max_kmh);
          put("clean-delay", p.flow_delay_s);
          put("p-speed_range-min", low);
          put("p-speed_range-max", high);
          this.machinePlaced("p-speed_range-min", low, p.speed_min_kmh);
          this.machinePlaced("p-speed_range-max", high, p.speed_max_kmh);
          // Filling a filter that is switched off would change nothing and say
          // nothing; switching it on is what picking a speed range means.
          const step = document.getElementById("en-speed_range");
          const switched = step && !step.checked;
          if (switched) step.checked = true;
          return `flow delay ${said(p.flow_delay_s)} s, speed ${said(low)}–` +
            `${said(high)} ${speedUnit}.` +
            (switched ? " The speed filter was switched on." : "");
        },
        collect: () => ({
          flow_delay_s: this.number("clean-delay", 0),
          speed_min_kmh: metric("speed", "p-speed_range-min"),
          speed_max_kmh: metric("speed", "p-speed_range-max"),
        }),
      },
      design: {
        hint: "A saved machine fills the implement width and the passes per strip.",
        // A yield map is usually what is selected, but the trial is laid out
        // for whatever applies the rates.
        kind: (d) => (d?.meta?.operation === "harvest" ? "seeder"
          : kindFromOperation(d) || "seeder"),
        fill: (p) => {
          const width = shown("length", p.implement_width_m);
          put("design-width", width);
          put("design-passes", p.passes_per_strip);
          this.machinePlaced("design-width", width, p.implement_width_m);
          return `implement width ${said(width)} ${Units.label.length()}, ` +
            `${p.passes_per_strip} pass(es) per strip.`;
        },
        collect: () => ({
          implement_width_m: metric("length", "design-width"),
          passes_per_strip: this.number("design-passes", 2),
        }),
      },
      package: {
        hint: "A saved machine picks the target monitor.",
        kind: (d) => kindFromOperation(d) || "other",
        fill: (p) => {
          const select = document.getElementById("pkg-monitor");
          const known = [...select.options].some((o) => o.value === p.monitor);
          select.value = known ? p.monitor : "generic";
          // The existing handler redraws what the platform accepts.
          select.dispatchEvent(new Event("change"));
          const label = select.options[select.selectedIndex]?.textContent || select.value;
          return known ? `platform ${label}.`
            : `there is no package layout for ${p.monitor_label}, so the platform is ${label}.`;
        },
        collect: () => ({ monitor: this.value("pkg-monitor") || "generic" }),
      },
    };
    return scopes[scope];
  },

  /* One select and two small buttons; the "Suggest from this file" step
   * lives inside the save dialog, where its result can be checked before
   * anything is written. */
  machinePicker(scope) {
    const { hint } = this.machineScope(scope);
    const picked = this.machinePickedIn(scope);
    return `
      <div class="row tight machine-row">
        <label class="field">
          <span>Machine</span>
          <select id="machine-${scope}" data-machine-picker>${
            this.machineOptions(picked?.name || "")}</select>
        </label>
        <button class="small" id="btn-machine-save-${scope}"
          title="Save the settings on this tab as a machine">${
            this.machineSaveLabel(picked)}</button>
        <button class="small ghost" id="btn-machine-delete-${scope}"
          title="Delete the chosen machine">✕</button>
      </div>
      <p class="hint tight">${hint}</p>`;
  },

  machineOptions(selected = "") {
    const profiles = this.state.machines?.profiles || [];
    const placeholder = this.state.machinesRefused ? "— profiles.json could not be read —"
      : profiles.length ? "— choose a machine —" : "— no machine saved yet —";
    // A profile typed into the file that the app would refuse to save is
    // listed, so it can be fixed from here, but marked.
    const label = (p) => `${p.name} · ${p.kind_label}` +
      (p.problems?.length ? " · ⚠ needs attention" : "");
    return [["", placeholder], ...profiles.map((p) => [p.name, label(p)])]
      .map(([value, text]) =>
        `<option value="${this.escape(value)}"${value === selected ? " selected" : ""}>${
          this.escape(text)}</option>`).join("");
  },

  /* With a machine picked, the save button edits it rather than adding one. */
  machineSaveLabel(picked) {
    return picked ? "Update machine…" : "Save as machine…";
  },

  bindMachinePicker(scope) {
    const select = document.getElementById(`machine-${scope}`);
    if (!select) return;
    select.addEventListener("change", () => this.pickMachine(scope, select.value));
    document.getElementById(`btn-machine-save-${scope}`)
      .addEventListener("click", () => this.openMachineDialog(scope));
    document.getElementById(`btn-machine-delete-${scope}`)
      .addEventListener("click", () => this.deleteMachine(scope));
    // The tab was just redrawn from its defaults — a tab switch, a unit
    // change — over whatever the machine picked here had filled in. Its
    // values go back, quietly: the picker shows which machine they are.
    const picked = this.machinePickedIn(scope);
    if (picked) this.applyMachine(scope, picked, { quiet: true });
    this.loadMachines();
  },

  bindMachineDialog() {
    document.getElementById("btn-machine-suggest")
      .addEventListener("click", () => this.suggestMachine());
    document.getElementById("btn-machine-save")
      .addEventListener("click", () => this.saveMachine());
  },

  /* The server owns the list: it is fetched on every render rather than kept
   * on the client, so a profile added in another window, or by hand in the
   * file, shows up at the next tab change. `always` repeats a refusal that
   * was already said: a click asked for the list and gets its answer. */
  async loadMachines({ always = false } = {}) {
    const listing = await this.api("/api/profiles").catch((err) => {
      // A profiles.json the app cannot read is refused, not overwritten: the
      // message names the file and what is wrong. Every tab asks for the list
      // again, and the same refusal is said once, not at every tab switch.
      if (always || this.state.machinesRefused !== err.message) {
        this.toast("Machine profiles unavailable", err.message, "error");
      }
      this.state.machinesRefused = err.message;
      return null;
    });
    if (listing) this.state.machinesRefused = null;
    this.setMachines(listing);
    return listing;
  },

  setMachines(listing) {
    this.state.machines = listing;
    for (const select of document.querySelectorAll("select[data-machine-picker]")) {
      this.drawMachinePicker(select.id.replace("machine-", ""));
    }
  },

  /* The picker's list and its save button, in line with the list the server
   * holds now and with what this tab picked. */
  drawMachinePicker(scope) {
    const select = document.getElementById(`machine-${scope}`);
    if (!select) return;
    const picked = this.machinePickedIn(scope);
    select.innerHTML = this.machineOptions(picked?.name || "");
    const button = document.getElementById(`btn-machine-save-${scope}`);
    if (button) button.textContent = this.machineSaveLabel(picked);
  },

  /* What the tab picked, remembered by name so the choice survives the tab
   * being redrawn; resolved against the list as it is now, so a machine
   * deleted in the meantime is simply no longer picked. */
  machinePickedIn(scope) {
    return this.machineNamed(this.state.machinePicked?.[scope]);
  },

  pickMachine(scope, name) {
    this.state.machinePicked = { ...(this.state.machinePicked || {}), [scope]: name };
    const profile = this.machineNamed(name);
    document.getElementById(`btn-machine-save-${scope}`).textContent =
      this.machineSaveLabel(profile);
    if (profile) this.applyMachine(scope, profile);
  },

  /* The machine's values onto the tab, with what was done said in a toast.
   * A profile the file holds but the app would refuse to save — an inverted
   * speed range, a kind it does not know — is not applied: its numbers would
   * go into the cleaning as they are. It stays picked, so "Update machine…"
   * opens on it and can put it right. */
  applyMachine(scope, profile, { quiet = false } = {}) {
    if (profile.problems?.length) {
      if (!quiet) {
        this.toast(`'${profile.name}' needs attention`,
          `${profile.problems.join(" ")} Fix it with "Update machine…", or in ` +
          `${this.state.machines.storage}. Nothing was filled in.`, "warn");
      }
      return;
    }
    const done = this.machineScope(scope).fill(profile);
    if (!quiet) this.toast(`Filled from '${profile.name}'`, done);
  },

  /* Names match the way the server matches them — case and spacing aside —
   * so the profile just saved is found under whatever spelling came back. */
  machineKey(name) {
    return String(name || "").split(/\s+/).join(" ").trim().toLowerCase();
  },

  machineNamed(name) {
    const key = this.machineKey(name);
    if (!key) return null;
    return (this.state.machines?.profiles || []).find((p) => this.machineKey(p.name) === key)
      || null;
  },

  machineKindLabel(kind) {
    return Object.fromEntries(this.state.machines?.kinds || [])[kind] || kind;
  },

  /* A typed value on its way to the server, in metric. Rounded to a
   * micrometre, not a millimetre: 33 ft is 10.0584 m, and cut to 10.058 it
   * came back on screen as 32.999 while the toast still said 33. Six
   * decimals keep profiles.json readable and are invisible at the three the
   * forms show. */
  machineMetric(kind, value) {
    return Math.round(Units.toInternal[kind](value) * 1e6) / 1e6;
  },

  /* What the app itself put in a physical field, with the metric number it
   * was converted from. A field still showing that value is read back as
   * that number rather than re-converted: 9 m shown as 29.528 ft and
   * converted back is 9.000134 m, a drift that would be written to
   * profiles.json and read as a change nobody made. Keyed by field id; the
   * ids are distinct across the tabs and the dialog. */
  machinePlaced(id, display, metric) {
    const placed = (this.state.machinePlaced = this.state.machinePlaced || {});
    if (metric == null || display === "") delete placed[id];
    else placed[id] = { display, metric };
  },

  /* A physical field read back in metric: the exact number the app placed
   * there while the field still shows it, the typed value converted once it
   * does not. Null when the field is empty. */
  machineFieldMetric(kind, id) {
    const value = this.number(id, null);
    if (value == null) return null;
    const placed = this.state.machinePlaced?.[id];
    return placed && placed.display === value ? placed.metric : this.machineMetric(kind, value);
  },

  /* "John Deere combine", or "My seeder" when the file does not say who made it. */
  machineNameFor(d, kind) {
    const brand = (d?.meta?.brand_label || "").trim();
    const known = brand && !/^(desconhecido|unknown|generic)/i.test(brand) ? brand : "My";
    const word = kind === "other" ? "machine"
      : this.machineKindLabel(kind).split(" /")[0].toLowerCase();
    return `${known} ${word}`;
  },

  /* The server names a suggestion with its width in metres; whoever reads
   * feet should see feet, rounded the way a header is spoken of. */
  machineNameWithWidth(p) {
    const base = p.name.replace(/\s+[\d.]+\s*m$/, "");
    if (!(p.implement_width_m > 0)) return base;
    const unit = Units.label.length();
    const width = Units.convert.length(p.implement_width_m);
    return `${base} ${Number(width.toFixed(unit === "m" ? 2 : 0))} ${unit}`;
  },

  async openMachineDialog(scope) {
    if (!this.state.machines && !(await this.loadMachines({ always: true }))) return;
    const d = this.state.selected;
    const handlers = this.machineScope(scope);
    // A picked machine is edited, not copied: the dialog opens on its name,
    // kind, monitor and notes, and saving under that name updates it without
    // asking. Otherwise the form starts a new one from the file.
    const picked = this.machinePickedIn(scope);
    const kind = picked?.kind || handlers.kind(d);
    const brands = this.state.catalog.brands.map((b) => b.key);
    const draft = picked ? { ...picked } : {
      name: this.machineNameFor(d, kind),
      kind,
      monitor: brands.includes(d?.meta?.brand) ? d.meta.brand : "generic",
      implement_width_m: null,
      flow_delay_s: kind === "combine" ? 12 : 0,
      passes_per_strip: 2,
      speed_min_kmh: null,
      speed_max_kmh: null,
      notes: "",
    };
    // What the tab shows wins over what was saved: a width corrected on the
    // form is the correction being saved. A field the machine filled and
    // nobody touched reads back as the exact number it came from.
    for (const [key, value] of Object.entries(handlers.collect())) {
      if (value !== undefined) draft[key] = value;
    }
    this.state.machineDialog = { scope, editing: picked?.name || null };
    this.renderMachineDialog(draft);
    document.getElementById("dlg-machine").showModal();
  },

  renderMachineDialog(p) {
    const lengthUnit = Units.label.length();
    const speedUnit = Units.label.speed();
    const editing = this.state.machineDialog?.editing;
    // A speed minimum of 0 is a real setting and stays 0; an empty box is
    // what makes the server fill in the kind's typical range instead.
    const shown = (kind, value) => (value == null ? "" : Number(Units.convert[kind](value).toFixed(3)));
    const brands = this.state.catalog.brands.map((b) => [b.key, b.label]);
    // Only the width keeps 0 for "unknown": that is what a suggestion sends
    // when the file has no swath, and the placeholder is the better prompt.
    const width = shown("length", p.implement_width_m || null);
    const low = shown("speed", p.speed_min_kmh);
    const high = shown("speed", p.speed_max_kmh);
    this.machinePlaced("mp-width", width, p.implement_width_m || null);
    this.machinePlaced("mp-speed-min", low, p.speed_min_kmh);
    this.machinePlaced("mp-speed-max", high, p.speed_max_kmh);

    document.querySelector("#dlg-machine h3").textContent =
      editing ? `Update '${editing}'` : "Save as a machine";
    document.getElementById("machine-body").innerHTML = `
      ${this.field("Name",
        `<input type="text" id="mp-name" value="${this.escape(p.name)}"
           placeholder="e.g. S780 with the 40 ft draper">`,
        editing ? `Under another name this is saved as a new machine; '${this.escape(editing)}' stays.`
          : "Saving under another machine's name asks before replacing it.")}
      <div class="row tight">
        ${this.field("Kind", this.selectInput("mp-kind", this.state.machines.kinds, p.kind))}
        ${this.field("Monitor", this.selectInput("mp-monitor", brands, p.monitor))}
      </div>
      <div class="row tight">
        ${this.field(`Implement width (${lengthUnit})`,
          `<input type="number" id="mp-width" value="${width}"
             step="any" min="0" placeholder="header, boom or drill">`)}
        ${this.field("Flow delay (s)", this.numberInput("mp-delay", p.flow_delay_s, "0.5", "0"),
          "Combines only; 0 for anything else.")}
      </div>
      <div class="row tight">
        ${this.field(`Speed from (${speedUnit})`,
          `<input type="number" id="mp-speed-min" value="${low}"
             step="any" min="0" placeholder="typical for the kind">`)}
        ${this.field(`to (${speedUnit})`,
          `<input type="number" id="mp-speed-max" value="${high}"
             step="any" min="0" placeholder="typical for the kind">`)}
        ${this.field("Passes per strip", this.numberInput("mp-passes", p.passes_per_strip, "1", "1"))}
      </div>
      ${this.field("Notes", `<input type="text" id="mp-notes" value="${this.escape(p.notes)}">`)}
      <div id="mp-problems"></div>
      <p class="hint tight">Saved in <span style="font:11px var(--mono)">${
        this.escape(this.state.machines.storage)}</span> — plain JSON, in metric.</p>`;

    document.getElementById("btn-machine-suggest").hidden = !this.state.selectedId;
  },

  /* Prefill from the selected dataset. Nothing is saved: the file is
   * evidence of what the machine was, and the operator confirms it. */
  async suggestMachine() {
    if (!this.state.selectedId) {
      this.toast("No file selected", "Pick a dataset on the left first.", "warn");
      return;
    }
    // The suggestion is for the kind the form shows, not for whichever
    // machine wrote the file: on the trial-layout tab a yield map is
    // selected, and the profile being made is the seeder's.
    const kind = this.value("mp-kind");
    const passes = this.value("mp-passes");
    const result = await this.busy(document.getElementById("dlg-machine"), () =>
      this.api("/api/profiles/suggest", {
        method: "POST", body: { dataset_id: this.state.selectedId, kind },
      }));
    if (!result) return;

    const profile = { ...result.profile, name: this.machineNameWithWidth(result.profile) };
    // Passes per strip are a decision, not something a file records.
    if (passes) profile.passes_per_strip = passes;
    // Suggesting into a machine being updated fills in its numbers, not a
    // new name: the file says what the machine is like, not what it is called.
    if (this.state.machineDialog?.editing) profile.name = this.value("mp-name");
    this.renderMachineDialog(profile);
    document.getElementById("mp-problems").innerHTML = result.problems.length
      ? `<div class="note warning">${result.problems.map((s) => this.escape(s)).join("<br>")}</div>`
      : `<div class="note ok">Read from '${this.escape(result.dataset_label)}'. ` +
        "Nothing stops this being saved as it is.</div>";
    this.toast("Suggested from the file",
      result.problems.length ? "Fill in what is missing, then save." : "Check the numbers, then save.");
  },

  async saveMachine() {
    const { scope, editing } = this.state.machineDialog || {};
    const name = (this.value("mp-name") || "").trim();

    // Checked here first, in the units on the screen: the server refuses an
    // inverted range too, but in km/h, quoting numbers nobody typed.
    const low = this.number("mp-speed-min", null);
    const high = this.number("mp-speed-max", null);
    if (low != null && high != null && low >= high) {
      this.toast("Check the speed range", "The minimum speed must be below the maximum " +
        `(got ${low} and ${high} ${Units.label.speed()}).`, "warn");
      return;
    }

    const body = {
      name,
      kind: this.value("mp-kind"),
      monitor: this.value("mp-monitor"),
      // A missing width goes as 0 so the server refuses it with its own words
      // rather than inventing a header.
      implement_width_m: this.machineFieldMetric("length", "mp-width") ?? 0,
      flow_delay_s: this.number("mp-delay", 0),
      passes_per_strip: this.number("mp-passes", 2),
      // Left empty, the speeds come back as the typical range for the kind.
      speed_min_kmh: this.machineFieldMetric("speed", "mp-speed-min"),
      speed_max_kmh: this.machineFieldMetric("speed", "mp-speed-max"),
      notes: this.value("mp-notes") || "",
      // Updating the machine that was picked is what the dialog opened for
      // and asks nothing; any other saved name is replaced only once the
      // person says so, below.
      replace: !!editing && this.machineKey(name) === this.machineKey(editing),
    };

    // A 400 lists every problem at once; busy() shows it verbatim. A 409 is
    // a question, with the saved machine's name in it.
    const listing = await this.busy(document.getElementById("dlg-machine"), async () => {
      try {
        return await this.api("/api/profiles", { method: "POST", body });
      } catch (err) {
        if (err.status !== 409) throw err;
        if (!window.confirm(`${err.message}\n\nReplace that machine with what the form shows?`)) {
          return null;
        }
        return this.api("/api/profiles", { method: "POST", body: { ...body, replace: true } });
      }
    });
    if (!listing) return;

    document.getElementById("dlg-machine").close();
    this.setMachines(listing);
    const saved = this.machineNamed(name);
    let filled = "";
    if (saved && document.getElementById(`machine-${scope}`)) {
      this.state.machinePicked = { ...(this.state.machinePicked || {}), [scope]: saved.name };
      this.drawMachinePicker(scope);
      filled = " " + this.machineScope(scope).fill(saved);
    }
    this.toast(editing ? "Machine updated" : "Machine saved",
      `'${saved?.name || name}' is ready on every tab.${filled}`);
  },

  async deleteMachine(scope) {
    const name = this.value(`machine-${scope}`);
    if (!name) {
      this.toast("Nothing to delete", "Choose a machine in the list first.", "warn");
      return;
    }
    if (!window.confirm(`Delete the machine '${name}'? Its saved settings are lost; ` +
                        "what is already filled in on this tab stays as it is.")) return;
    const listing = await this.busy(document.getElementById(`machine-${scope}`).parentElement,
      () => this.api(`/api/profiles/${encodeURIComponent(name)}`, { method: "DELETE" }));
    if (!listing) return;
    // Forgotten on every tab, not just this one: a machine saved later under
    // the same name is a different machine and is not picked by itself.
    for (const [tab, picked] of Object.entries(this.state.machinePicked || {})) {
      if (this.machineKey(picked) === this.machineKey(name)) this.state.machinePicked[tab] = "";
    }
    this.setMachines(listing);
    this.toast("Machine deleted", `'${name}' is gone from the list.`);
  },
});

/* ======================================================================
 * Project files
 *
 * A session is worth keeping: the datasets, the cleaning results, the
 * roles and the prices took an afternoon to put together, and the app
 * forgets all of it when it closes. "Save project" writes one .agrosuite
 * file holding the whole session; "Open project" brings it back as it
 * was, with the same dataset ids, so every report still points at its
 * datasets. The server owns the recent list; the client only asks for it.
 * ==================================================================== */

Object.assign(App, {
  bindProjectFiles() {
    document.getElementById("btn-project-save")
      .addEventListener("click", () => this.openSaveDialog());
    document.getElementById("btn-save-project-confirm")
      .addEventListener("click", () => this.saveProject());
    for (const id of ["save-name", "save-path"]) {
      document.getElementById(id).addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); this.saveProject(); }
      });
    }
    document.getElementById("btn-project-new")
      .addEventListener("click", () => this.newProject());

    // The same browser as "Open by path", pointed at projects: it lists
    // .agrosuite files already, and an .agrosuite pick goes to "open".
    document.getElementById("btn-project-open").addEventListener("click", () => {
      this.setPathDialogMode(true);
      document.getElementById("path-input").value = "";
      document.getElementById("dlg-path").showModal();
      const last = (this.state.recent || []).find((item) => item.exists);
      this.browse(last ? last.path : "");
      this.toast("Pick a project", "Choose a .agrosuite file, then press Open.");
    });
    // Cancelled or not, the dialog is a plain data picker the next time.
    document.getElementById("dlg-path")
      .addEventListener("close", () => this.setPathDialogMode(false));
  },

  setPathDialogMode(project) {
    this.state.projectOpen = project;
    document.getElementById("dlg-path-title").textContent =
      project ? "Open a project" : "Open by path";
  },

  /* ".agrosuite" is what the file browser and the drop zone go by: the
   * server refuses to import one as a layer anyway, and the refusal would
   * only arrive after the upload. */
  isProjectFile(name) { return /\.agrosuite$/i.test(name || ""); },

  when(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? String(iso)
      : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  },

  /* ---------------------------------------------------------------- save */

  openSaveDialog() {
    if (!this.state.datasets.length) {
      this.toast("Nothing to save yet",
        "The session holds no datasets. Open a file first.", "warn");
      return;
    }
    const current = this.state.projectFile;
    document.getElementById("save-name").value = this.state.project?.name || "";
    // Saved to disk before: propose where it went, so re-saving is a matter
    // of pressing Save. A file named after the project keeps following the
    // name — the folder is proposed, and a renamed project gets a new file
    // beside the old one — while a file saved under a name of its own is
    // re-saved as it is. A temporary folder is never proposed.
    document.getElementById("save-path").value = !current?.on_disk ? ""
      : current.named_after_project ? current.folder : current.path;
    document.getElementById("dlg-save-project").showModal();
    document.getElementById("save-name").select();
  },

  async saveProject() {
    // busy() blocks the pointer, not the keyboard: a second Enter while the
    // first save is on its way would run the whole flow twice and race the
    // server's "already exists" check.
    if (this.state.saving) return;
    const dialog = document.getElementById("dlg-save-project");
    const name = (this.value("save-name") || "").trim();
    const path = (this.value("save-path") || "").trim();
    if (!name) {
      this.toast("Name the project", "The file is named after it.", "warn");
      return;
    }

    // The name given here is the project's: it names the file, it is what
    // the next save proposes, and it comes back when the file is reopened.
    // It travels with the save, so a refused save renames nothing.
    // Re-saving the file that is open is the normal flow and asks nothing —
    // the server knows which file that is; any other existing file is
    // replaced only once the person says so.
    const request = { name, path: path || null, overwrite: false };

    this.state.saving = true;
    let result;
    try {
      result = await this.busy(dialog, async () => {
        try {
          return await this.api("/api/session/save", { method: "POST", body: request });
        } catch (err) {
          if (err.status !== 409) throw err;
          if (!window.confirm(`${err.message}\n\nReplace it with the current session?`)) {
            return null;
          }
          return this.api("/api/session/save", {
            method: "POST", body: { ...request, overwrite: true },
          });
        }
      });
    } finally {
      this.state.saving = false;
    }
    if (!result) return;

    dialog.close();
    this.state.projectFile = result.file;
    this.renderProjectFile();
    this.refreshRecent();
    this.refreshProject();
    this.toast("Project saved",
      `${result.path}\n${result.datasets} dataset(s), ${this.fileSize(result.size_bytes)}.` +
      (result.file.on_disk ? "" : "\nThat folder goes when the app closes: keep the file " +
        "through the download link in the Load data panel.") +
      (result.warning ? `\n${result.warning}` : ""),
      result.warning ? "warn" : "");
  },

  /* What is open, where it is, and the way to keep it. */
  renderProjectFile() {
    const box = document.getElementById("project-file");
    const file = this.state.projectFile;
    if (!file) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <div class="note ok" style="margin-top:8px">
        <b>${this.escape(file.name)}.agrosuite</b>${
          file.saved_at ? ` · saved ${this.escape(this.when(file.saved_at))}` : ""}
        ${file.on_disk ? `<br><span style="font:11px var(--mono);word-break:break-all">${
          this.escape(file.path)}</span>` : ""}
        ${file.download_url ? `<br><a href="${file.download_url}" download
          id="project-download">Download a copy</a>` : ""}
      </div>`;
  },

  /* -------------------------------------------------------------- recent */

  async refreshRecent() {
    const payload = await this.api("/api/session/recent").catch(() => null);
    if (!payload) return;
    this.state.recent = payload.recent;
    // The server remembers which file the session came from or last went
    // to; a copy kept only here would not survive a page reload, and the
    // next save would quietly go to the temporary folder.
    this.state.projectFile = payload.current;
    this.renderProjectFile();
    this.renderRecentProjects();
  },

  renderRecentProjects() {
    const box = document.getElementById("recent-projects");
    const items = this.state.recent || [];
    box.innerHTML = `<h4 style="margin:12px 0 4px">Recent projects</h4>`;
    if (!items.length) {
      box.innerHTML += '<p class="hint tight">None yet — "Save project" writes one.</p>';
      return;
    }
    const list = document.createElement("div");
    list.className = "file-list";
    for (const item of items) {
      const node = document.createElement("div");
      node.className = "item" + (item.exists ? "" : " unavailable") +
        (item.path === this.state.projectFile?.path ? " current" : "");
      node.title = item.exists ? item.path : `Not found: ${item.path}`;
      node.innerHTML = `<span class="icon">${item.exists ? "🗂" : "∅"}</span>
        <span class="name">${this.escape(item.name)}${item.saved_at
          ? `<span class="when">${this.escape(this.when(item.saved_at))}</span>` : ""}</span>`;
      node.addEventListener("click", () => {
        if (item.exists) { this.openProject(item.path); return; }
        this.toast("File not found", `${item.path}\nPlug the drive back in, or open ` +
          "the project by path from wherever it is now.", "warn");
      });
      list.appendChild(node);
    }
    box.appendChild(list);
  },

  /* ---------------------------------------------------------------- open */

  /* Opening and "New project" replace the session, so the person is asked
   * first. The client asks because the server cannot tell whether what it
   * holds was saved a minute ago or never. */
  confirmReplace(what) {
    const count = this.state.datasets.length;
    if (!count) return true;
    return window.confirm(`${what} closes the ${count} dataset(s) loaded now. ` +
      "Anything not saved to a project file is lost. Continue?");
  },

  /* A look at the file before anything is given up for it: what it holds
   * and when it was saved, or why it cannot be opened. Without it a folder,
   * or a ZIP of monitor data, went through "closes the N datasets" and only
   * then failed — the work was still there, but the question should never
   * have been asked. The server answers a refusal in words, not with an
   * error status: it is an answer, shown here. Null when refused. */
  async peekProject(path) {
    const peek = await this.api(`/api/session/peek?path=${encodeURIComponent(path)}`)
      .catch((err) => ({ ok: false, reason: err.message }));
    if (peek.ok) return peek;
    this.toast("Not a project to open", peek.reason, "warn");
    return null;
  },

  async openProject(path, peek = null) {
    peek = peek || await this.peekProject(path);
    if (!peek) return;
    const saved = peek.saved_at ? `, saved ${this.when(peek.saved_at)}` : "";
    if (!this.confirmReplace(
      `Opening '${peek.project || peek.name}' (${peek.datasets} dataset(s)${saved})`)) return;
    const result = await this.busy(document.querySelector("aside.left"), () =>
      this.api("/api/session/open", { method: "POST", body: { path } }));
    if (!result) return;
    await this.sessionReplaced(result);
  },

  async uploadProject(file) {
    if (!this.confirmReplace(`Opening '${file.name}'`)) return;
    const form = new FormData();
    form.append("file", file);
    const result = await this.busy(document.querySelector("aside.left"), () =>
      this.api("/api/session/upload", { method: "POST", body: form }));
    if (!result) return;
    // The copy lives in the session's temporary folder, not where the file
    // came from; the server reports it as not "on disk", and the save
    // dialog does not propose that folder.
    await this.sessionReplaced(result);
  },

  async newProject() {
    if (!this.confirmReplace("A new project")) return;
    const ok = await this.busy(document.querySelector("aside.left"), () =>
      this.api("/api/session/new", { method: "POST" }));
    if (!ok) return;
    this.resetSessionState();
    this.state.projectFile = null;
    this.renderProjectFile();
    this.renderRecentProjects();
    await this.refreshDatasets();
    await this.refreshProject();
    this.renderTab();
    this.toast("New project", "The session is empty. Load a file, or open a saved project.");
  },

  /* Everything the client cached about the old session goes: the report
   * caches keyed by dataset id, the map, the comparison. A reopened project
   * keeps its ids, so a stale cache would show a cleaning run after the
   * file was saved as if it were in the file. */
  resetSessionState() {
    if (this.state.compare) this.exitCompare({ reload: false });
    this.state.reports = {};
    this.state.joinReport = null;
    this.state.design = null;
    this.state.drawing = null;
    this.state.selectedId = null;
    this.state.selected = null;
    MapView.clearPoints();
    MapView.clearOverlays();
    this.clearRemovedOverlay();
    document.getElementById("legend").hidden = true;
    document.getElementById("map-status").textContent = "";
    // The skipped-files fold belongs to a drop made into the old session.
    this.renderDropReport([]);
  },

  async sessionReplaced(result) {
    this.resetSessionState();
    this.state.projectFile = result.file;
    // The trial layout is in the file with everything else: the Trial
    // layout tab puts it back on the map when it renders, and Export
    // offers it as the prescription again.
    this.state.design = result.design?.result || null;
    this.renderProjectFile();
    await this.refreshDatasets();
    await this.refreshProject();
    this.refreshRecent();
    if (this.state.datasets.length) await this.selectDataset(this.state.datasets[0].id);
    else this.renderTab();
    this.toast(`Opened '${result.project}'`,
      `${result.datasets.length} dataset(s) from ${result.name}.agrosuite` +
      (result.saved_at ? `, saved ${this.when(result.saved_at)}.` : ".") +
      (result.warning ? `\n${result.warning}` : ""),
      result.warning ? "warn" : "");
  },

  /* ------------------------------------------------------ stored reports */

  /* The server keeps every report; the client caches only the ones it saw
   * being made. After a reopen the cache is empty while the reports are
   * there, so a tab asks for what it is missing instead of showing nothing. */
  async storedReport(kind) {
    const id = this.state.selectedId;
    const report = await this.api(`/api/datasets/${id}/report/${kind}`).catch(() => null);
    // The selection may have moved on while the request was out.
    if (!report || this.state.selectedId !== id) return null;
    return report;
  },

  /* POST /clean answers with the report plus the two datasets it produced;
   * the stored report is the report alone, so the ids are read off the
   * dataset list: the clean copy and the removed records both hang off the
   * dataset that was cleaned, and the last of each is the latest run. */
  cleanResultFor(id, report) {
    const children = this.state.datasets.filter((d) => d.parent_id === id);
    const clean = children.filter((d) => d.origin === "clean" && d.has_clean_report).pop();
    const removed = children.filter((d) => d.origin === "clean_removed").pop();
    return {
      report,
      clean: clean ? { id: clean.id } : null,
      removed: removed ? { id: removed.id } : null,
    };
  },

  async restoreCleanReport(d) {
    const shape = this.cleanResultFor(d.id, null);
    // A clean copy carries the report of its own making; it is shown on the
    // dataset that was cleaned, where "view clean data" leads somewhere.
    if (d.origin === "clean" && !shape.clean) return;
    const report = await this.storedReport("clean");
    if (!report || this.state.tab !== "limpeza") return;
    const result = { ...shape, report };
    this.state.reports[`${d.id}:clean`] = result;
    this.renderCleanReport(result);
  },

  async restoreDifmReport(d) {
    const report = await this.storedReport("difm");
    if (!report || this.state.tab !== "difm") return;
    this.state.reports[`${d.id}:difm`] = report;
    this.renderDifmReport(report);
  },
});

/* ======================================================================
 * Printed report
 * ==================================================================== */

Object.assign(App, {
  /* A panel heading with the print button beside it. The button lives on
   * the report it prints, so what goes on paper is what is on screen. */
  printableHead(title, buttonId) {
    return `<div class="head"><h3>${title}</h3>
      <button class="small" id="${buttonId}"
              title="Write this report as a PDF, in the units shown on screen">
        Print report (PDF)</button></div>`;
  },

  /* The report belongs to the dataset that carries it: the clean copy after a
   * cleaning, the analysed dataset after DIFM. The unit set travels with the
   * request because the server keeps no display preference — the numbers on
   * paper must be the ones the user was looking at, not the metric ones
   * underneath. The project name is the heading. */
  async printReport(datasetId, button) {
    const prefs = Units.get();
    const body = {
      dataset_id: datasetId,
      units: Object.fromEntries(["yield_unit", "input_rate_unit", "area_unit",
        "length_unit", "speed_unit", "currency", "crop"].map((k) => [k, prefs[k]])),
      title: this.state.project?.name || null,
    };
    const result = await this.busy(button, async () => {
      try {
        return await this.api("/api/report", { method: "POST", body });
      } catch (err) {
        if (err.status !== 404) throw err;
        // The dataset the button named is no longer in the session: removed
        // from elsewhere — the MCP server, another tab — since this panel was
        // drawn. The list is the truth: refreshing it prunes the cached
        // result that carried the old id, and redrawing the tab rebuilds the
        // panel from what is still loaded. For a cleaning report that is the
        // original, which keeps its own copy and prints from that; when the
        // selected dataset itself is gone there is nothing to redraw for.
        const wasSelected = datasetId === this.state.selectedId;
        if (wasSelected) this.clearSelection();
        await this.refreshDatasets();
        this.renderTab();
        this.toast(wasSelected ? "That dataset is gone" : "The clean copy is gone",
          "It was removed from the session after this panel was drawn. The list " +
          "has been refreshed" + (wasSelected ? "; pick a dataset to go on."
            : " and the report redrawn from the original's own copy; press Print again."),
          "warn");
        return null;
      }
    });
    if (!result) return;

    // The file is served as an attachment, so the new tab is the download.
    // A pop-up blocker returns null; a clicked anchor needs no permission,
    // and the toast names the path on disk either way.
    if (!window.open(result.download_url, "_blank")) {
      const link = document.createElement("a");
      link.href = result.download_url;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }

    const names = { preflight: "first look", clean: "cleaning", difm: "DIFM analysis" };
    const printed = result.sections.map((s) => names[s]).filter(Boolean).join(", ");
    this.toast("Report written",
      `${result.filename}: ${printed || "header only"}, ` +
      `${result.pages} page${result.pages === 1 ? "" : "s"}.\nSaved at ${result.path}`);
  },
});
