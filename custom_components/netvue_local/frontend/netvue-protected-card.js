const STYLE = `
  :host { display:block }
  ha-card { padding:16px }
  .filters { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 }
  label { display:grid; gap:4px; flex:1 1 130px }
  input, select, button { font:inherit; min-width:0 }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,230px),1fr)); gap:12px }
  article { border:1px solid var(--divider-color,#888); border-radius:8px; overflow:hidden }
  video { display:block; width:100%; aspect-ratio:16/9; background:#111 }
  .details { padding:10px; display:grid; gap:6px; overflow-wrap:anywhere }
  .actions { display:flex; flex-wrap:wrap; gap:8px }
`;

function node(tag, parent, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  parent.appendChild(element);
  return element;
}

export function filterProtected(items, filters) {
  return items.filter((item) => {
    const time = new Date(item.covered_start);
    const day = Number.isFinite(time.getTime())
      ? `${time.getFullYear()}-${String(time.getMonth() + 1).padStart(2, "0")}-${String(time.getDate()).padStart(2, "0")}`
      : "";
    return (!filters.camera || item.camera_host === filters.camera)
      && (!filters.date || day === filters.date)
      && (!filters.category || item.category === filters.category)
      && (!filters.protection || item.protection === filters.protection);
  });
}

export class NetvueProtectedCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._items = [];
    this._cameras = [];
    this._generation = 0;
    this._renderGeneration = 0;
  }

  setConfig(config) {
    this._config = config;
    this._build();
    if (this._hass) this._load();
  }

  set hass(hass) {
    this._hass = hass;
    const cameras = Object.values(hass.states || {})
      .filter((state) => state.entity_id?.startsWith("camera.") && state.attributes?.netvue_entry_id)
      .map((state) => ({
        entry: state.attributes.netvue_entry_id,
        host: state.attributes.netvue_host,
        name: state.attributes.friendly_name || state.entity_id,
      }));
    const key = JSON.stringify(cameras);
    if (key !== this._cameraKey) {
      this._cameraKey = key;
      this._cameras = cameras;
      if (this._config) this._load();
    }
  }

  _build() {
    this.shadowRoot.replaceChildren();
    node("style", this.shadowRoot, STYLE);
    const card = node("ha-card", this.shadowRoot);
    node("h2", card, "Vidéos protégées");
    const filters = node("div", card);
    filters.className = "filters";
    this._filters = {};
    for (const [key, label, type] of [
      ["camera", "Caméra", "select"], ["date", "Date", "date"],
      ["category", "Catégorie", "select"], ["protection", "Origine", "select"],
    ]) {
      const wrapper = node("label", filters);
      node("span", wrapper, label);
      const input = node(type === "select" ? "select" : "input", wrapper);
      if (type !== "select") input.type = type;
      input.addEventListener("change", () => this._renderItems());
      this._filters[key] = input;
    }
    this._options(this._filters.protection, [["", "Toutes"], ["manual", "Manuelle"], ["auto", "Automatique"]]);
    const refresh = node("button", filters, "Actualiser");
    refresh.type = "button";
    refresh.addEventListener("click", () => this._load());
    this._message = node("p", card);
    this._message.setAttribute("role", "status");
    this._health = node("p", card);
    this._health.setAttribute("role", "status");
    this._grid = node("div", card);
    this._grid.className = "grid";
    const history = node("details", card);
    const summary = node("summary", history, "Historique des événements IA");
    summary.addEventListener("click", () => {
      if (!history.open) this._loadEvents();
    });
    this._events = node("div", history);
    const rules = node("details", card);
    node("summary", rules, "Règles d'enregistrement automatique");
    this._rules = node("div", rules);
    const ruleFields = [];
    for (const [key, label, type, value] of [
      ["name", "Nom", "text", "Personne à l'entrée"],
      ["category", "Catégorie", "select", ""],
      ["zone", "Zone", "select", ""],
      ["start", "Début", "time", "22:00"],
      ["end", "Fin", "time", "06:00"],
      ["confidence", "Confiance", "number", "0.38"],
    ]) {
      const wrapper = node("label", this._rules);
      node("span", wrapper, label);
      const input = node(type === "select" ? "select" : "input", wrapper);
      if (type !== "select") input.type = type;
      if (type === "number") input.min = "0.1", input.max = "0.95", input.step = "0.01";
      if (type === "text") input.value = value;
      if (type === "time") input.value = value;
      this[`_rule_${key}`] = input;
      ruleFields.push(input);
    }
    this._options(this._rule_category, [
      ["*", "Toutes les catégories"], ["person", "Personne"], ["bird", "Oiseau"],
      ["cat", "Chat"], ["dog", "Chien"], ["car", "Voiture"], ["bus", "Bus"],
      ["truck", "Camion"], ["bicycle", "Vélo"], ["motorcycle", "Moto"],
    ]);
    this._options(this._rule_zone, [["", "Toute l'image"]]);
    const targetsLabel = node("label", this._rules);
    node("span", targetsLabel, "Cibles notifications");
    this._notify_targets = node("input", targetsLabel);
    this._notify_targets.placeholder = "mobile_app_iphone";
    const cooldownLabel = node("label", this._rules);
    node("span", cooldownLabel, "Délai anti-doublon (secondes)");
    this._notify_cooldown = node("input", cooldownLabel);
    this._notify_cooldown.type = "number";
    this._notify_cooldown.min = "0";
    this._notify_cooldown.max = "86400";
    this._notify_cooldown.value = "30";
    const saveRules = node("button", this._rules, "Enregistrer la règle");
    saveRules.type = "button";
    saveRules.addEventListener("click", () => this._saveRules());
  }

  _options(select, choices) {
    const old = select.value;
    select.replaceChildren();
    for (const [value, title] of choices) {
      const option = node("option", select, title);
      option.value = value;
    }
    select.value = choices.some(([value]) => value === old) ? old : "";
  }

  async _load() {
    const generation = ++this._generation;
    ++this._renderGeneration;
    this._items = [];
    this._grid.replaceChildren();
    this._health.textContent = "";
    const camera = this._cameras?.[0];
    if (!camera) {
      this._message.textContent = "Aucune caméra Netvue disponible.";
      return;
    }
    this._message.textContent = "Chargement…";
    this._loadHealth(generation);
    this._loadRules(generation);
    try {
      let result;
      for (const candidate of this._cameras) {
        try {
          result = await this._hass.callApi("GET", `netvue_local/protected/${encodeURIComponent(candidate.entry)}`);
          this._indexCamera = candidate;
          break;
        } catch {
          if (generation !== this._generation) return;
        }
      }
      if (generation !== this._generation) return;
      if (!Array.isArray(result?.protected)) throw new Error("Réponse invalide");
      this._items = result.protected.filter((item) =>
        item && typeof item.id === "string" && /^[0-9a-f]{32}$/.test(item.id));
      this._options(this._filters.camera, [
        ["", "Toutes"], ...this._cameras.filter((item) => item.host).map((item) => [item.host, item.name]),
      ]);
      const categories = [...new Set(this._items.map((item) => item.category).filter(Boolean))].sort();
      this._options(this._filters.category, [["", "Toutes"], ...categories.map((category) => [category, category])]);
      this._renderItems();
    } catch {
      if (generation === this._generation) this._message.textContent = "Bibliothèque indisponible.";
    }
  }

  async _loadHealth(generation) {
    let health;
    try {
      health = await this._hass.callApi("GET", "netvue_local/protected-health");
    } catch (error) {
      health = error?.body?.storage;
    }
    if (generation !== this._generation) return;
    if (!health || !Number.isFinite(health.free_bytes) || typeof health.healthy !== "boolean") {
      this._health.textContent = "État du stockage indisponible.";
      return;
    }
    const free = (health.free_bytes / 1073741824).toFixed(1);
    this._health.textContent = `Stockage ${health.healthy ? "disponible" : "insuffisant"} : ${free} Gio libres.`;
  }

  async _loadRules(generation) {
    const camera = this._cameras?.[0];
    if (!camera) return;
    try {
      const data = await this._hass.callApi("GET", `netvue_local/rules/${encodeURIComponent(camera.entry)}`);
      if (generation !== this._generation) return;
      const existing = Array.isArray(data.rules) ? data.rules[0] : null;
      const zones = Object.entries(data.zones || {}).map(([id, zone]) =>
        [id, zone?.name || id]);
      if (existing?.zone && !zones.some(([id]) => id === existing.zone)) {
        zones.push([existing.zone, existing.zone]);
      }
      this._rule_zone.replaceChildren();
      this._options(this._rule_zone, [["", "Toute l'image"], ...zones]);
      if (existing) {
        this._rule_name.value = existing.name || "";
        this._rule_category.value = existing.category || "*";
        this._rule_zone.value = existing.zone || "";
        this._rule_start.value = existing.start || "22:00";
        this._rule_end.value = existing.end || "06:00";
        this._rule_confidence.value = String(existing.confidence ?? 0.38);
      }
      this._notify_targets.value = (Array.isArray(data.notification_targets) ? data.notification_targets : []).join(",");
      this._notify_cooldown.value = String(data.notification_cooldown ?? 30);
    } catch {
      if (generation === this._generation) this._rulesMessage = "Règles IA indisponibles.";
    }
  }

  async _saveRules() {
    const camera = this._cameras?.[0];
    if (!camera) return;
    const targets = this._notify_targets.value.split(",").map((value) => value.trim()).filter(Boolean);
    const cooldown = Number.parseInt(this._notify_cooldown.value, 10);
    const rule = {
      name: this._rule_name.value,
      enabled: true,
      category: this._rule_category.value,
      zone: this._rule_zone.value || null,
      days: [0, 1, 2, 3, 4, 5, 6],
      start: this._rule_start.value,
      end: this._rule_end.value,
      confidence: Number.parseFloat(this._rule_confidence.value),
      confirmation_frames: 3,
      notification: true,
    };
    try {
      await this._hass.callApi("PUT", `netvue_local/rules/${encodeURIComponent(camera.entry)}`, {
        rules: [rule],
        notification_targets: targets,
        notification_cooldown: Number.isFinite(cooldown) ? cooldown : 30,
      });
      this._message.textContent = "Règle d'enregistrement automatique enregistrée.";
    } catch (error) {
      this._message.textContent = error?.body?.error || "Impossible d'enregistrer la règle.";
    }
  }

  async _loadEvents() {
    const generation = this._generation;
    this._events.textContent = "Chargement…";
    const results = await Promise.all(this._cameras.map(async (camera) => {
      try {
        const data = await this._hass.callApi("GET", `netvue_local/events/${encodeURIComponent(camera.entry)}`);
        return (Array.isArray(data?.events) ? data.events : []).map((event) => ({ camera, event }));
      } catch {
        return null;
      }
    }));
    if (generation !== this._generation) return;
    this._events.replaceChildren();
    const events = results.filter(Boolean).flat().sort((a, b) => b.event.timestamp - a.event.timestamp);
    if (results.includes(null)) node("p", this._events, "Certaines caméras sont indisponibles.");
    if (!events.length) node("p", this._events, "Aucun événement.");
    for (const { camera, event } of events) {
      const time = new Date(Number(event.timestamp) * 1000).toLocaleString();
      node("p", this._events, `${time} · ${camera.name} · ${event.category || "?"} · ${event.event || "?"}${event.zone ? ` · ${event.zone}` : ""}`);
    }
  }

  async _renderItems() {
    const generation = ++this._renderGeneration;
    const filters = Object.fromEntries(Object.entries(this._filters).map(([key, input]) => [key, input.value]));
    const items = filterProtected(this._items, filters).sort((a, b) =>
      Date.parse(b.covered_start) - Date.parse(a.covered_start));
    this._grid.replaceChildren();
    this._message.textContent = items.length ? `${items.length} vidéo(s)` : "Aucune vidéo protégée.";
    for (const item of items) {
      const camera = this._indexCamera;
      const path = `/api/netvue_local/protected/${encodeURIComponent(camera.entry)}/${item.id}`;
      const article = node("article", this._grid);
      const video = node("video", article);
      video.controls = true;
      video.preload = "metadata";
      video.setAttribute("playsinline", "");
      const details = node("div", article);
      details.className = "details";
      node("strong", details, item.title || "Vidéo protégée");
      const cameraName = this._cameras.find((value) => value.host === item.camera_host)?.name
        || item.camera_host || "Caméra inconnue";
      node("span", details, `${cameraName} · ${new Date(item.covered_start).toLocaleString()}`);
      const seconds = Math.max(0, (Date.parse(item.covered_end) - Date.parse(item.covered_start)) / 1000);
      node("span", details, `${item.category || "Sans catégorie"} · ${item.protection === "manual" ? "Manuelle" : "Automatique"} · ${Math.round(seconds)} s · ${(Number(item.size || 0) / 1048576).toFixed(1)} Mo`);
      const actions = node("div", details);
      actions.className = "actions";
      const download = node("a", actions, "Télécharger");
      download.setAttribute("download", `netvue-${item.id}.mp4`);
      const remove = node("button", actions, "Supprimer");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Supprimer définitivement cette vidéo protégée ?")) return;
        remove.disabled = true;
        try {
          await this._hass.callApi("DELETE", `netvue_local/protected/${encodeURIComponent(camera.entry)}/${item.id}`);
          this._items = this._items.filter((value) => value.id !== item.id);
          this._renderItems();
        } catch {
          this._message.textContent = "Suppression impossible.";
          remove.disabled = false;
        }
      });
      try {
        const [play, save] = await Promise.all([
          this._hass.callWS({ type: "auth/sign_path", path }),
          this._hass.callWS({ type: "auth/sign_path", path: `${path}?download=1` }),
        ]);
        if (generation !== this._renderGeneration) return;
        video.src = `${play.path}#t=0.1`;
        download.href = save.path;
      } catch {
        if (generation === this._renderGeneration) {
          this._message.textContent = "Impossible d'autoriser la lecture.";
          download.hidden = true;
        }
      }
    }
  }

  getCardSize() { return 4; }
}

if (!customElements.get("netvue-protected-card")) {
  customElements.define("netvue-protected-card", NetvueProtectedCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "netvue-protected-card")) {
  window.customCards.push({ type: "netvue-protected-card", name: "Netvue - Vidéos protégées" });
}
