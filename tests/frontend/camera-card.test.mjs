import assert from "node:assert/strict";
import { test } from "node:test";

class FakeElement {
  constructor(tagName = "element") {
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
    }
    return child;
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }

  dispatch(type, event = {}) {
    this.listeners.get(type)?.(event);
  }

  dispatchEvent(event) {
    this.dispatch(event.type, event);
    return true;
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  attachShadow() {
    this.shadowRoot = new FakeElement("shadow-root");
    return this.shadowRoot;
  }
}

class FakeDocument {
  constructor() {
    this.fullscreenElement = null;
    this.listeners = new Map();
  }

  createElement(tagName) {
    const Element = customElements.get(tagName);
    if (Element) {
      return new Element();
    }
    return new FakeElement(tagName);
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }

  removeEventListener(type) {
    this.listeners.delete(type);
  }

  dispatch(type) {
    this.listeners.get(type)?.();
  }

  async exitFullscreen() {
    this.fullscreenElement = null;
    this.dispatch("fullscreenchange");
  }
}

globalThis.HTMLElement = class extends FakeElement {};
const definitions = new Map();
globalThis.customElements = {
  define(name, element) {
    definitions.set(name, element);
  },
  get(name) {
    return definitions.get(name);
  },
};
globalThis.document = new FakeDocument();
globalThis.window = {
  customCards: [],
  addEventListener() {},
  removeEventListener() {},
};

const { NetvueCameraCard } = await import("../../custom_components/netvue_local/frontend/netvue-camera-card.js");

function findByClass(node, className) {
  if (node.getAttribute("class") === className) {
    return node;
  }
  for (const child of node.children) {
    const match = findByClass(child, className);
    if (match) {
      return match;
    }
  }
  return null;
}

test("the full-screen button enters fullscreen and tracks browser exits", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  card._wrap.requestFullscreen = async () => {
    document.fullscreenElement = card._wrap;
    document.dispatch("fullscreenchange");
  };
  card._render();
  card.connectedCallback();

  const button = findByClass(card.shadowRoot, "fullscreen");
  assert.ok(button);
  assert.equal(button.disabled, false);

  button.dispatch("click");
  await Promise.resolve();
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.getAttribute("aria-label"), "Exit full screen");

  await document.exitFullscreen();
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.getAttribute("aria-label"), "Open camera in full screen");
  card.disconnectedCallback();
});

test("the full-screen button is disabled when the browser lacks support", () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();

  const button = findByClass(card.shadowRoot, "fullscreen");
  assert.ok(button);
  assert.equal(button.disabled, true);
});

test("the camera wall creates one existing camera card per distinct entity", () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  assert.deepEqual(
    WallCard.getStubConfig({
      states: {
        "camera.front_door": { attributes: { brand: "Netvue" } },
        "camera.garage": { attributes: { brand: "Netvue" } },
        "camera.driveway": { attributes: { brand: "Netvue" } },
      },
    }).entities,
    ["camera.front_door", "camera.garage"],
  );
  const wall = new WallCard();
  const hass = { states: {} };
  wall.hass = hass;
  wall.setConfig({ entities: ["camera.front_door", "camera.garage"] });
  wall.connectedCallback();

  assert.deepEqual(wall._cards.map((card) => card._config.entity), [
    "camera.front_door",
    "camera.garage",
  ]);
  assert.ok(wall._cards.every((card) => card.hass === hass));
  assert.throws(
    () => wall.setConfig({ entities: ["camera.front_door", "camera.front_door"] }),
    /distinct camera entities/,
  );
});

test("the status card summarizes availability and renders optional session details", () => {
  const StatusCard = customElements.get("netvue-camera-status-card");
  const card = new StatusCard();
  card.setConfig({
    cameras: [
      {
        camera: "camera.front_door",
        unavailable: "binary_sensor.front_door_camera_unavailable",
        session: "sensor.front_door_session",
        diagnostic: "sensor.front_door_diagnostic",
      },
      {
        camera: "camera.garage",
        unavailable: "binary_sensor.garage_camera_unavailable",
        diagnostic: "sensor.garage_diagnostic",
      },
    ],
  });
  card.hass = {
    language: "en",
    states: {
      "camera.front_door": { attributes: { friendly_name: "Front door" } },
      "binary_sensor.front_door_camera_unavailable": { state: "off" },
      "sensor.front_door_session": { state: "supervising" },
      "sensor.front_door_diagnostic": { state: "healthy" },
      "camera.garage": { attributes: { friendly_name: "Garage" } },
      "binary_sensor.garage_camera_unavailable": { state: "on" },
      "sensor.garage_diagnostic": { state: "connection_failed" },
    },
    formatEntityState: (state) => `translated:${state.state}`,
  };
  card.connectedCallback();

  assert.equal(card._count.textContent, "1/2 available");
  assert.equal(card._grid.children.length, 2);
  assert.equal(card._grid.children[0].children[1].children[1].textContent, "Available");
  assert.equal(card._grid.children[0].children[2].children[1].textContent, "translated:supervising");
  assert.equal(card._grid.children[1].children[1].children[1].textContent, "Unavailable");
  assert.equal(card._grid.children[1].children[2].children[1].className, "problem");
});

test("the status card discovers matching Netvue entities when no camera list is configured", () => {
  const StatusCard = customElements.get("netvue-camera-status-card");
  const hass = {
    states: {
      "camera.entry": {},
      "binary_sensor.entry_camera_unavailable": { state: "off" },
      "sensor.entry_session": { state: "supervising" },
      "sensor.entry_diagnostic": { state: "healthy" },
    },
    entities: {
      "camera.entry": { entity_id: "camera.entry", device_id: "device-1" },
      "binary_sensor.entry_camera_unavailable": {
        entity_id: "binary_sensor.entry_camera_unavailable",
        device_id: "device-1",
        translation_key: "camera_unavailable",
      },
      "sensor.entry_session": {
        entity_id: "sensor.entry_session",
        device_id: "device-1",
        translation_key: "session",
      },
      "sensor.entry_diagnostic": {
        entity_id: "sensor.entry_diagnostic",
        device_id: "device-1",
        translation_key: "diagnostic",
      },
    },
  };
  const card = new StatusCard();
  assert.deepEqual(StatusCard.getStubConfig(hass).entities, ["camera.entry"]);

  card.setConfig({ type: "custom:netvue-camera-status-card" });
  card.hass = hass;
  card.connectedCallback();
  assert.equal(card._count.textContent, "1/1 available");
});

test("all three cards expose visual editors that emit their updated configuration", () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  const StatusCard = customElements.get("netvue-camera-status-card");
  const cases = [
    {
      editor: NetvueCameraCard.getConfigElement(),
      config: { type: "custom:netvue-camera-card", entity: "camera.entry" },
      values: { entity: "camera.garage", name: "Garage" },
    },
    {
      editor: WallCard.getConfigElement(),
      config: { type: "custom:netvue-camera-wall-card", entities: ["camera.entry"] },
      values: { entities: ["camera.entry", "camera.garage"] },
    },
    {
      editor: StatusCard.getConfigElement(),
      config: { type: "custom:netvue-camera-status-card", entities: ["camera.entry"] },
      values: { entities: ["camera.entry", "camera.garage"] },
    },
  ];

  for (const { editor, config, values } of cases) {
    editor.hass = { language: "en" };
    editor.setConfig(config);
    editor.connectedCallback();
    let updated;
    editor.addEventListener("config-changed", (event) => {
      updated = event.detail.config;
    });
    editor._form.dispatch("value-changed", {
      detail: { value: { ...editor._form.data, ...values } },
    });
    assert.deepEqual(updated, { ...config, ...values });
  }

  const statusEditor = StatusCard.getConfigElement();
  statusEditor.setConfig({
    type: "custom:netvue-camera-status-card",
    cameras: [{
      camera: "camera.entry",
      unavailable: "binary_sensor.entry_camera_unavailable",
    }],
  });
  statusEditor.connectedCallback();
  assert.deepEqual(statusEditor._form.data.entities, ["camera.entry"]);
});
