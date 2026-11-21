import assert from "node:assert/strict";
import { test } from "node:test";

class FakeElement {
  constructor(tagName = "element") {
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.style = {
      setProperty(name, value) { this[name] = value; },
    };
  }

  appendChild(child) {
    child.parentNode?.removeChild(child);
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  querySelector(selector) {
    const match = /^\[(data-[a-z-]+)\]$/.exec(selector);
    if (!match || !this.innerHTML?.includes(match[1])) {
      return null;
    }
    this.selectedElements ??= new Map();
    if (!this.selectedElements.has(selector)) {
      this.selectedElements.set(selector, new FakeElement());
    }
    return this.selectedElements.get(selector);
  }

  get firstChild() {
    return this.children[0] || null;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
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

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  pause() {}

  load() {}

  focus() {
    this.focused = true;
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }

  play() {
    return Promise.resolve();
  }

  toggleAttribute(name, force) {
    if (force) {
      this.setAttribute(name, "");
    } else {
      this.attributes.delete(name);
    }
  }

  attachShadow() {
    this.shadowRoot = new FakeElement("shadow-root");
    return this.shadowRoot;
  }

  getBoundingClientRect() {
    return { left: 0, top: 0, width: 200, height: 100 };
  }

  getContext() {
    return { clearRect() {}, fillRect() {}, strokeRect() {} };
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

  createElementNS(namespace, tagName) {
    return this.createElement(tagName);
  }

  addEventListener(type, callback) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }

  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== callback));
  }

  dispatch(type, event = {}) {
    for (const callback of this.listeners.get(type) || []) {
      callback(event);
    }
  }

  async exitFullscreen() {
    this.fullscreenElement = null;
    this.fullscreenRoot.fullscreenElement = null;
    this.fullscreenRoot = null;
    this.dispatch("fullscreenchange");
  }

  enterFullscreen(root, element, host) {
    this.fullscreenRoot = root;
    root.fullscreenElement = element;
    this.fullscreenElement = host;
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
globalThis.ResizeObserver = class {
  constructor(callback) {
    this.callback = callback;
    this.targets = new Set();
  }
  observe(target) { this.targets.add(target); }
  disconnect() { this.targets.clear(); }
};
globalThis.window = {
  customCards: [],
  addEventListener() {},
  removeEventListener() {},
};

const { NetvueCameraCard } = await import("../../custom_components/netvue_local/frontend/netvue-camera-card.js");
const { mergeIntervals, intervalAt } = await import("../../custom_components/netvue_local/frontend/recording-timeline.js");

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

test("manual recording controls and the REC indicator reflect continuous mode", async () => {
  const calls = [];
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const recordingState = {
    state: "inactive",
    attributes: { netvue_entry_id: "entry1", manual: false, continuous: false, active: false },
  };
  card.hass = {
    states: {
      "camera.test": { attributes: { netvue_entry_id: "entry1" } },
      "sensor.recording": recordingState,
    },
    async callApi(method, path, body) {
      calls.push({ method, url: `/api/${path}`, body });
    },
  };

  assert.equal(card._recordButton.hidden, false);
  assert.equal(card._recordingIndicator.hidden, true);
  await card._toggleManualRecording();
  recordingState.attributes.manual = true;
  recordingState.attributes.active = true;
  card._render();
  assert.equal(card._recordingIndicator.hidden, false);
  assert.equal(card._recordButton.getAttribute("aria-label"), "Stop recording");
  await card._toggleManualRecording();
  recordingState.attributes.continuous = true;
  card._render();
  assert.equal(card._recordButton.hidden, true);
  assert.equal(card._recordingIndicator.hidden, false);
  await card._toggleManualRecording();
  recordingState.attributes.active = false;
  card._render();
  assert.equal(card._recordingIndicator.hidden, true);

  assert.deepEqual(calls, [
    { method: "POST", url: "/api/netvue_local/recordings/entry1", body: { enabled: true } },
    { method: "POST", url: "/api/netvue_local/recordings/entry1", body: { enabled: false } },
  ]);
});

test("settings open on the selected camera and save drawn zones without changing other AI options", async () => {
  const calls = [];
  const config = {
    ai_enabled: false, ai_confidence: 0.7, ai_fps: 2,
    ai_confirmation_frames: 4, ai_zones: {},
  };
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  card.hass = {
    states: {
      "camera.test": { attributes: { netvue_entry_id: "entry1", entity_picture: "/image" } },
    },
    async callApi(method, path, body) {
      calls.push({ method, path, body });
      return method === "GET" ? { ...config } : body;
    },
  };
  card._settingsButton.dispatch("click");
  assert.equal(card._settingsPanel.hidden, false);
  assert.equal(card._settingsPanel.tagName, "dialog");
  assert.equal(card._settingsPanel.open, true);
  const editor = card._zoneEditor;
  assert.equal(editor.hasAttribute("zones-only"), true);
  editor.connectedCallback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(editor._form.hidden, true);
  assert.equal(editor._preview.src, "/image");
  assert.deepEqual(editor._zones, { entrance: [0, 0, 1, 1] });
  assert.equal(editor._zoneEditor.querySelector("[data-zone-selection]").textContent,
    "The whole image is selected");
  assert.equal(calls.some(({ method }) => method === "PUT"), false);
  await editor._save();
  assert.deepEqual(calls.at(-1).body, { ...config, ai_zones: { entrance: [0, 0, 1, 1] } });
  assert.equal(editor._zoneEditor.querySelector("[data-zone-name]"), null);
  editor._resizeCanvas();
  editor._startZone({ clientX: 20, clientY: 20, pointerId: 1 });
  editor._finishZone({ clientX: 160, clientY: 80 });
  assert.equal(editor._zoneEditor.querySelector("[data-zone-selection]").textContent,
    "Custom detection area selected");
  config.ai_confidence = 0.8;
  await editor._save();
  assert.deepEqual(calls.at(-1), {
    method: "PUT",
    path: "netvue_local/detection/entry1",
    body: { ...config, ai_zones: { entrance: [0.1, 0.2, 0.8, 0.8] } },
  });
  card._settingsClose.dispatch("click");
  assert.equal(card._settingsPanel.hidden, true);
  assert.equal(card._settingsPanel.open, false);
  assert.equal(card._zoneEditor, null);
});

test("zone settings report a failed load without saving defaults", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const methods = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi(method) { methods.push(method); throw new Error("offline"); },
  };
  card._settingsButton.dispatch("click");
  const editor = card._zoneEditor;
  editor.connectedCallback();
  await new Promise((resolve) => setImmediate(resolve));
  await editor._save();
  assert.deepEqual(methods, ["GET"]);
  assert.equal(editor._zoneEditor.querySelector("[data-zone-message]").textContent,
    "Unable to load settings.");
});

test("drawing replaces the single zone and clearing removes it", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const config = {
    ai_enabled: true, ai_confidence: 0.45, ai_fps: 3, ai_confirmation_frames: 3,
    ai_zones: { garden: [0, 0, 0.2, 0.2], entrance: [0.4, 0.4, 0.6, 0.6] },
  };
  const saved = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi(method, _path, body) {
      if (method === "PUT") saved.push(body);
      return method === "GET" ? config : body;
    },
  };
  card._openSettings();
  const editor = card._zoneEditor;
  editor.connectedCallback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(editor._zones, { garden: [0, 0, 0.2, 0.2] });
  editor._resizeCanvas();
  for (const end of [{ clientX: 100, clientY: 50 }, { clientX: 160, clientY: 80 }]) {
    editor._startZone({ clientX: 20, clientY: 20, pointerId: 1 });
    editor._finishZone(end);
  }
  await editor._save();
  assert.deepEqual(saved[0].ai_zones, { garden: [0.1, 0.2, 0.8, 0.8] });
  editor._zoneEditor.querySelector("[data-clear-zone]").dispatch("click");
  await editor._save();
  assert.deepEqual(saved[1].ai_zones, {});
  editor._zoneEditor.querySelector("[data-full-zone]").dispatch("click");
  await editor._save();
  assert.deepEqual(saved[2].ai_zones, { garden: [0, 0, 1, 1] });
  assert.equal(editor._zoneEditor.querySelector("[data-zone-selection]").textContent,
    "The whole image is selected");
});

test("settings use the entry of each camera independently", async () => {
  const paths = [];
  const hass = {
    states: {
      "camera.first": { attributes: { netvue_entry_id: "first" } },
      "camera.second": { attributes: { netvue_entry_id: "second" } },
    },
    async callApi(_method, path) {
      paths.push(path);
      return { ai_zones: {} };
    },
  };
  for (const entity of ["camera.first", "camera.second"]) {
    const card = new NetvueCameraCard();
    card.setConfig({ entity });
    card._build();
    card.hass = hass;
    card._settingsButton.dispatch("click");
    card._zoneEditor.connectedCallback();
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.deepEqual(paths, ["netvue_local/detection/first", "netvue_local/detection/second"]);
});

test("volume control adjusts sound and restores it after muting", () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  assert.equal(card._volumeRange.value, "0");
  card._volumeRange.value = "0.35";
  card._volumeRange.dispatch("input");
  assert.equal(card._video.volume, 0.35);
  assert.equal(card._historyVideo.volume, 0.35);
  assert.equal(card._video.muted, false);
  assert.equal(card._muteButton.getAttribute("aria-label"), "Mute camera sound");
  card._muteButton.dispatch("click");
  assert.equal(card._video.muted, true);
  assert.equal(card._volumeRange.value, "0");
  card._muteButton.dispatch("click");
  assert.equal(card._video.muted, false);
  assert.equal(card._volumeRange.value, "0.35");
  card._volumeRange.value = "0";
  card._volumeRange.dispatch("input");
  assert.equal(card._video.muted, true);
  card._muteButton.dispatch("click");
  assert.equal(card._video.volume, 0.35);
  assert.equal(card._volumeRange.value, "0.35");
});

test("timeline merges overlaps and finds protected intervals independently", () => {
  const merged = mergeIntervals([
    { start: 100, end: 300 },
    { start: 250, end: 500 },
    { start: 700, end: 800 },
  ]);
  assert.deepEqual(merged.map(({ start, end }) => [start, end]), [[100, 500], [700, 800]]);
  assert.equal(intervalAt([{ id: "protected", start: 100, end: 500 }], 499)?.id, "protected");
  assert.equal(intervalAt([{ id: "protected", start: 100, end: 500 }], 500), null);
});

test("selecting a date without recordings keeps an exact empty window", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() { return { recordings: [], protected: [] }; },
    async callWS(message) { return { path: message.path }; },
  };
  await card._openHistory();
  assert.equal(card._historyRange.disabled, false);
  assert.equal(card._historyMessage.textContent, "No playable recording yet.");
  const selected = new Date(2026, 10, 21, 14, 30, 0).getTime();
  await card._positionHistoryDate(selected);
  const dayStart = new Date(2026, 10, 21, 0, 0, 0, 0).getTime();
  const dayEnd = new Date(2026, 10, 22, 0, 0, 0, 0).getTime() - 1;
  assert.equal(Number(card._historyRange.min), dayStart);
  assert.equal(Number(card._historyRange.max), dayEnd);
  assert.equal(card._historyCursor, selected);
  assert.equal(card._historyMessage.textContent, "No recording at this time.");
  assert.equal(card._historyVideo.getAttribute("src"), null);

  card._historyRange.value = String(selected + 3600000);
  card._historyRange.dispatch("input");
  card._historyRange.dispatch("change");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historyCursor, selected + 3600000);
});

test("protected-only footage remains playable and appears after temporary expiry", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = Date.parse("2026-11-20T12:00:00Z");
  const signed = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi(method, path) {
      if (path === "netvue_local/recordings/entry1") return { recordings: [] };
      if (path === "netvue_local/protected/entry1") {
        return { protected: [{
          id: "b".repeat(32), status: "saved",
          covered_start: new Date(start).toISOString(),
          covered_end: new Date(start + 600000).toISOString(),
        }] };
      }
      return { recordings: [] };
    },
    async callWS(message) {
      signed.push(message.path);
      return { path: `/signed${message.path}` };
    },
  };
  await card._openHistory();
  assert.equal(card._historySegment.id, "b".repeat(32));
  assert.ok(signed.includes(`/api/netvue_local/protected/entry1/${"b".repeat(32)}`));
  assert.equal(card._historyRail.children.length, 1);
  assert.equal(card._historyRail.children[0].getAttribute("class"), "history-protected");

  await card._historySeek(start - 60000);
  assert.equal(card._historySegment, null);
  assert.equal(card._historyMessage.textContent, "No recording at this time.");
  assert.equal(card._historyDownload.hidden, true);

  await card._historySeek(start + 300000);
  assert.equal(card._historySegment.id, "b".repeat(32));
});

test("timeline seeks across recordings, reports gaps and follows a growing segment", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const first = "a".repeat(32);
  const second = "b".repeat(32);
  const start = Date.parse("2026-11-20T12:00:00Z");
  const recordings = [
    { id: first, start: new Date(start).toISOString(), duration: 600 },
    { id: second, start: new Date(start + 600000).toISOString(), duration: 60 },
  ];
  const resolved = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi(method, path) {
      assert.equal(method, "GET");
      assert.equal(path, "netvue_local/recordings/entry1");
      return { recordings };
    },
    async callWS(message) {
      assert.equal(message.type, "auth/sign_path");
      resolved.push(message.path);
      return { path: `/signed${message.path}` };
    },
  };
  await card._openHistory();
  assert.equal(card._history, true);
  assert.equal(card._historyPanel.hidden, false);
  assert.ok(resolved.includes(`/api/netvue_local/playback/entry1/${second}?duration=60`));
  assert.ok(resolved.every((path) => !path.includes("/thumbnail/")));
  assert.equal(findByClass(card._historyPanel, "history-preview"), null);
  await card._historySeek(start + 300000);
  assert.ok(resolved.includes(`/api/netvue_local/playback/entry1/${first}?duration=600`));
  card._historyVideo.duration = 600;
  card._historyMetadata();
  assert.equal(card._historyVideo.currentTime, 300);
  await card._historySeek(start - 1000);
  assert.equal(card._historyMessage.textContent, "No recording at this time.");
  assert.equal(card._historyDownload.hidden, true);
  await card._historySeek(start + 599000);
  card._historyVideo.currentTime = 600;
  await card._historyEnded();
  assert.ok(resolved.includes(`/api/netvue_local/playback/entry1/${second}?duration=60`));
  card._historyVideo.currentTime = 60;
  recordings[1].duration = 90;
  await card._historyEnded();
  assert.equal(card._historyPendingSeek.offset, 60);
  card._closeHistory(false);
  assert.equal(card._historyPanel.hidden, true);
  assert.equal(card._video.hidden, false);
});

test("history hides REC and the fifteen-second controls seek across segments", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = Date.parse("2026-11-20T12:00:00Z");
  card.hass = {
    states: {
      "camera.test": { attributes: { netvue_entry_id: "entry1" } },
      "sensor.recording": {
        state: "continuous",
        attributes: { netvue_entry_id: "entry1", manual: false, continuous: true, active: true },
      },
    },
    async callApi() {
      return { recordings: [
        { id: "a".repeat(32), start: new Date(start).toISOString(), duration: 60 },
        { id: "b".repeat(32), start: new Date(start + 60000).toISOString(), duration: 60 },
      ] };
    },
    async callWS(message) { return { path: message.path }; },
  };
  assert.equal(card._recordingIndicator.hidden, false);
  await card._openHistory();
  assert.equal(card._recordingIndicator.hidden, true);
  await card._historySeek(start + 55000);
  card._historyForwardButton.dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historySegment.id, "b".repeat(32));
  assert.equal(card._historyPendingSeek.offset, 10);
  card._historyBackButton.dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historySegment.id, "a".repeat(32));
  assert.equal(card._historyPendingSeek.offset, 55);
  card._closeHistory(false);
  assert.equal(card._recordingIndicator.hidden, false);
});

test("history overlay exposes jumps, date, download and a clear return to live", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = new Date(2026, 10, 20, 12).getTime();
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() {
      return { recordings: [{ id: "a".repeat(32), start: new Date(start).toISOString(), duration: 7200 }] };
    },
    async callWS(message) { return { path: `/signed${message.path}` }; },
  };
  await card._openHistory();
  assert.equal(card._historyPlayer.hidden, false);
  assert.equal(card._historyVideo.getAttribute("controls"), "");
  const actions = findByClass(card._historyPlayer, "history-actions");
  assert.deepEqual(actions.children, [
    card._historyLiveButton, card._historyHourButton, card._historyTenButton,
    card._historyBackButton, card._historyForwardButton, card._historyDatePicker, card._historyDownload,
    card._historySettingsButton, card._historyFullscreenButton,
  ]);
  assert.equal(card._historyLiveButton.textContent, "LIVE");
  assert.equal(card._historyLiveButton.getAttribute("aria-label"), "Back to live");
  assert.equal(card._historyBackButton.getAttribute("aria-label"), "Go back 15 seconds");
  assert.equal(card._historyForwardButton.getAttribute("aria-label"), "Go forward 15 seconds");
  assert.equal(card._historyDateButton.getAttribute("aria-label"), "Recording date and time");
  assert.equal(card._historyDownload.getAttribute("aria-label"), "Download");
  assert.equal(card._historyDownload.hidden, false);
  assert.ok(card._historyDownload.href.startsWith("/signed/api/netvue_local/playback/"));
  await card._historySeek(start + 6600000);
  card._historyHourButton.dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historyCursor, start + 3000000);
  card._historyTenButton.dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historyCursor, start + 2400000);
  card._historyDatePicker.open = true;
  card._historyDate.value = "2026-11-20T12:10:00";
  card._historyDate.dispatch("change");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._historyDatePicker.open, false);
  assert.equal(card._historyCursor, start + 600000);
  card._historyDatePicker.open = true;
  let stopped = false;
  card._historyDatePicker.dispatch("keydown", { key: "Escape", stopPropagation() { stopped = true; } });
  assert.equal(card._historyDatePicker.open, false);
  assert.equal(card._historyDateButton.focused, true);
  assert.equal(stopped, true);
  card.hass = { ...card.hass, language: "fr" };
  assert.equal(card._historyLiveButton.textContent, "DIRECT");
  assert.equal(card._historyLiveButton.getAttribute("aria-label"), "Retour au direct");
  card._historyLiveButton.dispatch("click");
  assert.equal(card._history, false);
  assert.equal(card._historyPlayer.hidden, true);
  assert.equal(card._historyDownload.hidden, true);
});

test("only annotated export signs a download with the overlay option", async (t) => {
  const entries = new Map();
  t.mock.method(document, "createElement", (tag) => {
    const element = new FakeElement(tag);
    element.click = () => entries.set("download", element.href);
    return element;
  });
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const saved = new Map();
  window.localStorage = {
    getItem: (key) => saved.get(key),
    setItem: (key, value) => saved.set(key, value),
  };
  t.after(() => { delete window.localStorage; });
  const requests = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callWS(message) {
      requests.push(message);
      return { path: "/signed/annotated" };
    },
  };
  card._historySegment = { id: "a".repeat(32) };
  let prevented = 0;
  const event = { preventDefault() { prevented++; } };
  await card._downloadAnnotated(event);
  assert.equal(prevented, 0);
  card._overlaySettings.export.input.checked = true;
  card._overlaySettings.export.input.dispatch("change");
  await card._downloadAnnotated(event);
  assert.equal(prevented, 1);
  assert.equal(requests[0].path, `/api/netvue_local/recordings/entry1/${"a".repeat(32)}?overlay=1`);
  assert.equal(entries.get("download"), "/signed/annotated");
  const reloaded = new NetvueCameraCard();
  reloaded.setConfig({ entity: "camera.test" });
  reloaded.hass = card.hass;
  reloaded._build();
  assert.equal(reloaded._overlaySettings.export.input.checked, true);
  assert.equal(reloaded._overlaySettings.replay.input.checked, true);
});

test("timeline graduations follow the available recording bounds across days", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = new Date(2026, 11, 31, 12).getTime();
  const end = new Date(2027, 0, 1, 11).getTime();
  const recordings = [
    { id: "a".repeat(32), start: new Date(start).toISOString(), duration: 60 },
    { id: "b".repeat(32), start: new Date(end - 60000).toISOString(), duration: 60 },
  ];
  card.hass = {
    locale: { language: "fr-FR" },
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() { return { recordings }; },
    async callWS(message) { return { path: message.path }; },
  };
  await card._openHistory();
  assert.equal(card._historyRange.min, String(start));
  assert.equal(card._historyRange.max, String(end - 1));
  assert.equal(card._historyTicks.hidden, false);
  assert.equal(card._historyTicks.children.length, 5);
  const dateFormat = new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" });
  const timeFormat = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
  for (const [index, time] of [
    start, start + (end - 1 - start) / 4, start + (end - 1 - start) / 2,
    start + (end - 1 - start) * 3 / 4, end - 1,
  ].entries()) {
    const rounded = Math.round(time / 60000) * 60000;
    assert.equal(card._historyTickLabels[index].time.textContent, timeFormat.format(rounded));
    assert.equal(card._historyTickLabels[index].date.textContent, dateFormat.format(rounded));
  }
  card._historyRange.value = String(start + 30000);
  card._historyRange.dispatch("input");
  assert.equal(card._historyRange.getAttribute("aria-valuetext"),
    new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "medium" }).format(start + 30000));
  recordings.splice(1);
  await card._refreshHistory("entry1", card._historyGeneration);
  const shortTimeFormat = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  assert.equal(card._historyTickLabels[0].time.textContent, shortTimeFormat.format(start));
  assert.equal(card._historyTickLabels[0].date.textContent, "");
  recordings.splice(0);
  await card._refreshHistory("entry1", card._historyGeneration);
  assert.equal(card._historyTicks.hidden, false);
  assert.equal(card._historyRange.disabled, false);
  assert.equal(card._historyRail.children.length, 0);
  card._closeHistory(false);
});

test("timeline shows recorded intervals and the time under the moving cursor", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = Date.parse("2026-11-20T12:00:00Z");
  const recordings = [
    { id: "a".repeat(32), start: new Date(start).toISOString(), duration: 10 },
    { id: "b".repeat(32), start: new Date(start + 10000).toISOString(), duration: 10 },
    { id: "c".repeat(32), start: new Date(start + 40000).toISOString(), duration: 10 },
    { id: "d".repeat(32), start: new Date(start + 45000).toISOString(), duration: 15 },
  ];
  card.hass = {
    locale: { language: "fr-FR" },
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() { return { recordings }; },
    async callWS(message) { return { path: message.path }; },
  };
  await card._openHistory();
  assert.equal(card._historyRail.children.length, 2);
  const [first, second] = card._historyRail.children;
  assert.equal(first.style.left, "0%");
  assert.ok(Math.abs(parseFloat(first.style.width) - 20000 / 59999 * 100) < 0.001);
  assert.ok(Math.abs(parseFloat(second.style.left) - 40000 / 59999 * 100) < 0.001);
  assert.ok(Math.abs(parseFloat(second.style.width) - 19999 / 59999 * 100) < 0.001);
  const format = new Intl.DateTimeFormat("fr-FR", { timeStyle: "medium" });
  card._historyRange.value = String(start + 15000);
  card._historyRange.dispatch("input");
  assert.equal(card._historyPositionLabel.textContent, format.format(start + 15000));
  assert.ok(Math.abs(parseFloat(card._historyPanel.style["--history-position"]) - 15000 / 59999 * 100) < 0.001);
  assert.equal(card._historyCursorMarker.hidden, false);
  card._historyScrubbing = false;
  await card._historySeek(start + 40000);
  card._historyVideo.currentTime = 5;
  card._historyTimeUpdate();
  assert.equal(card._historyPositionLabel.textContent, format.format(start + 45000));
  recordings.splice(0, 2);
  await card._refreshHistory("entry1", card._historyGeneration);
  assert.equal(card._historyRail.children.length, 1);
  card._closeHistory(false);
});

test("timeline shows at most 24 hours and moves to older recordings without deleting them", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const start = Date.parse("2026-11-18T12:00:00Z");
  const day = 24 * 60 * 60 * 1000;
  const recordings = [
    { id: "a".repeat(32), start: new Date(start).toISOString(), duration: 600 },
    { id: "c".repeat(32), start: new Date(start + 2 * day - 3600000).toISOString(), duration: 7200 },
    { id: "b".repeat(32), start: new Date(start + 3 * day).toISOString(), duration: 600 },
  ];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi(method) { assert.equal(method, "GET"); return { recordings }; },
    async callWS(message) { return { path: message.path }; },
  };
  await card._openHistory();
  const latest = start + 3 * day + 600000 - 1;
  assert.equal(Number(card._historyRange.max), latest);
  assert.equal(Number(card._historyRange.max) - Number(card._historyRange.min), day);
  assert.equal(card._historyRail.children.length, 2);
  assert.equal(card._historyRail.children[0].style.left, "0%");
  assert.ok(parseFloat(card._historyRail.children[0].style.width) > 0);
  const older = start + 300000;
  await card._historySeek(older);
  assert.ok(Number(card._historyRange.min) <= older);
  assert.ok(Number(card._historyRange.max) >= older);
  assert.equal(Number(card._historyRange.max) - Number(card._historyRange.min), day);
  assert.equal(card._historySegment.id, "a".repeat(32));
  assert.equal(recordings.length, 3);
  assert.equal(card._historyRail.children.length, 1);
  await card._historySeek(start + 3 * day + 300000);
  assert.equal(Number(card._historyRange.max), latest);
  assert.equal(card._historySegment.id, "b".repeat(32));
  card._closeHistory(false);
});

test("timeline ignores a late resolution after returning to live", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const resolutions = [];
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() {
      return { recordings: [{ id: "a".repeat(32), start: "2026-11-20T12:00:00Z", duration: 60 }] };
    },
    callWS() { return new Promise((resolve) => { resolutions.push(resolve); }); },
  };
  const open = card._openHistory();
  await Promise.resolve();
  await Promise.resolve();
  card._closeHistory(false);
  for (const resolve of resolutions) resolve({ path: "/signed/late" });
  await open;
  assert.notEqual(card._historyVideo.src, "/signed/late");
});

test("timeline waits for more footage only while recording and cancels on exit", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const recording = { id: "a".repeat(32), start: "2026-11-20T12:00:00Z", duration: 60 };
  card.hass = {
    states: {
      "camera.test": { attributes: { netvue_entry_id: "entry1" } },
      "sensor.recording": {
        state: "continuous",
        attributes: { netvue_entry_id: "entry1", manual: false, continuous: true, active: true },
      },
    },
    async callApi() {
      return { recordings: [recording] };
    },
    async callWS(message) { return { path: message.path }; },
  };
  await card._openHistory();
  card._historyVideo.currentTime = 60;
  await card._historyEnded();
  assert.equal(card._historyMessage.textContent, "Waiting for new recording footage…");
  assert.notEqual(card._historyFollowTimer, null);
  card._clearTimer("_historyFollowTimer");
  recording.duration = 65;
  await card._historyEnded();
  assert.equal(card._historyMessage.textContent, "Waiting for new recording footage…");
  card._clearTimer("_historyFollowTimer");
  recording.duration = 72;
  await card._historyEnded();
  assert.equal(card._historyPendingSeek.offset, 60);
  await card._historyEnded();
  assert.notEqual(card._historyFollowTimer, null);
  card._closeHistory(false);
  assert.equal(card._historyFollowTimer, null);
});

test("seeking in the video cancels a pending follow refresh", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  const recording = { id: "a".repeat(32), start: "2026-11-20T12:00:00Z", duration: 60 };
  let resolveRefresh;
  let blockRefresh = false;
  let signed = 0;
  card.hass = {
    states: {
      "camera.test": { attributes: { netvue_entry_id: "entry1" } },
      "sensor.recording": {
        state: "continuous",
        attributes: { netvue_entry_id: "entry1", manual: false, continuous: true, active: true },
      },
    },
    async callApi() {
      if (blockRefresh) return new Promise((resolve) => { resolveRefresh = resolve; });
      return { recordings: [recording] };
    },
    async callWS(message) { signed++; return { path: message.path }; },
  };
  await card._openHistory();
  card._historyVideo.currentTime = 60;
  await card._historyEnded();
  assert.notEqual(card._historyFollowTimer, null);
  card._historyVideo.currentTime = 20;
  card._historyVideo.dispatch("seeking");
  assert.equal(card._historyFollowTimer, null);
  assert.equal(card._historyMessage.textContent, "");

  card._historyVideo.currentTime = 60;
  blockRefresh = true;
  const following = card._historyEnded();
  const before = signed;
  card._historyVideo.currentTime = 20;
  card._historyVideo.dispatch("seeking");
  recording.duration = 90;
  resolveRefresh({ recordings: [recording] });
  await following;
  assert.equal(signed, before);
  assert.equal(card._historyVideo.currentTime, 20);
  assert.equal(card._historyFollowTimer, null);
  card._closeHistory(false);
});

test("the full-screen button enters and exits across shadow DOM retargeting", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  card._wrap.requestFullscreen = async () => {
    document.enterFullscreen(card.shadowRoot, card._wrap, card);
  };
  card._render();
  card.connectedCallback();

  const button = findByClass(card.shadowRoot, "fullscreen");
  assert.ok(button);
  assert.equal(button.disabled, false);
  const enterIcon = button.innerHTML;

  button.dispatch("click");
  await Promise.resolve();
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.getAttribute("aria-label"), "Exit full screen");
  assert.notEqual(button.innerHTML, enterIcon);

  button.dispatch("click");
  await Promise.resolve();
  assert.equal(document.fullscreenElement, null);
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.getAttribute("aria-label"), "Open camera in full screen");
  assert.equal(button.innerHTML, enterIcon);

  button.dispatch("click");
  await Promise.resolve();
  await document.exitFullscreen();
  assert.equal(button.getAttribute("aria-pressed"), "false");
  card.disconnectedCallback();
});

test("the full-screen button uses CSS when the browser lacks native support", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card.connectedCallback();

  const button = findByClass(card.shadowRoot, "fullscreen");
  assert.ok(button);
  assert.equal(button.disabled, false);
  assert.equal(card._historyFullscreenButton.disabled, false);
  card._video.srcObject = { id: "stream" };
  await card._toggleFullscreen();
  assert.equal(card.hasAttribute("css-fullscreen"), true);
  assert.equal(card._wrap.hasAttribute("data-fullscreen"), true);
  assert.deepEqual(card._video.srcObject, { id: "stream" });
  document.dispatch("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(card.hasAttribute("css-fullscreen"), false);
  card.disconnectedCallback();
});

test("webkit fullscreen and rejected requests preserve replay position", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card.connectedCallback();
  card._history = true;
  card._historyVideo.currentTime = 42;
  card._historyVideo.src = "recording";
  card._wrap.webkitRequestFullscreen = () => {
    document.webkitFullscreenElement = card;
    document.dispatch("webkitfullscreenchange");
  };
  document.webkitExitFullscreen = () => {
    document.webkitFullscreenElement = null;
    document.dispatch("webkitfullscreenchange");
  };
  await card._toggleFullscreen();
  assert.equal(card._historyFullscreenButton.getAttribute("aria-pressed"), "true");
  await card._toggleFullscreen();
  assert.equal(card._historyFullscreenButton.getAttribute("aria-pressed"), "false");
  card._wrap.requestFullscreen = async () => { throw new Error("not allowed"); };
  await card._toggleFullscreen();
  assert.equal(card.hasAttribute("css-fullscreen"), true);
  await card._toggleFullscreen();
  assert.equal(card._historyVideo.currentTime, 42);
  assert.equal(card._historyVideo.src, "recording");
  assert.equal(card._history, true);
  delete document.webkitExitFullscreen;
  delete document.webkitFullscreenElement;
  card.disconnectedCallback();
});

test("live boxes hold until the next AI state without following unrelated updates", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 100000 });
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card.connectedCallback();
  const detection = {
    state: "on",
    attributes: {
      state: "detecting", enabled: true, last_frame_at: 100,
      detections: [
        { category: "person", confidence: 0.87, frame_width: 640, frame_height: 360,
          box: { x1: 40, y1: 50, x2: 180, y2: 260 } },
        { category: "dog", confidence: 0.9, frame_width: 640, frame_height: 360,
          box: { x1: 300, y1: 70, x2: 430, y2: 290 } },
      ],
    },
  };
  card.hass = {
    entities: {
      "camera.test": { entity_id: "camera.test", device_id: "device1" },
      "binary_sensor.other": { entity_id: "binary_sensor.other", device_id: "device2", translation_key: "ai_detection" },
      "binary_sensor.test": { entity_id: "binary_sensor.test", device_id: "device1", translation_key: "ai_detection" },
    },
    states: {
      "camera.test": { state: "unavailable", attributes: {} },
      "binary_sensor.other": { state: "on", attributes: { ...detection.attributes, detections: [{ category: "cat" }] } },
      "binary_sensor.test": detection,
    },
    language: "fr",
  };
  card._view = { state: "live" };
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), false);
  assert.deepEqual(
    card._detectionOverlay.children.filter((item) => item.tagName === "text").map((item) => item.textContent),
    ["Personne 87 %", "Chien 90 %"],
  );
  card._overlaySettings.live.input.checked = false;
  card._overlaySettings.live.input.dispatch("change");
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), true);
  card._history = true;
  card._render();
  card._history = false;
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), true);
  card._overlaySettings.live.input.checked = true;
  card._overlaySettings.live.input.dispatch("change");
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), false);
  card._history = true;
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), true);
  card._history = false;
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), false);
  const firstBox = card._detectionOverlay.firstChild;
  t.mock.timers.tick(60000);
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), false);
  assert.equal(card._detectionOverlay.firstChild, firstBox);
  const detections = detection.attributes.detections;
  detection.attributes.detections = [];
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), true);
  detection.attributes.detections = detections;
  detection.state = "unavailable";
  card._render();
  assert.equal(card._detectionOverlay.hasAttribute("hidden"), true);
  card.disconnectedCallback();
});

test("detection boxes follow the active picture instead of the replay timeline or black bars", () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card.connectedCallback();
  const live = card._video;
  const replay = card._historyVideo;
  const overlay = card._detectionOverlay;
  const size = () => [overlay.style.width, overlay.style.height];
  const detections = [{
    category: "car", frame_width: 640, frame_height: 360,
    box: { x1: 20, y1: 40, x2: 100, y2: 120 },
  }];

  Object.assign(live, { clientWidth: 320, clientHeight: 240 });
  live.dispatch("emptied");
  card._drawDetections(detections);
  assert.equal(overlay.parentNode, live.parentNode);
  assert.deepEqual(size(), ["0px", "0px"]);
  Object.assign(live, { videoWidth: 1920, videoHeight: 1080 });
  live.dispatch("loadedmetadata");
  assert.deepEqual(size(), ["320px", "180px"]);

  Object.assign(replay, { videoWidth: 640, videoHeight: 480, clientWidth: 800, clientHeight: 450 });
  card._history = true;
  card._drawDetections(detections);
  assert.equal(overlay.parentNode, replay.parentNode);
  assert.deepEqual(size(), ["600px", "450px"]);
  replay.clientHeight = 300;
  card._detectionResizeObserver.callback();
  assert.deepEqual(size(), ["400px", "300px"]);

  card._history = false;
  card._drawDetections(detections);
  assert.equal(overlay.parentNode, live.parentNode);
  assert.deepEqual(size(), ["320px", "180px"]);
  card.disconnectedCallback();
  assert.equal(card._detectionResizeObserver.targets.size, 0);
});

test("replay holds the preceding sample until a clear update and ignores future samples", () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card.connectedCallback();
  card._history = true;
  card._historySegment = { start: 100000 };
  card._historyDetections = [
    { timestamp: 101, frame_width: 640, frame_height: 360,
      boxes: [{ category: "car", x1: 20, y1: 40, x2: 100, y2: 120 }] },
    { timestamp: 103, boxes: [] },
  ];
  const renderAt = (time) => {
    card._historyVideo.currentTime = time;
    card._render();
    return !card._detectionOverlay.hasAttribute("hidden");
  };
  assert.equal(renderAt(0.9), false);
  assert.equal(renderAt(1), true);
  assert.equal(renderAt(2.9), true);
  assert.equal(renderAt(3), false);
  assert.equal(renderAt(1.5), true);
  card._overlaySettings.replay.input.checked = false;
  card._overlaySettings.replay.input.dispatch("change");
  assert.equal(renderAt(1.5), false);
  assert.equal(card._overlaySettings.live.input.checked, true);
  assert.equal(card._overlaySettings.export.input.checked, false);
  card.disconnectedCallback();
});

test("CSS fullscreen from a wall keeps the active video and hides unavailable wall navigation", async () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  const wall = new WallCard();
  wall.setConfig({ entities: ["camera.entry", "camera.garage"] });
  wall.connectedCallback();
  const card = wall._cards[1];
  card.connectedCallback();
  card._video.srcObject = { id: "stream" };
  await card._toggleFullscreen();
  assert.equal(card.hasAttribute("css-fullscreen"), true);
  assert.equal(card._nextButton.hidden, true);
  assert.deepEqual(card._video.srcObject, { id: "stream" });
  await card._toggleFullscreen();
  assert.equal(card.hasAttribute("css-fullscreen"), false);
  card.disconnectedCallback();
  wall.disconnectedCallback();
});

test("history fullscreen includes the timeline and exits without closing history", async () => {
  const card = new NetvueCameraCard();
  card.setConfig({ entity: "camera.test" });
  card._build();
  card._wrap.requestFullscreen = async () => {
    document.enterFullscreen(card.shadowRoot, card._wrap, card);
  };
  card.hass = {
    states: { "camera.test": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() { return { recordings: [] }; },
    async callWS() {},
  };
  card.connectedCallback();
  await card._openHistory();
  assert.equal(card._historyVideo.getAttribute("controlslist"), "nofullscreen");

  card._historyFullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(card.shadowRoot.fullscreenElement, card._wrap);
  assert.ok(card._wrap.children.includes(card._historyPanel));
  assert.equal(card._historyPanel.hidden, false);
  assert.equal(card._historyFullscreenButton.getAttribute("aria-label"), "Exit full screen");

  card._historyFullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(document.fullscreenElement, null);
  assert.equal(card._history, true);
  assert.equal(card._historyPanel.hidden, false);
  card.disconnectedCallback();
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
  wall.disconnectedCallback();
});

test("wall fullscreen switches existing cameras, releases talk and restores the grid", async () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  const wall = new WallCard();
  wall.setConfig({ entities: ["camera.entry", "camera.garage", "camera.garden"] });
  wall.connectedCallback();
  wall._grid.requestFullscreen = async () => {
    document.enterFullscreen(wall.shadowRoot, wall._grid, wall);
  };
  for (const card of wall._cards) {
    card.connectedCallback();
  }

  const [entry, garage, garden] = wall._cards;
  assert.equal(entry._nextButton.hidden, true);
  garage._fullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(garage.getAttribute("wall-active"), "");
  assert.equal(garage._nextButton.hidden, false);
  assert.equal(garage._previousButton.getAttribute("aria-label"), "Previous camera");

  let releases = 0;
  garage._ptt = { release: () => { releases++; } };
  garage._nextButton.dispatch("click");
  assert.equal(releases, 1);
  assert.equal(garden.getAttribute("wall-active"), "");
  assert.equal(garage.getAttribute("wall-active"), null);
  assert.equal(garden._fullscreenButton.getAttribute("aria-label"), "Exit full screen");

  garden._nextButton.dispatch("click");
  assert.equal(entry.getAttribute("wall-active"), "");
  entry._previousButton.dispatch("click");
  assert.equal(garden.getAttribute("wall-active"), "");
  garden._fullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(document.fullscreenElement, null);
  assert.ok(wall._cards.every((card) => card.getAttribute("wall-active") === null));
  assert.ok(wall._cards.every((card) => card._nextButton.hidden));
  assert.deepEqual(wall._cards, [entry, garage, garden]);
  garage._ptt = null;
  for (const card of wall._cards) card.disconnectedCallback();
  wall.disconnectedCallback();
});

test("history fullscreen in a camera wall keeps the active timeline visible", async () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  const wall = new WallCard();
  wall.setConfig({ entities: ["camera.entry", "camera.garage"] });
  wall.connectedCallback();
  wall._grid.requestFullscreen = async () => {
    document.enterFullscreen(wall.shadowRoot, wall._grid, wall);
  };
  const card = wall._cards[1];
  card.connectedCallback();
  card.hass = {
    states: { "camera.garage": { attributes: { netvue_entry_id: "entry1" } } },
    async callApi() { return { recordings: [] }; },
    async callWS() {},
  };
  await card._openHistory();
  card._historyFullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(wall.shadowRoot.fullscreenElement, wall._grid);
  assert.equal(card.getAttribute("wall-active"), "");
  assert.equal(card._historyPanel.hidden, false);
  assert.equal(card._historyFullscreenButton.getAttribute("aria-label"), "Exit full screen");
  card._historyFullscreenButton.dispatch("click");
  await Promise.resolve();
  assert.equal(document.fullscreenElement, null);
  assert.equal(card._history, true);
  card.disconnectedCallback();
  wall.disconnectedCallback();
});

test("wall fullscreen survives configuration changes and browser exit", async () => {
  const WallCard = customElements.get("netvue-camera-wall-card");
  const wall = new WallCard();
  wall.setConfig({ entities: ["camera.entry", "camera.garage"] });
  wall.connectedCallback();
  wall._grid.requestFullscreen = async () => {
    document.enterFullscreen(wall.shadowRoot, wall._grid, wall);
  };
  wall._cards.forEach((card) => card.connectedCallback());
  wall._cards[1]._fullscreenButton.dispatch("click");
  await Promise.resolve();

  wall.setConfig({ entities: ["camera.garage", "camera.garden"] });
  wall._cards.forEach((card) => card.connectedCallback());
  assert.equal(wall._activeCard._config.entity, "camera.garage");
  assert.equal(wall._activeCard.getAttribute("wall-active"), "");
  await document.exitFullscreen();
  assert.ok(wall._cards.every((card) => card.getAttribute("wall-active") === null));
  assert.equal(wall._activeCard, null);
  wall._cards.forEach((card) => card.disconnectedCallback());
  wall.disconnectedCallback();
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
