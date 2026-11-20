// Lovelace card: video, camera audio and push-to-talk on ONE RTCPeerConnection.

import { createSignalling } from "./signalling.js";
import { NetvueSession } from "./session.js";
import { KEYBOARD, PressOwner } from "./press-owner.js";
import { pttErrorKey, translator } from "./i18n.js";
import "./netvue-camera-wall-card.js";
import "./netvue-camera-status-card.js";

const HIDDEN_PAUSE_MS = 60000;
const ERROR_TOAST_MS = 4000;
const ENTITY_RE = /^camera\.[a-z0-9_]+$/;

const STYLE = `
:host { display: block; }
ha-card { overflow: hidden; position: relative; }
.wrap { position: relative; background: #000; line-height: 0; }
video { width: 100%; max-height: 80vh; background: #000; }
.wrap:fullscreen {
  width: 100vw; height: 100vh; height: 100dvh; display: flex; align-items: center; justify-content: center;
}
.wrap:fullscreen video {
  width: 100%; height: 100%; max-height: 100vh; max-height: 100dvh; object-fit: contain;
}
.overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 12px; color: #fff; background: rgba(0,0,0,.55); line-height: 1.4; }
.overlay[hidden], .toast[hidden], button[hidden] { display: none; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 8px;
  padding: 8px; background: linear-gradient(transparent, rgba(0,0,0,.6)); line-height: 1.4; }
.status { flex: 1; color: #fff; font-size: 14px; }
button { font: inherit; color: #fff; background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.5);
  border-radius: 20px; padding: 8px 14px; cursor: pointer; min-height: 40px; }
button:focus-visible { outline: 3px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
button[disabled] { opacity: .5; cursor: default; }
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

export class NetvueCameraCard extends HTMLElement {
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
    if (!this._config || !this._hass || this._paused) {
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
      if (document.fullscreenElement === this._wrap) {
        await document.exitFullscreen();
      } else {
        await this._wrap.requestFullscreen();
      }
    } catch {
      this._showFullscreenError();
    }
  }

  _fullscreenSupported() {
    return typeof this._wrap.requestFullscreen === "function"
      && typeof document.exitFullscreen === "function";
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
    this._overlay = el("div", { class: "overlay" }, wrap);
    this._overlayText = el("div", {}, this._overlay);
    this._retry = el("button", { type: "button" }, this._overlay);
    this._retry.addEventListener("click", () => this._retryClicked());
    this._toast = el("div", { class: "toast", role: "alert" }, wrap);
    const bar = el("div", { class: "bar" }, wrap);
    this._status = el("div", { class: "status" }, bar);
    this._muteButton = el("button", { type: "button", class: "mute" }, bar);
    this._muteButton.addEventListener("click", () => this._toggleMute());
    this._fullscreenButton = el("button", { type: "button", class: "fullscreen" }, bar);
    this._fullscreenButton.addEventListener("click", () => this._toggleFullscreen());
    this._pttButton = el("button", { type: "button", class: "ptt" }, bar);
    this._bindPtt(this._pttButton);
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
    this._overlay.hidden = !(busy || failed);
    this._overlayText.textContent = busy || failed ? label : "";
    this._retry.hidden = state !== "error";
    this._retry.textContent = t("retry");
    this._toast.hidden = !this._toastKey;
    this._toast.textContent = this._toastKey ? t(this._toastKey) : "";
    const talking = state === "live" && ptt.pressed;
    this._status.textContent = talking && ptt.phase === "speaking" ? `${label} — ${t("halfDuplex")}` : label;
    this._live.textContent = this._toastKey ? t(this._toastKey) : label;
    this._muteButton.textContent = this._muted ? "🔇" : "🔊";
    this._muteButton.setAttribute("aria-label", this._muted ? t("unmute") : t("mute"));
    this._muteButton.setAttribute("aria-pressed", String(!this._muted));
    const fullscreen = document.fullscreenElement === this._wrap;
    const fullscreenLabel = fullscreen ? t("exitFullscreen") : t("fullscreen");
    const fullscreenSupported = this._fullscreenSupported();
    this._fullscreenButton.textContent = "⛶";
    this._fullscreenButton.disabled = !fullscreenSupported;
    this._fullscreenButton.setAttribute(
      "aria-label",
      fullscreenSupported ? fullscreenLabel : t("fullscreenUnsupported"),
    );
    this._fullscreenButton.setAttribute("aria-pressed", String(fullscreen));
    this._fullscreenButton.title = fullscreenSupported
      ? fullscreenLabel
      : t("fullscreenUnsupported");
    this._pttButton.textContent = "🎤";
    const available = state === "live" && ptt.ready;
    this._pttButton.disabled = !available && !ptt.pressed;
    this._pttButton.setAttribute("aria-pressed", String(ptt.pressed));
    this._pttButton.setAttribute("aria-label", available || ptt.pressed ? t("talk") : t("talkNotReady"));
    this._pttButton.title = this._pttButton.getAttribute("aria-label");
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
