import assert from "node:assert/strict";
import { test } from "node:test";
import { setImmediate } from "node:timers/promises";

class Element {
  constructor(tag = "element") {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.value = "";
    this.attributes = {};
  }
  attachShadow() { return this.shadowRoot = new Element("shadow"); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}
globalThis.HTMLElement = Element;
class SelectElement extends Element {
  get type() { return "select-one"; }
}
const definitions = new Map();
globalThis.customElements = {
  get: (name) => definitions.get(name),
  define: (name, value) => definitions.set(name, value),
};
globalThis.document = {
  createElement: (tag) => (tag === "select" ? new SelectElement(tag) : new Element(tag)),
};
globalThis.window = { confirm: () => false };
const { NetvueProtectedCard, filterProtected } = await import(
  "../../custom_components/netvue_local/frontend/netvue-protected-card.js"
);

const items = [
  { id: "a".repeat(32), camera_host: "one", category: "person", protection: "manual",
    covered_start: "2026-01-03T10:00:00", covered_end: "2026-01-03T10:01:00", size: 1024 },
  { id: "b".repeat(32), camera_host: "two", category: "car", protection: "auto",
    covered_start: "2026-01-04T10:00:00", covered_end: "2026-01-04T10:01:00", size: 1024 },
];

test("filters intersect camera, local date, category and origin", () => {
  assert.deepEqual(filterProtected(items, {
    camera: "two", date: "2026-01-04", category: "car", protection: "auto",
  }), [items[1]]);
  assert.deepEqual(filterProtected(items, {
    camera: "two", date: "2026-01-03", category: "car", protection: "auto",
  }), []);
});

test("library signs playback and download and only deletes after confirmation", async () => {
  const calls = [];
  const card = new NetvueProtectedCard();
  card.setConfig({});
  card.hass = {
    states: {
      "camera.one": { entity_id: "camera.one", attributes: {
        netvue_entry_id: "entry1", netvue_host: "one", friendly_name: "Entrée",
      } },
      "camera.two": { entity_id: "camera.two", attributes: {
        netvue_entry_id: "entry2", netvue_host: "two", friendly_name: "Jardin",
      } },
    },
    async callApi(method, path) {
      calls.push([method, path]);
      if (path === "netvue_local/protected-health") return { healthy: true, free_bytes: 2147483648 };
      return method === "GET" ? { protected: items } : {};
    },
    async callWS(request) {
      calls.push(["sign", request.path]);
      return { path: `${request.path}${request.path.includes("?") ? "&" : "?"}auth=signature` };
    },
  };
  await setImmediate();
  assert.match(card._health.textContent, /2\.0 Gio libres/);
  assert.equal(card._grid.children.length, 2);
  const first = card._grid.children[0];
  assert.match(first.children[0].src, /protected\/entry1\/b{32}\?auth=signature/);
  const actions = first.children[1].children[3];
  assert.match(actions.children[0].href, /download=1&auth=signature/);
  const remove = actions.children[1];
  await remove.listeners.click();
  assert.equal(calls.filter(([method]) => method === "DELETE").length, 0);
  window.confirm = () => true;
  await remove.listeners.click();
  assert.deepEqual(calls.find(([method]) => method === "DELETE"), [
    "DELETE", `netvue_local/protected/entry1/${"b".repeat(32)}`,
  ]);
  assert.equal(card._grid.children.length, 1);
});

test("library reports low space returned with a service error", async () => {
  const card = new NetvueProtectedCard();
  card.setConfig({});
  card.hass = {
    states: { "camera.one": { entity_id: "camera.one", attributes: {
      netvue_entry_id: "entry1", netvue_host: "one",
    } } },
    async callApi(method, path) {
      if (path === "netvue_local/protected-health") {
        throw { body: { storage: { healthy: false, free_bytes: 0 } } };
      }
      return { protected: [] };
    },
  };
  await setImmediate();
  assert.match(card._health.textContent, /insuffisant.*0\.0 Gio/);
});

test("library tries another camera if the first bridge session is unavailable", async () => {
  const paths = [];
  const card = new NetvueProtectedCard();
  card.setConfig({});
  card.hass = {
    states: Object.fromEntries(["one", "two"].map((host) => [
      `camera.${host}`, { entity_id: `camera.${host}`, attributes: {
        netvue_entry_id: host, netvue_host: host,
      } },
    ])),
    async callApi(method, path) {
      paths.push(path);
      if (path === "netvue_local/protected/one") throw new Error("offline");
      if (path === "netvue_local/protected-health") return { healthy: true, free_bytes: 0 };
      return { protected: [items[0]] };
    },
    async callWS(request) { return { path: request.path }; },
  };
  await setImmediate();
  assert.ok(paths.includes("netvue_local/protected/two"));
  assert.match(card._grid.children[0].children[0].src, /protected\/two\//);
});

test("event history displays persisted events from each camera", async () => {
  const card = new NetvueProtectedCard();
  card.setConfig({});
  card.hass = {
    states: { "camera.one": { entity_id: "camera.one", attributes: {
      netvue_entry_id: "one", netvue_host: "one", friendly_name: "Entrée",
    } } },
    async callApi(method, path) {
      if (path === "netvue_local/events/one") {
        return { events: [{ timestamp: 1800000000, category: "person", event: "zone_entered", zone: "porte" }] };
      }
      if (path === "netvue_local/protected-health") return { healthy: true, free_bytes: 0 };
      return { protected: [] };
    },
  };
  await setImmediate();
  await card._loadEvents();
  assert.match(card._events.children[0].textContent, /Entrée · person · zone_entered · porte/);
});
