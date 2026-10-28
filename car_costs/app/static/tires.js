// Frise des pneus : une ligne par essieu, une barre par période de montage d'une paire.
// Chaque modification est envoyée au serveur, qui renvoie le nouvel état complet.
(() => {
  const BASE = document.currentScript.dataset.base || "";
  const DAY = 864e5;
  const PX = 0.6; // pixels par jour
  const AXLES = [["avant", "Avant"], ["arriere", "Arrière"]];
  const COLORS = ["#2f6fb3", "#7a5cc7", "#c7781c", "#2e8b57", "#b8457a", "#4b8f9c", "#8a6d3b"];

  let data = JSON.parse(document.getElementById("tl-data").textContent);
  let selected = null; // id de période
  let picked = null; // paire touchée au téléphone, en attente d'un essieu
  let busy = false;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const parse = (iso) => Date.parse(iso + "T12:00:00");
  const iso = (t) => { const d = new Date(t); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const fdate = (t) => { const d = new Date(typeof t === "string" ? parse(t) : t); return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`; };
  const fkm = (n) => (n == null ? "–" : Math.round(n).toLocaleString("fr-CH"));

  // Échelle : du 1er janvier de l'année de départ à 5 mois après aujourd'hui.
  let START, TODAY, END, WIDTH;
  const scale = () => {
    TODAY = parse(data.today);
    START = new Date(new Date(parse(data.start)).getFullYear(), 0, 1, 12).getTime();
    END = TODAY + 150 * DAY;
    WIDTH = Math.round((END - START) / DAY * PX);
  };
  const x = (t) => (t - START) / DAY * PX;
  const timeAt = (px) => START + Math.round(px / PX) * DAY;

  const pairs = () => data.sets.flatMap((s, i) => s.pairs.map((p) => ({ ...p, set: s, color: COLORS[i % COLORS.length] })));
  const pair = (id) => pairs().find((p) => p.id === id);
  const periods = (axle) => data.periods.filter((p) => p.axle === axle);
  const until = (p) => (p.end ? parse(p.end) : TODAY);

  const toast = (msg, err = false) => { const el = $("tl-toast"); el.textContent = msg || ""; el.className = "tl-toast" + (err ? " err" : ""); };

  async function send(action, body) {
    if (busy) return false;
    busy = true;
    try {
      const res = await fetch(`${BASE}/tires/timeline/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const out = await res.json().catch(() => ({ error: "Réponse illisible du serveur" }));
      if (!res.ok || out.error) { toast(out.error || `Erreur ${res.status}`, true); render(); return false; }
      data = out.timeline;
      toast(out.message);
      if (selected && !data.periods.some((p) => p.id === selected)) selected = null;
      render();
      return true;
    } catch (err) {
      toast("Add-on injoignable, réessaie", true);
      render();
      return false;
    } finally {
      busy = false;
    }
  }

  // ---------------------------------------------------------------- rendu

  function render() {
    scale();
    let axis = "";
    for (let y = new Date(START).getFullYear(); y <= new Date(END).getFullYear(); y++) {
      for (let m = 0; m < 12; m++) {
        const t = new Date(y, m, 1, 12).getTime();
        if (t < START || t > END) continue;
        axis += m === 0 ? `<div class="tl-tick" style="left:${x(t)}px">${y}</div>` : `<div class="tl-tick month" style="left:${x(t)}px"></div>`;
      }
    }
    $("tl").innerHTML = `<div class="tl-axis" style="width:${WIDTH}px">${axis}</div>` + AXLES.map(([axle, name]) => {
      const ps = periods(axle);
      return `<div class="tl-lane"><div class="tl-label">${name}</div>
        <div class="tl-track ${picked ? "armed" : ""}" data-axle="${axle}" style="width:${WIDTH}px">
          <div class="tl-today" style="left:${x(TODAY)}px" title="Aujourd'hui"></div>
          ${ps.map(bar).join("")}
          ${ps.map((p, i) => (i > 0 && ps[i - 1].end === p.start ? handle(p, name) : "")).join("")}
        </div></div>`;
    }).join("");
    renderSets();
    renderPanel();
  }

  function bar(p) {
    const info = pair(p.pair);
    const w = Math.max(4, x(until(p)) - x(parse(p.start)));
    const winter = info.set.season === "hiver";
    return `<div class="tl-bar ${winter ? "hiver" : ""} ${p.id === selected ? "selected" : ""}" tabindex="0" role="button"
      data-period="${p.id}" style="left:${x(parse(p.start))}px;width:${w}px;background-color:${info.color}"
      title="${esc(info.label)} · ${fdate(p.start)} → ${p.end ? fdate(p.end) : "aujourd'hui"}">
      ${w > 64 ? `${winter ? "Hiver" : info.set.season === "été" ? "Été" : "4 saisons"} · paire ${esc(info.name)}<small>${fkm(p.km)} km</small>` : ""}</div>`;
  }

  function handle(p, name) {
    return `<div class="tl-handle" tabindex="0" role="slider" data-period="${p.id}" style="left:${x(parse(p.start))}px"
      aria-label="Changement ${name} du ${fdate(p.start)}" aria-valuetext="${fdate(p.start)}"></div>`;
  }

  function renderSets() {
    if (!data.sets.length) {
      $("tl-sets").innerHTML = `<p class="muted">Aucun achat de pneus. Commence par « Nouvel achat ».</p>`;
      return;
    }
    $("tl-sets").innerHTML = data.sets.map((s, i) => `
      <div class="tl-set">
        <div class="tl-set-name"><span class="tl-swatch" style="background:${COLORS[i % COLORS.length]}"></span>
          <a href="${BASE}/tires/${s.id}">${esc(s.label)}</a></div>
        <div class="muted">${esc(s.season)}${s.size ? ` · ${esc(s.size)}` : ""} · ${s.count} pneus${s.purchase_date ? ` · achat ${fdate(s.purchase_date)}` : ""}</div>
        <div class="tl-chips">${s.pairs.map((p) => {
          const cls = p.retired ? "retired" : p.axle ? "mounted" : "free";
          const status = p.retired ? `éliminée le ${fdate(p.retired)}` : p.axle ? `à l'${p.axle === "avant" ? "avant" : "arrière"}` : "au garage";
          return `<button type="button" class="tl-chip ${cls} ${picked === p.id ? "picked" : ""}" data-pair="${p.id}"
            title="${fkm(p.km)} km${p.tread != null ? ` · ${p.tread} mm` : ""}">
            <i style="background:${COLORS[i % COLORS.length]}"></i><i style="background:${COLORS[i % COLORS.length]}"></i>
            paire ${esc(p.name)} · ${status} · ${fkm(p.km)} km</button>`;
        }).join("")}</div>
      </div>`).join("");
  }

  function renderPanel() {
    const panel = $("tl-panel");
    if (picked) return pickedPanel(panel);
    const p = data.periods.find((q) => q.id === selected);
    if (!p) { panel.hidden = true; return; }
    const info = pair(p.pair);
    const isLast = !data.periods.some((q) => q.pair === p.pair && parse(q.start) > parse(p.start));
    panel.hidden = false;
    panel.innerHTML = `<h3>${esc(info.label)} · ${p.axle === "avant" ? "avant" : "arrière"}</h3>
      <div class="tl-facts">
        <div><span>Montée le</span><b>${fdate(p.start)}</b></div>
        <div><span>Démontée le</span><b>${p.end ? fdate(p.end) : "toujours montée"}</b></div>
        <div><span>Km sur cette période</span><b>${fkm(p.km)}</b></div>
        <div><span>Km de la paire</span><b>${fkm(info.km)}</b></div>
        <div><span>Sculpture au démontage</span><b>${p.tread != null ? `${p.tread} mm` : "–"}</b></div>
      </div>
      <div class="tl-row">
        <input id="tl-tread" inputmode="decimal" placeholder="mm" value="${p.tread ?? ""}" aria-label="Sculpture en mm" size="5">
        <button type="button" class="secondary" data-do="tread">Noter la sculpture</button>
      </div>
      ${!p.end ? `<div class="tl-row">
        <input type="date" id="tl-end" value="${data.today}" max="${data.today}" min="${iso(parse(p.start) + DAY)}" aria-label="Date de démontage">
        <button type="button" class="secondary" data-do="end">Démonter sans remplacer</button></div>` : ""}
      <div class="tl-row">
        ${isLast && !info.retired ? `<input type="date" id="tl-retire" value="${p.end || data.today}" max="${data.today}" aria-label="Date d'élimination">
          <button type="button" class="secondary" data-do="retire">Éliminer la paire (usée ou vendue)</button>` : ""}
        <button type="button" class="link danger" data-do="delete">Supprimer cette période</button>
        <button type="button" class="link" data-do="close">Fermer</button>
      </div>`;
  }

  function pickedPanel(panel) {
    const info = pair(picked);
    panel.hidden = false;
    panel.innerHTML = `<h3>Monter ${esc(info.label)}</h3>
      <p class="muted">Touche un essieu sur la frise à la date voulue, ou choisis une date précise :</p>
      <div class="tl-row">
        <input type="date" id="tl-mount-date" value="${data.today}" max="${data.today}" aria-label="Date de montage">
        <button type="button" data-do="mount" data-axle="avant">À l'avant</button>
        <button type="button" data-do="mount" data-axle="arriere">À l'arrière</button>
        <button type="button" class="link" data-do="unpick">Annuler</button>
      </div>`;
  }

  // ---------------------------------------------------------------- panneau

  $("tl-panel").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-do]");
    if (!btn) return;
    const what = btn.dataset.do;
    if (what === "close") { selected = null; return render(); }
    if (what === "unpick") { picked = null; return render(); }
    if (what === "mount") {
      const id = picked;
      picked = null;
      return send("mount", { pair: id, axle: btn.dataset.axle, date: $("tl-mount-date").value });
    }
    const p = data.periods.find((q) => q.id === selected);
    if (!p) return;
    const tread = $("tl-tread")?.value || null;
    if (what === "tread") return send("tread", { period: p.id, tread_mm: tread });
    if (what === "end") return send("end", { period: p.id, date: $("tl-end").value, tread_mm: tread });
    if (what === "retire") return send("retire", { pair: p.pair, date: $("tl-retire").value, tread_mm: tread });
    if (what === "delete") {
      if (btn.dataset.confirm) return send("delete", { period: p.id });
      btn.dataset.confirm = "1";
      btn.textContent = "Confirmer la suppression";
    }
  });

  // ---------------------------------------------------------------- frise : clic, poignées, clavier

  const tip = document.createElement("div");
  tip.className = "tl-tip";
  tip.hidden = true;
  document.body.appendChild(tip);
  const showTip = (e, text) => { tip.hidden = false; tip.textContent = text; tip.style.left = `${e.clientX + 12}px`; tip.style.top = `${e.clientY - 32}px`; };

  $("tl").addEventListener("click", (ev) => {
    const track = ev.target.closest(".tl-track");
    if (picked && track) {
      const id = picked;
      picked = null;
      const t = Math.min(timeAt(ev.clientX - track.getBoundingClientRect().left), TODAY);
      return send("mount", { pair: id, axle: track.dataset.axle, date: iso(t) });
    }
    const b = ev.target.closest(".tl-bar");
    if (b) { selected = +b.dataset.period; render(); }
  });

  // Les poignées sur l'autre essieu au même jour bougent ensemble si « lier » est coché.
  function linkedHandles(period) {
    const handles = [...$("tl").querySelectorAll(`.tl-handle[data-period="${period.id}"]`)];
    if ($("tl-link").checked) {
      for (const other of data.periods) {
        if (other.axle !== period.axle && other.start === period.start) {
          const h = $("tl").querySelector(`.tl-handle[data-period="${other.id}"]`);
          if (h) handles.push(h);
        }
      }
    }
    return handles;
  }

  function previewMove(period, t) {
    for (const h of linkedHandles(period)) {
      const p = data.periods.find((q) => q.id === +h.dataset.period);
      const track = h.parentElement;
      const bar = track.querySelector(`.tl-bar[data-period="${p.id}"]`);
      const prev = periods(p.axle).find((q) => q.end === p.start);
      const prevBar = prev && track.querySelector(`.tl-bar[data-period="${prev.id}"]`);
      h.style.left = `${x(t)}px`;
      if (bar) { bar.style.left = `${x(t)}px`; bar.style.width = `${Math.max(4, x(until(p)) - x(t))}px`; }
      if (prevBar) prevBar.style.width = `${Math.max(4, x(t) - x(parse(prev.start)))}px`;
    }
  }

  $("tl").addEventListener("pointerdown", (ev) => {
    const h = ev.target.closest(".tl-handle");
    if (!h || busy) return;
    ev.preventDefault();
    const period = data.periods.find((q) => q.id === +h.dataset.period);
    const prev = periods(period.axle).find((q) => q.end === period.start);
    const low = prev ? parse(prev.start) + DAY : START;
    const high = Math.min(until(period), TODAY + DAY) - DAY;
    const track = h.parentElement;
    let t = parse(period.start);
    h.setPointerCapture(ev.pointerId);
    h.classList.add("active");
    const move = (e) => {
      t = Math.min(Math.max(timeAt(e.clientX - track.getBoundingClientRect().left), low), high);
      previewMove(period, t);
      showTip(e, fdate(t));
    };
    const up = () => {
      h.removeEventListener("pointermove", move);
      h.removeEventListener("pointerup", up);
      h.removeEventListener("pointercancel", up);
      tip.hidden = true;
      h.classList.remove("active");
      if (iso(t) !== period.start) send("move", { period: period.id, date: iso(t), linked: $("tl-link").checked });
    };
    h.addEventListener("pointermove", move);
    h.addEventListener("pointerup", up);
    h.addEventListener("pointercancel", up);
  });

  $("tl").addEventListener("keydown", (ev) => {
    const h = ev.target.closest(".tl-handle");
    if (h && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) {
      ev.preventDefault();
      const period = data.periods.find((q) => q.id === +h.dataset.period);
      const step = (ev.shiftKey ? 7 : 1) * (ev.key === "ArrowLeft" ? -1 : 1);
      send("move", { period: period.id, date: iso(parse(period.start) + step * DAY), linked: $("tl-link").checked }).then(() =>
        $("tl").querySelector(`.tl-handle[data-period="${period.id}"]`)?.focus());
      return;
    }
    const b = ev.target.closest(".tl-bar");
    if (b && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); b.click(); }
  });

  // ---------------------------------------------------------------- garage : glisser ou toucher une paire

  $("tl-sets").addEventListener("pointerdown", (ev) => {
    const chip = ev.target.closest(".tl-chip.free");
    if (!chip || busy || ev.pointerType === "touch") return; // au doigt : toucher puis choisir l'essieu
    ev.preventDefault();
    const id = +chip.dataset.pair;
    const start = [ev.clientX, ev.clientY];
    let ghost = null, target = null, t = null;
    const tracks = [...$("tl").querySelectorAll(".tl-track")];
    chip.setPointerCapture(ev.pointerId);
    const move = (e) => {
      if (!ghost) {
        if (Math.hypot(e.clientX - start[0], e.clientY - start[1]) < 6) return;
        ghost = chip.cloneNode(true);
        ghost.classList.add("tl-ghost");
        document.body.appendChild(ghost);
      }
      ghost.style.left = `${e.clientX}px`;
      ghost.style.top = `${e.clientY}px`;
      target = tracks.find((tr) => { const r = tr.getBoundingClientRect(); return e.clientY >= r.top && e.clientY <= r.bottom && e.clientX >= r.left && e.clientX <= r.right; }) || null;
      tracks.forEach((tr) => tr.classList.toggle("drop", tr === target));
      if (target) {
        t = Math.min(timeAt(e.clientX - target.getBoundingClientRect().left), TODAY);
        showTip(e, `Monter à l'${target.dataset.axle === "avant" ? "avant" : "arrière"} le ${fdate(t)}`);
      } else tip.hidden = true;
    };
    const up = () => {
      chip.removeEventListener("pointermove", move);
      chip.removeEventListener("pointerup", up);
      chip.removeEventListener("pointercancel", up);
      tip.hidden = true;
      tracks.forEach((tr) => tr.classList.remove("drop"));
      if (ghost) {
        ghost.remove();
        if (target) send("mount", { pair: id, axle: target.dataset.axle, date: iso(t) });
      } else {
        picked = picked === id ? null : id; // simple clic : même chemin qu'au doigt
        selected = null;
        render();
      }
    };
    chip.addEventListener("pointermove", move);
    chip.addEventListener("pointerup", up);
    chip.addEventListener("pointercancel", up);
  });

  $("tl-sets").addEventListener("click", (ev) => {
    const chip = ev.target.closest(".tl-chip");
    if (!chip) return;
    const id = +chip.dataset.pair;
    if (chip.classList.contains("free")) {
      if (ev.detail === 0 || ev.pointerType === "touch" || matchMedia("(pointer: coarse)").matches) {
        picked = picked === id ? null : id; // clavier ou doigt
        selected = null;
        render();
      }
      return;
    }
    const open = data.periods.find((p) => p.pair === id && !p.end);
    if (open) { selected = open.id; picked = null; render(); }
  });

  $("tl-season").addEventListener("click", () => send("season", { date: $("tl-season-date").value }));

  render();
  const sc = $("tl-scroller");
  sc.scrollLeft = Math.max(0, x(TODAY) - sc.clientWidth + 160);
})();
