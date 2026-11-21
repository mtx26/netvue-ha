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
    if (this._zoneBuilt) return;
    this._zoneBuilt = true;
    this._zones = {};
    this._drawing = null;
    this._zoneName = "entrance";
    const root = this.shadowRoot;
    const style = document.createElement("style");
    style.textContent = `
      .zone-editor { margin-top: 16px; padding: 12px; border: 1px solid var(--divider-color); border-radius: 8px; }
      .zone-editor h3 { margin: 0 0 10px; }
      .zone-controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
      .zone-stage { position: relative; margin-top:12px; min-height:180px; background:#111; overflow:hidden; }
      .zone-stage img { display:block; width:100%; max-height:480px; object-fit:contain; user-select:none; }
      .zone-stage canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:crosshair; }
      .zone-controls input[type="range"] { width:120px; }
      .zone-controls button { min-height:36px; }
    `;
    root.appendChild(style);
    this._zoneEditor = document.createElement("section");
    this._zoneEditor.className = "zone-editor";
    this._zoneEditor.innerHTML = `
      <h3>Zones de détection</h3>
      <div class="zone-controls">
        <label><input type="checkbox" data-ai-enabled checked> Détection activée</label>
        <label>Zone
          <select data-zone-name>
            <option value="entrance">Entrée</option>
            <option value="doorstep">Seuil</option>
            <option value="garden">Jardin</option>
            <option value="driveway">Allée</option>
          </select>
        </label>
        <label>Confiance <input type="range" data-confidence min="10" max="95" value="45"><output data-confidence-output>45%</output></label>
        <label>FPS <input type="range" data-fps min="1" max="10" value="3"><output data-fps-output>3</output></label>
        <label>Confirmation <input type="range" data-confirmation min="1" max="10" value="3"><output data-confirmation-output>3</output></label>
        <button type="button" data-clear-zone>Effacer la zone</button>
        <button type="button" data-save-zones>Enregistrer</button>
      </div>
      <div class="zone-stage">
        <img data-zone-preview alt="">
        <canvas data-zone-canvas></canvas>
      </div>
      <p data-zone-message></p>
    `;
    root.appendChild(this._zoneEditor);
    this._canvas = this._zoneEditor.querySelector("[data-zone-canvas]");
    this._preview = this._zoneEditor.querySelector("[data-zone-preview]");
    this._canvas.addEventListener("pointerdown", (event) => this._startZone(event));
    this._canvas.addEventListener("pointermove", (event) => this._moveZone(event));
    this._canvas.addEventListener("pointerup", (event) => this._finishZone(event));
    this._zoneEditor.querySelector("[data-zone-name]").addEventListener("change", (event) => {
      this._zoneName = event.target.value;
      this._drawZones();
    });
    this._zoneEditor.querySelector("[data-clear-zone]").addEventListener("click", () => {
      delete this._zones[this._zoneName];
      this._drawZones();
    });
    this._zoneEditor.querySelector("[data-save-zones]").addEventListener("click", () => this._save());
    for (const [selector, output] of [
      ["[data-confidence]", "[data-confidence-output]"],
      ["[data-fps]", "[data-fps-output]"],
      ["[data-confirmation]", "[data-confirmation-output]"],
    ]) {
      const input = this._zoneEditor.querySelector(selector);
      const label = this._zoneEditor.querySelector(output);
      input.addEventListener("input", () => {
        label.value = input === this._zoneEditor.querySelector("[data-confidence]")
          ? `${input.value}%`
          : input.value;
      });
    }
    this._preview.addEventListener("load", () => this._resizeCanvas());
    window.addEventListener("resize", () => this._resizeCanvas());
  }

  set hass(hass) {
    super.hass = hass;
    this._loadZoneConfig();
  }

  get hass() {
    return this._hass;
  }

  setConfig(config) {
    super.setConfig(config);
    this._loadZoneConfig();
  }

  _entryId() {
    const entity = this._config.entity;
    if (!entity || !this._hass?.entities?.[entity]) return null;
    return this._hass.entities[entity].config_entry_id || null;
  }

  async _loadZoneConfig() {
    if (!this._zoneBuilt || !this._hass) return;
    const entryId = this._entryId();
    if (!entryId) return;
    try {
      const response = await fetch(`/api/netvue_local/detection/${encodeURIComponent(entryId)}`);
      if (!response.ok) return;
      const config = await response.json();
      this._zones = config.ai_zones || {};
      this._zoneEditor.querySelector("[data-ai-enabled]").checked = config.ai_enabled;
      this._zoneEditor.querySelector("[data-confidence]").value =
        Math.round(config.ai_confidence * 100);
      this._zoneEditor.querySelector("[data-confidence-output]").value =
        `${Math.round(config.ai_confidence * 100)}%`;
      this._zoneEditor.querySelector("[data-fps]").value = config.ai_fps;
      this._zoneEditor.querySelector("[data-fps-output]").value = config.ai_fps;
      this._zoneEditor.querySelector("[data-confirmation]").value =
        config.ai_confirmation_frames;
      this._zoneEditor.querySelector("[data-confirmation-output]").value =
        config.ai_confirmation_frames;
      this._preview.src = `/api/camera_proxy/${this._config.entity}?time=${Date.now()}`;
      this._drawZones();
    } catch (_error) {
      this._zoneEditor.querySelector("[data-zone-message]").textContent =
        "Impossible de charger la configuration.";
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
      this._zones[this._zoneName] = rect;
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
    if (!entryId) return;
    const confidence = Number(this._zoneEditor.querySelector("[data-confidence]").value) / 100;
    const fps = Number(this._zoneEditor.querySelector("[data-fps]").value);
    const confirmation = Number(this._zoneEditor.querySelector("[data-confirmation]").value);
    const enabled = this._zoneEditor.querySelector("[data-ai-enabled]").checked;
    const message = this._zoneEditor.querySelector("[data-zone-message]");
    try {
      const response = await fetch(`/api/netvue_local/detection/${encodeURIComponent(entryId)}`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ai_enabled: enabled,
          ai_confidence: confidence,
          ai_fps: fps,
          ai_confirmation_frames: confirmation,
          ai_zones: this._zones,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      message.textContent = "Configuration enregistrée.";
    } catch (_error) {
      message.textContent = "Échec de l’enregistrement.";
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
