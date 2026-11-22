// Lovelace card: video, camera audio and push-to-talk on ONE RTCPeerConnection.

import { createSignalling } from "./signalling.js";
import { NetvueSession } from "./session.js";
import { KEYBOARD, PressOwner } from "./press-owner.js";
import { pttErrorKey, translator } from "./i18n.js";
import { localDateTime, recordingSegments, segmentAt, mergeIntervals, protectedRecordingInterval, intervalAt } from "./recording-timeline.js";
import "./netvue-camera-wall-card.js";
import "./netvue-camera-status-card.js";
import "./netvue-protected-card.js";
import "./card-editors.js";

const HIDDEN_PAUSE_MS = 60000;
const ERROR_TOAST_MS = 4000;
const HISTORY_FOLLOW_POLL_MS = 5000;
const HISTORY_FOLLOW_BUFFER_MS = 10000;
const HISTORY_WINDOW_MS = 12 * 60 * 60 * 1000;
const OVERLAY_DEFAULTS = { live: true, replay: true, export: false };
const OVERLAY_REFRESH_MS = 30000;
const HISTORY_SAMPLE_MAX_GAP_SECONDS = 3;
const ENTITY_RE = /^camera\.[a-z0-9_]+$/;
const DETECTION_LABELS_FR = {
  person: "Personne", bird: "Oiseau", cat: "Chat", dog: "Chien",
  car: "Voiture", bus: "Bus", truck: "Camion", bicycle: "Vélo", motorcycle: "Moto",
};

const STYLE = `
:host { display: block; }
:host([css-fullscreen]) { position: fixed; inset: 0; z-index: 2147483647; width: 100vw; height: 100vh; height: 100dvh; }
ha-card { overflow: hidden; position: relative; }
.wrap { position: relative; container-type: inline-size; background: #000; line-height: 0; }
video { width: 100%; max-height: 80vh; background: #000; }
.live-player, .history-player { position: relative; }
.live-player { isolation: isolate; }
.live-player video, .history-player video { display: block; object-fit: contain; }
.wrap[data-history] .live-player, .history-player[hidden] { display: none; }
.wrap[data-fullscreen], :host([wall-active]) .wrap {
  width: 100vw; height: 100vh; height: 100dvh; display: flex; align-items: center; justify-content: center;
}
.wrap[data-fullscreen] .live-player, :host([wall-active]) .live-player { width: 100%; height: 100%; }
.wrap[data-fullscreen] video, :host([wall-active]) video {
  width: 100%; height: 100%; max-height: 100vh; max-height: 100dvh; object-fit: contain;
}
.overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 12px; color: #fff; background: rgba(0,0,0,.55); line-height: 1.4; }
.overlay[hidden], .toast[hidden], button[hidden] { display: none; }
.detections { position: absolute; z-index: 1; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 0; height: 0; overflow: hidden; pointer-events: none; }
.detections[hidden] { display: none; }
.detections rect { fill: none; stroke: #00d4ff; stroke-width: 4; vector-effect: non-scaling-stroke; }
.detections text { fill: white; font: bold 16px sans-serif; paint-order: stroke; stroke: black; stroke-width: 4px; stroke-linejoin: round; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 4px;
  padding: 8px; background: linear-gradient(transparent, rgba(0,0,0,.6)); line-height: 1.4; }
.status { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: #fff; font-size: 14px; }
button { font: inherit; color: #fff; background: rgba(0,0,0,.8); border: 1px solid rgba(255,255,255,.5);
  border-radius: 20px; padding: 8px 14px; cursor: pointer; min-height: 40px; }
.bar button, .history-actions > button, .history-actions > a, .history-date > summary {
  box-sizing: border-box; width: 44px; height: 44px; min-height: 44px; padding: 0; border: 0;
  border-radius: 50%; background: transparent; color: #fff; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 44px; }
.bar button[hidden] { display: none; }
.bar button:hover, .bar button:focus-visible, .history-actions > button:hover,
.history-actions > button:focus-visible, .history-actions > a:hover, .history-actions > a:focus-visible,
.history-date > summary:hover, .history-date > summary:focus-visible { background: rgba(0,0,0,.8); }
.volume-control { position: relative; z-index: 1; display: flex; align-items: center;
  flex: 0 0 auto; border-radius: 24px; }
.volume-control:hover, .volume-control:focus-within { background: rgba(0,0,0,.8); }
.volume-range { box-sizing: border-box; flex: 0 0 0; width: 0; height: 44px; min-width: 0;
  margin: 0; padding: 0; border-radius: 22px 0 0 22px; background: rgba(0,0,0,.8);
  opacity: 0; pointer-events: none; appearance: none; -webkit-appearance: none;
  transition: flex-basis .15s, width .15s, opacity .15s; }
.volume-control:hover .volume-range, .volume-control:focus-within .volume-range {
  flex-basis: 78px; width: 78px; padding: 0 6px; opacity: 1; pointer-events: auto;
}
@container (max-width: 334px) {
  .volume-control { flex: 0 0 44px; }
  .volume-range { position: absolute; right: 0; bottom: 44px; }
}
.volume-range::-webkit-slider-runnable-track { height: 4px; border-radius: 2px; background: #bbb; }
.volume-range::-moz-range-track { height: 4px; border-radius: 2px; background: #bbb; }
.volume-range::-webkit-slider-thumb { width: 12px; height: 12px; margin-top: -4px; border: 0;
  border-radius: 50%; background: #fff; appearance: none; -webkit-appearance: none; }
.volume-range::-moz-range-thumb { width: 12px; height: 12px; border: 0; border-radius: 50%; background: #fff; }
.volume-range:focus-visible { outline: 2px solid #fff; outline-offset: -2px; }
.bar .mute svg path:first-child { fill: currentColor; stroke: none; }
.wrap[data-history] .bar { display: none; }
.history-player video::-webkit-media-controls-fullscreen-button { display: none; }
.history-actions { position: absolute; top: 4px; right: 4px; z-index: 2; display: flex;
  justify-content: flex-end; align-items: center; width: min(338px, calc(100% - 8px));
  border-radius: 24px; background: rgba(0,0,0,.75); line-height: 1.4; }
.history-actions > button, .history-actions > a, .history-date { flex: 1 1 0; min-width: 0; max-width: 40px; }
.history-actions > .history-live { flex: 0 0 58px; max-width: 58px; font: 600 11px sans-serif; }
.history-actions > button, .history-actions > a, .history-date > summary { width: 100%; height: 40px; min-height: 40px; }
.history-actions > a[hidden], .history-actions > button[hidden] { display: none; }
.history-actions svg { width: 20px; height: 20px; fill: none;
  stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.history-actions svg text { fill: currentColor; stroke: none; font: bold 8px sans-serif; }
.history-date { position: relative; }
.history-date > summary { list-style: none; }
.history-date > summary::-webkit-details-marker { display: none; }
.history-date > summary:focus-visible, .history-actions > a:focus-visible {
  outline: 3px solid var(--primary-color, #03a9f4); outline-offset: 2px;
}
.history-date-content { position: absolute; top: 100%; right: 0; box-sizing: border-box; width: 220px;
  padding: 8px; border: 1px solid #666; border-radius: 8px; background: #222; color: #fff;
  font: 13px sans-serif; line-height: 1.4; box-shadow: 0 4px 12px rgba(0,0,0,.5); }
.history-date-content label { display: block; padding: 8px; }
.history-date-content input { box-sizing: border-box; width: 100%; margin-top: 6px; padding: 5px;
  border: 1px solid #888; border-radius: 4px; color: #fff; background: #111; color-scheme: dark; font: inherit; }
.history-panel { position: relative; margin: 8px; padding: 12px; border-radius: 8px;
  background: rgba(0,0,0,.9); color: #fff; line-height: 1.4; }
.wrap[data-fullscreen][data-history], :host([wall-active]) .wrap[data-history] { flex-direction: column; }
.wrap[data-fullscreen][data-history] .history-player, :host([wall-active]) .wrap[data-history] .history-player {
  flex: 1 1 0; min-height: 0; width: 100%;
}
.wrap[data-fullscreen][data-history] .history-player video, :host([wall-active]) .history-player video {
  width: 100%; height: 100%;
}
.wrap[data-fullscreen][data-history] .history-panel, :host([wall-active]) .wrap[data-history] .history-panel {
  flex: 0 0 auto; width: calc(100% - 32px);
}
.history-panel[hidden], video[hidden] { display: none; }
.history-range-wrap { position: relative; }
.history-ticks:not([hidden]) + .history-range-wrap { margin-top: -5px; }
.history-rail { position: absolute; top: 10px; left: 10px; right: 10px; height: 8px;
  border-radius: 4px; background: #303438; overflow: hidden; pointer-events: none; }
.history-available { position: absolute; top: 0; height: 100%; background: #299b6b; }
.history-protected { position: absolute; top: 0; height: 100%; background: #9b59b6; z-index: 1; }
.history-cursor { position: absolute; z-index: 2; top: 0; bottom: 0; left: 10px; right: 10px;
  pointer-events: none; }
.history-cursor[hidden] { display: none; }
.history-cursor::before, .history-cursor::after { content: ""; position: absolute;
  left: var(--history-position); transform: translateX(-50%); background: #f5a623; }
.history-cursor::before { top: 8px; width: 3px; height: 22px; }
.history-cursor::after { top: 2px; width: 12px; height: 12px; border-radius: 50%; }
.history-panel input[type="range"] { position: relative; z-index: 1; display: block; box-sizing: border-box;
  width: 100%; height: 28px; margin: 0; padding: 0; cursor: pointer;
  touch-action: pan-x;
  appearance: none; -webkit-appearance: none; background: transparent; }
.history-panel input[type="range"]::-webkit-slider-runnable-track { height: 8px; background: transparent; }
.history-panel input[type="range"]::-moz-range-track { height: 8px; background: transparent; }
.history-panel input[type="range"]::-webkit-slider-thumb { width: 20px; height: 20px; margin-top: -6px;
  border: 0; background: transparent;
  appearance: none; -webkit-appearance: none; }
.history-panel input[type="range"]::-moz-range-thumb { box-sizing: border-box; width: 20px; height: 20px;
  border: 0; background: transparent; }
.history-panel input[type="range"]:focus-visible { outline: 2px solid #fff; outline-offset: 3px; }
.history-panel input[type="range"]:disabled { cursor: default; }
.history-position { position: relative; height: 23px; margin: 2px 10px 0; pointer-events: none; }
.history-position[hidden] { display: none; }
.history-position-label { position: absolute; left: clamp(36px, var(--history-position), calc(100% - 36px));
  transform: translateX(-50%); white-space: nowrap; color: #fff; font-size: 13px; }
.history-ticks { position: relative; display: flex; justify-content: space-between; margin: 0 10px;
  height: 34px; color: #ddd; font-size: 11px; line-height: 1.25; }
.history-ticks[hidden] { display: none; }
.history-tick { position: relative; align-self: flex-end; width: 1px; height: 3px; background: #aaa; }
.history-tick-label { position: absolute; bottom: 2px; left: 0; transform: translateX(-50%);
  text-align: center; white-space: nowrap; }
.history-tick:first-child .history-tick-label { transform: none; text-align: left; }
.history-tick:last-child .history-tick-label { transform: translateX(-100%); text-align: right; }
.history-tick-date { display: block; color: #aaa; font-size: 10px; }
.history-message { min-height: 1.4em; font-size: 13px; }
.history-actions > button[data-protected] { color: #be81d6; }
.history-protection-message:empty { display: none; }
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
.bar .ptt[aria-pressed="true"] { background: var(--error-color, #db4437); }
.toast { position: absolute; left: 8px; right: 8px; top: 8px; padding: 8px 12px; border-radius: 8px;
  color: #fff; background: rgba(176,0,32,.9); line-height: 1.4; font-size: 14px; }
.bar .overlay-toggle[aria-pressed="true"], .history-actions .overlay-toggle[aria-pressed="true"] {
  color: #00d4ff; }
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
  exitFullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h5V3M21 8h-5V3M21 16h-5v5M3 16h5v5"/></svg>',
  record: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7"/></svg>',
  history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
  backHour: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10a8 8 0 1 1 2 7M4 5v5h5"/><text x="8" y="15">1h</text></svg>',
  backMinutes: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10a8 8 0 1 1 2 7M4 5v5h5"/><text x="6" y="15" style="font-size:7px">10m</text></svg>',
  backFifteen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10a8 8 0 1 1 2 7M4 5v5h5"/><text x="8" y="15">15</text></svg>',
  forwardFifteen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 10a8 8 0 1 0-2 7M20 5v5h-5"/><text x="8" y="15">15</text></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.5 3 8.5 7 10 4-1.5 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>',
  download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m-4-4 4 4 4-4M4 17v3h16v-3"/></svg>',
  settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"/><path d="m19.4 15 .1.1 1.4 1.1-1.6 2.8-1.7-.5a8 8 0 0 1-1.8 1l-.4 1.7h-3.2l-.4-1.7a8 8 0 0 1-1.8-1l-1.7.5-1.6-2.8 1.4-1.1a8 8 0 0 1 0-2l-1.4-1.1 1.6-2.8 1.7.5a8 8 0 0 1 1.8-1l.4-1.7h3.2l.4 1.7a8 8 0 0 1 1.8 1l1.7-.5 1.6 2.8-1.4 1.1a8 8 0 0 1 0 2z" transform="translate(-1.6 -2.1)"/></svg>',
  boxes: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 4v4H4M16 20v-4h4"/></svg>',
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
    this._volume = 1;
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
    this._historyProtected = [];
    this._historySegment = null;
    this._historyCursor = null;
    this._historyScrubbing = false;
    this._historyFollowTimer = null;
    this._historyFollowPending = false;
    this._overlayPreferences = { ...OVERLAY_DEFAULTS };
    this._overlayOverrides = {};
    this._overlayGeneration = 0;
    this._overlayFetchAfter = 0;
    this._overlayFetchPending = false;
    this._historyDetections = [];
    this._cssFullscreen = false;
    this._onRelease = () => this._releasePtt();
    this._onVisibility = () => this._visibilityChanged();
    this._onWindowPointer = (event) => this._pointerEnded(event);
    this._onFullscreenChange = () => this._render();
    this._onKeyDown = (event) => {
      if (event.key === "Escape" && this._cssFullscreen) {
        event.preventDefault();
        this._cssFullscreen = false;
        this._render();
      }
    };
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
    this._loadOverlayPreferences();
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
    this._loadOverlayPreferences();
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
    this._loadOverlayPreferences(true);
    this._detectionResizeObserver.observe(this._video);
    this._detectionResizeObserver.observe(this._historyVideo);
    window.addEventListener("blur", this._onRelease);
    window.addEventListener("pagehide", this._onRelease);
    window.addEventListener("pointerup", this._onWindowPointer);
    window.addEventListener("pointercancel", this._onWindowPointer);
    document.addEventListener("visibilitychange", this._onVisibility);
    document.addEventListener("fullscreenchange", this._onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", this._onFullscreenChange);
    document.addEventListener("keydown", this._onKeyDown);
    this._sync();
  }

  disconnectedCallback() {
    this._connected = false;
    this._detectionResizeObserver.disconnect();
    this._releasePtt();
    window.removeEventListener("blur", this._onRelease);
    window.removeEventListener("pagehide", this._onRelease);
    window.removeEventListener("pointerup", this._onWindowPointer);
    window.removeEventListener("pointercancel", this._onWindowPointer);
    document.removeEventListener("visibilitychange", this._onVisibility);
    document.removeEventListener("fullscreenchange", this._onFullscreenChange);
    document.removeEventListener("webkitfullscreenchange", this._onFullscreenChange);
    document.removeEventListener("keydown", this._onKeyDown);
    this._clearTimer("_hiddenTimer");
    this._clearTimer("_toastTimer");
    this._cssFullscreen = false;
    this.removeAttribute("css-fullscreen");
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

  _setVolume(value) {
    const wasMuted = this._muted;
    if (value > 0) this._volume = value;
    this._muted = value === 0;
    this._video.volume = this._volume;
    this._historyVideo.volume = this._volume;
    this._video.muted = this._muted;
    if (wasMuted && !this._muted) this._play();
    this._render();
  }

  async _toggleFullscreen() {
    if (this._cssFullscreen) {
      this._cssFullscreen = false;
      this._render();
      return;
    }
    try {
      if (this._isFullscreen()) {
        const webkit = this._nativeFullscreen("webkitFullscreenElement")
          && !this._nativeFullscreen("fullscreenElement");
        const exit = webkit
          ? document.webkitExitFullscreen || document.exitFullscreen
          : document.exitFullscreen || document.webkitExitFullscreen;
        if (!exit) throw new Error("Fullscreen exit unavailable");
        await exit.call(document);
      } else if (this._wall && typeof this._wall._grid.requestFullscreen === "function"
          && typeof document.exitFullscreen === "function") {
        await this._wall._openFullscreen(this);
      } else {
        const request = this._wrap.requestFullscreen || this._wrap.webkitRequestFullscreen;
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (request && exit) {
          await request.call(this._wrap);
        } else {
          this._cssFullscreen = true;
          this._render();
        }
      }
    } catch {
      if (this._isFullscreen()) {
        this._showFullscreenError();
      } else {
        if (this._wall) this._wall._activeCard = null;
        this._cssFullscreen = true;
        this._render();
      }
    }
  }

  _isFullscreen() {
    return this._cssFullscreen
      || (this._wall && this._wall._isFullscreen() && this._wall._activeCard === this)
      || this._nativeFullscreen("fullscreenElement")
      || this._nativeFullscreen("webkitFullscreenElement");
  }

  _nativeFullscreen(property) {
    return this.shadowRoot[property] === this._wrap
      || this._wall?.shadowRoot?.[property] === this
      || document[property] === this;
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
      this._loadOverlayPreferences(true);
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
    this._livePlayer = el("div", { class: "live-player" }, wrap);
    const video = (this._video = el("video", { playsinline: "", autoplay: "" }, this._livePlayer));
    video.muted = true;
    this._detectionOverlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this._detectionOverlay.setAttribute("class", "detections");
    this._detectionOverlay.setAttribute("viewBox", "0 0 640 360");
    this._detectionOverlay.setAttribute("preserveAspectRatio", "none");
    this._detectionOverlay.setAttribute("aria-hidden", "true");
    this._detectionOverlay.setAttribute("hidden", "");
    this._livePlayer.appendChild(this._detectionOverlay);
    this._historyPlayer = el("div", { class: "history-player", hidden: "" }, wrap);
    this._historyVideo = el("video", {
      playsinline: "", controls: "", controlslist: "nofullscreen", hidden: "",
    }, this._historyPlayer);
    const fitDetections = () => this._fitDetectionOverlay();
    this._detectionResizeObserver = new ResizeObserver(fitDetections);
    for (const source of [video, this._historyVideo]) {
      for (const event of ["loadedmetadata", "resize", "emptied"]) {
        source.addEventListener(event, fitDetections);
      }
    }
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
    const historyActions = el("div", { class: "history-actions" }, this._historyPlayer);
    this._historyLiveButton = el("button", { type: "button", class: "history-live" }, historyActions);
    this._historyLiveButton.addEventListener("click", () => this._closeHistory(true));
    this._historyHourButton = el("button", { type: "button" }, historyActions);
    setIcon(this._historyHourButton, ICONS.backHour);
    this._historyHourButton.addEventListener("click", () => this._historyJump(-3600000));
    this._historyTenButton = el("button", { type: "button" }, historyActions);
    setIcon(this._historyTenButton, ICONS.backMinutes);
    this._historyTenButton.addEventListener("click", () => this._historyJump(-600000));
    this._historyBackButton = el("button", { type: "button" }, historyActions);
    setIcon(this._historyBackButton, ICONS.backFifteen);
    this._historyBackButton.addEventListener("click", () => this._historyJump(-15000));
    this._historyForwardButton = el("button", { type: "button" }, historyActions);
    setIcon(this._historyForwardButton, ICONS.forwardFifteen);
    this._historyForwardButton.addEventListener("click", () => this._historyJump(15000));
    this._historyDatePicker = el("details", { class: "history-date" }, historyActions);
    this._historyDateButton = el("summary", {}, this._historyDatePicker);
    setIcon(this._historyDateButton, ICONS.calendar);
    this._historyDatePicker.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._historyDatePicker.open) {
        this._historyDatePicker.open = false;
        this._historyDateButton.focus();
        event.stopPropagation();
      }
    });
    const historyDateContent = el("div", { class: "history-date-content" }, this._historyDatePicker);
    const historyDateLabel = el("label", {}, historyDateContent);
    this._historyDateLabel = el("span", {}, historyDateLabel);
    this._historyDate = el("input", { type: "datetime-local", step: "1" }, historyDateLabel);
    this._historyDate.addEventListener("change", () => {
      const time = new Date(this._historyDate.value).getTime();
      if (Number.isFinite(time)) {
        this._historyDatePicker.open = false;
        this._positionHistoryDate(time);
      }
    });
    this._historyProtectButton = el("button", { type: "button" }, historyActions);
    setIcon(this._historyProtectButton, ICONS.shield);
    this._historyProtectButton.addEventListener("click", () => this._protectHistorySegment());
    this._historyDownload = el("a", { download: "netvue-recording.mp4", hidden: "" }, historyActions);
    setIcon(this._historyDownload, ICONS.download);
    this._historyDownload.addEventListener("click", (event) => this._downloadAnnotated(event));
    this._historySettingsButton = el("button", { type: "button" }, historyActions);
    this._historySettingsButton.addEventListener("click", () => this._openSettings());
    this._historyOverlayButton = el("button", { type: "button", class: "overlay-toggle" }, historyActions);
    this._historyOverlayButton.addEventListener("click", () =>
      this._setOverlayPreference("replay", !this._overlayEnabled("replay")));
    this._historyFullscreenButton = el("button", { type: "button", class: "history-fullscreen" }, historyActions);
    this._historyFullscreenButton.addEventListener("click", () => this._toggleFullscreen());
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
    this._settingsButton = el("button", { type: "button", class: "settings" }, bar);
    this._settingsButton.addEventListener("click", () => this._openSettings());
    this._liveOverlayButton = el("button", { type: "button", class: "overlay-toggle" }, bar);
    this._liveOverlayButton.addEventListener("click", () =>
      this._setOverlayPreference("live", !this._overlayEnabled("live")));
    this._pttButton = el("button", { type: "button", class: "ptt" }, bar);
    this._bindPtt(this._pttButton);
    const volumeControl = el("div", { class: "volume-control" }, bar);
    this._volumeRange = el("input", {
      class: "volume-range", type: "range", min: "0", max: "1", step: "0.01",
    }, volumeControl);
    this._volumeRange.addEventListener("input", () => this._setVolume(Number(this._volumeRange.value)));
    this._muteButton = el("button", { type: "button", class: "mute" }, volumeControl);
    this._muteButton.addEventListener("click", () => this._toggleMute());
    this._fullscreenButton = el("button", { type: "button", class: "fullscreen" }, bar);
    this._fullscreenButton.addEventListener("click", () => this._toggleFullscreen());
    this._historyPanel = el("div", { class: "history-panel", hidden: "" }, wrap);
    this._historyTicks = el("div", { class: "history-ticks", "aria-hidden": "true" }, this._historyPanel);
    this._historyTickLabels = [];
    for (let index = 0; index < 5; index++) {
      const tick = el("span", { class: "history-tick" }, this._historyTicks);
      const label = el("span", { class: "history-tick-label" }, tick);
      this._historyTickLabels.push({
        time: el("span", {}, label),
        date: el("span", { class: "history-tick-date" }, label),
      });
    }
    const historyRangeWrap = el("div", { class: "history-range-wrap" }, this._historyPanel);
    this._historyRail = el("div", { class: "history-rail", "aria-hidden": "true" }, historyRangeWrap);
    this._historyCursorMarker = el("div", { class: "history-cursor", hidden: "", "aria-hidden": "true" }, historyRangeWrap);
    this._historyRange = el("input", { type: "range", step: "1000" }, historyRangeWrap);
    this._historyRange.addEventListener("input", () => {
      this._historyScrubbing = true;
      const time = Number(this._historyRange.value);
      this._historyDate.value = localDateTime(time);
      this._renderHistoryPosition(time);
    });
    this._historyRange.addEventListener("change", () => {
      this._historyScrubbing = false;
      this._historySeek(Number(this._historyRange.value));
    });
    this._historyPosition = el("div", { class: "history-position", hidden: "", "aria-hidden": "true" }, this._historyPanel);
    this._historyPositionLabel = el("span", { class: "history-position-label" }, this._historyPosition);
    this._protectMessage = el("div", { class: "history-message history-protection-message", role: "status" }, this._historyPanel);
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

  async _loadOverlayPreferences(force = false) {
    const entryId = this._entryId();
    const entityId = this._config?.entity;
    if (entityId !== this._overlayEntityId
        || (entryId && this._overlayEntryId && entryId !== this._overlayEntryId)) {
      this._overlayEntityId = entityId;
      this._overlayEntryId = null;
      this._overlayPreferences = { ...OVERLAY_DEFAULTS };
      this._overlayOverrides = {};
      this._overlayGeneration++;
      this._overlayFetchAfter = 0;
      this._overlayFetchPending = false;
    }
    if (!entryId || !this._hass?.callApi || this._overlayFetchPending
        || (!force && Date.now() < this._overlayFetchAfter)) return;
    this._overlayEntryId = entryId;
    const generation = this._overlayGeneration;
    this._overlayFetchPending = true;
    this._overlayFetchAfter = Date.now() + OVERLAY_REFRESH_MS;
    try {
      const saved = await this._hass.callApi(
        "GET", `netvue_local/overlay/${encodeURIComponent(entryId)}`,
      );
      if (generation !== this._overlayGeneration || entryId !== this._entryId()) return;
      if (!saved || typeof saved !== "object" || Array.isArray(saved)) {
        throw new Error("Invalid overlay preferences");
      }
      for (const name of Object.keys(OVERLAY_DEFAULTS)) {
        if (typeof saved?.[name] === "boolean") this._overlayPreferences[name] = saved[name];
      }
      this._render();
    } catch (error) {
      if (generation === this._overlayGeneration) {
        console.warn("Netvue: unable to load overlay preferences", error);
      }
    } finally {
      if (generation === this._overlayGeneration) this._overlayFetchPending = false;
    }
  }

  _overlayEnabled(key) {
    return this._overlayOverrides[key] ?? this._overlayPreferences[key];
  }

  _setOverlayPreference(key, checked) {
    if (!["live", "replay", "export"].includes(key) || typeof checked !== "boolean") return;
    this._overlayOverrides[key] = checked;
    this._render();
  }

  _renderDetections() {
    if (!this._overlayEnabled("live") || !this._config || this._view.state !== "live") {
      this._drawDetections([]);
      return;
    }
    const registry = this._hass?.entities || {};
    const device = registry[this._config.entity]?.device_id;
    const entity = Object.values(registry).find((item) =>
      device && item.device_id === device && item.translation_key === "ai_detection"
      && item.entity_id?.startsWith("binary_sensor."),
    );
    const state = entity && this._hass.states[entity.entity_id];
    const attributes = state?.attributes;
    if (state?.state !== "on" || !attributes.enabled || attributes.state !== "detecting"
        || !Array.isArray(attributes.detections)) {
      this._drawDetections([]);
      return;
    }
    this._drawDetections(attributes.detections.slice(0, 12).map((detection) => ({
      ...detection,
      zoneNames: Array.isArray(detection.zone_ids) ? detection.zone_ids
        .map((id) => attributes.zone_names?.[id])
        .filter((name) => typeof name === "string") : [],
    })));
  }

  _fitDetectionOverlay() {
    const video = this._history ? this._historyVideo : this._video;
    const scale = video.videoWidth > 0 && video.videoHeight > 0
      ? Math.min(video.clientWidth / video.videoWidth, video.clientHeight / video.videoHeight) : 0;
    this._detectionOverlay.style.width = `${scale ? video.videoWidth * scale : 0}px`;
    this._detectionOverlay.style.height = `${scale ? video.videoHeight * scale : 0}px`;
  }

  _drawDetections(detections) {
    const player = this._history ? this._historyPlayer : this._livePlayer;
    if (this._detectionOverlay.parentNode !== player) {
      player.appendChild(this._detectionOverlay);
      this._fitDetectionOverlay();
    }
    const key = JSON.stringify(detections);
    if (key === this._renderedDetections) return;
    this._renderedDetections = key;
    while (this._detectionOverlay.firstChild) {
      this._detectionOverlay.removeChild(this._detectionOverlay.firstChild);
    }
    for (const detection of detections) {
      if (typeof detection?.category !== "string") continue;
      const confidence = detection.confidence;
      const score = typeof confidence === "number" && Number.isFinite(confidence)
        && confidence >= 0 && confidence <= 1 ? ` ${Math.round(confidence * 100)} %` : "";
      const label = Object.prototype.hasOwnProperty.call(DETECTION_LABELS_FR, detection.category)
        ? DETECTION_LABELS_FR[detection.category] : "Objet non identifié";
      const box = detection.box;
      const frameWidth = detection.frame_width;
      const frameHeight = detection.frame_height;
      if (box && Number.isFinite(frameWidth) && Number.isFinite(frameHeight)
          && frameWidth > 0 && frameHeight > 0
          && [box.x1, box.y1, box.x2, box.y2].every(Number.isFinite)) {
        this._detectionOverlay.setAttribute("viewBox", `0 0 ${frameWidth} ${frameHeight}`);
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", String(box.x1));
        rect.setAttribute("y", String(box.y1));
        rect.setAttribute("width", String(Math.max(0, box.x2 - box.x1)));
        rect.setAttribute("height", String(Math.max(0, box.y2 - box.y1)));
        rect.setAttribute("class", "detection-box");
        this._detectionOverlay.appendChild(rect);
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", String(Math.max(0, box.x1)));
        text.setAttribute("y", String(Math.max(0, box.y1 - 4)));
        text.setAttribute("class", "detection-label");
        text.textContent = [`${label}${score}`, ...(detection.zoneNames || [])].join(" · ");
        this._detectionOverlay.appendChild(text);
      }
    }
    const visible = this._detectionOverlay.children.length > 0;
    this._detectionOverlay.toggleAttribute("hidden", !visible);
  }

  _isAdmin() {
    return this._hass?.user?.is_admin === true;
  }

  async _openSettings() {
    const entryId = this._entryId();
    if (!entryId || !this._isAdmin()) return;
    this._releasePtt();
    if (this._isFullscreen()) await this._toggleFullscreen();
    if (entryId !== this._entryId()) return;
    window.history.pushState(null, "", `/netvue-settings?config_entry=${encodeURIComponent(entryId)}`);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }

  async _downloadAnnotated(event) {
    if (!this._overlayEnabled("export")) return;
    event.preventDefault();
    const segment = this._historySegment;
    const entryId = this._entryId();
    if (!segment || !entryId || this._exportPending) return;
    this._exportPending = true;
    try {
      const signed = await this._hass.callWS({
        type: "auth/sign_path",
        path: `/api/netvue_local/recordings/${encodeURIComponent(entryId)}/${segment.id}?overlay=1`,
        expires: 300,
      });
      const link = document.createElement("a");
      link.href = signed.path;
      link.download = "netvue-recording-annotated.mp4";
      link.click();
    } catch {
      if (this._history) this._historyMessage.textContent = this._t("historyExportError");
    } finally {
      this._exportPending = false;
    }
  }

  _historySegmentIsProtected(segment) {
    return this._historyProtected.some((item) =>
      item.id === segment.id || (item.start <= segment.start && item.end >= segment.end));
  }

  _renderHistoryProtection() {
    const segment = this._historySegment;
    const protectedSegment = segment && this._historySegmentIsProtected(segment);
    const label = this._t(protectedSegment ? "protectAlreadySaved" : "protectVideo");
    const button = this._historyProtectButton;
    button.disabled = !segment || !this._entryId() || protectedSegment || Boolean(this._protectPending);
    button.toggleAttribute("data-protected", Boolean(protectedSegment));
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  async _protectHistorySegment() {
    const entryId = this._entryId();
    const segment = this._historySegment;
    if (!this._history || !entryId || !segment || !this._hass?.callApi || this._protectPending
        || this._historySegmentIsProtected(segment)) return;
    this._protectPending = true;
    this._renderHistoryProtection();
    this._protectMessage.textContent = this._t("protectPending");
    try {
      const result = await this._hass.callApi(
        "POST",
        `netvue_local/protected/${encodeURIComponent(entryId)}`,
        {
          start: segment.startTime,
          end: segment.endTime,
          protection: "manual",
        },
      );
      if (!this._history || entryId !== this._entryId()) return;
      const saved = protectedRecordingInterval(result);
      if (!saved) throw new Error("Invalid protected recording");
      this._historyProtected = [...this._historyProtected.filter((item) => item.id !== saved.id), saved];
      this._renderHistoryAvailability();
      this._protectMessage.textContent = this._t("protectDone");
    } catch (error) {
      if (this._history && entryId === this._entryId()) {
        this._protectMessage.textContent = error?.body?.error || this._t("protectError");
      }
    } finally {
      this._protectPending = false;
      this._renderHistoryProtection();
    }
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
    this._historyProtected = [];
    this._historyRange.disabled = true;
    this._historyTicks.hidden = true;
    this._historyCursorMarker.hidden = true;
    this._historyPosition.hidden = true;
    while (this._historyRail.firstChild) this._historyRail.removeChild(this._historyRail.firstChild);
    const generation = ++this._historyGeneration;
    this._releasePtt();
    this._stopSession();
    this._video.hidden = true;
    this._historyPlayer.hidden = false;
    this._historyVideo.hidden = false;
    this._historyPanel.hidden = false;
    this._wrap.setAttribute("data-history", "");
    const now = Date.now();
    this._setHistoryWindow(now);
    this._historyMessage.textContent = this._t("historyLoading");
    this._render();
    try {
      await this._refreshHistory(entryId, generation);
      await this._refreshProtectedHistory(entryId, generation);
      if (!this._history || generation !== this._historyGeneration) return;
      const intervals = mergeIntervals([...this._historySegments, ...this._historyProtected]);
      const windowStart = now - HISTORY_WINDOW_MS;
      const latest = intervals.findLast(({ start, end }) => start <= now && end > windowStart);
      if (latest) {
        await this._historySeek(Math.max(windowStart, Math.min(now, latest.end - 1)));
      } else {
        this._historyCursor = now;
        this._historyRange.value = String(now);
        this._historyDate.value = localDateTime(now);
        this._renderHistoryPosition(now);
        this._historyMessage.textContent = this._t(intervals.length ? "historyGap" : "historyEmpty");
      }
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
    this._historyRange.disabled = false;
    this._historyTicks.hidden = false;
    this._renderHistoryAvailability();
  }

  async _refreshProtectedHistory(entryId, generation) {
    const previous = this._historyProtected;
    try {
      const data = await this._hass.callApi(
        "GET",
        `netvue_local/protected/${encodeURIComponent(entryId)}`,
      );
      if (!this._history || generation !== this._historyGeneration || previous !== this._historyProtected) return;
      this._historyProtected = (Array.isArray(data?.protected) ? data.protected : [])
        .map(protectedRecordingInterval)
        .filter(Boolean);
      this._renderHistoryAvailability();
    } catch {
      return;
    }
  }

  _setHistoryWindow(end) {
    this._historyRange.min = String(end - HISTORY_WINDOW_MS);
    this._historyRange.max = String(end);
    this._renderHistoryTicks();
    this._renderHistoryAvailability();
  }

  async _positionHistoryDate(time) {
    this._historyScrubbing = false;
    this._setHistoryWindow(time + HISTORY_WINDOW_MS / 2);
    await this._historySeek(time);
  }

  _historyTimeLabel(time) {
    const language = this._hass?.locale?.language || this._hass?.language || undefined;
    if (!this._historyTimeFormatter || this._historyTimeLanguage !== language) {
      this._historyTimeLanguage = language;
      this._historyTimeFormatter = new Intl.DateTimeFormat(language, { dateStyle: "short", timeStyle: "medium" });
      this._historyClockFormatter = new Intl.DateTimeFormat(language, { timeStyle: "medium" });
    }
    return this._historyTimeFormatter.format(time);
  }

  _renderHistoryPosition(time) {
    const start = Number(this._historyRange.min);
    const end = Number(this._historyRange.max);
    const position = Math.max(0, Math.min(1, (time - start) / Math.max(1, end - start))) * 100;
    this._historyRange.setAttribute("aria-valuetext", this._historyTimeLabel(time));
    this._historyPanel.style.setProperty("--history-position", `${position}%`);
    this._historyPositionLabel.textContent = this._historyClockFormatter.format(time);
    this._historyCursorMarker.hidden = false;
    this._historyPosition.hidden = false;
  }

  _renderHistoryAvailability() {
    while (this._historyRail.firstChild) this._historyRail.removeChild(this._historyRail.firstChild);
    const start = Number(this._historyRange.min);
    const end = Number(this._historyRange.max);
    const duration = Math.max(1, end - start);
    let span = null;
    const addSpan = () => {
      if (!span) return;
      const node = el("span", { class: "history-available" }, this._historyRail);
      node.style.left = `${(span.start - start) / duration * 100}%`;
      node.style.width = `${(span.end - span.start) / duration * 100}%`;
    };
    for (const segment of mergeIntervals(this._historySegments)) {
      if (segment.end <= start) continue;
      if (segment.start >= end) break;
      const clipped = { start: Math.max(start, segment.start), end: Math.min(end, segment.end) };
      if (span && clipped.start <= span.end) {
        span.end = Math.max(span.end, clipped.end);
      } else {
        addSpan();
        span = clipped;
      }
    }
    addSpan();
    let protectedSpan = null;
    const addProtectedSpan = () => {
      if (!protectedSpan) return;
      const node = el("span", { class: "history-protected" }, this._historyRail);
      node.style.left = `${(protectedSpan.start - start) / duration * 100}%`;
      node.style.width = `${(protectedSpan.end - protectedSpan.start) / duration * 100}%`;
    };
    for (const protectedItem of mergeIntervals(this._historyProtected)) {
      if (protectedItem.end <= start) continue;
      if (protectedItem.start >= end) break;
      const clipped = {
        start: Math.max(start, protectedItem.start),
        end: Math.min(end, protectedItem.end),
      };
      if (protectedSpan && clipped.start <= protectedSpan.end) {
        protectedSpan.end = Math.max(protectedSpan.end, clipped.end);
      } else {
        addProtectedSpan();
        protectedSpan = clipped;
      }
    }
    addProtectedSpan();
  }

  _renderHistoryTicks() {
    const start = Number(this._historyRange.min);
    const end = Number(this._historyRange.max);
    const language = this._hass?.locale?.language || this._hass?.language || undefined;
    const precision = 60000;
    const first = new Date(Math.round(start / precision) * precision);
    const last = new Date(Math.round(end / precision) * precision);
    const showDate = first.toDateString() !== last.toDateString();
    const showYear = first.getFullYear() !== last.getFullYear();
    const timeFormat = new Intl.DateTimeFormat(language, { hour: "2-digit", minute: "2-digit" });
    const dateFormat = new Intl.DateTimeFormat(language, {
      day: "2-digit", month: "2-digit", ...(showYear ? { year: "numeric" } : {}),
    });
    this._historyTickLabels.forEach((label, index) => {
      const time = Math.round((start + (end - start) * index / 4) / precision) * precision;
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
    this._renderHistoryProtection();
    this._historyPendingSeek = null;
    this._historyCursor = time;
    this._historyDetections = [];
    this._historyDownload.hidden = true;
    this._renderHistoryDetections();
    if (!Number.isFinite(Number(this._historyRange.min))
        || !Number.isFinite(Number(this._historyRange.max))
        || time < Number(this._historyRange.min)
        || time > Number(this._historyRange.max)) {
      this._setHistoryWindow(time + HISTORY_WINDOW_MS / 2);
    }
    this._historyRange.value = String(time);
    this._renderHistoryPosition(Number(this._historyRange.value));
    this._historyDate.value = localDateTime(time);
    const protectedSegment = intervalAt(this._historyProtected, time);
    let segment = protectedSegment
      ? { id: protectedSegment.id, start: protectedSegment.start, end: protectedSegment.end }
      : segmentAt(this._historySegments, time);
    if (!segment) {
      try {
        await this._refreshHistory(entryId, generation);
        await this._refreshProtectedHistory(entryId, generation);
      } catch {
        if (this._history && generation === this._historyGeneration) {
          this._historyMessage.textContent = this._t("historyLoadError");
        }
        return;
      }
      if (!this._history || generation !== this._historyGeneration) return;
      const refreshedProtected = intervalAt(this._historyProtected, time);
      segment = refreshedProtected
        ? { id: refreshedProtected.id, start: refreshedProtected.start, end: refreshedProtected.end }
        : segmentAt(this._historySegments, time);
    }
    if (!segment) {
      this._historySegment = null;
      this._historyVideo.removeAttribute("src");
      this._historyVideo.load();
      this._historyDownload.hidden = true;
      this._historyMessage.textContent = this._t("historyGap");
      return;
    }
    this._historyMessage.textContent = this._t("historyLoading");
    try {
      const protectedSelection = intervalAt(this._historyProtected, time);
      const isProtected = Boolean(protectedSelection && protectedSelection.id === segment.id);
      const duration = Math.floor(segment.end - segment.start) / 1000;
      const base = `${encodeURIComponent(entryId)}/${segment.id}`;
      const path = isProtected
        ? `/api/netvue_local/protected/${base}`
        : `/api/netvue_local/playback/${base}?duration=${duration}`;
      const media = await this._hass.callWS({
        type: "auth/sign_path",
        path,
        expires: 86400,
      });
      if (!this._history || generation !== this._historyGeneration) return;
      this._historySegment = segment;
      this._renderHistoryProtection();
      this._historyPendingSeek = { generation, offset: (time - segment.start) / 1000 };
      this._historyVideo.src = media.path;
      this._historyVideo.muted = this._muted;
      this._historyVideo.volume = this._volume;
      this._historyDownload.href = media.path;
      this._historyDownload.hidden = false;
      this._historyDetections = [];
      if (!isProtected && this._hass?.callApi) {
        this._hass.callApi("GET", `netvue_local/recordings/${encodeURIComponent(entryId)}/${segment.id}/metadata`)
          .then((metadata) => {
            if (!this._history || generation !== this._historyGeneration) return;
            this._historyDetections = Array.isArray(metadata?.events)
              ? metadata.events.filter((sample) => Number.isFinite(sample?.timestamp))
                .sort((a, b) => a.timestamp - b.timestamp)
              : [];
            this._renderHistoryDetections();
          })
          .catch(() => {
            if (!this._history || generation !== this._historyGeneration) return;
            this._historyDetections = [];
            this._renderHistoryDetections();
          });
      }
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
    this._renderHistoryDetections();
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
      this._setHistoryWindow(this._historyCursor + HISTORY_WINDOW_MS / 2);
    }
    this._historyRange.value = String(this._historyCursor);
    this._renderHistoryPosition(Number(this._historyRange.value));
    this._historyDate.value = localDateTime(this._historyCursor);
    this._renderHistoryDetections();
  }

  _renderHistoryDetections() {
    if (!this._overlayEnabled("replay") || !this._historySegment || this._historyPendingSeek
        || !Number.isFinite(this._historyVideo.currentTime)) {
      this._drawDetections([]);
      return;
    }
    const absoluteSeconds = (this._historySegment.start / 1000)
      + this._historyVideo.currentTime;
    const event = this._historyDetections.findLast((sample) => sample.timestamp <= absoluteSeconds);
    const boxes = Array.isArray(event?.boxes)
      && absoluteSeconds - event.timestamp < HISTORY_SAMPLE_MAX_GAP_SECONDS ? event.boxes : [];
    this._drawDetections(boxes.slice(0, 32).map((box) => ({
      box, category: box.category ?? event.category, confidence: box.confidence ?? event.confidence,
      frame_width: event.frame_width, frame_height: event.frame_height,
    })));
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
    this._protectMessage.textContent = "";
    this._historyPendingSeek = null;
    this._historyScrubbing = false;
    this._historyFollowPending = false;
    this._clearTimer("_historyFollowTimer");
    this._historyVideo.pause();
    this._historyVideo.removeAttribute("src");
    this._historyVideo.load();
    this._historyVideo.hidden = true;
    this._historyPlayer.hidden = true;
    this._historyDatePicker.open = false;
    this._historyPanel.hidden = true;
    this._historyDownload.hidden = true;
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
    this._muteButton.title = this._muted ? t("unmute") : t("mute");
    this._volumeRange.value = String(this._muted ? 0 : this._volume);
    this._volumeRange.setAttribute("aria-label", t("volume"));
    const fullscreen = this._isFullscreen();
    this.toggleAttribute("css-fullscreen", this._cssFullscreen);
    this._wrap.toggleAttribute("data-fullscreen", Boolean(fullscreen));
    if (this._history) this._renderHistoryDetections();
    else this._renderDetections();
    const navigation = fullscreen && this._wall && this._wall._isFullscreen()
      && this._wall._cards.length > 1;
    this._previousButton.hidden = !navigation;
    this._nextButton.hidden = !navigation;
    this._previousButton.setAttribute("aria-label", t("previousCamera"));
    this._nextButton.setAttribute("aria-label", t("nextCamera"));
    this._previousButton.title = t("previousCamera");
    this._nextButton.title = t("nextCamera");
    const fullscreenLabel = fullscreen ? t("exitFullscreen") : t("fullscreen");
    for (const button of [this._fullscreenButton, this._historyFullscreenButton]) {
      setIcon(button, fullscreen ? ICONS.exitFullscreen : ICONS.fullscreen);
      button.disabled = false;
      button.setAttribute("aria-label", fullscreenLabel);
      button.setAttribute("aria-pressed", String(fullscreen));
      button.title = button.getAttribute("aria-label");
    }
    this._renderHistoryProtection();
    setIcon(this._pttButton, ICONS.mic);
    const available = state === "live" && ptt.ready;
    this._pttButton.disabled = !available && !ptt.pressed;
    this._pttButton.setAttribute("aria-pressed", String(ptt.pressed));
    this._pttButton.setAttribute("aria-label", available || ptt.pressed ? t("talk") : t("talkNotReady"));
    this._pttButton.title = this._pttButton.getAttribute("aria-label");
    const recording = this._recordingState();
    const manual = Boolean(recording?.attributes?.manual);
    const continuous = Boolean(recording?.attributes?.continuous);
    this._recordingIndicator.hidden = this._history || !recording
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
    for (const button of [this._settingsButton, this._historySettingsButton]) {
      setIcon(button, ICONS.settings);
      button.disabled = !this._entryId();
      button.hidden = !this._isAdmin();
      button.setAttribute("aria-label", t("cameraSettings"));
      button.title = t("cameraSettings");
    }
    for (const [button, key, label] of [
      [this._liveOverlayButton, "live", "overlayLive"],
      [this._historyOverlayButton, "replay", "overlayReplay"],
    ]) {
      setIcon(button, ICONS.boxes);
      button.setAttribute("aria-label", t(label));
      button.setAttribute("aria-pressed", String(this._overlayEnabled(key)));
      button.title = t(label);
    }
    this._historyLiveButton.textContent = t("historyLiveShort");
    for (const [button, text] of [
      [this._historyLiveButton, t("historyLive")],
      [this._historyHourButton, t("historyBackHour")],
      [this._historyTenButton, t("historyBackTenMinutes")],
      [this._historyBackButton, t("historyBackFifteenSeconds")],
      [this._historyForwardButton, t("historyForwardFifteenSeconds")],
      [this._historyDateButton, t("historyDate")],
      [this._historyDownload, t("historyDownload")],
    ]) {
      button.setAttribute("aria-label", text);
      button.title = text;
    }
    this._historyDateLabel.textContent = t("historyDate");
    this._historyDate.setAttribute("aria-label", t("historyDate"));
    this._historyRange.setAttribute("aria-label", t("historyTimeline"));
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
