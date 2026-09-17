/* Conversão e formatação de unidades no cliente.
 *
 * O servidor fala sempre em unidades internas (kg/ha, ha, m, km/h, kg). Toda
 * a conversão para o que o usuário escolheu acontece aqui, na hora de
 * exibir — assim nenhum número guardado depende da preferência da tela, e
 * trocar de acre para hectare não exige recarregar nada. */

const Units = (() => {
  let catalog = null;
  let prefs = null;

  /* Peso de teste do bushel, em kg, por cultura. Vem do servidor. */
  function bushelKg(cropKey) {
    const crop = (catalog?.crops || []).find((c) => c.key === cropKey);
    return crop ? crop.bushel_kg : 25.4012;
  }

  /* Fator que leva a unidade escolhida à interna. O bushel é resolvido aqui
   * porque depende da cultura, que o usuário pode trocar a qualquer momento. */
  function factor(group, unitKey) {
    const info = catalog?.groups?.[group];
    if (!info) return 1;
    const entry = info.units.find((u) => u.key === unitKey);
    if (!entry) return 1;
    if (entry.factor != null) return entry.factor;
    const weight = bushelKg(prefs.crop);
    if (group === "mass") return weight;
    if (group === "rate_mass") return weight / catalog.bushel_kg_per_acre_divisor;
    return 1;
  }

  function init(unitCatalog, presetKey) {
    catalog = unitCatalog;
    applyPreset(presetKey || catalog.default_preset);
  }

  function applyPreset(key) {
    const preset = catalog.presets[key] || catalog.presets[catalog.default_preset];
    prefs = { preset: key, ...preset };
    return prefs;
  }

  function set(key, value) { prefs[key] = value; }
  function get() { return prefs; }
  function all() { return catalog; }

  function currencySymbol() {
    const entry = (catalog?.currencies || []).find((c) => c.key === prefs.currency);
    return entry ? entry.symbol : "$";
  }

  /* --- conversões nomeadas, uma por grandeza exibida na interface --- */

  const convert = {
    yield: (v) => (v == null ? null : v / factor("rate_mass", prefs.yield_unit)),
    inputRate: (v) => (v == null ? null : v / factor("rate_mass", prefs.input_rate_unit)),
    area: (v) => (v == null ? null : v / factor("area", prefs.area_unit)),
    length: (v) => (v == null ? null : v / factor("length", prefs.length_unit)),
    speed: (v) => (v == null ? null : v / factor("speed", prefs.speed_unit)),
    mass: (v) => (v == null ? null : v / factor("mass", prefs.mass_unit)),
  };

  /* Caminho inverso: da unidade da tela para a interna, ao enviar ao servidor. */
  const toInternal = {
    yield: (v) => (v == null ? null : v * factor("rate_mass", prefs.yield_unit)),
    inputRate: (v) => (v == null ? null : v * factor("rate_mass", prefs.input_rate_unit)),
    area: (v) => (v == null ? null : v * factor("area", prefs.area_unit)),
    length: (v) => (v == null ? null : v * factor("length", prefs.length_unit)),
    speed: (v) => (v == null ? null : v * factor("speed", prefs.speed_unit)),
  };

  const label = {
    yield: () => prefs.yield_unit,
    inputRate: () => prefs.input_rate_unit,
    area: () => prefs.area_unit,
    length: () => prefs.length_unit,
    speed: () => prefs.speed_unit,
    mass: () => prefs.mass_unit,
  };

  /* Operações em que a variável principal é produção colhida. Em todas as
   * outras — aplicação, plantio, prescrição — ela é dose de insumo, e sair
   * mostrando lb/ac de fertilizante como bu/ac de grão é erro grosseiro. */
  const YIELD_OPERATIONS = new Set(["harvest"]);

  /* Escolhe a conversão certa a partir do nome canônico da coluna e do tipo
   * de operação do dataset. */
  function forColumn(column, operation) {
    const isYield = operation == null
      ? true                              // sem contexto, rendimento é o padrão
      : YIELD_OPERATIONS.has(operation);
    switch (column) {
      case "value":
        return isYield
          ? { conv: convert.yield, unit: label.yield(), kind: "yield" }
          : { conv: convert.inputRate, unit: label.inputRate(), kind: "inputRate" };
      case "target_rate":
      case "applied_rate":
        return { conv: convert.inputRate, unit: label.inputRate(), kind: "inputRate" };
      case "speed_kmh":
        return { conv: convert.speed, unit: label.speed(), kind: "speed" };
      case "swath_m":
      case "distance_m":
      case "elev_m":
        return { conv: convert.length, unit: label.length(), kind: "length" };
      case "moisture_pct":
        return { conv: (v) => v, unit: "%", kind: null };
      default:
        return { conv: (v) => v, unit: "", kind: null };
    }
  }

  /* Formatação numérica em português, com casas decimais proporcionais à
   * magnitude — 0,84 precisa de duas casas; 12.553 não precisa de nenhuma. */
  function num(value, decimals) {
    if (value == null || !isFinite(value)) return "—";
    let places = decimals;
    if (places == null) {
      const abs = Math.abs(value);
      // Zero exato não ganha casas decimais: "0,000" numa tabela de doses
      // sugere precisão que não existe.
      places = abs === 0 ? 0 : abs >= 1000 ? 0 : abs >= 100 ? 1 : abs >= 1 ? 2 : 3;
    }
    return value.toLocaleString("pt-BR", {
      minimumFractionDigits: places,
      maximumFractionDigits: places,
    });
  }

  function money(value, decimals = 2) {
    if (value == null || !isFinite(value)) return "—";
    return `${currencySymbol()} ${num(value, decimals)}`;
  }

  /* Valor com unidade, já convertido a partir do interno. */
  function show(value, kind, decimals) {
    const converted = convert[kind] ? convert[kind](value) : value;
    const unit = label[kind] ? label[kind]() : "";
    return `${num(converted, decimals)}${unit ? " " + unit : ""}`;
  }


  /* Massa (kg) e área (ha) por trás de uma unidade de dose como "bu/ac".
   * Preço é sempre cotado pelo numerador — dólar por bushel, por libra, por
   * saca — então converter um preço exige separar as duas metades. */
  const MASS_PART = { kg: 1, t: 1000, sc: 60, lb: 0.45359237, g: 0.001, bu: null };
  const AREA_PART = { ha: 1, ac: 0.40468564224, alq: 2.42, m2: 1e-4 };

  function rateUnitParts(unitKey) {
    const [num, den] = String(unitKey || "").split("/");
    let mass = MASS_PART[num];
    if (mass === null || mass === undefined) {
      mass = num === "bu" ? bushelKg(prefs.crop) : null;
    }
    return { massKg: mass, areaHa: AREA_PART[den] ?? null };
  }

  /* Preço cotado por unidade do numerador -> preço por kg (interno). */
  function priceToInternal(price, rateUnitKey) {
    const { massKg } = rateUnitParts(rateUnitKey);
    return massKg ? price / massKg : price;
  }

  function priceFromInternal(pricePerKg, rateUnitKey) {
    const { massKg } = rateUnitParts(rateUnitKey);
    return massKg ? pricePerKg * massKg : pricePerKg;
  }

  /* Rótulo do numerador, para escrever "US$ / bu" no formulário. */
  function rateNumerator(unitKey) {
    return String(unitKey || "").split("/")[0] || "";
  }

  return {
    init, applyPreset, set, get, all, factor, forColumn,
    convert, toInternal, label, num, money, show, currencySymbol, bushelKg,
    rateUnitParts, priceToInternal, priceFromInternal, rateNumerator,
  };
})();
