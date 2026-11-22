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
