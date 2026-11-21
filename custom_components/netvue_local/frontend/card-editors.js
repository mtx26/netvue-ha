import { translator } from "./i18n.js";

const CAMERA_SELECTOR = {
  entity: {
    filter: { domain: "camera", integration: "netvue_local" },
  },
};

class NetvueCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._built = false;
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (this._built) {
      return;
    }
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    this._form = document.createElement("ha-form");
    this._form.addEventListener("value-changed", (event) => this._valueChanged(event));
    root.appendChild(this._form);
    this._render();
  }

  _formData(config) {
    return config;
  }

  _configFromValue(value) {
    return { ...this._config, ...value };
  }

  _render() {
    if (!this._built) {
      return;
    }
    const t = translator(this._hass && (this._hass.locale?.language || this._hass.language));
    this._form.hass = this._hass;
    this._form.schema = this.constructor.schema;
    this._form.data = this._formData(this._config);
    this._form.computeLabel = (schema) => t(schema.label);
    this._form.computeHelper = (schema) => schema.helper ? t(schema.helper) : "";
  }

  _valueChanged(event) {
    this._config = this._configFromValue(event.detail.value);
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: this._config },
      bubbles: true,
      composed: true,
    }));
  }
}

class NetvueCameraCardEditor extends NetvueCardEditor {
  static schema = [
    {
      name: "entity",
      label: "editorCamera",
      helper: "editorCameraHelp",
      required: true,
      selector: CAMERA_SELECTOR,
    },
    {
      name: "name",
      label: "editorName",
      selector: { text: {} },
    },
  ];

  connectedCallback() {
    super.connectedCallback();
    if (!this.hasAttribute("zones-only")) return;
    this._form.hidden = true;
    if (this._zoneBuilt) {
      window.addEventListener("resize", this._onZoneResize);
      this._loadZoneConfig();
      return;
    }
    this._zoneBuilt = true;
    this._zones = {};
    this._drawing = null;
    this._zoneName = "entrance";
    const root = this.shadowRoot;
    const style = document.createElement("style");
    style.textContent = `
      .zone-editor { margin-top:12px; }
      ha-form[hidden] { display:none; }
      .zone-controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
      .zone-stage { position:relative; margin:12px auto 0; width:fit-content; max-width:100%; background:#111; overflow:hidden; }
      .zone-stage img { display:block; width:auto; max-width:100%; max-height:72dvh; user-select:none; }
      .zone-stage canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:crosshair; }
      .zone-controls button { min-height:40px; padding:8px 16px; border:1px solid var(--divider-color, #666);
        border-radius:20px; background:transparent; color:var(--primary-text-color, #fff);
        font:500 14px var(--paper-font-body1_-_font-family, Roboto, sans-serif); cursor:pointer; }
      .zone-controls button:not([data-save-zones]):hover { background:var(--secondary-background-color, #333); }
      .zone-controls [data-save-zones]:hover { filter:brightness(0.9); }
      .zone-controls button:focus-visible { outline:2px solid var(--primary-color, #03a9f4); outline-offset:2px; }
      .zone-controls [data-save-zones] { background:var(--primary-color, #03a9f4);
        border-color:var(--primary-color, #03a9f4); color:var(--text-primary-color, #fff); }
    `;
    root.appendChild(style);
    this._zoneEditor = document.createElement("section");
    this._zoneEditor.className = "zone-editor";
    this._zoneEditor.innerHTML = `
      <div class="zone-controls">
        <button type="button" data-full-zone></button>
        <button type="button" data-clear-zone></button>
        <button type="button" data-save-zones></button>
      </div>
      <div class="zone-stage">
        <img data-zone-preview alt="">
        <canvas data-zone-canvas></canvas>
      </div>
      <p data-zone-message></p>
    `;
    root.appendChild(this._zoneEditor);
    const t = translator(this._hass?.locale?.language || this._hass?.language);
    this._zoneEditor.querySelector("[data-full-zone]").textContent = t("zoneFull");
    this._zoneEditor.querySelector("[data-clear-zone]").textContent = t("zoneClear");
    this._zoneEditor.querySelector("[data-save-zones]").textContent = t("zoneSave");
    this._canvas = this._zoneEditor.querySelector("[data-zone-canvas]");
    this._preview = this._zoneEditor.querySelector("[data-zone-preview]");
    this._canvas.addEventListener("pointerdown", (event) => this._startZone(event));
    this._canvas.addEventListener("pointermove", (event) => this._moveZone(event));
    this._canvas.addEventListener("pointerup", (event) => this._finishZone(event));
    this._zoneEditor.querySelector("[data-clear-zone]").addEventListener("click", () => {
      delete this._zones[this._zoneName];
      this._drawZones();
    });
    this._zoneEditor.querySelector("[data-full-zone]").addEventListener("click", () => {
      this._zones = { [this._zoneName]: [0, 0, 1, 1] };
      this._drawZones();
    });
    this._zoneEditor.querySelector("[data-save-zones]").addEventListener("click", () => this._save());
    this._preview.addEventListener("load", () => this._resizeCanvas());
    this._preview.addEventListener("error", () => {
      this._zoneEditor.querySelector("[data-zone-message]").textContent =
        this._translateZone("zoneImageError");
    });
    this._onZoneResize = () => this._resizeCanvas();
    window.addEventListener("resize", this._onZoneResize);
    this._loadZoneConfig();
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._onZoneResize);
    this._loadedEntryId = null;
    this._loadGeneration = (this._loadGeneration || 0) + 1;
  }

  set hass(hass) {
    super.hass = hass;
    if (this._loadedEntryId !== this._entryId()) this._loadZoneConfig();
  }

  get hass() {
    return this._hass;
  }

  setConfig(config) {
    const previous = this._config.entity;
    super.setConfig(config);
    if (previous !== config.entity) this._loadZoneConfig();
  }

  _entryId() {
    const entity = this._config.entity;
    if (!entity) return null;
    return this._hass?.states?.[entity]?.attributes?.netvue_entry_id
      || this._hass?.entities?.[entity]?.config_entry_id || null;
  }

  _translateZone(key) {
    return translator(this._hass?.locale?.language || this._hass?.language)(key);
  }

  async _loadZoneConfig() {
    if (!this._zoneBuilt || !this._hass?.callApi) return;
    const entryId = this._entryId();
    if (!entryId) return;
    const generation = this._loadGeneration = (this._loadGeneration || 0) + 1;
    this._loadedEntryId = entryId;
    this._aiConfig = null;
    const message = this._zoneEditor.querySelector("[data-zone-message]");
    message.textContent = "";
    try {
      const config = await this._hass.callApi("GET", `netvue_local/detection/${encodeURIComponent(entryId)}`);
      if (generation !== this._loadGeneration) return;
      this._aiConfig = config;
      const existing = Object.entries(config.ai_zones)[0];
      this._zoneName = existing?.[0] || "entrance";
      this._zones = { [this._zoneName]: existing?.[1] || [0, 0, 1, 1] };
      this._preview.src = this._hass.states?.[this._config.entity]?.attributes?.entity_picture
        || `/api/camera_proxy/${this._config.entity}`;
      this._drawZones();
    } catch (_error) {
      if (generation !== this._loadGeneration) return;
      this._loadedEntryId = null;
      message.textContent = this._translateZone("zoneLoadError");
    }
  }

  _resizeCanvas() {
    if (!this._canvas || !this._preview) return;
    const rect = this._preview.getBoundingClientRect();
    this._canvas.width = rect.width;
    this._canvas.height = rect.height;
    this._drawZones();
  }

  _point(event) {
    const rect = this._canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  }

  _startZone(event) {
    if (!this._canvas.width || !this._canvas.height) return;
    this._canvas.setPointerCapture?.(event.pointerId);
    this._drawing = this._point(event);
  }

  _moveZone(event) {
    if (!this._drawing) return;
    const end = this._point(event);
    this._drawZones({
      [this._zoneName]: [
        Math.min(this._drawing.x, end.x),
        Math.min(this._drawing.y, end.y),
        Math.max(this._drawing.x, end.x),
        Math.max(this._drawing.y, end.y),
      ],
    });
  }

  _finishZone(event) {
    if (!this._drawing) return;
    const end = this._point(event);
    const rect = [
      Math.min(this._drawing.x, end.x),
      Math.min(this._drawing.y, end.y),
      Math.max(this._drawing.x, end.x),
      Math.max(this._drawing.y, end.y),
    ];
    this._drawing = null;
    if (rect[2] - rect[0] > 0.02 && rect[3] - rect[1] > 0.02) {
      this._zones = { [this._zoneName]: rect };
    }
    this._drawZones();
  }

  _drawZones(extra = {}) {
    if (!this._canvas) return;
    const ctx = this._canvas.getContext("2d");
    const width = this._canvas.width;
    const height = this._canvas.height;
    ctx.clearRect(0, 0, width, height);
    const colors = {
      entrance: "#03a9f4", doorstep: "#ff9800", garden: "#4caf50", driveway: "#ab47bc",
    };
    for (const [name, rect] of Object.entries({ ...this._zones, ...extra })) {
      const [x1, y1, x2, y2] = rect;
      ctx.strokeStyle = colors[name] || "#fff";
      ctx.fillStyle = `${ctx.strokeStyle}33`;
      ctx.lineWidth = 3;
      ctx.fillRect(x1 * width, y1 * height, (x2 - x1) * width, (y2 - y1) * height);
      ctx.strokeRect(x1 * width, y1 * height, (x2 - x1) * width, (y2 - y1) * height);
    }
  }

  async _save() {
    const entryId = this._entryId();
    const message = this._zoneEditor.querySelector("[data-zone-message]");
    if (!entryId || !this._aiConfig || entryId !== this._loadedEntryId) {
      message.textContent = this._translateZone("zoneLoadError");
      return;
    }
    const generation = this._loadGeneration;
    try {
      const path = `netvue_local/detection/${encodeURIComponent(entryId)}`;
      const config = await this._hass.callApi("GET", path);
      if (generation !== this._loadGeneration || entryId !== this._entryId()) return;
      this._aiConfig = await this._hass.callApi("PUT", path, {
          ai_enabled: config.ai_enabled,
          ai_confidence: config.ai_confidence,
          ai_fps: config.ai_fps,
          ai_confirmation_frames: config.ai_confirmation_frames,
          ai_zones: this._zones,
      });
      message.textContent = this._translateZone("zoneSaved");
    } catch (_error) {
      message.textContent = this._translateZone("zoneSaveError");
    }
  }
}

class NetvueCameraWallCardEditor extends NetvueCardEditor {
  static schema = [
    {
      name: "entities",
      label: "editorCameras",
      helper: "editorCamerasHelp",
      required: true,
      selector: { entity: { ...CAMERA_SELECTOR.entity, multiple: true } },
    },
  ];
}

class NetvueCameraStatusCardEditor extends NetvueCardEditor {
  static schema = [
    {
      name: "entities",
      label: "editorStatusCameras",
      helper: "editorStatusCamerasHelp",
      required: true,
      selector: { entity: { ...CAMERA_SELECTOR.entity, multiple: true } },
    },
  ];

  _formData(config) {
    return {
      ...config,
      entities: config.entities || config.cameras?.map((camera) => camera.camera) || [],
    };
  }

  _configFromValue(value) {
    const config = { ...this._config, ...value };
    delete config.cameras;
    return config;
  }
}

if (!customElements.get("netvue-camera-card-editor")) {
  customElements.define("netvue-camera-card-editor", NetvueCameraCardEditor);
}
if (!customElements.get("netvue-camera-wall-card-editor")) {
  customElements.define("netvue-camera-wall-card-editor", NetvueCameraWallCardEditor);
}
if (!customElements.get("netvue-camera-status-card-editor")) {
  customElements.define("netvue-camera-status-card-editor", NetvueCameraStatusCardEditor);
}
