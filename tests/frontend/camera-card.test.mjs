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
    this.children.push(child);
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

  addEventListener(type, callback) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }

  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== callback));
  }

  dispatch(type) {
    for (const callback of this.listeners.get(type) || []) {
      callback();
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
  const editor = card._zoneEditor;
  assert.equal(editor.hasAttribute("zones-only"), true);
  editor.connectedCallback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(editor._form.hidden, true);
  assert.equal(editor._preview.src, "/image");
  editor._resizeCanvas();
  editor._startZone({ clientX: 20, clientY: 20, pointerId: 1 });
  editor._finishZone({ clientX: 160, clientY: 80 });
  config.ai_confidence = 0.8;
  await editor._save();
  assert.deepEqual(calls.at(-1), {
    method: "PUT",
    path: "netvue_local/detection/entry1",
    body: { ...config, ai_zones: { entrance: [0.1, 0.2, 0.8, 0.8] } },
  });
  card._settingsClose.dispatch("click");
  assert.equal(card._settingsPanel.hidden, true);
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
  assert.ok(resolved.includes(`/api/netvue_local/thumbnail/entry1/${second}?offset=59`));
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
  assert.equal(card._historyTicks.hidden, true);
  assert.equal(card._historyRange.disabled, true);
  assert.equal(card._historyRail.children.length, 0);
  assert.equal(card._historyCursorMarker.hidden, true);
  assert.equal(card._historyPosition.hidden, true);
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
