import "./detection-zone-editor.js";
import { translator } from "./i18n.js";

const FLOW_PATH = "config/config_entries/options/flow";
const PAGES = ["camera", "detection", "zones", "player", "global_settings"];
const TRANSLATION_PREFIX = "component.netvue_local.options.";

const STYLE = `
  :host { display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); }
  main { max-width:900px; margin:0 auto; padding:16px; }
  header, nav { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:16px; }
  nav { margin-top:16px; }
  header h1 { flex:1 1 200px; margin:0; font-size:24px; }
  select, input, button { font:inherit; color:inherit; }
  select, input:not([type=checkbox]), button { min-height:40px; padding:6px 12px; box-sizing:border-box;
    border:1px solid var(--divider-color, #999); border-radius:8px; background:var(--card-background-color, #fff); }
  button { cursor:pointer; }
  button:disabled { cursor:default; opacity:.6; }
  nav button[aria-current], [data-save] { border-color:var(--primary-color); background:var(--primary-color);
    color:var(--text-primary-color, #fff); }
  section { padding:16px; border-radius:12px; background:var(--card-background-color, #fff); }
  section h2 { margin:0; font-size:20px; }
  form label { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px;
    padding:10px 0; border-bottom:1px solid var(--divider-color); }
  form input:not([type=checkbox]) { width:min(100%, 250px); }
  [data-save] { margin-top:16px; }
  a { color:var(--primary-color); }
  [hidden] { display:none !important; }
`;

export function entityForEntry(states, entryId) {
  const cameras = Object.entries(states || {})
    .filter(([entityId, state]) => entityId.startsWith("camera.") && state?.attributes?.netvue_entry_id)
    .map(([entityId, state]) => ({
      entityId,
      entryId: state.attributes.netvue_entry_id,
      name: state.attributes.friendly_name || entityId,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const selected = entryId ? cameras.find((camera) => camera.entryId === entryId) : cameras[0];
  return { cameras, selected: selected || null };
}

export class NetvueSettingsPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    this._sync();
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `<style>${STYLE}</style><main>
        <header><h1 data-title></h1><a data-back href="/config/integrations/integration/netvue_local"></a></header>
        <label><span data-camera-label></span> <select data-camera></select></label>
        <nav aria-label="Netvue"></nav>
        <section hidden><h2></h2><p data-description></p><form>
          <div data-fields></div><button type="submit" data-save hidden></button>
        </form></section>
        <p data-message role="status"></p><button type="button" data-retry hidden></button>
      </main>`;
      this._select = root.querySelector("[data-camera]");
      this._select.addEventListener("change", () => this._navigate(this._select.value, this._page));
      this._nav = root.querySelector("nav");
      this._buttons = PAGES.map((page) => {
        const button = document.createElement("button");
        button.type = "button";
        button.addEventListener("click", () => this._navigate(this._entryId, page));
        this._nav.appendChild(button);
        return button;
      });
      this._section = root.querySelector("section");
      this._description = root.querySelector("[data-description]");
      this._fields = root.querySelector("[data-fields]");
      this._save = root.querySelector("[data-save]");
      this._message = root.querySelector("[data-message]");
      this._retry = root.querySelector("[data-retry]");
      this._retry.addEventListener("click", () => this._open());
      root.querySelector("form").addEventListener("submit", (event) => {
        event.preventDefault();
        this._submit();
      });
      this._locationChanged = () => this._sync();
    }
    window.addEventListener("popstate", this._locationChanged);
    window.addEventListener("location-changed", this._locationChanged);
    this._sync();
  }

  disconnectedCallback() {
    window.removeEventListener("popstate", this._locationChanged);
    window.removeEventListener("location-changed", this._locationChanged);
    this._pageKey = null;
    this._request = null;
    this._discardFlow();
  }

  _navigate(entryId, page) {
    const query = new URLSearchParams({ config_entry: entryId, page });
    history.pushState(null, "", `${location.pathname}?${query}`);
    this._sync();
  }

  _sync() {
    const root = this.shadowRoot;
    if (!root || !this._hass || !this.isConnected) return;
    const params = new URLSearchParams(location.search);
    const requestedPage = params.get("page");
    const page = PAGES.includes(requestedPage) ? requestedPage : "camera";
    const { cameras, selected } = entityForEntry(
      this._hass.states, params.get("config_entry") || params.get("entry_id"),
    );
    const language = this._hass.locale?.language || this._hass.language || "en";
    const t = translator(language);
    root.querySelector("[data-title]").textContent = t("settingsTitle");
    root.querySelector("[data-camera-label]").textContent = t("settingsCamera");
    root.querySelector("[data-back]").textContent = t("settingsBack");
    this._save.textContent = t("zoneSave");
    this._retry.textContent = t("settingsRetry");
    const options = JSON.stringify(cameras);
    if (options !== this._options) {
      this._options = options;
      this._select.replaceChildren(...cameras.map((camera) => new Option(camera.name, camera.entryId)));
    }
    this._select.value = selected?.entryId || "";
    this._select.disabled = !cameras.length || Boolean(this._saving);
    this._nav.hidden = !selected;
    const key = JSON.stringify([selected?.entityId, selected?.entryId, page, language]);
    if (key === this._pageKey) return;
    this._pageKey = key;
    this._entryId = selected?.entryId;
    this._entityId = selected?.entityId;
    this._page = page;
    this._language = language;
    this._t = t;
    this._updateTabs();
    this._open();
  }

  _discardFlow() {
    const flowId = this._flowId;
    this._flowId = null;
    if (flowId) this._deleteFlow(flowId);
  }

  _deleteFlow(flowId) {
    return this._hass.callApi("DELETE", `${FLOW_PATH}/${flowId}`).catch(() => {});
  }

  async _open(saved = false) {
    const request = this._request = {};
    this._discardFlow();
    this._fields.replaceChildren();
    this._save.hidden = true;
    this._retry.hidden = true;
    this._section.hidden = true;
    this._message.textContent = this._t(this._entryId ? (saved ? "zoneSaved" : "settingsLoading") : "settingsNoCamera");
    if (!this._entryId) return;

    const entryId = this._entryId;
    const page = this._page;
    try {
      const translations = this._translationsLanguage === this._language
        ? this._translations
        : (await this._hass.callWS({
          type: "frontend/get_translations", language: this._language,
          category: "options", integration: ["netvue_local"],
        })).resources;
      if (request !== this._request) return;
      this._translations = translations;
      this._translationsLanguage = this._language;
      this._updateTabs();
      const menu = await this._hass.callApi("POST", FLOW_PATH, { handler: entryId });
      if (request !== this._request) {
        if (menu.flow_id) await this._deleteFlow(menu.flow_id);
        return;
      }
      this._flowId = menu.flow_id;
      if (menu.type !== "menu" || !menu.menu_options.includes(page)) throw new Error("Missing options page");
      const flowId = this._flowId;
      const form = await this._hass.callApi("POST", `${FLOW_PATH}/${flowId}`, { next_step_id: page });
      if (request !== this._request) return;
      if (form.type !== "form") throw new Error("Missing options form");
      this._showForm(form);
      this._message.textContent = saved ? this._t("zoneSaved") : "";
    } catch {
      if (request !== this._request) return;
      this._discardFlow();
      this._message.textContent = this._t("zoneLoadError");
      this._retry.hidden = false;
    }
  }

  _label(key, fallback = "", placeholders = {}) {
    const message = this._translations?.[TRANSLATION_PREFIX + key] || fallback;
    return message.replace(/\{(\w+)\}/g, (match, name) => placeholders[name] ?? match);
  }

  _updateTabs() {
    this._buttons.forEach((button, index) => {
      const page = PAGES[index];
      button.textContent = this._label(`step.${page}.title`, this._t(`settings_${page}`));
      button.toggleAttribute("aria-current", page === this._page);
      if (page === this._page) button.setAttribute("aria-current", "page");
      button.disabled = Boolean(this._saving);
    });
  }

  _showForm(form) {
    const step = `step.${this._page}`;
    this._zoneEditor = null;
    this._inputs = new Map();
    this._section.querySelector("h2").textContent = this._label(`${step}.title`, this._t(`settings_${this._page}`));
    this._description.textContent = this._page === "zones" ? ""
      : this._label(`${step}.description`, "", form.description_placeholders);
    this._description.hidden = !this._description.textContent;
    this._fields.replaceChildren();
    for (const field of form.data_schema) {
      if (field.name === "ai_zones") {
        this._zoneEditor = document.createElement("netvue-detection-zone-editor");
        this._zoneEditor.configure(this._t, field.default || {}, this._hass, this._entityId);
        this._fields.appendChild(this._zoneEditor);
        continue;
      }
      const label = document.createElement("label");
      label.textContent = this._label(`${step}.data.${field.name}`, field.name);
      const input = document.createElement("input");
      input.type = { boolean: "checkbox", integer: "number", float: "number" }[field.type]
        || (field.selector?.text?.type === "password" ? "password" : "text");
      if (input.type === "checkbox") {
        input.checked = Boolean(field.default);
      } else {
        input.value = field.default ?? "";
        input.required = Boolean(field.required) && field.name !== "turn_key_id";
      }
      if (input.type === "number") {
        input.step = field.type === "integer" ? "1" : "any";
        if (field.valueMin !== undefined) input.min = field.valueMin;
        if (field.valueMax !== undefined) input.max = field.valueMax;
      }
      label.appendChild(input);
      this._fields.appendChild(label);
      this._inputs.set(field.name, input);
    }
    this._section.hidden = false;
    this._save.hidden = false;
  }

  async _submit() {
    if (this._saving || !this._flowId) return;
    const request = this._request;
    const values = Object.fromEntries(Array.from(this._inputs, ([key, input]) => [key,
      input.type === "checkbox" ? input.checked : input.type === "number" ? input.valueAsNumber : input.value,
    ]));
    if (this._zoneEditor) values.ai_zones = this._zoneEditor.zones;
    this._saving = true;
    this._save.disabled = true;
    this._select.disabled = true;
    this._fields.inert = true;
    this._updateTabs();
    try {
      const result = await this._hass.callApi("POST", `${FLOW_PATH}/${this._flowId}`, values);
      if (request !== this._request) return;
      if (result.type === "create_entry") {
        this._flowId = null;
        await this._open(true);
      } else if (result.type === "form") {
        this._message.textContent = Object.values(result.errors || {})
          .map((error) => this._label(`error.${error}`, this._t("zoneSaveError"))).join(" ")
          || this._t("zoneSaveError");
      } else {
        this._discardFlow();
        this._message.textContent = this._t("zoneSaveError");
        this._retry.hidden = false;
      }
    } catch {
      if (request === this._request) {
        this._message.textContent = this._t("zoneSaveError");
      }
    } finally {
      this._saving = false;
      this._save.disabled = false;
      this._select.disabled = !this._select.options.length;
      this._fields.inert = false;
      this._updateTabs();
    }
  }
}

if (!customElements.get("netvue-settings-panel")) {
  customElements.define("netvue-settings-panel", NetvueSettingsPanel);
}
