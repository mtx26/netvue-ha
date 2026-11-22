import assert from "node:assert/strict";
import { test } from "node:test";

class Element {
  constructor() {
    this.children = [];
    this.elements = new Map();
    this.listeners = new Map();
    this.hidden = false;
  }
  attachShadow() { return this.shadowRoot = new Element(); }
  querySelector(selector) {
    if (!this.elements.has(selector)) this.elements.set(selector, new Element());
    return this.elements.get(selector);
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  toggleAttribute() {}
  setAttribute() {}
  get options() { return this.children; }
}

globalThis.HTMLElement = Element;
const definitions = new Map();
globalThis.customElements = {
  get: (name) => definitions.get(name),
  define: (name, value) => definitions.set(name, value),
};
globalThis.document = { createElement: () => new Element() };
globalThis.Option = class {
  constructor(text, value) { this.text = text; this.value = value; }
};
globalThis.window = { addEventListener() {}, removeEventListener() {} };
globalThis.location = { pathname: "/netvue-settings", search: "?config_entry=entry-offline" };

const { NetvueSettingsPanel } = await import("../../custom_components/netvue_local/frontend/netvue-settings-panel.js");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function panelWithHass(callApi) {
  const panel = new NetvueSettingsPanel();
  panel.isConnected = true;
  panel.hass = {
    language: "fr",
    states: {
      "camera.garage": { attributes: { netvue_entry_id: "entry-online", friendly_name: "Garage" } },
      "camera.entree": { state: "unavailable", attributes: {} },
      "camera.jardin": { attributes: { netvue_entry_id: "entry-jardin", friendly_name: "Jardin" } },
    },
    callApi,
    async callWS() { return { resources: {} }; },
  };
  panel.connectedCallback();
  return panel;
}

test("settings select a configured camera without an entity and save its new address", async () => {
  const calls = [];
  const panel = panelWithHass(async (method, path, body) => {
    calls.push({ method, path, body });
    if (method === "GET") return [
      { entry_id: "entry-offline", title: "Entrée", domain: "netvue_local" },
      { entry_id: "entry-online", title: "Garage", domain: "netvue_local" },
      { entry_id: "entry-jardin", title: "Jardin", domain: "netvue_local" },
    ];
    if (path === "config/config_entries/options/flow") {
      return { type: "menu", flow_id: "flow1", menu_options: ["camera"] };
    }
    if (body.next_step_id) {
      return { type: "form", data_schema: [{ name: "host", type: "string", default: "192.168.1.1" }] };
    }
    return { type: "create_entry" };
  });
  await tick();
  assert.deepEqual(panel._select.options.map(({ text, value }) => [text, value]), [
    ["Entrée", "entry-offline"], ["Garage", "entry-online"], ["Jardin", "entry-jardin"],
  ]);
  assert.equal(panel._select.value, "entry-offline");
  assert.equal(panel._entryId, "entry-offline");
  assert.equal(panel._entityId, null);
  panel._inputs.get("host").value = "192.168.1.2";
  await panel._submit();
  assert.ok(calls.some(({ method, path, body }) =>
    method === "POST" && path.endsWith("/flow1") && body.host === "192.168.1.2"));
});

test("camera list errors are visible and retryable", async () => {
  let attempts = 0;
  const panel = panelWithHass(async (method) => {
    if (method === "GET" && ++attempts === 1) throw new Error("offline");
    return [];
  });
  await tick();
  assert.equal(panel._message.textContent, "Impossible de charger la configuration.");
  assert.equal(panel._retry.hidden, false);
  panel._retry.listeners.get("click")();
  await tick();
  assert.equal(attempts, 2);
  assert.equal(panel._message.textContent, "Aucune caméra Netvue disponible.");
});

test("a late camera list is ignored after the panel disconnects", async () => {
  let resolve;
  const panel = panelWithHass(() => new Promise((done) => { resolve = done; }));
  panel.disconnectedCallback();
  panel.isConnected = false;
  resolve([{ entry_id: "late", title: "Late" }]);
  await tick();
  assert.equal(panel._entries, null);
});

test("zone preview reports an unavailable image without an entity", async () => {
  const Editor = customElements.get("netvue-detection-zone-editor");
  const editor = new Editor();
  editor._entityId = null;
  editor._t = (key) => key;
  editor._stage = new Element();
  editor._status = new Element();
  editor._hass = { callWS() { throw new Error("unexpected request"); } };
  await editor._loadPreview();
  assert.equal(editor._stage.hidden, true);
  assert.equal(editor._status.textContent, "zoneImageError");
});
