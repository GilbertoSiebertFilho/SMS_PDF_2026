/* AgroSuite — interface.
 *
 * Fluxo de cinco passos, um por aba: carregar os dados, limpar, analisar,
 * desenhar ensaio, exportar. O estado vive num objeto só; cada aba redesenha
 * o painel da direita a partir dele. */

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
    catch (err) { this.toast("Não deu certo", err.message, "error"); return null; }
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
      this.toast("Servidor indisponível", err.message, "error");
      return;
    }

    this.buildUnitPreset();
    this.bindTopbar();
    this.bindImport();
    this.bindDialogs();
    this.renderTab();
    this.refreshDatasets();
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
      this.toast("Unidades", this.state.units.presets[event.target.value].description);
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
      const origin = { clean: "limpo", clean_removed: "removidos", units: "convertido",
                       demo: "demo", upload: "enviado", path: "disco" }[item.origin] || item.origin;
      card.innerHTML = `
        <div class="title">${this.escape(item.label)}</div>
        <div class="meta">
          <span class="tag ${item.origin === "clean" ? "accent" : ""}">${origin}</span>
          <span>${Units.num(item.rows, 0)} reg.</span>
          <span>${this.escape(item.meta.brand_label)}</span>
        </div>`;
      card.addEventListener("click", () => this.selectDataset(item.id));
      box.appendChild(card);
    }
  },

  async selectDataset(id) {
    this.state.selectedId = id;
    const detail = await this.api(`/api/datasets/${id}`).catch((err) => {
      this.toast("Não deu certo", err.message, "error");
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
      ? `${Units.num(payload.count, 0)} de ${Units.num(payload.total, 0)} pontos (amostrados)`
      : `${Units.num(payload.count, 0)} pontos`;

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

  /* Arredonda para o "número redondo" mais próximo na ordem de grandeza:
   * 44,8 vira 50; 4,48 vira 5; 448 vira 450. */
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

  /* ----------------------------------------------- render do painel direito */

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
      panel.innerHTML = `<div class="panel"><h3>Dados</h3>
        <div class="empty">Carregue um arquivo à esquerda, ou comece por um dos
        conjuntos de demonstração.</div></div>`;
      return;
    }

    const meta = d.meta;
    const stats = d.stats || {};
    const areaUnit = Units.label.area();
    const confidence = meta.extra?.brand_confidence;

    panel.innerHTML = `
      <div class="panel">
        <h3>Origem</h3>
        <div class="stat-grid">
          <div class="stat"><div class="k">Monitor identificado</div>
            <div class="v" style="font-size:13px">${this.escape(meta.brand_label)}</div>
            <div class="d">${confidence != null
              ? `confiança ${Math.round(confidence * 100)}%` : "informado"}</div></div>
          <div class="stat"><div class="k">Operação</div>
            <div class="v" style="font-size:13px">${this.escape(meta.operation_label)}</div>
            <div class="d">${this.escape(meta.source_format)}</div></div>
          <div class="stat"><div class="k">Registros</div>
            <div class="v">${Units.num(d.rows, 0)}</div>
            <div class="d">${d.metric_crs || ""}</div></div>
          <div class="stat"><div class="k">Área trabalhada</div>
            <div class="v">${Units.num(Units.convert.area(d.area_ha))}</div>
            <div class="d">${areaUnit}</div></div>
        </div>
        <div class="row tight" style="margin-top:10px">
          <button class="small" id="btn-declare-units">Unidades do arquivo</button>
          ${meta.brand === "augmenta"
            ? '<button class="small" id="btn-augmenta">Vigor × dose</button>' : ""}
        </div>
      </div>

      <div class="panel">
        <h3>Variável principal · ${this.escape(
          this.state.catalog.columns[this.state.colorColumn] || this.state.colorColumn)}</h3>
        ${this.statsBlock(stats, this.state.colorColumn)}
      </div>

      ${meta.extra?.jd_card ? this.cardPanel(meta.extra.jd_card, meta.extra.jd_card_layers) : ""}

      ${meta.notes?.length ? `<div class="panel"><h3>Observações da leitura</h3>
        ${meta.notes.map((n) => `<div class="note">${this.escape(n)}</div>`).join("")}</div>` : ""}

      ${Object.keys(meta.extra?.column_mapping || {}).length ? `
      <div class="panel"><h3>Colunas reconhecidas</h3>
        <details class="fold"><summary>${
          Object.keys(meta.extra.column_mapping).length} colunas mapeadas</summary>
          <div class="inner scroll-x"><table class="data">
            <tr><th>No arquivo</th><th>Interpretada como</th></tr>
            ${Object.entries(meta.extra.column_mapping).map(([raw, canonical]) =>
              `<tr><td>${this.escape(raw)}</td><td>${this.escape(
                this.state.catalog.columns[canonical] || canonical)}</td></tr>`).join("")}
          </table></div>
        </details>
      </div>` : ""}

      <div class="panel"><h3>Primeiras linhas</h3>
        <div class="scroll-x" id="preview"></div>
      </div>

      <div class="panel">
        <button class="wide" id="btn-remove-dataset">Remover da sessão</button>
      </div>`;

    this.renderPreview(document.getElementById("preview"), d.preview);

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

  /* Inventário do cartão John Deere: o que dá para ler e o que é
   * proprietário. Saber que o arquivo existe mas é fechado é diferente de
   * achar que o cartão veio vazio. */
  cardPanel(card, layers) {
    const roleLabels = {
      boundary: "Contorno", guidance: "Linhas AB",
      prescription: "Prescrição", data: "Dados de operação",
    };
    return `
      <div class="panel">
        <h3>Cartão John Deere</h3>
        <div class="note">${this.escape(card.summary)}</div>
        ${layers?.length ? `<h4>Camadas que o app consegue abrir</h4>
          <div class="scroll-x"><table class="data">
            <tr><th>Arquivo</th><th>Papel</th></tr>
            ${layers.map((l) => `<tr>
              <td style="font-family:var(--mono);font-size:11px">${this.escape(l.relative)}</td>
              <td>${this.escape(roleLabels[l.role] || l.role)}</td></tr>`).join("")}
          </table></div>` : ""}
        ${card.proprietary?.length ? `<details class="fold" style="margin-top:8px">
          <summary>${card.proprietary.length} arquivo(s) em formato proprietário</summary>
          <div class="inner">
            ${card.proprietary.map((f) => `<p class="hint tight">
              <span style="font-family:var(--mono)">${this.escape(f.relative)}</span> — ${
                this.escape(f.kind)}</p>`).join("")}
            <p class="hint tight" style="margin-top:6px">Esses arquivos só abrem no
            software da John Deere ou no próprio display. Para trazê-los ao AgroSuite,
            reexporte no SMS escolhendo shapefile em vez de GreenStar.</p>
          </div></details>` : ""}
      </div>`;
  },

  statsBlock(stats, column) {
    if (!stats || !stats.n) return '<div class="empty">Sem estatísticas.</div>';
    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const cell = (label, value, decimals) =>
      `<div class="stat"><div class="k">${label}</div>
        <div class="v">${Units.num(conv(value), decimals)}</div>
        <div class="d">${unit}</div></div>`;
    return `<div class="stat-grid">
      ${cell("Média", stats.mean)}
      ${cell("Mediana", stats.median)}
      ${cell("Desvio padrão", stats.std)}
      <div class="stat"><div class="k">Coef. de variação</div>
        <div class="v">${Units.num(stats.cv, 1)}</div><div class="d">%</div></div>
      ${cell("Mínimo", stats.min)}
      ${cell("Máximo", stats.max)}
      ${cell("Percentil 5", stats.p05)}
      ${cell("Percentil 95", stats.p95)}
    </div>`;
  },

  renderPreview(node, preview) {
    if (!node || !preview?.rows?.length) {
      if (node) node.innerHTML = '<div class="empty">Sem linhas.</div>';
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
        ? `<p class="hint">Mostrando 10 de ${preview.columns.length} colunas.</p>` : "");
  },

  async runAugmenta() {
    const report = await this.busy(document.getElementById("right-panel"), () =>
      this.api(`/api/datasets/${this.state.selectedId}/augmenta`, { method: "POST" }));
    if (!report) return;
    if (!report.available) { this.toast("Augmenta", report.reason, "warn"); return; }

    const rows = report.classes.map((c) =>
      `<tr><td>${this.escape(c.classe)}</td><td class="num">${Units.num(c.n, 0)}</td>
       <td class="num">${Units.num(c.dose_media)}</td>
       <td class="num">${Units.num(c.desvio)}</td></tr>`).join("");
    const panel = document.getElementById("right-panel");
    const block = document.createElement("div");
    block.className = "panel";
    block.innerHTML = `<h3>Augmenta · vigor × dose</h3>
      <div class="note ${report.amplitude_relativa_pct < 5 ? "atencao" : "ok"}">${
        this.escape(report.leitura)}</div>
      <div class="stat-grid" style="margin:8px 0">
        <div class="stat"><div class="k">Correlação</div>
          <div class="v">${Units.num(report.correlacao, 2)}</div><div class="d">vigor × dose</div></div>
        <div class="stat"><div class="k">Amplitude da dose</div>
          <div class="v">${Units.num(report.amplitude_relativa_pct, 1)}</div><div class="d">%</div></div>
      </div>
      <div class="scroll-x"><table class="data">
        <tr><th>Classe de vigor</th><th>n</th><th>Dose média</th><th>Desvio</th></tr>${rows}
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
      panel.innerHTML = `<div class="panel"><h3>Limpeza</h3>
        <div class="empty">Escolha um conjunto de dados primeiro.</div></div>`;
      return;
    }

    const presets = this.state.catalog.presets;
    const suggested = presets[d.meta.operation] ? d.meta.operation : "minimal";
    const current = this.state.cleanPreset || suggested;
    const config = presets[current];

    panel.innerHTML = `
      <div class="panel">
        <h3>Limpeza de dados</h3>
        ${this.field("Perfil", this.selectInput("clean-preset",
          Object.entries(presets).map(([k, v]) => [k, v.label]), current))}
        <div class="note">${this.escape(config.description)}</div>
        ${this.field("Variável a limpar", this.selectInput("clean-column",
          this.columnOptions(), this.state.colorColumn))}
        ${this.field(`Atraso de fluxo do sensor (s)`,
          this.numberInput("clean-delay", config.corrections.flow_delay_s ?? 0, "0.5", "0"),
          "Segundos entre o corte e a leitura do sensor. Zero desativa a correção.")}
      </div>

      <div class="panel">
        <h3>Filtros</h3>
        <div id="clean-steps"></div>
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-clean">Executar limpeza</button>
        <p class="hint tight">Os dados originais são preservados: a limpeza cria
        conjuntos novos, “limpo” e “removidos”.</p>
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

    /* Parâmetros que são grandezas físicas aparecem na unidade escolhida
     * pelo usuário e voltam ao métrico na hora de enviar. */
    const PARAM_META = {
      min_fraction: { label: "Fração mínima da largura", step: "0.05", suffix: "0–1" },
      max_overlap_pct: { label: "Sobreposição tolerada", step: "5", suffix: "%" },
      buffer_m: { label: "Bordadura", step: "1", suffix: lengthUnit, kind: "length" },
      start_m: { label: "Descartar no início", step: "1", suffix: lengthUnit, kind: "length" },
      end_m: { label: "Descartar no fim", step: "1", suffix: lengthUnit, kind: "length" },
      min_points: { label: "Mínimo de registros", step: "1", suffix: "reg." },
      max_change_pct: { label: "Variação máxima", step: "5", suffix: "%" },
      max_jump_m: { label: "Salto máximo", step: "1", suffix: lengthUnit, kind: "length" },
      k: { label: "Desvios padrão (k)", step: "0.25", suffix: "σ" },
      lower_pct: { label: "Percentil inferior", step: "0.5", suffix: "%" },
      upper_pct: { label: "Percentil superior", step: "0.5", suffix: "%" },
      k_neighbors: { label: "Vizinhos", step: "1", suffix: "pontos" },
      threshold: { label: "Limite de desvio", step: "0.5", suffix: "MAD" },
      method: { label: "Método", choices: [["std", "Desvio padrão"], ["percentile", "Percentil"]] },
      drop_zero: { label: "Descartar zeros", bool: true },
      drop_negative: { label: "Descartar negativos", bool: true },
      drop_duplicates: { label: "Descartar duplicatas", bool: true },
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
        const label = meta.label || (key === "min" ? "Mínimo" : key === "max" ? "Máximo" : key);
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
          ${params || '<p class="hint tight">Sem parâmetros.</p>'}
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
    this.toast("Limpeza concluída",
      `${Units.num(result.report.totais.removidos, 0)} registros removidos ` +
      `(${Units.num(result.report.totais.pct_removido, 1)}%).`);
  },

  renderCleanReport(result) {
    const box = document.getElementById("clean-report");
    if (!box) return;
    const report = result.report;
    const totals = report.totais;
    const column = report.coluna_analisada;
    const { conv, unit } = Units.forColumn(column, this.state.selected?.meta?.operation);
    const before = report.estatisticas.antes;
    const after = report.estatisticas.depois;

    const stepRows = report.etapas.filter((s) => !s.skipped).map((s) =>
      `<tr><td title="${this.escape(s.detail)}">${this.escape(s.label)}</td>
       <td class="num">${Units.num(s.removed, 0)}</td>
       <td class="num">${Units.num(s.removed / totals.entrada * 100, 1)}%</td>
       <td class="num">${Units.num(s.remaining, 0)}</td></tr>`).join("");

    const skipped = report.etapas.filter((s) => s.skipped);

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
        <h3>Laudo da limpeza</h3>
        ${report.leitura.map((f) =>
          `<div class="note ${f.nivel}">${this.escape(f.texto)}</div>`).join("")}
        <div class="stat-grid" style="margin-top:10px">
          <div class="stat"><div class="k">Entraram</div>
            <div class="v">${Units.num(totals.entrada, 0)}</div><div class="d">registros</div></div>
          <div class="stat"><div class="k">Ficaram</div>
            <div class="v">${Units.num(totals.mantidos, 0)}</div><div class="d">registros</div></div>
          <div class="stat"><div class="k">Removidos</div>
            <div class="v">${Units.num(totals.pct_removido, 1)}</div><div class="d">%</div></div>
          <div class="stat"><div class="k">Área trabalhada</div>
            <div class="v">${Units.num(Units.convert.area(totals.area_ha_depois))}</div>
            <div class="d">${Units.label.area()}</div></div>
        </div>
        ${report.correcoes.length ? report.correcoes.map((c) =>
          `<div class="note" style="margin-top:8px">${this.escape(c)}</div>`).join("") : ""}
      </div>

      <div class="panel">
        <h3>Antes e depois</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Estatística</th><th>Antes</th><th>Depois</th><th>Δ</th></tr>
          ${compare(`Média (${unit})`, before.mean, after.mean, null, conv)}
          ${compare(`Mediana (${unit})`, before.median, after.median, null, conv)}
          ${compare(`Desvio (${unit})`, before.std, after.std, null, conv)}
          ${compare("Coef. variação (%)", before.cv, after.cv, 1)}
          ${compare(`Mínimo (${unit})`, before.min, after.min, null, conv)}
          ${compare(`Máximo (${unit})`, before.max, after.max, null, conv)}
        </table></div>
        <h4>Distribuição</h4>
        <div id="clean-hist"></div>
        <p class="hint tight">Cinza: antes. Verde: depois.</p>
      </div>

      <div class="panel">
        <h3>O que cada filtro removeu</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Filtro</th><th>Removidos</th><th>% total</th><th>Restaram</th></tr>
          ${stepRows || '<tr><td colspan="4">Nenhum filtro ativo.</td></tr>'}
        </table></div>
        ${skipped.length ? `<details class="fold" style="margin-top:8px">
          <summary>${skipped.length} filtro(s) não puderam rodar</summary>
          <div class="inner">${skipped.map((s) =>
            `<p class="hint tight"><b>${this.escape(s.label)}</b>: ${this.escape(s.detail)}</p>`
          ).join("")}</div></details>` : ""}
        <h4>Distribuição das remoções</h4>
        <div id="clean-bars"></div>
      </div>

      <div class="panel">
        <h3>Conjuntos gerados</h3>
        <div class="row tight">
          <button id="btn-goto-clean">Ver dados limpos</button>
          ${result.removed ? '<button id="btn-goto-removed">Ver removidos</button>' : ""}
        </div>
      </div>`;

    Charts.histogram(
      document.getElementById("clean-hist"),
      this.scaleHistogram(report.histograma.antes, conv),
      this.scaleHistogram(report.histograma.depois, conv),
    );
    Charts.bars(document.getElementById("clean-bars"),
      report.por_motivo.map((r) => ({ label: r.motivo, value: r.registros })));

    document.getElementById("btn-goto-clean")?.addEventListener("click",
      () => this.selectDataset(result.clean.id));
    document.getElementById("btn-goto-removed")?.addEventListener("click",
      () => this.selectDataset(result.removed.id));
  },

  /* O histograma vem em unidade interna; as bordas precisam acompanhar a
   * unidade escolhida, senão o eixo mostraria kg/ha num gráfico rotulado bu/ac. */
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
      panel.innerHTML = `<div class="panel"><h3>Análise DIFM</h3>
        <div class="empty">Escolha um conjunto com dose aplicada e rendimento.</div></div>`;
      return;
    }

    const columns = d.numeric_columns || [];
    const guessRate = ["applied_rate", "target_rate", "rate"].find((c) => columns.includes(c))
      || columns[0];
    const guessValue = columns.includes("value") ? "value" : columns[0];
    const zoneOptions = [["", "— sem zonas —"],
      ...(d.columns || []).filter((c) => !["lon", "lat", "x", "y"].includes(c))
        .map((c) => [c, this.state.catalog.columns[c] || c])];

    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();
    const yieldNum = Units.rateNumerator(yieldUnit);
    const inputNum = Units.rateNumerator(inputUnit);
    const symbol = Units.currencySymbol();
    const lengthUnit = Units.label.length();

    panel.innerHTML = `
      <div class="panel">
        <h3>Ensaio</h3>
        ${this.field("Coluna de dose aplicada",
          this.selectInput("difm-rate", this.columnOptions(), guessRate))}
        ${this.field("Coluna de rendimento",
          this.selectInput("difm-value", this.columnOptions(), guessValue))}
        ${this.field("Coluna de zona (opcional)",
          this.selectInput("difm-zone", zoneOptions, ""),
          "Com zonas, o app compara taxa única contra taxa variável.")}
      </div>

      <div class="panel">
        <h3>Preços</h3>
        ${this.field(`Preço do produto (${symbol}/${yieldNum})`,
          this.numberInput("difm-price", 0, "0.01", "0"))}
        ${this.field(`Custo do insumo (${symbol}/${inputNum})`,
          this.numberInput("difm-cost", 0, "0.01", "0"),
          "A dose ótima é onde o quilo a mais de insumo deixa de se pagar.")}
      </div>

      <div class="panel">
        <h3>Agregação</h3>
        <div class="row tight">
          ${this.field(`Célula (${lengthUnit})`,
            this.numberInput("difm-cell", Math.round(Units.convert.length(20)), "1", "1"))}
          ${this.field(`Margem de borda (${lengthUnit})`,
            this.numberInput("difm-edge", Math.round(Units.convert.length(6)), "1", "0"))}
        </div>
        <p class="hint tight">A margem descarta a transição entre faixas, onde as
        doses se misturam. Precisa ser maior que metade do espaçamento entre passadas
        para ter efeito.</p>
        ${this.field("Modelos a testar", `<select id="difm-models" multiple size="4">${
          Object.entries(this.state.catalog.response_models).map(([k, v]) =>
            `<option value="${k}" selected>${this.escape(v)}</option>`).join("")}</select>`,
          "O de maior R² é escolhido automaticamente.")}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-difm">Analisar resposta</button>
      </div>

      <div id="difm-report"></div>`;

    document.getElementById("btn-run-difm").addEventListener("click", () => this.runDifm());
    const existing = this.state.reports[`${this.state.selectedId}:difm`];
    if (existing) this.renderDifmReport(existing);
  },

  async runDifm() {
    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();

    /* Os preços entram por unidade de venda (dólar por bushel, por libra) e
     * precisam virar preço por unidade interna antes de ir ao servidor. */
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
      this.toast("Informe o preço", "Sem preço do produto não há dose econômica ótima.", "warn");
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

    const model = report.modelo_escolhido;
    const economy = report.economia || {};
    const yieldUnit = Units.label.yield();
    const inputUnit = Units.label.inputRate();
    const symbol = Units.currencySymbol();
    const areaUnit = Units.label.area();

    /* A dose vem em unidade interna de massa por área; na tela ela aparece
     * na unidade de insumo (lb/ac no padrão canadense). */
    const rate = (v) => Units.num(Units.convert.inputRate(v));
    const yld = (v) => Units.num(Units.convert.yield(v));
    /* Lucro interno é por hectare; converter para a área escolhida. */
    const perArea = (v) => Units.money(v * Units.factor("area", Units.get().area_unit));

    const rateRows = (report.por_dose || []).map((r) =>
      `<tr><td class="num">${rate(r.dose)}</td>
       <td class="num">${Units.num(r.n, 0)}</td>
       <td class="num">${yld(r.rendimento_medio)}</td>
       <td class="num">${r.lucro_medio != null ? perArea(r.lucro_medio) : "—"}</td></tr>`).join("");

    const zones = report.zonas;
    const zoneRows = (zones?.por_zona || []).map((z) => z.erro
      ? `<tr><td>${this.escape(z.zona)}</td><td colspan="4">${this.escape(z.erro)}</td></tr>`
      : `<tr><td>${this.escape(z.zona)}</td>
         <td class="num">${Units.num(z.celulas, 0)}</td>
         <td class="num">${Units.num(z.r2, 3)}</td>
         <td class="num">${rate(z.dose_otima)}</td>
         <td class="num">${perArea(z.lucro_na_otima)}</td></tr>`).join("");

    box.innerHTML = `
      <div class="panel">
        <h3>Recomendação</h3>
        <div class="stat-grid">
          <div class="stat"><div class="k">Dose econômica ótima</div>
            <div class="v">${rate(economy.dose_otima)}</div>
            <div class="d">${inputUnit}</div></div>
          <div class="stat"><div class="k">Rendimento esperado</div>
            <div class="v">${yld(economy.rendimento_na_otima)}</div>
            <div class="d">${yieldUnit}</div></div>
          <div class="stat"><div class="k">Margem na dose ótima</div>
            <div class="v" style="font-size:13px">${perArea(economy.lucro_na_otima)}</div>
            <div class="d">por ${areaUnit}</div></div>
          <div class="stat"><div class="k">Máximo agronômico</div>
            <div class="v">${economy.dose_maximo_agronomico != null
              ? rate(economy.dose_maximo_agronomico) : "—"}</div>
            <div class="d">${inputUnit}</div></div>
        </div>
        ${economy.no_limite_da_faixa ? `<div class="note alerta" style="margin-top:8px">
          A dose ótima caiu no extremo da faixa testada — o ensaio não mostrou o
          ponto de retorno decrescente. Trate como “pelo menos isso”.</div>` : ""}
        ${(report.observacoes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${report.parametros ? `<p class="hint tight" style="margin-top:8px">
          Célula de ${Units.num(Units.convert.length(report.parametros.cell_m), 0)}
          ${Units.label.length()} · margem de borda de
          ${Units.num(Units.convert.length(report.parametros.edge_margin_m), 0)}
          ${Units.label.length()}.</p>` : ""}
      </div>

      <div class="panel">
        <h3>Curva de resposta</h3>
        <div id="difm-curve"></div>
        <div class="stat-grid" style="margin-top:10px">
          <div class="stat"><div class="k">Modelo</div>
            <div class="v" style="font-size:13px">${this.escape(model.label)}</div>
            <div class="d">${model.n} células</div></div>
          <div class="stat"><div class="k">R²</div>
            <div class="v">${Units.num(model.r2, 3)}</div>
            <div class="d">RMSE ${yld(model.rmse)} ${yieldUnit}</div></div>
        </div>
        ${model.message ? `<div class="note atencao" style="margin-top:8px">${
          this.escape(model.message)}</div>` : ""}
        <details class="fold" style="margin-top:8px">
          <summary>Todos os modelos testados</summary>
          <div class="inner scroll-x"><table class="data">
            <tr><th>Modelo</th><th>R²</th><th>RMSE</th></tr>
            ${(report.modelos_avaliados || []).map((m) =>
              `<tr><td>${this.escape(m.label)}</td>
               <td class="num">${Units.num(m.r2, 3)}</td>
               <td class="num">${yld(m.rmse)}</td></tr>`).join("")}
          </table></div>
        </details>
      </div>

      <div class="panel">
        <h3>Resposta por dose</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Dose (${inputUnit})</th><th>Células</th>
              <th>Rend. (${yieldUnit})</th><th>Margem/${areaUnit}</th></tr>
          ${rateRows}
        </table></div>
      </div>

      ${zones ? `<div class="panel">
        <h3>Por zona · ${this.escape(zones.coluna_zona)}</h3>
        <div class="scroll-x"><table class="data">
          <tr><th>Zona</th><th>Células</th><th>R²</th>
              <th>Ótima (${inputUnit})</th><th>Margem/${areaUnit}</th></tr>
          ${zoneRows}
        </table></div>
        ${zones.comparacao?.leitura ? `
          <div class="note ${zones.comparacao.ganho_por_ha > 0 ? "ok" : "atencao"}"
               style="margin-top:10px">${this.escape(zones.comparacao.leitura)}</div>
          <div class="stat-grid" style="margin-top:8px">
            <div class="stat"><div class="k">Melhor dose única</div>
              <div class="v">${rate(zones.comparacao.dose_unica_otima)}</div>
              <div class="d">${inputUnit}</div></div>
            <div class="stat"><div class="k">Ganho da taxa variável</div>
              <div class="v" style="font-size:13px">${perArea(zones.comparacao.ganho_por_ha)}</div>
              <div class="d">por ${areaUnit}</div></div>
          </div>` : ""}
      </div>` : ""}

      <div class="panel">
        <button class="wide" id="btn-difm-to-rx">Gerar prescrição a partir desta análise</button>
      </div>`;

    Charts.responseCurve(document.getElementById("difm-curve"), report.curva, economy, {
      yieldConv: (v) => Units.convert.yield(v),
      rateConv: (v) => Units.convert.inputRate(v),
      yieldUnit,
      rateUnit: inputUnit,
    });

    document.getElementById("btn-difm-to-rx").addEventListener("click", () =>
      this.prescriptionFromDifm(report));
  },

  /* Transforma o resultado da análise numa prescrição: dose ótima por zona
   * quando há zonas, ou dose única quando não há. */
  prescriptionFromDifm(report) {
    const zones = report.zonas?.por_zona?.filter((z) => !z.erro && z.dose_otima != null);
    if (!zones?.length) {
      const dose = report.economia?.dose_otima;
      if (dose == null) { this.toast("Sem dose", "A análise não produziu dose ótima.", "warn"); return; }
      this.state.pendingRx = { uniform: dose };
      this.toast("Dose levada para exportação",
        `Taxa única de ${Units.num(Units.convert.inputRate(dose))} ${Units.label.inputRate()}. ` +
        "Vá até a aba Exportar.");
    } else {
      this.state.pendingRx = {
        byZone: Object.fromEntries(zones.map((z) => [z.zona, z.dose_otima])),
        zoneColumn: report.zonas.coluna_zona,
      };
      this.toast("Doses por zona levadas para exportação",
        zones.map((z) => `${z.zona}: ${Units.num(Units.convert.inputRate(z.dose_otima))}`).join(" · "));
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

    /* Doses sugeridas em torno de uma dose usual, na unidade do usuário —
     * um ensaio útil precisa cobrir bem abaixo e bem acima da prática atual.
     * O passo é arredondado para um número redondo na unidade escolhida:
     * ninguém programa o monitor em 44,8 lb/ac. */
    const typical = Units.convert.inputRate(100);
    const step = this.roundStep(typical / 2);
    const suggested = [0, 1, 2, 3, 4].map((i) => i * step).join(", ");

    panel.innerHTML = `
      <div class="panel">
        <h3>Contorno do talhão</h3>
        ${this.field("A partir de um conjunto carregado", this.selectInput("design-source",
          [["", "— escolher —"], ...datasets], this.state.selectedId || ""),
          "Use um shapefile de contorno, ou um mapa de colheita (o app usa o casco dos pontos).")}
        <div class="row tight">
          <button class="small" id="btn-draw-boundary">Desenhar no mapa</button>
          <button class="small" id="btn-clear-boundary">Limpar desenho</button>
        </div>
        <p class="hint tight" id="draw-status"></p>
      </div>

      <div class="panel">
        <h3>Tratamentos</h3>
        ${this.field(`Doses a testar (${inputUnit}, separadas por vírgula)`,
          `<input type="text" id="design-rates" value="${suggested}">`,
          "Pelo menos três, e vale incluir uma faixa sem aplicação como testemunha.")}
        <div class="row tight">
          ${this.field(`Largura do implemento (${lengthUnit})`,
            this.numberInput("design-width", defaultWidth, "1", "1"))}
          ${this.field("Passadas por faixa",
            this.numberInput("design-passes", 2, "1", "1"))}
        </div>
        <div class="row tight">
          ${this.field("Blocos (repetições)", this.numberInput("design-blocks", 4, "1", "1"))}
          ${this.field(`Bordadura (${lengthUnit})`,
            this.numberInput("design-buffer", Math.round(Units.convert.length(15)), "1", "0"))}
        </div>
        ${this.field("Direção das faixas (graus, vazio = lado mais longo)",
          `<input type="number" id="design-angle" step="1" placeholder="automático">`)}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-design">Gerar desenho</button>
        <p class="hint tight">Duas passadas por faixa deixam a passada central
        livre do efeito das doses vizinhas — é o que permite descartar a borda
        na análise sem perder o tratamento inteiro.</p>
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
    status.textContent = "Clique nos vértices do talhão. Clique em “Gerar desenho” ao fechar.";

    const handler = (event) => {
      if (!this.state.drawing) { MapView.offClick(handler); return; }
      this.state.drawing.push([event.latlng.lng, event.latlng.lat]);
      MapView.setPolygons([this.state.drawing.concat([this.state.drawing[0]])], { fill: true });
      status.textContent =
        `${this.state.drawing.length} vértice(s). Mínimo de 3 para fechar o talhão.`;
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
      this.toast("Doses insuficientes",
        `Informe ao menos três doses em ${inputUnit}.`, "warn");
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
    else { this.toast("Sem contorno", "Escolha um conjunto ou desenhe o talhão.", "warn"); return; }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/design", { method: "POST", body }));
    if (!result) return;

    this.state.design = result;
    MapView.setFeatures(result.features, "dose");
    MapView.fitOverlays();
    this.renderDesignReport(result);
    this.toast("Ensaio desenhado",
      `${result.summary.faixas} faixas em ${result.summary.blocos} bloco(s).`);
  },

  renderDesignReport(result) {
    const box = document.getElementById("design-report");
    if (!box) return;
    const s = result.summary;
    const inputUnit = Units.label.inputRate();
    const lengthUnit = Units.label.length();
    const areaUnit = Units.label.area();

    const reps = Object.entries(s.repeticoes_por_dose).map(([dose, count]) =>
      `<tr><td class="num">${Units.num(Units.convert.inputRate(Number(dose)))}</td>
       <td class="num">${count}</td></tr>`).join("");

    box.innerHTML = `
      <div class="panel">
        <h3>Desenho gerado</h3>
        ${(result.warnings || []).map((w) =>
          `<div class="note atencao">${this.escape(w)}</div>`).join("")}
        <div class="stat-grid" style="margin-top:8px">
          <div class="stat"><div class="k">Faixas</div>
            <div class="v">${s.faixas}</div><div class="d">${s.blocos} bloco(s)</div></div>
          <div class="stat"><div class="k">Largura da faixa</div>
            <div class="v">${Units.num(Units.convert.length(s.largura_faixa_m), 0)}</div>
            <div class="d">${lengthUnit} · ${s.passadas_por_faixa} passada(s)</div></div>
          <div class="stat"><div class="k">Área do ensaio</div>
            <div class="v">${Units.num(Units.convert.area(s.area_total_ha))}</div>
            <div class="d">${areaUnit}</div></div>
          <div class="stat"><div class="k">Direção</div>
            <div class="v">${Units.num(s.direcao_graus, 0)}°</div>
            <div class="d">azimute</div></div>
        </div>
        <h4>Repetições por dose</h4>
        <table class="data">
          <tr><th>Dose (${inputUnit})</th><th>Faixas</th></tr>${reps}
        </table>
      </div>
      <div class="panel">
        <h3>Linha de orientação</h3>
        <p class="hint tight">A linha AB precisa seguir a mesma direção das faixas;
        se o operador entrar noutro ângulo, as passadas cruzam os tratamentos e o
        ensaio se perde.</p>
        <button class="wide" id="btn-make-ab" style="margin-bottom:6px">
          Gerar linha AB na direção do ensaio
        </button>
        <div id="ab-status"></div>
      </div>
      <div class="panel">
        <button class="primary wide" id="btn-design-export">Exportar este ensaio</button>
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
      this.toast("Sem ensaio", "Gere o desenho antes de criar a linha AB.", "warn");
      return;
    }
    const body = {
      angle_deg: this.state.design.summary.direcao_graus,
      name: "AB do ensaio",
    };
    if (this.state.drawing?.length >= 3) body.boundary = this.state.drawing;
    else body.boundary_dataset_id = this.value("design-source") || this.state.selectedId;

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/guidance", { method: "POST", body }));
    if (!result) return;

    this.state.guidance = [result.line];
    this.renderGuidanceStatus();
    this.drawDesignWithGuidance();
    this.toast("Linha AB criada", `Rumo de bússola ${Units.num(result.line.heading, 1)}°.`);
  },

  renderGuidanceStatus() {
    const box = document.getElementById("ab-status");
    if (!box) return;
    box.innerHTML = (this.state.guidance || []).map((line) => `
      <div class="note ok">
        <b>${this.escape(line.name)}</b> — rumo ${Units.num(line.heading, 1)}°<br>
        <span style="font:11px var(--mono)">
          A ${line.a[1].toFixed(6)}, ${line.a[0].toFixed(6)}<br>
          B ${line.b[1].toFixed(6)}, ${line.b[0].toFixed(6)}
        </span>
      </div>`).join("");
  },

  /* Desenha as faixas e, por cima, a linha AB — é a conferência visual de
   * que as duas coisas estão na mesma direção. */
  drawDesignWithGuidance() {
    if (!this.state.design) return;
    MapView.setFeatures(this.state.design.features, "dose");
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
        <h3>Como exportar</h3>
        ${this.field("Modo", this.selectInput("exp-mode", [
          ["package", "Pacote pronto para o monitor"],
          ["files", "Arquivos avulsos"],
        ], mode), mode === "package"
          ? "Monta a pasta do pen drive no arranjo que o monitor procura, com contorno, linhas AB e prescrição."
          : "Gera só os arquivos escolhidos, sem estrutura de pasta.")}
        ${!hasDesign && !hasDataset ? '<div class="empty">Nada para exportar ainda.</div>' : ""}
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

  /* ---------------------------------------------------- modo pacote ---- */

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
        <h3>Monitor de destino</h3>
        ${this.field("Plataforma", this.selectInput("pkg-monitor",
          monitors.map((m) => [m.key, m.label]), chosen))}
        <div id="pkg-accepts"></div>
      </div>

      <div class="panel">
        <h3>O que vai no pacote</h3>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-rx"${hasDesign ? " checked" : ""}
            ${hasDesign ? "" : "disabled"}>
          <span>Prescrição${hasDesign
            ? ` — ${this.state.design.summary.faixas} faixas do ensaio`
            : " (desenhe o ensaio antes)"}</span></label>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-boundary" checked>
          <span>Contorno do talhão</span></label>
        <label class="inline" style="margin-bottom:7px">
          <input type="checkbox" id="pkg-guidance"${hasGuidance ? " checked" : ""}
            ${hasGuidance ? "" : "disabled"}>
          <span>Linhas AB${hasGuidance
            ? ` — ${this.state.guidance.length} linha(s)`
            : " (gere na aba Desenhar ensaio)"}</span></label>
        ${this.field("Contorno vindo de", this.selectInput("pkg-boundary-src",
          [["", "— desenho / ensaio atual —"], ...datasets],
          this.state.selectedId || ""),
          "Um contorno importado do monitor é mais fiel que o casco dos pontos.")}
      </div>

      <div class="panel">
        <h3>Dose</h3>
        ${this.field("Tipo", this.selectInput("pkg-kind",
          Object.entries(this.state.catalog.rate_kinds), "mass"))}
        ${this.field("Unidade gravada", this.selectInput("pkg-unit",
          this.state.units.groups.rate_mass.units.map((u) => [u.key, u.label]),
          Units.label.inputRate()))}
        ${this.field("Cultura (unidades em bushel)", this.selectInput("pkg-crop",
          this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}
        ${this.field(`Célula da grade ISOXML (${lengthUnit})`,
          this.numberInput("pkg-cell", Math.round(Units.convert.length(10)), "1", "1"),
          "Célula menor gera arquivo maior; 10 m atende quase todo terminal.")}
      </div>

      <div class="panel">
        <h3>Identificação</h3>
        <div class="row tight">
          ${this.field("Talhão", `<input type="text" id="pkg-field" value="Talhao">`)}
          ${this.field("Tarefa", `<input type="text" id="pkg-task" value="Prescricao">`)}
        </div>
        <div class="row tight">
          ${this.field("Cliente", `<input type="text" id="pkg-customer" value="AgroSuite">`)}
          ${this.field("Fazenda", `<input type="text" id="pkg-farm" value="Fazenda">`)}
        </div>
        ${this.field("Produto", `<input type="text" id="pkg-product" value="Produto">`)}
      </div>

      <div class="panel">
        <button class="primary wide" id="btn-run-package">Montar pacote</button>
      </div>`;

    const showAccepts = () => {
      const monitor = monitors.find((m) => m.key === this.value("pkg-monitor"));
      const node = document.getElementById("pkg-accepts");
      if (!monitor) { node.innerHTML = ""; return; }
      const labels = this.state.catalog.artifact_labels;
      node.innerHTML = `
        <p class="hint tight">Aceita: ${monitor.accepts.map((a) => labels[a] || a).join(" · ")}</p>
        <p class="hint tight">Campo de dose no shapefile: <b>${monitor.rate_field}</b></p>
        <details class="fold" style="margin-top:6px">
          <summary>Como carregar no ${this.escape(monitor.label)}</summary>
          <div class="inner">${monitor.instructions.map((i) =>
            `<p class="hint tight">• ${this.escape(i)}</p>`).join("")}</div>
        </details>`;
      /* Desabilita o que a plataforma não recebe, em vez de deixar o usuário
       * marcar e só descobrir depois que aquilo não foi gerado. */
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
    showAccepts();
  },

  async runPackage() {
    const body = {
      monitor: this.value("pkg-monitor"),
      rate_property: "dose",
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
      this.toast("Pacote vazio", "Marque ao menos um item para incluir.", "warn");
      return;
    }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/export/package", { method: "POST", body }));
    if (!result) return;
    this.renderPackageReport(result);
    this.toast("Pacote montado", `${result.contents.length} item(ns) para ${result.monitor_label}.`);
  },

  renderPackageReport(result) {
    const box = document.getElementById("export-report");
    const labels = this.state.catalog.artifact_labels;

    box.innerHTML = `
      <div class="panel">
        <h3>Pacote para ${this.escape(result.monitor_label)}</h3>
        <a class="btn primary wide" style="display:block;text-decoration:none"
           href="${result.bundle.download_url}" download>
          Baixar ${this.escape(result.bundle.filename)}
        </a>
        <p class="hint tight">Gravado em:<br>
          <code style="font-size:11px">${this.escape(result.folder)}</code></p>
        <div class="scroll-x" style="margin-top:10px"><table class="data">
          <tr><th>Arquivo</th><th>Conteúdo</th></tr>
          ${result.contents.map((c) => `<tr>
            <td style="font-family:var(--mono);font-size:11px">${this.escape(c.path)}</td>
            <td style="text-align:left">${this.escape(
              c.artifact === "isoxml" ? "ISOXML" : (labels[c.artifact] || c.artifact))}
              — ${this.escape(c.detail)}</td></tr>`).join("")}
        </table></div>
        ${(result.observacoes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${(result.skipped || []).map((s) =>
          `<div class="note atencao" style="margin-top:6px">${this.escape(s)}</div>`).join("")}
      </div>
      ${this.verificationPanel(result.verificacao)}
      <div class="panel">
        <h3>Como carregar</h3>
        ${result.instructions.map((step, i) =>
          `<p class="hint tight">${i + 1}. ${this.escape(step)}</p>`).join("")}
        <p class="hint tight" style="margin-top:8px">O arquivo <b>LEIA-ME.txt</b>
        dentro do pacote repete estas instruções, para consultar na cabine.</p>
      </div>`;
  },

  /* A verificação roda sobre os arquivos já gravados. Falha em vermelho é
   * impedimento real; atenção em amarelo depende do firmware do display. */
  verificationPanel(check) {
    if (!check) return "";
    const noteClass = { ok: "ok", atencao: "atencao", falha: "alerta" }[check.verdict] || "";
    const icon = { ok: "✓", atencao: "!", falha: "✕" };

    const groups = (check.groups || []).map((group) => {
      const failed = group.checks.filter((c) => c.status !== "ok");
      const rows = group.checks.map((c) => `
        <div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="flex:none;width:14px;text-align:center;font-weight:700;color:${
            c.status === "ok" ? "var(--ok)"
            : c.status === "atencao" ? "var(--warn)" : "var(--danger)"
          }">${icon[c.status]}</span>
          <span style="min-width:0">
            <b style="font-size:12px">${this.escape(c.item)}</b>
            <span style="font-size:12px;color:var(--text-muted)"> — ${this.escape(c.message)}</span>
            ${c.fix ? `<br><span class="hint tight">${this.escape(c.fix)}</span>` : ""}
          </span>
        </div>`).join("");
      return `<details class="fold"${failed.length ? " open" : ""}>
        <summary>${this.escape(group.arquivo)} · ${this.escape(group.tipo)}${
          failed.length ? ` — ${failed.length} ponto(s) a ver` : " — tudo conferido"}</summary>
        <div class="inner">${rows}</div>
      </details>`;
    }).join("");

    return `
      <div class="panel">
        <h3>Verificação antes de levar</h3>
        <div class="note ${noteClass}">${this.escape(check.summary)}</div>
        <div class="stat-grid" style="margin:10px 0">
          <div class="stat"><div class="k">Conferidos</div>
            <div class="v" style="color:var(--ok)">${check.totals.ok}</div>
            <div class="d">sem ressalva</div></div>
          <div class="stat"><div class="k">A confirmar</div>
            <div class="v" style="color:var(--warn)">${check.totals.atencao}</div>
            <div class="d">na tela do monitor</div></div>
          <div class="stat"><div class="k">Impedimentos</div>
            <div class="v" style="color:var(--danger)">${check.totals.falha}</div>
            <div class="d">o monitor recusaria</div></div>
          <div class="stat"><div class="k">Destino</div>
            <div class="v" style="font-size:12px">${this.escape(check.monitor_label)}</div>
            <div class="d">perfil aplicado</div></div>
        </div>
        ${groups}
        <p class="hint tight" style="margin-top:10px">${this.escape(check.ressalva)}</p>
      </div>`;
  },

  /* -------------------------------------------- modo arquivos avulsos -- */

  renderFilesForm() {
    const box = document.getElementById("export-form");
    const hasDesign = !!this.state.design;
    const hasDataset = !!this.state.selectedId;
    const brands = this.state.catalog.brands.filter((b) => b.export_formats.length);
    const source = this.state.exportSource || (hasDesign ? "design" : "dataset");
    const lengthUnit = Units.label.length();

    box.innerHTML = `
      <div class="panel">
        <h3>Origem</h3>
        ${this.field("O que exportar", this.selectInput("exp-source", [
          ...(hasDesign ? [["design", "Ensaio desenhado (prescrição)"]] : []),
          ...(hasDataset ? [["dataset", `Dados: ${this.state.selected?.label ?? ""}`]] : []),
        ], source))}
      </div>
      <div class="panel">
        <h3>Formatos</h3>
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
        <h3>Ajustes</h3>
        ${this.field("Nome do campo de dose conforme", this.selectInput("exp-brand",
          brands.map((b) => [b.key, b.label]), "john_deere"))}
        ${this.field("Tipo de dose", this.selectInput("exp-kind",
          Object.entries(this.state.catalog.rate_kinds), "mass"))}
        ${this.field("Unidade gravada", this.selectInput("exp-unit",
          this.state.units.groups.rate_mass.units.map((u) => [u.key, u.label]),
          Units.label.inputRate()))}
        ${this.field("Cultura", this.selectInput("exp-crop",
          this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}
        <div class="row tight">
          ${this.field(`Célula ISOXML (${lengthUnit})`,
            this.numberInput("exp-cell", Math.round(Units.convert.length(10)), "1", "1"))}
          ${this.field("Nome", `<input type="text" id="exp-task" value="Prescricao">`)}
        </div>
        <input type="hidden" id="exp-field" value="Talhao">
        <input type="hidden" id="exp-product" value="Produto">
      </div>
      <div class="panel">
        <button class="primary wide" id="btn-run-export">Gerar arquivos</button>
      </div>`;

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
  },

  async runExport() {
    const formats = [
      ["shapefile", this.checked("fmt-shapefile")],
      ["isoxml", this.checked("fmt-isoxml")],
      ["csv", this.checked("fmt-csv")],
      ["geojson", this.checked("fmt-geojson")],
    ].filter(([, on]) => on).map(([f]) => f);

    if (!formats.length) { this.toast("Escolha um formato", "", "warn"); return; }

    const body = {
      formats,
      brand: this.value("exp-brand"),
      rate_property: "dose",
      rate_kind: this.value("exp-kind"),
      rate_unit: this.value("exp-unit"),
      crop: this.value("exp-crop"),
      cell_m: Units.toInternal.length(this.number("exp-cell", 10)),
      task_name: this.value("exp-task") || "Prescricao",
      field_name: this.value("exp-field") || "Talhao",
      product_name: this.value("exp-product") || "Produto",
    };

    if (this.value("exp-source") === "design") {
      if (!this.state.design) { this.toast("Sem ensaio", "Desenhe o ensaio primeiro.", "warn"); return; }
      body.features = this.state.design.features;
    } else {
      if (!this.state.selectedId) { this.toast("Sem dados", "Escolha um conjunto.", "warn"); return; }
      body.dataset_id = this.state.selectedId;
      if (formats.includes("isoxml")) {
        this.toast("ISOXML precisa de polígonos",
          "Use o pacote a partir do ensaio desenhado para gerar ISOXML.", "warn");
        return;
      }
    }

    const result = await this.busy(document.getElementById("right-panel"), () =>
      this.api("/api/export", { method: "POST", body }));
    if (!result) return;
    this.renderExportReport(result);
    this.toast("Arquivos gerados", `${result.bundle.entries.length} arquivo(s) no pacote.`);
  },

  renderExportReport(result) {
    const box = document.getElementById("export-report");
    if (!box) return;
    const rows = result.outputs.map((o) => {
      const detail = o.rate_field ? `campo de dose: ${o.rate_field}`
        : o.cols ? `grade ${o.rows} × ${o.cols}, ${Units.num(o.cells_with_rate, 0)} células com dose`
        : o.features != null ? `${Units.num(o.features, 0)} feições`
        : o.rows != null ? `${Units.num(o.rows, 0)} linhas` : "";
      return `<tr><td>${this.escape(o.format)}</td><td>${this.escape(detail)}</td></tr>`;
    }).join("");
    const warnings = result.outputs.flatMap((o) => o.warnings || []);

    box.innerHTML = `
      <div class="panel">
        <h3>Arquivos gerados</h3>
        <a class="btn primary wide" style="display:block;text-decoration:none"
           href="${result.bundle.download_url}" download>
          Baixar ${this.escape(result.bundle.filename)}
        </a>
        <p class="hint tight">Gravado em:<br>
          <code style="font-size:11px">${this.escape(result.folder)}</code></p>
        <div class="scroll-x" style="margin-top:10px"><table class="data">
          <tr><th>Formato</th><th>Detalhe</th></tr>${rows}
        </table></div>
        ${(result.observacoes || []).map((o) =>
          `<div class="note" style="margin-top:8px">${this.escape(o)}</div>`).join("")}
        ${warnings.map((w) =>
          `<div class="note atencao" style="margin-top:6px">${this.escape(w)}</div>`).join("")}
      </div>`;
  },
});

/* ======================================================================
 * Importação e diálogos
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
      this.toast("Arquivo carregado",
        `${result.meta.brand_label} · ${Units.num(result.rows, 0)} registros.`);
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

    /* Arrastar e soltar sobre o mapa. */
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
      const result = await this.busy(document.getElementById("dlg-path"), () =>
        this.api("/api/import/path", { method: "POST", body: { path } }));
      if (!result) return;
      document.getElementById("dlg-path").close();
      await this.refreshDatasets();
      await this.selectDataset(result.id);
      this.toast("Carregado",
        `${result.meta.brand_label} · ${Units.num(result.rows, 0)} registros.`);
    });
    document.getElementById("btn-import-units-apply")
      .addEventListener("click", () => this.applyImportUnits());
  },

  async browse(path) {
    const payload = await this.api(`/api/browse?path=${encodeURIComponent(path || "")}`)
      .catch((err) => { this.toast("Não deu certo", err.message, "error"); return null; });
    if (!payload) return;

    this.state.browseParent = payload.parent;
    document.getElementById("path-current").textContent = payload.path;
    const box = document.getElementById("path-browser");
    box.innerHTML = "";

    if (!payload.entries.length) {
      box.innerHTML = '<div class="empty">Nada que o app consiga abrir nesta pasta.</div>';
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
      <p class="hint">Os dados ficam guardados sempre em métrico; estas escolhas
      mudam apenas como os números aparecem e em que unidade os arquivos são gravados.</p>
      ${this.field("Rendimento", this.selectInput("u-yield", options("rate_mass"), prefs.yield_unit))}
      ${this.field("Dose de insumo", this.selectInput("u-input", options("rate_mass"), prefs.input_rate_unit))}
      ${this.field("Área", this.selectInput("u-area", options("area"), prefs.area_unit))}
      ${this.field("Comprimento e largura", this.selectInput("u-length", options("length"), prefs.length_unit))}
      ${this.field("Velocidade", this.selectInput("u-speed", options("speed"), prefs.speed_unit))}
      ${this.field("Massa", this.selectInput("u-mass", options("mass"), prefs.mass_unit))}
      ${this.field("Cultura (peso do bushel)", this.selectInput("u-crop",
        this.state.units.crops.map((c) => [c.key, `${c.label} — ${c.bushel_kg.toFixed(2)} kg/bu`]),
        prefs.crop))}
      ${this.field("Moeda", this.selectInput("u-currency",
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
    const options = (group) => [["", "— manter como está —"],
      ...groups[group].units.map((u) => [u.key, u.label])];

    document.getElementById("import-units-body").innerHTML = `
      ${this.field("Rendimento / dose no arquivo",
        this.selectInput("iu-rate", options("rate_mass"), ""))}
      ${this.field("Velocidade no arquivo", this.selectInput("iu-speed", options("speed"), ""))}
      ${this.field("Largura e distância no arquivo",
        this.selectInput("iu-length", options("length"), ""))}
      ${this.field("Cultura (para bushel)", this.selectInput("iu-crop",
        this.state.units.crops.map((c) => [c.key, c.label]), Units.get().crop))}`;
    document.getElementById("dlg-import-units").showModal();
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
      this.toast("Nada a converter", "Escolha ao menos uma unidade.", "warn");
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
    this.toast("Unidades convertidas", result.conversoes.join("\n"));
  },
});

document.addEventListener("DOMContentLoaded", () => App.init());
