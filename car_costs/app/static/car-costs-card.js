/*
 * Carte Lovelace « Frais de voiture » : voiture, charge, batterie, coûts et diagnostic.
 *
 * Copiée par l'add-on dans /config/www ; ressource à déclarer :
 *   url: /local/car-costs-card.js?v=0.5.0   type: module
 * Puis dans un tableau de bord :
 *   type: custom:car-costs-card
 *   image: /api/image/serve/<id>/original      # facultatif : photo de la voiture
 *
 * Données : capteurs sensor.frais_voiture_* publiés par l'add-on, et entités
 * OVMS / EVCC trouvées automatiquement ou indiquées sous `entities:`.
 */

const VERSION = "0.5.0";

// Recherche automatique : [domaines, motifs dans l'identifiant (points remplacés par _), par priorité].
const DISCOVERY = {
  soc: [["sensor"], [/normalized_soc/, /xvu_b_soc|v_b_soc(?!h)/, /(^|_)soc($|_)/, /state_of_charge/, /battery_level/]],
  range: [["sensor"], [/estimated_range/, /v_b_range_est/, /range_est/, /v_b_range_ideal/, /(^|_)range($|_)/]],
  charge_state: [["sensor"], [/(^|_)charge_state$/, /v_c_state/, /charging_state/]],
  charging_power: [["sensor"], [/evcc.*charge_power/, /v_c_power/, /charge_power/, /charging_power/]],
  plug: [["binary_sensor"], [/evcc.*connected/, /v_c_pilot/, /plug/, /cable/]],
  aux12v: [["sensor"], [/12v_battery_voltage/, /v_b_12v_voltage(?!_)/, /12v_voltage/, /(^|_)12v($|_)/]],
  outside: [["sensor"], [/ambient_temperature/, /v_e_temp/, /outside_temp/]],
  cabin: [["sensor"], [/cabin_temperature/, /v_e_cabintemp/, /cabin_temp/]],
  battery_temp: [["sensor"], [/v_b_temp/, /battery_temperature/, /battery_temp/]],
  tire_health: [["sensor"], [/tire_health/, /tyre_health/]],
  hvac: [["switch", "binary_sensor"], [/hvac_active/, /v_e_hvac(?!_switch)/, /hvac/]],
  hvac_toggle: [["switch"], [/v_e_hvac_switch/, /hvac_switch/, /hvac/]],
  odometer: [["sensor"], [/(^|_)odometer($|_)/, /v_p_odometer/]],
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

const fmonth = (iso) => (iso ? `${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "–");

const esc = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const num = (state) => {
  const value = parseFloat(state?.state);
  return isNaN(value) ? null : value;
};

const healthColor = (soh) => (soh === null ? "var(--ccc-muted)" : soh >= 90 ? "#22c55e" : soh >= 80 ? "#eab308" : "#ef4444");
const socColor = (soc) => (soc === null ? "#64748b" : soc >= 50 ? "#22c55e" : soc >= 20 ? "#f59e0b" : "#ef4444");
const tempColor = (t) =>
  t === null ? "rgba(148,163,184,.35)" : t <= 0 ? "rgba(59,130,246,.75)" : t <= 10 ? "rgba(14,165,233,.7)" : t <= 21 ? "rgba(34,197,94,.7)" : t <= 29 ? "rgba(249,115,22,.75)" : "rgba(239,68,68,.8)";

let gradientId = 0;

class CarCostsCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { prefix: "sensor.frais_voiture_", entities: {}, map_path: "/map", hours: 24, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._signature = null;
    this._ids = null;
    this._socHistory = null;
    this._uid = `ccc${++gradientId}`;
  }

  getCardSize() {
    return 12;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || !this._ids) this._ids = this._discover();
    const ids = this._ids;
    // Ne redessiner que si l'une des entités utilisées a changé.
    const signature = Object.values(ids)
      .map((id) => (id && hass.states[id] ? hass.states[id].last_updated + hass.states[id].state : ""))
      .join("|");
    if (signature !== this._signature) {
      this._signature = signature;
      this._render();
    }
    this._loadSocHistory();
  }

  _discover() {
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
    const all = Object.keys(this._hass.states).filter((id) => !id.startsWith(prefix));
    for (const [key, [domains, patterns]] of Object.entries(DISCOVERY)) {
      ids[key] = this._config.entities[key] || null;
      if (ids[key]) continue;
      const candidates = all.filter((id) => domains.includes(id.split(".")[0]));
      for (const pattern of patterns) {
        const match = candidates.find((id) => pattern.test(id.split(".")[1]) && !/soh|frais_voiture/.test(id));
        if (match) {
          ids[key] = match;
          break;
        }
      }
    }
    if (!this._config.entities.hvac_toggle && ids.hvac && ids.hvac.startsWith("switch.") && !ids.hvac_toggle) ids.hvac_toggle = ids.hvac;
    return ids;
  }

  async _loadSocHistory() {
    const id = this._ids?.soc;
    const now = Date.now();
    if (!id || !this._hass.callApi || this._loading || (this._socLoaded && now - this._socLoaded < 10 * 60 * 1000)) return;
    this._loading = true;
    try {
      const start = new Date(now - this._config.hours * 3600 * 1000).toISOString();
      const data = await this._hass.callApi(
        "GET",
        `history/period/${start}?filter_entity_id=${id}&minimal_response&no_attributes&end_time=${new Date(now).toISOString()}`
      );
      this._socHistory = (data?.[0] || [])
        .map((p) => [Date.parse(p.last_changed || p.lu * 1000), parseFloat(p.state ?? p.s)])
        .filter((p) => !isNaN(p[0]) && !isNaN(p[1]));
      this._socLoaded = now;
      this._render();
    } catch (err) {
      this._socLoaded = now; // pas d'historique (recorder exclu...) : on n'insiste pas
    } finally {
      this._loading = false;
    }
  }

  _state(key) {
    const id = this._ids?.[key];
    return id ? this._hass.states[id] : undefined;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _action(kind) {
    if (kind === "hvac" && this._ids.hvac_toggle) {
      const [domain] = this._ids.hvac_toggle.split(".");
      this._hass.callService(domain === "switch" ? "switch" : "homeassistant", "toggle", { entity_id: this._ids.hvac_toggle });
    } else if (kind === "map") {
      window.history.pushState(null, "", this._config.map_path);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }
  }

  _render() {
    if (!this._hass) return;
    const s = (key) => this._state(key);
    const vehicle = s("vehicle");
    const battery = s("battery");
    if (!vehicle && !battery && !s("soc")) {
      this.shadowRoot.innerHTML = `${this._style()}<ha-card><div class="empty">
        <strong>Frais de voiture</strong><p>Capteurs <code>${esc(this._config.prefix)}*</code> introuvables :
        lance une synchronisation dans l'add-on (page Véhicule).</p></div></ha-card>`;
      return;
    }
    const sections = this._config.sections || ["hero", "actions", "soc_chart", "costs", "battery", "diagnostic"];
    const parts = {
      hero: () => this._hero(),
      actions: () => this._actions(),
      soc_chart: () => this._socChart(),
      costs: () => this._costs(),
      battery: () => (battery ? this._battery(battery.attributes || {}, num(battery)) : ""),
      diagnostic: () => this._diagnostic(vehicle?.attributes || {}),
    };
    this.shadowRoot.innerHTML = `${this._style()}<ha-card>${sections.map((name) => (parts[name] ? parts[name]() : "")).join("")}</ha-card>`;
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      if (el.dataset.entity) el.addEventListener("click", () => this._moreInfo(el.dataset.entity));
    });
    this.shadowRoot.querySelectorAll("[data-action]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._action(el.dataset.action);
      })
    );
  }

  // ------------------------------------------------------------ en-tête « showroom »

  _charging() {
    const state = String(this._state("charge_state")?.state || "").toLowerCase();
    const powerEntity = this._state("charging_power");
    let power = num(powerEntity);
    // EVCC publie des W, OVMS des kW
    const unit = String(powerEntity?.attributes?.unit_of_measurement || "").toLowerCase();
    if (power !== null && (unit === "w" || (!unit.includes("k") && power > 100))) power = power / 1000;
    const byState = /^(charging|topoff|en charge|charge en cours)/.test(state) && !/not|pas/.test(state);
    return { active: byState || (power !== null && power > 0.1), power, state: this._state("charge_state")?.state };
  }

  _hero() {
    const ids = this._ids;
    const vehicle = this._state("vehicle");
    const attrs = vehicle?.attributes || {};
    const title = this._config.name || attrs.friendly_name || "Ma voiture";
    const soc = num(this._state("soc"));
    const range = num(this._state("range"));
    const odometer = num(vehicle) ?? num(this._state("odometer"));
    const charging = this._charging();
    const plugged = this._state("plug")?.state === "on";
    const dark = this._hass.themes?.darkMode;
    const image = (dark && this._config.image_dark) || this._config.image;

    const chip = (key, label) => {
      const t = num(this._state(key));
      if (t === null) return "";
      return `<div class="chip" data-entity="${esc(ids[key])}" style="background:${tempColor(t)}">
        <span>${label}</span><b>${fmt(t, 1)}°</b></div>`;
    };
    let status = "Garée";
    if (charging.active) status = `En charge${charging.power ? ` · ${fmt(charging.power, 1)} kW` : ""}`;
    else if (plugged) status = "Branchée";
    else if (charging.state && !/unknown|unavailable/.test(charging.state)) status = esc(charging.state);

    return `<section class="hero ${charging.active ? "is-charging" : ""}">
      <div class="hero-top">
        <div class="identity" data-entity="${esc(ids.vehicle)}">
          <div class="name">${esc(title)}</div>
          <div class="sub">${attrs.plate ? `<span class="plate">${esc(attrs.plate)}</span>` : ""}
            <span>${fmt(odometer)} km</span></div>
        </div>
        <div class="chips">${chip("outside", "Extérieur")}${chip("cabin", "Intérieur")}</div>
      </div>
      <div class="stage">
        ${image ? `<img class="photo" src="${esc(image)}" alt="">` : this._carSvg()}
        ${this._socRing(soc, charging.active)}
      </div>
      <div class="hero-bottom">
        <div class="range" data-entity="${esc(ids.range)}"><span class="big">${range === null ? "–" : fmt(range)}</span> km
          <small>d'autonomie estimée</small></div>
        <div class="status ${charging.active ? "charging" : plugged ? "plugged" : ""}" data-entity="${esc(ids.charge_state || ids.plug)}">
          <span class="dot"></span>${status}</div>
      </div>
    </section>`;
  }

  _socRing(soc, charging) {
    const r = 52;
    const c = 2 * Math.PI * r;
    const pct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
    const color = socColor(soc);
    const g = `${this._uid}-soc`;
    return `<div class="ring ${charging ? "charging" : ""}" data-entity="${esc(this._ids.soc)}">
      <svg viewBox="0 0 128 128">
        <defs><linearGradient id="${g}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="${color}" stop-opacity=".55"/><stop offset="1" stop-color="${color}"/></linearGradient></defs>
        <circle cx="64" cy="64" r="${r}" class="track"/>
        <circle cx="64" cy="64" r="${r}" class="arc" stroke="url(#${g})" stroke-dasharray="${(c * pct) / 100} ${c}" transform="rotate(-90 64 64)"/>
      </svg>
      <div class="ring-text"><span class="soc">${soc === null ? "–" : fmt(soc)}<small>%</small></span>
        <span class="ring-label">${charging ? "⚡ charge" : "batterie"}</span></div>
    </div>`;
  }

  _actions() {
    const hvac = this._state("hvac");
    const buttons = [];
    if (this._ids.hvac_toggle || hvac) {
      const on = hvac?.state === "on";
      buttons.push(`<button class="action ${on ? "on" : ""}" data-action="hvac" title="Climatisation">
        <ha-icon icon="mdi:air-conditioner"></ha-icon><span>Climatisation</span><small>${on ? "en marche" : "arrêtée"}</small></button>`);
    }
    if (this._config.map_path) {
      buttons.push(`<button class="action" data-action="map" title="Carte">
        <ha-icon icon="mdi:map-marker-radius"></ha-icon><span>GPS voiture</span><small>voir la carte</small></button>`);
    }
    const aux = num(this._state("aux12v"));
    if (aux !== null) {
      buttons.push(`<button class="action ${aux < 12.2 ? "warn" : ""}" data-entity="${esc(this._ids.aux12v)}">
        <ha-icon icon="mdi:car-battery"></ha-icon><span>${fmt(aux, 1)} V</span><small>batterie 12 V</small></button>`);
    }
    return buttons.length ? `<section class="actions">${buttons.join("")}</section>` : "";
  }

  _socChart() {
    const points = this._socHistory;
    if (!points || points.length < 2) return "";
    const w = 320;
    const h = 70;
    const t0 = Date.now() - this._config.hours * 3600 * 1000;
    const t1 = Date.now();
    const x = (t) => ((Math.max(t, t0) - t0) / (t1 - t0)) * w;
    const y = (v) => h - 4 - (Math.max(0, Math.min(100, v)) / 100) * (h - 8);
    // Courbe en escalier jusqu'à maintenant.
    let line = `M${x(points[0][0]).toFixed(1)} ${y(points[0][1]).toFixed(1)}`;
    for (let i = 1; i < points.length; i++) line += ` H${x(points[i][0]).toFixed(1)} V${y(points[i][1]).toFixed(1)}`;
    line += ` H${w}`;
    const g = `${this._uid}-socfill`;
    const color = socColor(points[points.length - 1][1]);
    const values = points.map((p) => p[1]);
    return `<section class="panel" data-entity="${esc(this._ids.soc)}">
      <div class="panel-head"><span class="section-title">Charge sur ${this._config.hours} h</span>
        <span class="muted">min ${fmt(Math.min(...values))} % · max ${fmt(Math.max(...values))} %</span></div>
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="soc-chart">
        <defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
        ${[25, 50, 75].map((v) => `<line x1="0" x2="${w}" y1="${y(v)}" y2="${y(v)}" class="grid"/>`).join("")}
        <path d="${line} V${h} H${x(points[0][0]).toFixed(1)} Z" fill="url(#${g})"/>
        <path d="${line}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      </svg>
      <div class="axis"><span>il y a ${this._config.hours} h</span><span>maintenant</span></div>
    </section>`;
  }

  _costs() {
    const s = (key) => this._state(key);
    const tile = (label, value, unit, key, extra = "") =>
      `<div class="stat ${extra}" data-entity="${esc(this._ids[key])}"><span class="label">${label}</span>
        <span class="value">${value}</span><span class="unit">${unit}</span></div>`;
    if (!s("month") && !s("per_km")) return "";
    return `<section><div class="section-title">Coûts</div><div class="stats">
      ${tile("Ce mois", fmt(num(s("month"))), "CHF", "month")}
      ${tile("Par km", fmt(num(s("per_km")), 2), "CHF", "per_km")}
      ${tile("Économie", fmt(num(s("savings"))), "CHF vs essence", "savings", "good")}
      ${tile("Cette année", fmt(num(s("year"))), "CHF", "year")}
    </div></section>`;
  }

  // ------------------------------------------------------------ batterie

  _battery(a, soh) {
    const color = healthColor(soh);
    const history = a.history || [];
    const capHistory = a.capacity_history || [];
    const trend = history.length > 1 && soh !== null ? soh - history[0][1] : null;
    const capacity = a.capacity_kwh;
    const capTrend = capHistory.length > 1 ? capHistory[capHistory.length - 1][1] - capHistory[0][1] : null;
    const facts = [
      ["mdi:ev-station", fmt(a.charges), "recharges", a.first_charge ? `depuis le ${fdate(a.first_charge)} (EVCC)` : "EVCC"],
      ["mdi:lightning-bolt", `${fmt(a.total_kwh)} kWh`, "énergie chargée", a.estimated_kwh ? `dont ${fmt(a.estimated_kwh)} estimés avant EVCC` : "depuis l'achat"],
      ["mdi:battery-sync", fmt(a.cycles, 1), "cycles complets", `équivalents, sur ${fmt(a.usable_kwh, 1)} kWh utiles`],
      ["mdi:solar-power-variant", a.solar_pct === null || a.solar_pct === undefined ? "–" : `${fmt(a.solar_pct)} %`, "solaire", "part des recharges EVCC"],
      ["mdi:speedometer", a.kwh_per_100km ? fmt(a.kwh_per_100km, 1) : "–", "kWh/100 km", a.km ? `sur ${fmt(a.km)} km` : "moyenne"],
      ["mdi:cash", a.avg_price_kwh ? fmt(a.avg_price_kwh, 3) : "–", "CHF/kWh", "prix moyen EVCC"],
    ];
    return `<section class="battery" data-entity="${esc(this._ids.battery)}">
      <div class="section-title">Batterie</div>
      <div class="duo">
        <div class="panel">
          <div class="metric">${this._gauge(soh, color)}
            <div><span class="metric-value" style="color:${color}">${soh === null ? "–" : fmt(soh, 1)}<small>%</small></span>
            <span class="muted">santé (SoH)${trend !== null ? ` · ${trend >= 0 ? "+" : ""}${fmt(trend, 1)} pts depuis ${fmonth(history[0][0])}` : ""}</span></div>
          </div>
          ${this._sparkline(history, color, "%")}
        </div>
        <div class="panel">
          <div class="metric"><ha-icon icon="mdi:battery-high" class="metric-icon"></ha-icon>
            <div><span class="metric-value">${capacity ? fmt(capacity, 1) : fmt(a.usable_kwh, 1)}<small>kWh</small></span>
            <span class="muted">${capacity ? `capacité mesurée${capTrend !== null ? ` · ${capTrend >= 0 ? "+" : ""}${fmt(capTrend, 1)} kWh depuis ${fmonth(capHistory[0][0])}` : ""}` : "capacité nominale (pas de mesure OVMS)"}</span></div>
          </div>
          ${this._sparkline(capHistory, "var(--ccc-accent)", "kWh")}
        </div>
      </div>
      <div class="facts">${facts
        .map(([icon, value, label, hint]) => `<div class="fact"><ha-icon icon="${icon}"></ha-icon>
          <div><b>${value}</b> <span>${label}</span><small>${hint}</small></div></div>`)
        .join("")}</div>
      ${this._cells(a.cells || [])}
    </section>`;
  }

  _gauge(soh, color) {
    // Demi-cercle de 70 % (seuil courant de garantie) à 100 %.
    const pct = soh === null ? 0 : Math.max(0, Math.min(1, (soh - 70) / 30));
    const r = 36;
    const half = Math.PI * r;
    return `<svg viewBox="-4 0 96 54" class="gauge">
      <path d="M8 46 A${r} ${r} 0 0 1 80 46" class="track"/>
      <path d="M8 46 A${r} ${r} 0 0 1 80 46" class="arc" stroke="${color}" stroke-dasharray="${half * pct} ${half}"/>
      <text x="8" y="53" class="tick" text-anchor="middle">70</text><text x="80" y="53" class="tick" text-anchor="middle">100</text>
    </svg>`;
  }

  _sparkline(history, color, unit) {
    if (history.length < 2) return `<div class="spark-empty muted">L'historique se construit jour après jour.</div>`;
    const w = 220;
    const h = 56;
    const values = history.map((p) => p[1]);
    const pad = Math.max((Math.max(...values) - Math.min(...values)) * 0.15, 0.2);
    const lo = Math.min(...values) - pad;
    const hi = Math.max(...values) + pad;
    const t0 = Date.parse(history[0][0]);
    const span = Math.max(Date.parse(history[history.length - 1][0]) - t0, 1);
    const pts = history.map((p) => [((Date.parse(p[0]) - t0) / span) * w, h - 3 - ((p[1] - lo) / (hi - lo)) * (h - 6)]);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    const g = `${this._uid}-sp${unit === "%" ? "s" : "c"}`;
    const last = pts[pts.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="spark">
      <defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".3"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <path d="${line} L${w} ${h} L0 ${h} Z" fill="url(#${g})"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3" fill="${color}"/>
    </svg>
    <div class="axis"><span>${fmonth(history[0][0])}</span><span>${fmt(values[values.length - 1], 1)} ${unit}</span></div>`;
  }

  _cells(cells) {
    if (!cells.length) return "";
    const min = Math.min(...cells);
    const max = Math.max(...cells);
    const avg = cells.reduce((a, b) => a + b, 0) / cells.length;
    const lo = Math.floor(min - Math.max(1, (max - min) * 0.3));
    const hi = Math.ceil(max + 0.5);
    const weakest = cells.indexOf(min);
    const w = 300;
    const h = 52;
    const slot = w / cells.length;
    const bars = cells
      .map((v, i) => {
        const bh = Math.max(2, ((v - lo) / (hi - lo)) * h);
        return `<rect x="${(i * slot + slot * 0.15).toFixed(2)}" y="${(h - bh).toFixed(2)}" width="${Math.max(slot * 0.7, 1).toFixed(2)}"
          height="${bh.toFixed(2)}" rx="${Math.min(1.5, slot * 0.3).toFixed(2)}" class="${i === weakest ? "weak" : "cell"}"><title>Cellule ${i + 1} : ${fmt(v, 1)} %</title></rect>`;
      })
      .join("");
    return `<div class="panel cells">
      <div class="panel-head"><span class="section-title">${cells.length} cellules</span>
        <span class="muted">écart ${fmt(max - min, 1)} pts</span></div>
      <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}</svg>
      <div class="legend"><span>min <b class="bad">${fmt(min, 1)} %</b> (n° ${weakest + 1})</span>
        <span>moyenne <b>${fmt(avg, 1)} %</b></span><span>max <b>${fmt(max, 1)} %</b></span></div>
    </div>`;
  }

  // ------------------------------------------------------------ diagnostic

  _diagnostic(attrs) {
    const ids = this._ids;
    const rows = [];
    const add = (icon, label, value, level = "", entity = "") =>
      rows.push(`<div class="row ${level}" data-entity="${esc(entity)}"><ha-icon icon="${icon}"></ha-icon>
        <span class="row-label">${label}</span><span class="row-value">${value}</span></div>`);
    const temp = num(this._state("battery_temp"));
    if (temp !== null) add("mdi:thermometer", "Température batterie", `${fmt(temp, 0)} °C`, "", ids.battery_temp);
    const tire = this._state("tire_health");
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
    if (!rows.length) return "";
    return `<section><div class="section-title">Diagnostic et rappels</div>${rows.join("")}</section>`;
  }

  _carSvg() {
    const g = `${this._uid}-car`;
    return `<svg class="photo car" viewBox="0 0 240 100" aria-hidden="true">
      <defs><linearGradient id="${g}" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#1d4ed8"/></linearGradient></defs>
      <ellipse cx="122" cy="88" rx="104" ry="7" fill="#000" opacity=".5"/>
      <path d="M22 66 C22 54 30 50 44 48 L74 26 C80 21 88 19 96 19 L150 19 C160 19 167 23 173 30 L190 48 C210 50 222 54 224 64 L224 70 C224 74 221 76 217 76 L26 76 C23 76 22 73 22 70 Z" fill="url(#${g})"/>
      <path d="M82 30 L100 24 L128 24 L128 46 L62 46 Z M134 24 L152 24 C158 24 162 27 166 32 L178 46 L134 46 Z" fill="#0f172a" opacity=".55"/>
      <circle cx="66" cy="76" r="14" fill="#0b0f19" stroke="#334155" stroke-width="3"/><circle cx="66" cy="76" r="5" fill="#94a3b8"/>
      <circle cx="184" cy="76" r="14" fill="#0b0f19" stroke="#334155" stroke-width="3"/><circle cx="184" cy="76" r="5" fill="#94a3b8"/>
    </svg>`;
  }

  _style() {
    return `<style>
      :host {
        --ccc-accent: var(--primary-color, #1f6feb);
        --ccc-muted: var(--secondary-text-color, #6b7280);
        --ccc-surface: color-mix(in srgb, var(--primary-text-color, #111) 5%, transparent);
        display: block;
        container-type: inline-size;
      }
      ha-card { padding: 0 0 16px; overflow: hidden; }
      ha-card > section { padding: 0 16px; }
      [data-entity]:not([data-entity=""]) { cursor: pointer; }
      .muted { color: var(--ccc-muted); font-size: 12px; }
      .empty { padding: 16px; } .empty p { color: var(--ccc-muted); }
      .section-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--ccc-muted); }
      section > .section-title { display: block; margin: 18px 0 8px; }

      /* En-tête « showroom », toujours sombre */
      ha-card > section.hero { position: relative; padding: 16px 16px 14px; color: #f8fafc;
        background: radial-gradient(120% 80% at 50% 95%, #1e3a5f 0%, #0b1220 55%, #05070c 100%); }
      .hero.is-charging { background: radial-gradient(120% 80% at 50% 95%, #14532d 0%, #0b1220 55%, #05070c 100%); }
      .hero-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; position: relative; z-index: 2; }
      .name { font-size: 20px; font-weight: 700; letter-spacing: -.01em; }
      .sub { display: flex; gap: 8px; align-items: center; font-size: 13px; color: rgba(248,250,252,.7); margin-top: 3px; }
      .plate { border: 1.5px solid rgba(248,250,252,.8); border-radius: 4px; padding: 0 6px; font-weight: 600; font-size: 11px; letter-spacing: .06em; color: #f8fafc; }
      .chips { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
      .chip { display: flex; flex-direction: column; align-items: center; padding: 4px 10px; border-radius: 10px; min-width: 58px;
        backdrop-filter: blur(6px); box-shadow: inset 0 0 0 1px rgba(255,255,255,.15); }
      .chip span { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; opacity: .9; }
      .chip b { font-size: 16px; }
      .stage { position: relative; display: flex; align-items: center; justify-content: center; min-height: 150px; margin-top: 4px; }
      .photo { width: 78%; max-height: 190px; object-fit: contain; display: block; margin-right: 18%;
        -webkit-mask-image: radial-gradient(ellipse 75% 80% at 50% 55%, #000 60%, transparent 100%);
        mask-image: radial-gradient(ellipse 75% 80% at 50% 55%, #000 60%, transparent 100%); }
      .photo.car { -webkit-mask-image: none; mask-image: none; filter: drop-shadow(0 12px 18px rgba(56,189,248,.25)); }
      .ring { position: absolute; right: 0; top: 50%; transform: translateY(-50%); width: 112px; height: 112px;
        border-radius: 50%; background: rgba(2,6,23,.55); backdrop-filter: blur(8px); box-shadow: 0 0 0 1px rgba(255,255,255,.08); }
      .ring svg { width: 100%; height: 100%; }
      .ring .track { fill: none; stroke: rgba(255,255,255,.1); stroke-width: 10; }
      .ring .arc { fill: none; stroke-width: 10; stroke-linecap: round; transition: stroke-dasharray 1s ease; }
      .ring.charging .arc { animation: pulse 2s ease-in-out infinite; }
      .ring-text { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
      .soc { font-size: 28px; font-weight: 700; line-height: 1; } .soc small { font-size: 13px; margin-left: 1px; }
      .ring-label { font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: rgba(248,250,252,.6); margin-top: 3px; }
      .hero-bottom { display: flex; justify-content: space-between; align-items: flex-end; gap: 10px; margin-top: 4px; }
      .range { font-size: 13px; color: rgba(248,250,252,.75); } .range .big { font-size: 26px; font-weight: 700; color: #f8fafc; }
      .range small { display: block; font-size: 11px; color: rgba(248,250,252,.55); }
      .status { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; padding: 5px 12px; border-radius: 999px;
        background: rgba(255,255,255,.08); box-shadow: inset 0 0 0 1px rgba(255,255,255,.12); white-space: nowrap; }
      .status .dot { width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; }
      .status.plugged .dot { background: #38bdf8; } .status.charging .dot { background: #22c55e; box-shadow: 0 0 0 4px rgba(34,197,94,.25); animation: pulse 1.6s infinite; }

      /* Actions */
      .actions { display: flex; gap: 8px; margin-top: 14px; }
      .action { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px;
        border: none; border-radius: 14px; background: var(--ccc-surface); color: var(--primary-text-color); font: inherit; cursor: pointer; text-align: left;
        transition: transform .15s ease, background .2s ease; }
      .action:hover { transform: translateY(-1px); } .action:active { transform: scale(.98); }
      .action ha-icon { --mdc-icon-size: 22px; color: var(--ccc-muted); }
      .action span { font-size: 14px; font-weight: 600; } .action small { font-size: 11px; color: var(--ccc-muted); }
      .action.on { background: color-mix(in srgb, #0ea5e9 18%, transparent); } .action.on ha-icon { color: #0ea5e9; }
      .action.warn ha-icon, .action.warn span { color: #ca8a04; }

      /* Panneaux et graphiques */
      .panel { background: var(--ccc-surface); border-radius: 16px; padding: 12px; min-width: 0; }
      section.panel { margin: 12px 16px 0; padding: 12px; }
      .panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
      .soc-chart { width: 100%; height: 70px; display: block; } .soc-chart .grid { stroke: var(--ccc-muted); stroke-opacity: .2; stroke-dasharray: 2 4; }
      .axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--ccc-muted); margin-top: 2px; }

      .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
      .stat { background: var(--ccc-surface); border-radius: 14px; padding: 10px 12px; display: flex; flex-direction: column; min-width: 0; }
      .stat .label { font-size: 11px; color: var(--ccc-muted); }
      .stat .value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .stat .unit { font-size: 11px; color: var(--ccc-muted); } .stat.good .value { color: #16a34a; }

      .duo { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .metric { display: flex; align-items: center; gap: 10px; }
      .metric > div { display: flex; flex-direction: column; min-width: 0; }
      .metric-value { font-size: 26px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; } .metric-value small { font-size: 13px; margin-left: 2px; }
      .metric-icon { --mdc-icon-size: 40px; color: var(--ccc-accent); width: 56px; display: flex; justify-content: center; }
      .gauge { width: 76px; flex: none; } .gauge .track { fill: none; stroke: var(--ccc-surface); stroke-width: 9; stroke-linecap: round; }
      .gauge .track { stroke: color-mix(in srgb, var(--primary-text-color, #111) 10%, transparent); }
      .gauge .arc { fill: none; stroke-width: 9; stroke-linecap: round; } .gauge .tick { font-size: 7px; fill: var(--ccc-muted); }
      .spark { width: 100%; height: 56px; display: block; margin-top: 8px; } .spark-empty { margin-top: 12px; }

      .facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
      .fact { display: flex; gap: 8px; align-items: flex-start; background: var(--ccc-surface); border-radius: 14px; padding: 10px; min-width: 0; }
      .fact ha-icon { --mdc-icon-size: 20px; color: var(--ccc-accent); flex: none; margin-top: 1px; }
      .fact b { font-size: 16px; font-variant-numeric: tabular-nums; } .fact span { font-size: 12px; color: var(--ccc-muted); }
      .fact small { display: block; font-size: 10.5px; color: var(--ccc-muted); margin-top: 2px; line-height: 1.3; }

      .cells { margin-top: 8px; } .cells svg { width: 100%; height: 52px; display: block; }
      .cells .cell { fill: var(--ccc-accent); opacity: .7; } .cells .weak { fill: #ef4444; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; color: var(--ccc-muted); margin-top: 6px; }
      .legend b { color: var(--primary-text-color); } .legend b.bad { color: #ef4444; }

      .row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 12px; }
      .row + .row { margin-top: 2px; } .row:hover { background: var(--ccc-surface); }
      .row ha-icon { --mdc-icon-size: 20px; color: var(--ccc-muted); flex: none; }
      .row-label { flex: 1; min-width: 0; font-size: 14px; }
      .row-value { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .row.ok ha-icon, .row.ok .row-value { color: #16a34a; }
      .row.warn ha-icon, .row.warn .row-value { color: #ca8a04; }
      .row.bad { background: color-mix(in srgb, #ef4444 10%, transparent); } .row.bad ha-icon, .row.bad .row-value { color: #ef4444; }

      @keyframes pulse { 50% { opacity: .55; } }
      @container (max-width: 440px) {
        .stats { grid-template-columns: repeat(2, 1fr); }
        .duo { grid-template-columns: 1fr; }
        .facts { grid-template-columns: repeat(2, 1fr); }
        .ring { width: 96px; height: 96px; } .soc { font-size: 24px; }
        .photo { width: 72%; margin-right: 24%; }
        .actions { flex-wrap: wrap; } .action { flex: 1 1 40%; }
      }
    </style>`;
  }
}

customElements.define("car-costs-card", CarCostsCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "car-costs-card",
  name: "Frais de voiture",
  description: "Voiture, charge, santé et capacité de la batterie, coûts et diagnostic.",
  preview: true,
});
console.info(`%c car-costs-card %c ${VERSION} `, "background:#1f6feb;color:#fff;border-radius:3px 0 0 3px", "background:#22d3ee;color:#000;border-radius:0 3px 3px 0");
