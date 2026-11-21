// Lovelace card: video, camera audio and push-to-talk on ONE RTCPeerConnection.

import { createSignalling } from "./signalling.js";
import { NetvueSession } from "./session.js";
import { KEYBOARD, PressOwner } from "./press-owner.js";
import { pttErrorKey, translator } from "./i18n.js";
import { localDateTime, recordingSegments, segmentAt } from "./recording-timeline.js";
import "./netvue-camera-wall-card.js";
import "./netvue-camera-status-card.js";
import "./card-editors.js";

const HIDDEN_PAUSE_MS = 60000;
const ERROR_TOAST_MS = 4000;
const HISTORY_FOLLOW_POLL_MS = 5000;
const HISTORY_FOLLOW_BUFFER_MS = 10000;
const HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000;
const ENTITY_RE = /^camera\.[a-z0-9_]+$/;

const STYLE = `
:host { display: block; }
ha-card { overflow: hidden; position: relative; }
.wrap { position: relative; background: #000; line-height: 0; }
video { width: 100%; max-height: 80vh; background: #000; }
.wrap:fullscreen, :host([wall-active]) .wrap {
  width: 100vw; height: 100vh; height: 100dvh; display: flex; align-items: center; justify-content: center;
}
.wrap:fullscreen video, :host([wall-active]) video {
  width: 100%; height: 100%; max-height: 100vh; max-height: 100dvh; object-fit: contain;
}
.overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 12px; color: #fff; background: rgba(0,0,0,.55); line-height: 1.4; }
.overlay[hidden], .toast[hidden], button[hidden] { display: none; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 8px;
  padding: 8px; background: linear-gradient(transparent, rgba(0,0,0,.6)); line-height: 1.4; }
.status { flex: 1; color: #fff; font-size: 14px; }
button { font: inherit; color: #fff; background: rgba(0,0,0,.8); border: 1px solid rgba(255,255,255,.5);
  border-radius: 20px; padding: 8px 14px; cursor: pointer; min-height: 40px; }
.bar button { width: 44px; height: 44px; min-height: 44px; padding: 0; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 44px; }
.bar button[hidden] { display: none; }
.wrap[data-history] .bar { display: none; }
.history-panel { position: relative; margin: 8px; padding: 12px; border-radius: 8px;
  background: rgba(0,0,0,.9); color: #fff; line-height: 1.4; }
.wrap:fullscreen[data-history], :host([wall-active]) .wrap[data-history] { flex-direction: column; }
.wrap:fullscreen[data-history] video, :host([wall-active]) .wrap[data-history] video {
  flex: 1 1 0; min-height: 0;
}
.wrap:fullscreen[data-history] .history-panel, :host([wall-active]) .wrap[data-history] .history-panel {
  flex: 0 0 auto; width: calc(100% - 32px);
}
.history-panel[hidden], video[hidden] { display: none; }
.history-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
.history-panel button, .history-panel a { box-sizing: border-box; min-height: 36px; padding: 5px 10px;
  font-size: 13px; }
.history-panel a { display: inline-flex; align-items: center; color: #fff; background: rgba(0,0,0,.8);
  border: 1px solid rgba(255,255,255,.5); border-radius: 20px; text-decoration: none; }
.history-panel a[hidden] { display: none; }
.history-panel input[type="datetime-local"] { box-sizing: border-box; max-width: 100%; min-height: 36px;
  padding: 4px 8px; color: #fff; background: #222; border: 1px solid #888; border-radius: 6px;
  color-scheme: dark; font: inherit; font-size: 13px; }
.history-panel input[type="range"] { display: block; box-sizing: border-box; width: 100%; height: 24px;
  margin: 0; cursor: pointer; accent-color: var(--primary-color, #03a9f4); }
.history-panel input[type="range"]:disabled { cursor: default; }
.history-ticks { position: relative; display: flex; justify-content: space-between; margin: 0 8px;
  min-height: 35px; color: #ddd; font-size: 11px; line-height: 1.25; }
.history-ticks[hidden] { display: none; }
.history-tick { position: relative; width: 1px; height: 5px; background: #aaa; }
.history-tick-label { position: absolute; top: 8px; left: 0; transform: translateX(-50%);
  text-align: center; white-space: nowrap; }
.history-tick:first-child .history-tick-label { transform: none; text-align: left; }
.history-tick:last-child .history-tick-label { transform: translateX(-100%); text-align: right; }
.history-tick-date { display: block; color: #aaa; }
.history-message { min-height: 1.4em; font-size: 13px; }
.history-preview { display: block; max-width: 160px; max-height: 90px; margin-bottom: 4px; }
.history-preview[hidden] { display: none; }
.bar button svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8;
  stroke-linecap: round; stroke-linejoin: round; }
.navigate { position: absolute; top: 50%; transform: translateY(-50%); z-index: 1;
  width: 44px; height: 44px; padding: 0; border-radius: 50%; font-size: 30px; line-height: 1; }
.previous { left: 8px; }
.next { right: 8px; }
.recording-indicator { position: absolute; top: 8px; right: 8px; padding: 4px 8px;
  border-radius: 4px; background: #b00020; color: #fff; font-size: 13px; line-height: 1.4; }
.recording-indicator[hidden] { display: none; }
button:focus-visible { outline: 3px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
button[disabled] { color: rgba(255,255,255,.65); cursor: default; }
.ptt { touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
.ptt[aria-pressed="true"] { background: var(--error-color, #db4437); border-color: #fff; }
.toast { position: absolute; left: 8px; right: 8px; top: 8px; padding: 8px 12px; border-radius: 8px;
  color: #fff; background: rgba(176,0,32,.9); line-height: 1.4; font-size: 14px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
`;

function el(tag, attrs = {}, parent) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, value);
  }
  if (parent) {
    parent.appendChild(node);
  }
  return node;
}

function setIcon(button, icon) {
  button.innerHTML = icon;
}

const ICONS = {
  mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6.5 10.5v1a5.5 5.5 0 0 0 11 0v-1M12 17v4M8.5 21h7"/></svg>',
  muted: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 7 9H3v6h4l4 4z"/><path d="m16 9 5 6M21 9l-5 6"/></svg>',
  speaker: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 7 9H3v6h4l4 4z"/><path d="M15 9a5 5 0 0 1 0 6M18 6a9 9 0 0 1 0 12"/></svg>',
  fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5"/></svg>',
  record: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7"/></svg>',
  history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
};

export class NetvueCameraCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("netvue-camera-card-editor");
  }

  static getStubConfig(hass, entities) {
    const cameras = (entities || Object.keys((hass && hass.states) || {})).filter((id) =>
      id.startsWith("camera."),
    );
    const netvue = cameras.find((id) => hass && hass.states[id]?.attributes?.brand === "Netvue");
    return { type: "custom:netvue-camera-card", entity: netvue || cameras[0] || "camera.netvue" };
  }

  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._session = null;
    this._ptt = null;
    this._pttSnapshot = { ready: false, pressed: false, phase: "idle" };
    this._view = { state: "stopped" };
    this._muted = true;
    this._toastKey = null;
    this._toastTimer = null;
    this._hiddenTimer = null;
    this._owner = new PressOwner();
    this._built = false;
    this._connected = false;
    this._paused = false;
    this._wall = null;
    this._history = false;
    this._historyGeneration = 0;
    this._historySegments = [];
    this._historySegment = null;
    this._historyCursor = null;
    this._historyScrubbing = false;
    this._historyFollowTimer = null;
    this._historyFollowPending = false;
    this._onRelease = () => this._releasePtt();
    this._onVisibility = () => this._visibilityChanged();
    this._onWindowPointer = (event) => this._pointerEnded(event);
    this._onFullscreenChange = () => this._render();
  }

  setConfig(config) {
    if (!config || typeof config.entity !== "string" || !ENTITY_RE.test(config.entity)) {
      throw new Error("entity: a camera entity (camera.xxx) is required");
    }
    if (config.name !== undefined && typeof config.name !== "string") {
      throw new Error("name must be a string");
    }
    const changed = this._config && this._config.entity !== config.entity;
    this._config = { ...config };
    if (this._built) {
      this._render();
    }
    if (changed && this._connected) {
      this._closeHistory(false);
      this._restart();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (this._connected) {
      this._sync();
    }
    if (this._built) {
      this._render();
    }
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    this._build();
    this._connected = true;
    window.addEventListener("blur", this._onRelease);
    window.addEventListener("pagehide", this._onRelease);
    window.addEventListener("pointerup", this._onWindowPointer);
    window.addEventListener("pointercancel", this._onWindowPointer);
    document.addEventListener("visibilitychange", this._onVisibility);
    document.addEventListener("fullscreenchange", this._onFullscreenChange);
    this._sync();
  }

  disconnectedCallback() {
    this._connected = false;
    this._releasePtt();
    window.removeEventListener("blur", this._onRelease);
    window.removeEventListener("pagehide", this._onRelease);
    window.removeEventListener("pointerup", this._onWindowPointer);
    window.removeEventListener("pointercancel", this._onWindowPointer);
    document.removeEventListener("visibilitychange", this._onVisibility);
    document.removeEventListener("fullscreenchange", this._onFullscreenChange);
    this._clearTimer("_hiddenTimer");
    this._clearTimer("_toastTimer");
    this._paused = false;
    this._closeHistory(false);
    this._stopSession();
    this._video.srcObject = null;
  }

  _entityState() {
    return this._hass && this._config ? this._hass.states[this._config.entity] : undefined;
  }

  _available() {
    const state = this._entityState();
    return Boolean(state) && state.state !== "unavailable";
  }

  // Start or stop the session so that it matches the entity availability.
  _sync() {
    if (!this._config || !this._hass || this._paused || this._history) {
      return;
    }
    if (!this._available()) {
      if (this._session && this._session.active) {
        this._stopSession();
      }
      this._view = { state: "unavailable" };
    } else if (!this._session || (!this._session.active && this._view.state === "unavailable")) {
      this._startSession();
    }
  }

  _startSession() {
    this._stopSession();
    this._session = new NetvueSession({
      entity: this._config.entity,
      signalling: createSignalling(() => this._hass),
      timers: window,
      isHidden: () => document.hidden,
      getPlaybackTime: () => this._video.currentTime,
      onState: (view) => {
        this._view = view;
        this._render();
      },
      onStream: (stream) => this._setStream(stream),
      onPtt: (ptt) => {
        this._ptt = ptt;
        this._pttSnapshot = ptt ? ptt.snapshot : { ready: false, pressed: false, phase: "idle" };
        this._render();
      },
      onPttChange: (snapshot) => {
        if (!snapshot.pressed) {
          // The press ended (possibly server side): a later keyup/pointerup may never reach us.
          this._owner.clear();
        }
        this._pttSnapshot = snapshot;
        this._render();
      },
      onPttError: (code) => this._showMicError(code),
    });
    this._session.start();
  }

  _stopSession() {
    if (this._session) {
      this._session.stop();
      this._session = null;
    }
    this._ptt = null;
    this._pttSnapshot = { ready: false, pressed: false, phase: "idle" };
  }

  _restart() {
    this._stopSession();
    this._view = { state: "stopped" };
    this._sync();
  }

  _setStream(stream) {
    const video = this._video;
    if (!stream) {
      video.srcObject = null;
      return;
    }
    video.muted = this._muted;
    video.srcObject = stream;
    this._play();
  }

  _play() {
    const video = this._video;
    const result = video.play();
    if (result && result.catch) {
      result.catch(() => {
        // Autoplay policy: fall back to muted playback.
        if (!video.muted) {
          this._muted = true;
          video.muted = true;
          video.play().catch(() => {});
          this._render();
        }
      });
    }
  }

  _toggleMute() {
    this._muted = !this._muted;
    this._video.muted = this._muted;
    if (!this._muted) {
      this._play();
    }
    this._render();
  }

  async _toggleFullscreen() {
    try {
      if (this._isFullscreen()) {
        await document.exitFullscreen();
      } else if (this._wall) {
        await this._wall._openFullscreen(this);
      } else {
        await this._wrap.requestFullscreen();
      }
    } catch {
      this._showFullscreenError();
    }
  }

  _fullscreenSupported() {
    const target = this._wall ? this._wall._grid : this._wrap;
    return typeof target.requestFullscreen === "function"
      && typeof document.exitFullscreen === "function";
  }

  _isFullscreen() {
    return this._wall
      ? this._wall._isFullscreen() && this._wall._activeCard === this
      : this.shadowRoot.fullscreenElement === this._wrap;
  }

  _showMicError(code) {
    this._showToast(pttErrorKey(code));
  }

  _showFullscreenError() {
    this._showToast("fullscreenFailed");
  }

  _showToast(key) {
    this._toastKey = key;
    this._clearTimer("_toastTimer");
    this._toastTimer = setTimeout(() => {
      this._toastTimer = null;
      this._toastKey = null;
      this._render();
    }, ERROR_TOAST_MS);
    this._render();
  }

  _clearTimer(name) {
    if (this[name] !== null) {
      clearTimeout(this[name]);
      this[name] = null;
    }
  }

  _releasePtt() {
    this._owner.clear();
    if (this._ptt) {
      this._ptt.release();
    }
  }

  _visibilityChanged() {
    if (document.hidden) {
      this._releasePtt();
      this._clearTimer("_hiddenTimer");
      this._hiddenTimer = setTimeout(() => {
        this._hiddenTimer = null;
        this._paused = true;
        this._stopSession();
        this._view = { state: "paused" };
        this._render();
      }, HIDDEN_PAUSE_MS);
    } else {
      this._clearTimer("_hiddenTimer");
      if (this._paused) {
        this._paused = false;
        this._view = { state: "stopped" };
        this._sync();
      }
    }
  }

  _beginPress(owner) {
    if (!this._owner.begin(owner)) {
      return false;
    }
    if (!this._ptt || !this._ptt.press()) {
      this._owner.end(owner);
    }
    return true;
  }

  _endPress(owner) {
    if (this._owner.end(owner)) {
      this._releasePtt();
    }
  }

  _pointerEnded(event) {
    this._endPress(event.pointerId);
  }

  _build() {
    if (this._built) {
      return;
    }
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    el("style", {}, root).textContent = STYLE;
    const card = el("ha-card", {}, root);
    const wrap = el("div", { class: "wrap" }, card);
    this._wrap = wrap;
    const video = (this._video = el("video", { playsinline: "", autoplay: "" }, wrap));
    video.muted = true;
    this._historyVideo = el("video", { playsinline: "", controls: "", hidden: "" }, wrap);
    this._historyVideo.addEventListener("loadedmetadata", () => this._historyMetadata());
    this._historyVideo.addEventListener("timeupdate", () => this._historyTimeUpdate());
    this._historyVideo.addEventListener("ended", () => this._historyEnded());
    this._historyVideo.addEventListener("seeking", () => {
      if (!this._historyFollowPending) return;
      this._historyFollowPending = false;
      this._historyGeneration++;
      this._clearTimer("_historyFollowTimer");
      this._historyMessage.textContent = "";
    });
    this._historyVideo.addEventListener("error", () => {
      if (this._history) this._historyMessage.textContent = this._t("historyPlaybackError");
    });
    this._overlay = el("div", { class: "overlay" }, wrap);
    this._overlayText = el("div", {}, this._overlay);
    this._retry = el("button", { type: "button" }, this._overlay);
    this._retry.addEventListener("click", () => this._retryClicked());
    this._toast = el("div", { class: "toast", role: "alert" }, wrap);
    this._recordingIndicator = el("div", {
      class: "recording-indicator", hidden: "", role: "status",
    }, wrap);
    this._recordingIndicator.textContent = "● REC";
    this._previousButton = el("button", { type: "button", class: "navigate previous" }, wrap);
    this._previousButton.textContent = "‹";
    this._previousButton.addEventListener("click", () => this._wall?._navigate(-1));
    this._nextButton = el("button", { type: "button", class: "navigate next" }, wrap);
    this._nextButton.textContent = "›";
    this._nextButton.addEventListener("click", () => this._wall?._navigate(1));
    const bar = el("div", { class: "bar" }, wrap);
    this._status = el("div", { class: "status" }, bar);
    this._recordButton = el("button", { type: "button", class: "record" }, bar);
    this._recordButton.addEventListener("click", () => this._toggleManualRecording());
    this._historyButton = el("button", { type: "button", class: "history-button" }, bar);
    this._historyButton.addEventListener("click", () => this._openHistory());
    this._pttButton = el("button", { type: "button", class: "ptt" }, bar);
    this._bindPtt(this._pttButton);
    this._muteButton = el("button", { type: "button", class: "mute" }, bar);
    this._muteButton.addEventListener("click", () => this._toggleMute());
    this._fullscreenButton = el("button", { type: "button", class: "fullscreen" }, bar);
    this._fullscreenButton.addEventListener("click", () => this._toggleFullscreen());
    this._historyPanel = el("div", { class: "history-panel", hidden: "" }, wrap);
    this._historyPreview = el("img", { class: "history-preview", hidden: "" }, this._historyPanel);
    this._historyPreview.addEventListener("error", () => {
      this._historyPreview.hidden = true;
      if (this._history) this._historyMessage.textContent = this._t("historyPreviewError");
    });
    this._historyRange = el("input", { type: "range", step: "1000" }, this._historyPanel);
    this._historyRange.addEventListener("input", () => {
      this._historyScrubbing = true;
      const time = Number(this._historyRange.value);
      this._historyDate.value = localDateTime(time);
      this._historyRange.setAttribute("aria-valuetext", this._historyTimeLabel(time));
    });
    this._historyRange.addEventListener("change", () => {
      this._historyScrubbing = false;
      this._historySeek(Number(this._historyRange.value));
    });
    this._historyTicks = el("div", { class: "history-ticks", "aria-hidden": "true" }, this._historyPanel);
    this._historyTickLabels = [];
    for (let index = 0; index < 5; index++) {
      const tick = el("span", { class: "history-tick" }, this._historyTicks);
      if (index % 2 === 0) {
        const label = el("span", { class: "history-tick-label" }, tick);
        this._historyTickLabels.push({
          time: el("span", {}, label),
          date: el("span", { class: "history-tick-date" }, label),
        });
      }
    }
    const historyControls = el("div", { class: "history-controls" }, this._historyPanel);
    this._historyLiveButton = el("button", { type: "button" }, historyControls);
    this._historyLiveButton.addEventListener("click", () => this._closeHistory(true));
    this._historyHourButton = el("button", { type: "button" }, historyControls);
    this._historyHourButton.textContent = "−1 h";
    this._historyHourButton.addEventListener("click", () => this._historyJump(-3600000));
    this._historyTenButton = el("button", { type: "button" }, historyControls);
    this._historyTenButton.textContent = "−10 min";
    this._historyTenButton.addEventListener("click", () => this._historyJump(-600000));
    this._historyDate = el("input", { type: "datetime-local", step: "1" }, historyControls);
    this._historyDate.addEventListener("change", () => {
      const time = new Date(this._historyDate.value).getTime();
      if (Number.isFinite(time)) this._historySeek(time);
    });
    this._historyDownload = el("a", { download: "netvue-recording.mp4", hidden: "" }, historyControls);
    this._historyMessage = el("div", { class: "history-message", role: "status" }, this._historyPanel);
    this._live = el("div", { class: "sr", role: "status", "aria-live": "polite" }, wrap);
    this._render();
  }

  _retryClicked() {
    this._paused = false;
    this._view = { state: "connecting" };
    if (this._session) {
      this._session.retry();
    } else {
      this._sync();
    }
  }

  _bindPtt(button) {
    button.addEventListener("contextmenu", (event) => event.preventDefault());
    button.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) {
        return;
      }
      event.preventDefault();
      if (this._owner.active) {
        return;
      }
      try {
        button.setPointerCapture(event.pointerId);
      } catch {
        // The window listeners still end the press.
      }
      this._beginPress(event.pointerId);
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      button.addEventListener(type, (event) => this._pointerEnded(event));
    }
    button.addEventListener("keydown", (event) => {
      if (event.key !== " " && event.key !== "Enter" && event.key !== "Spacebar") {
        return;
      }
      event.preventDefault();
      if (!event.repeat) {
        this._beginPress(KEYBOARD);
      }
    });
    button.addEventListener("keyup", (event) => {
      if (event.key === " " || event.key === "Enter" || event.key === "Spacebar") {
        event.preventDefault();
        this._endPress(KEYBOARD);
      }
    });
    button.addEventListener("blur", () => this._releasePtt());
  }

  _entryId() {
    const state = this._entityState();
    return state?.attributes?.netvue_entry_id || null;
  }

  _recordingState() {
    const entryId = this._entryId();
    if (!entryId) return null;
    for (const [entityId, state] of Object.entries(this._hass?.states || {})) {
      if (entityId.startsWith("sensor.")
          && state.attributes?.netvue_entry_id === entryId
          && Object.prototype.hasOwnProperty.call(state.attributes, "manual")) {
        return state;
      }
    }
    return null;
  }

  async _toggleManualRecording() {
    const entryId = this._entryId();
    if (!entryId || !this._hass?.callApi || this._recordingPending) return;
    const current = this._recordingState();
    if (!current || current.state === "unavailable" || current.state === "unknown"
        || current.attributes?.continuous) return;
    const enabled = !(current?.attributes?.manual);
    this._recordingPending = true;
    this._render();
    try {
      await this._hass.callApi(
        "POST",
        `netvue_local/recordings/${encodeURIComponent(entryId)}`,
        { enabled },
      );
    } catch (error) {
      this._toastKey = "recordingError";
    } finally {
      this._recordingPending = false;
      this._render();
    }
  }

  async _openHistory() {
    const entryId = this._entryId();
    if (!entryId || !this._hass?.callApi || !this._hass?.callWS) return;
    this._history = true;
    this._historySegments = [];
    this._historyRange.disabled = true;
    this._historyTicks.hidden = true;
    const generation = ++this._historyGeneration;
    this._releasePtt();
    this._stopSession();
    this._video.hidden = true;
    this._historyVideo.hidden = false;
    this._historyPanel.hidden = false;
    this._wrap.setAttribute("data-history", "");
    this._historyMessage.textContent = this._t("historyLoading");
    this._render();
    try {
      await this._refreshHistory(entryId, generation);
      if (!this._history || generation !== this._historyGeneration) return;
      const last = this._historySegments.at(-1);
      if (!last) {
        this._historyMessage.textContent = this._t("historyEmpty");
        return;
      }
      await this._historySeek(Math.max(last.start, last.end - 1000));
    } catch {
      if (this._history && generation === this._historyGeneration) {
        this._historyMessage.textContent = this._t("historyLoadError");
      }
    }
  }

  async _refreshHistory(entryId, generation) {
    const data = await this._hass.callApi("GET", `netvue_local/recordings/${encodeURIComponent(entryId)}`);
    const segments = recordingSegments(data.recordings);
    if (!this._history || generation !== this._historyGeneration) return;
    this._historySegments = segments;
    this._historyRange.disabled = !segments.length;
    this._historyTicks.hidden = !segments.length;
    if (segments.length) {
      this._setHistoryWindow(this._historyCursor);
    }
  }

  _setHistoryWindow(time) {
    if (!this._historySegments.length) return;
    const first = this._historySegments[0].start;
    const last = this._historySegments.at(-1).end - 1;
    let end = last;
    if (Number.isFinite(time) && time < last - HISTORY_WINDOW_MS) {
      end = Math.min(last, Math.max(first + HISTORY_WINDOW_MS, time + HISTORY_WINDOW_MS / 2));
    }
    this._historyRange.min = String(Math.max(first, end - HISTORY_WINDOW_MS));
    this._historyRange.max = String(end);
    this._renderHistoryTicks();
  }

  _historyTimeLabel(time) {
    const language = this._hass?.locale?.language || this._hass?.language || undefined;
    if (!this._historyTimeFormatter || this._historyTimeLanguage !== language) {
      this._historyTimeLanguage = language;
      this._historyTimeFormatter = new Intl.DateTimeFormat(language, { dateStyle: "short", timeStyle: "medium" });
    }
    return this._historyTimeFormatter.format(time);
  }

  _renderHistoryTicks() {
    const start = Number(this._historyRange.min);
    const end = Number(this._historyRange.max);
    const language = this._hass?.locale?.language || this._hass?.language || undefined;
    const first = new Date(start);
    const last = new Date(end);
    const showDate = first.toDateString() !== last.toDateString();
    const showYear = first.getFullYear() !== last.getFullYear();
    const timeFormat = new Intl.DateTimeFormat(language, {
      hour: "2-digit", minute: "2-digit", ...(end - start < 120000 ? { second: "2-digit" } : {}),
    });
    const dateFormat = new Intl.DateTimeFormat(language, {
      day: "2-digit", month: "2-digit", ...(showYear ? { year: "numeric" } : {}),
    });
    this._historyTickLabels.forEach((label, index) => {
      const time = start + (end - start) * index / 2;
      label.time.textContent = timeFormat.format(time);
      label.date.textContent = showDate ? dateFormat.format(time) : "";
    });
  }

  async _historySeek(time) {
    if (!this._history || !Number.isFinite(time)) return;
    this._clearTimer("_historyFollowTimer");
    this._historyFollowPending = false;
    const generation = ++this._historyGeneration;
    const entryId = this._entryId();
    this._historyVideo.pause();
    this._historySegment = null;
    this._historyPendingSeek = null;
    this._historyPreview.hidden = true;
    this._historyPreview.removeAttribute("src");
    this._historyCursor = time;
    if (time < Number(this._historyRange.min) || time > Number(this._historyRange.max)) {
      this._setHistoryWindow(time);
    }
    this._historyRange.value = String(time);
    this._historyRange.setAttribute("aria-valuetext", this._historyTimeLabel(time));
    this._historyDate.value = localDateTime(time);
    let segment = segmentAt(this._historySegments, time);
    if (!segment) {
      try {
        await this._refreshHistory(entryId, generation);
      } catch {
        if (this._history && generation === this._historyGeneration) {
          this._historyMessage.textContent = this._t("historyLoadError");
        }
        return;
      }
      if (!this._history || generation !== this._historyGeneration) return;
      segment = segmentAt(this._historySegments, time);
    }
    if (!segment) {
      this._historySegment = null;
      this._historyVideo.removeAttribute("src");
      this._historyVideo.load();
      this._historyDownload.hidden = true;
      this._historyPreview.hidden = true;
      this._historyMessage.textContent = this._t("historyGap");
      return;
    }
    this._historyMessage.textContent = this._t("historyLoading");
    try {
      const duration = Math.floor(segment.end - segment.start) / 1000;
      const base = `${encodeURIComponent(entryId)}/${segment.id}`;
      const offset = Math.floor((time - segment.start) / 1000);
      const [media, preview] = await Promise.all([
        this._hass.callWS({
          type: "auth/sign_path",
          path: `/api/netvue_local/playback/${base}?duration=${duration}`,
          expires: 86400,
        }),
        this._hass.callWS({
          type: "auth/sign_path",
          path: `/api/netvue_local/thumbnail/${base}?offset=${offset}`,
          expires: 86400,
        }),
      ]);
      if (!this._history || generation !== this._historyGeneration) return;
      this._historySegment = segment;
      this._historyPendingSeek = { generation, offset: (time - segment.start) / 1000 };
      this._historyVideo.src = media.path;
      this._historyPreview.src = preview.path;
      this._historyPreview.hidden = false;
      this._historyVideo.muted = this._muted;
      this._historyDownload.href = media.path;
      this._historyDownload.hidden = false;
      this._historyVideo.load();
    } catch {
      if (this._history && generation === this._historyGeneration) {
        this._historyMessage.textContent = this._t("historyPlaybackError");
      }
    }
  }

  _historyMetadata() {
    const pending = this._historyPendingSeek;
    if (!this._history || !pending || pending.generation !== this._historyGeneration) return;
    this._historyPendingSeek = null;
    if (!Number.isFinite(this._historyVideo.duration)) {
      this._historyMessage.textContent = this._t("historyPlaybackError");
      return;
    }
    this._historyVideo.currentTime = Math.min(pending.offset, Math.max(0, this._historyVideo.duration - 0.1));
    this._historyMessage.textContent = "";
    const generation = pending.generation;
    this._historyVideo.play().catch(() => {
      if (this._history && generation === this._historyGeneration) {
        this._historyMessage.textContent = this._t("historyPressPlay");
      }
    });
  }

  _historyTimeUpdate() {
    if (!this._history || !this._historySegment || this._historyScrubbing) return;
    this._historyCursor = this._historySegment.start + this._historyVideo.currentTime * 1000;
    if (this._historyCursor < Number(this._historyRange.min)
        || this._historyCursor > Number(this._historyRange.max)) {
      this._setHistoryWindow(this._historyCursor);
    }
    this._historyRange.value = String(this._historyCursor);
    this._historyRange.setAttribute("aria-valuetext", this._historyTimeLabel(this._historyCursor));
    this._historyDate.value = localDateTime(this._historyCursor);
  }

  async _historyEnded() {
    if (!this._history || !this._historySegment) return;
    this._historyFollowPending = true;
    const generation = ++this._historyGeneration;
    const previous = this._historySegment;
    const cursor = previous.start + this._historyVideo.currentTime * 1000;
    try {
      await this._refreshHistory(this._entryId(), generation);
      if (!this._history || generation !== this._historyGeneration) return;
      const current = this._historySegments.find((segment) => segment.id === previous.id);
      const next = this._historySegments.find((segment) => segment.start > previous.start
        && segment.end > previous.end);
      const active = Boolean(this._recordingState()?.attributes?.active);
      if (current && (current.end > previous.end + HISTORY_FOLLOW_BUFFER_MS
          || ((next || !active) && current.end > previous.end + 1000))) {
        await this._historySeek(Math.min(cursor, current.end - 1));
        return;
      }
      if (next && next.start <= previous.end + 2000) {
        await this._historySeek(Math.max(next.start, previous.end));
      } else if (!next && active) {
        this._historyMessage.textContent = this._t("historyWaiting");
        this._historyFollowTimer = setTimeout(() => {
          this._historyFollowTimer = null;
          this._historyEnded();
        }, HISTORY_FOLLOW_POLL_MS);
      } else {
        this._historyFollowPending = false;
        this._historyMessage.textContent = next ? this._t("historyGap") : this._t("historyEnd");
      }
    } catch {
      if (this._history && generation === this._historyGeneration) {
        this._historyFollowPending = false;
        this._historyMessage.textContent = this._t("historyLoadError");
      }
    }
  }

  _historyJump(delta) {
    if (this._historyCursor !== null) this._historySeek(this._historyCursor + delta);
  }

  _closeHistory(resume) {
    if (!this._history) return;
    this._history = false;
    this._historyGeneration++;
    this._historySegment = null;
    this._historyPendingSeek = null;
    this._historyCursor = null;
    this._historyScrubbing = false;
    this._historyFollowPending = false;
    this._clearTimer("_historyFollowTimer");
    this._historyVideo.pause();
    this._historyVideo.removeAttribute("src");
    this._historyVideo.load();
    this._historyVideo.hidden = true;
    this._historyPanel.hidden = true;
    this._historyDownload.hidden = true;
    this._historyPreview.hidden = true;
    this._wrap.removeAttribute("data-history");
    this._video.hidden = false;
    if (resume && this._connected) {
      this._view = { state: "stopped" };
      this._sync();
    }
    this._render();
  }

  _label() {
    const t = this._t;
    const { state, error } = this._view;
    const ptt = this._pttSnapshot;
    if (state === "unavailable") return t("unavailable");
    if (state === "paused") return t("paused");
    if (state === "connecting") return t("connecting");
    if (state === "reconnecting") return t("reconnecting");
    if (state === "error") return error && error.code === "netvue_webrtc_busy" ? t("busy") : t("error");
    if (state === "live") {
      if (ptt.pressed) return t(ptt.phase === "speaking" ? "speaking" : "opening");
      return this._muted ? t("live") : t("listening");
    }
    return "";
  }

  _render() {
    if (!this._built) {
      return;
    }
    const t = (this._t = translator(this._hass && (this._hass.locale?.language || this._hass.language)));
    const { state } = this._view;
    const ptt = this._pttSnapshot;
    const label = this._label();
    const busy = state === "connecting" || state === "reconnecting";
    const failed = state === "error" || state === "unavailable" || state === "paused";
    this._overlay.hidden = this._history || !(busy || failed);
    this._overlayText.textContent = busy || failed ? label : "";
    this._retry.hidden = state !== "error";
    this._retry.textContent = t("retry");
    this._toast.hidden = !this._toastKey;
    this._toast.textContent = this._toastKey ? t(this._toastKey) : "";
    const talking = state === "live" && ptt.pressed;
    this._status.textContent = talking && ptt.phase === "speaking" ? `${label} — ${t("halfDuplex")}` : label;
    this._live.textContent = this._toastKey ? t(this._toastKey) : label;
    setIcon(this._muteButton, this._muted ? ICONS.muted : ICONS.speaker);
    this._muteButton.setAttribute("aria-label", this._muted ? t("unmute") : t("mute"));
    this._muteButton.setAttribute("aria-pressed", String(!this._muted));
    const fullscreen = this._isFullscreen();
    const navigation = fullscreen && this._wall && this._wall._cards.length > 1;
    this._previousButton.hidden = !navigation;
    this._nextButton.hidden = !navigation;
    this._previousButton.setAttribute("aria-label", t("previousCamera"));
    this._nextButton.setAttribute("aria-label", t("nextCamera"));
    this._previousButton.title = t("previousCamera");
    this._nextButton.title = t("nextCamera");
    const fullscreenLabel = fullscreen ? t("exitFullscreen") : t("fullscreen");
    const fullscreenSupported = this._fullscreenSupported();
    setIcon(this._fullscreenButton, ICONS.fullscreen);
    this._fullscreenButton.disabled = !fullscreenSupported;
    this._fullscreenButton.setAttribute(
      "aria-label",
      fullscreenSupported ? fullscreenLabel : t("fullscreenUnsupported"),
    );
    this._fullscreenButton.setAttribute("aria-pressed", String(fullscreen));
    this._fullscreenButton.title = fullscreenSupported
      ? fullscreenLabel
      : t("fullscreenUnsupported");
    setIcon(this._pttButton, ICONS.mic);
    const available = state === "live" && ptt.ready;
    this._pttButton.disabled = !available && !ptt.pressed;
    this._pttButton.setAttribute("aria-pressed", String(ptt.pressed));
    this._pttButton.setAttribute("aria-label", available || ptt.pressed ? t("talk") : t("talkNotReady"));
    this._pttButton.title = this._pttButton.getAttribute("aria-label");
    const recording = this._recordingState();
    const manual = Boolean(recording?.attributes?.manual);
    const continuous = Boolean(recording?.attributes?.continuous);
    this._recordingIndicator.hidden = !recording
      || recording.state === "unavailable"
      || recording.state === "unknown"
      || !recording.attributes?.active;
    setIcon(this._recordButton, ICONS.record);
    this._recordButton.hidden = continuous;
    this._recordButton.disabled = !this._entryId()
      || !recording || recording.state === "unavailable" || recording.state === "unknown"
      || Boolean(this._recordingPending);
    this._recordButton.setAttribute("aria-pressed", String(manual));
    this._recordButton.setAttribute(
      "aria-label",
      manual ? t("stopRecording") : t("startRecording"),
    );
    this._recordButton.title = this._recordButton.getAttribute("aria-label");
    this._recordButton.style.color = manual
      ? "var(--error-color, #db4437)"
      : "";
    setIcon(this._historyButton, ICONS.history);
    this._historyButton.disabled = !this._entryId();
    this._historyButton.setAttribute("aria-label", t("recordingHistory"));
    this._historyButton.title = t("recordingHistory");
    this._historyLiveButton.textContent = t("historyLive");
    this._historyDate.setAttribute("aria-label", t("historyDate"));
    this._historyRange.setAttribute("aria-label", t("historyTimeline"));
    this._historyDownload.textContent = t("historyDownload");
    this._historyPreview.setAttribute("alt", t("historyPreview"));
    const name = this._config && (this._config.name || this._entityState()?.attributes?.friendly_name);
    this._video.setAttribute("aria-label", name || "");
  }
}

if (!customElements.get("netvue-camera-card")) {
  customElements.define("netvue-camera-card", NetvueCameraCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "netvue-camera-card",
  name: "Netvue Camera",
  description: "Video, camera audio and push-to-talk on a single WebRTC connection",
});
