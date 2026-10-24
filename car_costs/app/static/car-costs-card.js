/*
 * Carte Lovelace « Frais de voiture » : voiture, batterie, santé et diagnostic.
 *
 * Copiée par l'add-on dans /config/www ; ressource à déclarer :
 *   url: /local/car-costs-card.js   type: module
 * Puis dans un tableau de bord :
 *   type: custom:car-costs-card
 *
 * Données : capteurs sensor.frais_voiture_* publiés par l'add-on, et entités
 * OVMS (charge, autonomie, 12 V, pneus) trouvées automatiquement ou indiquées
 * sous `entities:`.
 */

const VERSION = "0.4.0";

// Recherche automatique des entités OVMS (identifiant HA, points remplacés par _).
const DISCOVERY = {
  soc: [/xvu_b_soc|v_b_soc(?!h)/, /(^|_)soc($|_)(?!h)/, /state_of_charge/, /battery_level/],
  range: [/v_b_range_est/, /range_est/, /v_b_range_ideal/, /(^|_)range($|_)/],
  charging: [/v_c_charging/, /(^|_)charging($|_)/, /charge_state/, /v_c_state/],
  power: [/v_c_power/, /charge_power/, /charging_power/],
  aux12v: [/v_b_12v_voltage(?!_)/, /12v_voltage/, /(^|_)12v($|_)/, /aux_battery/],
  temperature: [/v_b_temp/, /battery_temp/],
  tire_health: [/tire_health/, /tyre_health/],
  outside: [/v_e_temp/, /ambient_temp/, /outside_temp/],
  odometer: [/(^|_)odometer($|_)/, /v_p_odometer/],
};

const fmt = (value, digits = 0) => {
  if (value === null || value === undefined || value === "" || isNaN(Number(value))) return "–";
  const [int, dec] = Math.abs(Number(value)).toFixed(digits).split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, "’");
  return (Number(value) < 0 ? "-" : "") + grouped + (dec ? "." + dec : "");
};

const fdate = (iso) => {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
};

const esc = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const num = (state) => {
  const value = parseFloat(state?.state);
  return isNaN(value) ? null : value;
};

const healthColor = (soh) => (soh === null ? "var(--ccc-muted)" : soh >= 90 ? "#22c55e" : soh >= 80 ? "#eab308" : "#ef4444");

class CarCostsCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { prefix: "sensor.frais_voiture_", entities: {}, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._signature = null;
  }

  getCardSize() {
    return 9;
  }

  set hass(hass) {
    this._hass = hass;
    const ids = this._entityIds();
    // Ne redessiner que si l'une des entités utilisées a changé.
    const signature = Object.values(ids)
      .map((id) => (id && hass.states[id] ? hass.states[id].last_updated + hass.states[id].state : ""))
      .join("|");
    if (signature === this._signature) return;
    this._signature = signature;
    this._render(ids);
  }

  _entityIds() {
    if (this._ids && this._idsFor === this._hass.states) return this._ids;
    const prefix = this._config.prefix;
    const ids = {
      vehicle: prefix + "vehicule",
      battery: prefix + "batterie",
      month: prefix + "mois",
      year: prefix + "annee",
      per_km: prefix + "par_km",
      savings: prefix + "economie_essence",
      reminder: prefix + "prochain_rappel",
    };
    const all = Object.keys(this._hass.states).filter((id) => /^(sensor|binary_sensor)\./.test(id) && !id.startsWith(prefix));
    for (const [key, patterns] of Object.entries(DISCOVERY)) {
      ids[key] = this._config.entities[key] || null;
      if (ids[key]) continue;
      for (const pattern of patterns) {
        const match = all.find((id) => pattern.test(id.split(".")[1]) && !/soh/.test(id));
        if (match) {
          ids[key] = match;
          break;
        }
      }
    }
    this._ids = ids;
    this._idsFor = this._hass.states;
    return ids;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _render(ids) {
    const s = (key) => (ids[key] ? this._hass.states[ids[key]] : undefined);
    const vehicle = s("vehicle");
    const battery = s("battery");
    const attrs = vehicle?.attributes || {};
    const soc = num(s("soc"));
    const range = num(s("range"));
    const chargingState = s("charging")?.state;
    const charging = ["on", "true", "charging", "yes"].includes(String(chargingState).toLowerCase());
    const soh = num(battery);
    const cells = battery?.attributes?.cells || [];
    const history = battery?.attributes?.history || [];
    const odometer = num(vehicle) ?? num(s("odometer"));
    const title = this._config.title || vehicle?.attributes?.friendly_name || "Ma voiture";

    if (!vehicle && !battery) {
      this.shadowRoot.innerHTML = `${this._style()}<ha-card><div class="empty">
        <strong>Frais de voiture</strong><p>Capteurs <code>${esc(this._config.prefix)}*</code> introuvables :
        lance une synchronisation dans l'add-on (page Véhicule).</p></div></ha-card>`;
      return;
    }

    this.shadowRoot.innerHTML = `${this._style()}
      <ha-card>
        <section class="hero">
          <div class="identity">
            <div class="name">${esc(title)}</div>
            <div class="sub">${attrs.model && attrs.model !== title ? esc(attrs.model) : ""}${attrs.plate ? `<span class="plate">${esc(attrs.plate)}</span>` : ""}</div>
            <div class="odo" data-entity="${esc(ids.vehicle)}"><span>${fmt(odometer)}</span> km</div>
            ${this._carSvg(charging)}
          </div>
          ${this._socRing(soc, range, charging, num(s("power")), ids)}
        </section>

        <section class="stats">
          ${this._stat("Ce mois", s("month") ? fmt(num(s("month")), 0) : "–", "CHF", ids.month)}
          ${this._stat("Par km", s("per_km") ? fmt(num(s("per_km")), 2) : "–", "CHF", ids.per_km)}
          ${this._stat("Économie", s("savings") ? fmt(num(s("savings")), 0) : "–", "CHF vs essence", ids.savings, "good")}
          ${this._stat("Cette année", s("year") ? fmt(num(s("year")), 0) : "–", "CHF", ids.year)}
        </section>

        ${battery ? this._health(soh, cells, history, ids.battery) : ""}

        <section class="diag">
          <div class="section-title">Diagnostic</div>
          ${this._diagnostics(ids, s, attrs)}
        </section>
      </ha-card>`;

    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) =>
      el.addEventListener("click", () => this._moreInfo(el.dataset.entity))
    );
  }

  _stat(label, value, unit, entity, extra = "") {
    return `<div class="stat ${extra}" data-entity="${esc(entity || "")}">
      <span class="label">${label}</span><span class="value">${value}</span><span class="unit">${unit}</span></div>`;
  }

  _socRing(soc, range, charging, power, ids) {
    const r = 54;
    const circumference = 2 * Math.PI * r;
    const pct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
    const color = pct >= 50 ? "#22c55e" : pct >= 20 ? "#eab308" : "#ef4444";
    return `<div class="ring ${charging ? "charging" : ""}" data-entity="${esc(ids.soc || "")}">
      <svg viewBox="0 0 128 128">
        <defs><linearGradient id="soc-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="${color}" stop-opacity=".65"/><stop offset="1" stop-color="${color}"/></linearGradient></defs>
        <circle cx="64" cy="64" r="${r}" class="track"/>
        <circle cx="64" cy="64" r="${r}" class="arc" stroke="url(#soc-g)"
          stroke-dasharray="${(circumference * pct) / 100} ${circumference}" transform="rotate(-90 64 64)"/>
      </svg>
      <div class="ring-text">
        <span class="soc">${soc === null ? "–" : fmt(soc)}<small>%</small></span>
        <span class="range">${range === null ? (soc === null ? "charge inconnue" : "") : `${fmt(range)} km`}</span>
        ${charging ? `<span class="bolt">⚡ ${power ? fmt(power, 1) + " kW" : "en charge"}</span>` : ""}
      </div>
    </div>`;
  }

  _health(soh, cells, history, entity) {
    const color = healthColor(soh);
    const spark = this._sparkline(history, color);
    let cellBlock = "";
    if (cells.length) {
      const min = Math.min(...cells);
      const max = Math.max(...cells);
      const avg = cells.reduce((a, b) => a + b, 0) / cells.length;
      const lo = Math.floor(min - Math.max(1, (max - min) * 0.3));
      const hi = Math.ceil(max + 0.5);
      const weakest = cells.indexOf(min);
      const w = 300;
      const h = 56;
      const slot = w / cells.length;
      const bars = cells
        .map((v, i) => {
          const bh = Math.max(2, ((v - lo) / (hi - lo)) * h);
          return `<rect x="${(i * slot + slot * 0.15).toFixed(2)}" y="${(h - bh).toFixed(2)}" width="${Math.max(slot * 0.7, 1).toFixed(2)}"
            height="${bh.toFixed(2)}" rx="${Math.min(1.5, slot * 0.3).toFixed(2)}" class="${i === weakest ? "weak" : "cell"}"><title>Cellule ${i + 1} : ${fmt(v, 1)} %</title></rect>`;
        })
        .join("");
      cellBlock = `<div class="cells">
        <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}</svg>
        <div class="cell-legend"><span>${cells.length} cellules</span>
          <span>min <b class="bad">${fmt(min, 1)}</b> (n° ${weakest + 1})</span>
          <span>moy. <b>${fmt(avg, 1)}</b></span><span>max <b>${fmt(max, 1)}</b></span>
          <span>écart <b>${fmt(max - min, 1)}</b> pts</span></div></div>`;
    }
    const first = history.length ? history[0][1] : null;
    const trend = first !== null && soh !== null && history.length > 1 ? soh - first : null;
    return `<section class="health" data-entity="${esc(entity)}">
      <div class="section-title">Santé de la batterie</div>
      <div class="health-row">
        <div class="soh">
          ${this._gauge(soh, color)}
          <div class="soh-text"><span class="soh-value" style="color:${color}">${soh === null ? "–" : fmt(soh, 1)}<small>%</small></span>
          <span class="muted">SoH${trend !== null ? ` · ${trend >= 0 ? "+" : ""}${fmt(trend, 1)} pts depuis ${fdate(history[0][0]).slice(3)}` : ""}</span></div>
        </div>
        <div class="spark">${spark}</div>
      </div>
      ${cellBlock}
    </section>`;
  }

  _gauge(soh, color) {
    // Demi-cercle de 70 % (seuil courant de garantie) à 100 %.
    const pct = soh === null ? 0 : Math.max(0, Math.min(1, (soh - 70) / 30));
    const r = 36;
    const half = Math.PI * r;
    return `<svg viewBox="-4 0 96 52" class="gauge">
      <path d="M8 46 A${r} ${r} 0 0 1 80 46" class="track"/>
      <path d="M8 46 A${r} ${r} 0 0 1 80 46" class="arc" stroke="${color}" stroke-dasharray="${half * pct} ${half}"/>
      <text x="8" y="52" class="tick" text-anchor="middle">70</text><text x="80" y="52" class="tick" text-anchor="middle">100</text>
    </svg>`;
  }

  _sparkline(history, color) {
    if (history.length < 2) return `<span class="muted">L'historique se construit jour après jour.</span>`;
    const w = 220;
    const h = 64;
    const values = history.map((p) => p[1]);
    const lo = Math.min(...values) - 0.3;
    const hi = Math.max(...values) + 0.3;
    const t0 = Date.parse(history[0][0]);
    const span = Math.max(Date.parse(history[history.length - 1][0]) - t0, 1);
    const pts = history.map((p) => [((Date.parse(p[0]) - t0) / span) * w, h - 4 - ((p[1] - lo) / (hi - lo)) * (h - 8)]);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    const area = `${line} L${w} ${h} L0 ${h} Z`;
    const last = pts[pts.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="spark-g" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <path d="${area}" fill="url(#spark-g)"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3" fill="${color}"/>
    </svg>
    <div class="spark-axis"><span>${fdate(history[0][0])}</span><span>${fdate(history[history.length - 1][0])}</span></div>`;
  }

  _diagnostics(ids, s, attrs) {
    const rows = [];
    const add = (icon, label, value, level = "", entity = "") =>
      rows.push(`<div class="row ${level}" data-entity="${esc(entity)}"><ha-icon icon="${icon}"></ha-icon>
        <span class="row-label">${label}</span><span class="row-value">${value}</span></div>`);

    const aux = num(s("aux12v"));
    if (aux !== null) add("mdi:car-battery", "Batterie 12 V", `${fmt(aux, 1)} V`, aux < 12.2 ? "warn" : "ok", ids.aux12v);
    const temp = num(s("temperature"));
    if (temp !== null) add("mdi:thermometer", "Température batterie", `${fmt(temp, 0)} °C`, "", ids.temperature);
    const tire = s("tire_health");
    if (tire) {
      const value = isNaN(parseFloat(tire.state)) ? tire.state : `${fmt(tire.state, 0)} ${tire.attributes?.unit_of_measurement || ""}`;
      add("mdi:car-tire-alert", "Pneus (OVMS)", esc(value), /ok|good|normal/i.test(tire.state) ? "ok" : "", ids.tire_health);
    }
    for (const t of attrs.tires || []) {
      const season = { été: "☀️", hiver: "❄️", "4 saisons": "🌦️" }[t.season] || "";
      const tread = t.tread_mm !== null && t.tread_mm !== undefined ? ` · ${fmt(t.tread_mm, 1)} mm` : "";
      add("mdi:tire", `${season} ${esc(t.label)}`, `${fmt(t.km)} km${tread}`, "", ids.vehicle);
    }
    if (attrs.next_inspection) {
      const days = Math.round((Date.parse(attrs.next_inspection) - Date.now()) / 86400000);
      add("mdi:clipboard-check-outline", "Prochaine expertise", fdate(attrs.next_inspection), days < 0 ? "bad" : days < 120 ? "warn" : "", ids.vehicle);
    }
    for (const a of attrs.alerts || []) {
      add(a.overdue ? "mdi:alert-circle" : "mdi:bell-outline", esc(a.text), fdate(a.date), a.overdue ? "bad" : "warn", ids.reminder);
    }
    if (!rows.length) rows.push(`<div class="muted">Rien à signaler.</div>`);
    return rows.join("");
  }

  _carSvg(charging) {
    // Citadine stylisée.
    return `<svg class="car" viewBox="0 0 240 90" aria-hidden="true">
      <defs><linearGradient id="car-g" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="var(--ccc-accent)" stop-opacity=".9"/><stop offset="1" stop-color="var(--ccc-accent2)" stop-opacity=".9"/></linearGradient></defs>
      <path d="M18 62 C18 50 26 46 40 44 L70 22 C76 17 84 15 92 15 L150 15 C160 15 167 19 173 26 L190 44 C210 46 224 50 226 60 L226 66 C226 70 223 72 219 72 L22 72 C19 72 18 69 18 66 Z" fill="url(#car-g)"/>
      <path d="M78 26 L96 20 L128 20 L128 42 L58 42 Z M134 20 L152 20 C158 20 162 23 166 28 L178 42 L134 42 Z" fill="var(--ccc-glass)"/>
      <circle cx="62" cy="72" r="15" class="wheel"/><circle cx="62" cy="72" r="6" class="hub"/>
      <circle cx="186" cy="72" r="15" class="wheel"/><circle cx="186" cy="72" r="6" class="hub"/>
      ${charging ? `<path d="M212 30 L204 44 L211 44 L206 56 L218 40 L211 40 L216 30 Z" class="spark-bolt"/>` : ""}
    </svg>`;
  }

  _style() {
    return `<style>
      :host {
        --ccc-accent: var(--primary-color, #1f6feb);
        --ccc-accent2: #22d3ee;
        --ccc-muted: var(--secondary-text-color, #6b7280);
        --ccc-surface: color-mix(in srgb, var(--primary-text-color, #111) 5%, transparent);
        --ccc-glass: color-mix(in srgb, var(--card-background-color, #fff) 70%, transparent);
        display: block;
        container-type: inline-size;
      }
      ha-card { padding: 18px; overflow: hidden; position: relative; }
      ha-card::before { content: ""; position: absolute; inset: -40% -20% auto auto; width: 70%; height: 90%;
        background: radial-gradient(closest-side, color-mix(in srgb, var(--ccc-accent) 18%, transparent), transparent);
        pointer-events: none; }
      [data-entity]:not([data-entity=""]) { cursor: pointer; }
      .muted { color: var(--ccc-muted); font-size: 12px; }
      .empty { padding: 8px; } .empty p { color: var(--ccc-muted); }
      .hero { display: flex; justify-content: space-between; align-items: center; gap: 12px; position: relative; }
      .identity { min-width: 0; flex: 1; }
      .name { font-size: 22px; font-weight: 700; letter-spacing: -.01em; }
      .sub { color: var(--ccc-muted); font-size: 13px; display: flex; gap: 8px; align-items: center; margin-top: 2px; }
      .plate { border: 1.5px solid currentColor; border-radius: 4px; padding: 0 6px; font-weight: 600; font-size: 11px; letter-spacing: .05em; }
      .odo { margin-top: 6px; font-size: 13px; color: var(--ccc-muted); } .odo span { font-size: 18px; font-weight: 600; color: var(--primary-text-color); }
      .car { width: 100%; max-width: 260px; margin-top: 8px; filter: drop-shadow(0 10px 14px color-mix(in srgb, var(--ccc-accent) 30%, transparent)); }
      .car .wheel { fill: #1f2937; stroke: color-mix(in srgb, var(--card-background-color, #fff) 60%, #111); stroke-width: 3; }
      .car .hub { fill: #9ca3af; } .car .spark-bolt { fill: #facc15; animation: pulse 1.6s ease-in-out infinite; }
      .ring { position: relative; width: 140px; height: 140px; flex: none; }
      .ring svg { width: 100%; height: 100%; }
      .ring .track { fill: none; stroke: var(--ccc-surface); stroke-width: 11; }
      .ring .arc { fill: none; stroke-width: 11; stroke-linecap: round; transition: stroke-dasharray .8s ease; }
      .ring.charging .arc { animation: pulse 2s ease-in-out infinite; }
      .ring-text { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
      .soc { font-size: 32px; font-weight: 700; line-height: 1; } .soc small { font-size: 14px; font-weight: 600; margin-left: 1px; }
      .range { color: var(--ccc-muted); font-size: 12px; margin-top: 4px; }
      .bolt { margin-top: 4px; font-size: 11px; font-weight: 600; color: #ca8a04; }
      .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 16px 0 4px; }
      .stat { background: var(--ccc-surface); border-radius: 14px; padding: 10px 12px; display: flex; flex-direction: column; min-width: 0; }
      .stat .label { font-size: 11px; color: var(--ccc-muted); text-transform: uppercase; letter-spacing: .04em; }
      .stat .value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .stat .unit { font-size: 11px; color: var(--ccc-muted); }
      .stat.good .value { color: #16a34a; }
      .section-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--ccc-muted); margin: 16px 0 8px; }
      .health-row { display: flex; gap: 14px; align-items: center; }
      .soh { display: flex; align-items: center; gap: 10px; flex: none; }
      .gauge { width: 96px; } .gauge .track { fill: none; stroke: var(--ccc-surface); stroke-width: 9; stroke-linecap: round; }
      .gauge .arc { fill: none; stroke-width: 9; stroke-linecap: round; } .gauge .tick { font-size: 7px; fill: var(--ccc-muted); }
      .soh-text { display: flex; flex-direction: column; }
      .soh-value { font-size: 28px; font-weight: 700; line-height: 1.1; } .soh-value small { font-size: 13px; }
      .spark { flex: 1; min-width: 0; } .spark svg { width: 100%; height: 64px; display: block; }
      .spark-axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--ccc-muted); }
      .cells { margin-top: 12px; background: var(--ccc-surface); border-radius: 14px; padding: 10px 12px; }
      .cells svg { width: 100%; height: 56px; display: block; }
      .cells .cell { fill: var(--ccc-accent); opacity: .7; } .cells .weak { fill: #ef4444; }
      .cell-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; color: var(--ccc-muted); margin-top: 6px; }
      .cell-legend b { color: var(--primary-text-color); } .cell-legend b.bad { color: #ef4444; }
      .row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 12px; }
      .row + .row { margin-top: 4px; }
      .row:hover { background: var(--ccc-surface); }
      .row ha-icon { --mdc-icon-size: 20px; color: var(--ccc-muted); flex: none; }
      .row-label { flex: 1; min-width: 0; font-size: 14px; }
      .row-value { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .row.ok ha-icon, .row.ok .row-value { color: #16a34a; }
      .row.warn ha-icon, .row.warn .row-value { color: #ca8a04; }
      .row.bad { background: color-mix(in srgb, #ef4444 10%, transparent); } .row.bad ha-icon, .row.bad .row-value { color: #ef4444; }
      @keyframes pulse { 50% { opacity: .55; } }
      @container (max-width: 430px) { .stats { grid-template-columns: repeat(2, 1fr); } .ring { width: 116px; height: 116px; } .soc { font-size: 26px; }
        .health-row { flex-direction: column; align-items: stretch; } }
    </style>`;
  }
}

customElements.define("car-costs-card", CarCostsCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "car-costs-card",
  name: "Frais de voiture",
  description: "Voiture, batterie (charge et santé), coûts et diagnostic.",
  preview: true,
});
console.info(`%c car-costs-card %c ${VERSION} `, "background:#1f6feb;color:#fff;border-radius:3px 0 0 3px", "background:#22d3ee;color:#000;border-radius:0 3px 3px 0");
