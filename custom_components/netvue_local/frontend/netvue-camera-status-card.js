import { translator } from "./i18n.js";

const CAMERA_RE = /^camera\.[a-z0-9_]+$/;
const BINARY_SENSOR_RE = /^binary_sensor\.[a-z0-9_]+$/;
const SENSOR_RE = /^sensor\.[a-z0-9_]+$/;
const STYLE = `
:host { display: block; }
ha-card { padding: 16px; }
.heading { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.heading h2 { font-size: 1.2em; margin: 0; }
.empty { color: var(--secondary-text-color); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: 12px; }
.camera { border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px; }
.camera h3 { font-size: 1em; margin: 0 0 10px; }
.row { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; }
.label { color: var(--secondary-text-color); }
.ok { color: var(--success-color, #2e7d32); }
.problem { color: var(--error-color, #db4437); }
.unknown { color: var(--secondary-text-color); }
`;

function isEntity(entity, pattern, required) {
  return (entity === undefined && !required)
    || (typeof entity === "string" && pattern.test(entity));
}

function create(tag, className, parent) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (parent) {
    parent.appendChild(node);
  }
  return node;
}

function discoverCameras(hass, entityIds) {
  if (!hass) {
    return [];
  }
  const states = hass.states || {};
  const registry = hass.entities || {};
  const cameras = (entityIds || Object.keys(states))
    .filter((entityId) => entityId.startsWith("camera."));
  return cameras.flatMap((camera) => {
    const deviceId = registry[camera]?.device_id;
    if (!deviceId) {
      return [];
    }
    const deviceEntities = Object.values(registry).filter((entity) => entity.device_id === deviceId);
    const unavailable = deviceEntities.find((entity) => entity.translation_key === "camera_unavailable");
    if (!unavailable) {
      return [];
    }
    return [{
      camera,
      unavailable: unavailable.entity_id,
      session: deviceEntities.find((entity) => entity.translation_key === "session")?.entity_id,
      diagnostic: deviceEntities.find((entity) => entity.translation_key === "diagnostic")?.entity_id,
    }];
  });
}

export class NetvueCameraStatusCard extends HTMLElement {
  static getStubConfig(hass, entities) {
    return {
      type: "custom:netvue-camera-status-card",
      cameras: discoverCameras(hass, entities),
    };
  }

  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._autoDiscover = false;
    this._built = false;
  }

  setConfig(config) {
    if (!config || (config.cameras !== undefined && !Array.isArray(config.cameras))) {
      throw new Error("cameras must be a list of camera status entries");
    }
    this._autoDiscover = !config.cameras || config.cameras.length === 0;
    const cameras = (config.cameras || []).map((camera) => {
      if (!camera || typeof camera !== "object"
        || !isEntity(camera.camera, CAMERA_RE, true)
        || !isEntity(camera.unavailable, BINARY_SENSOR_RE, true)
        || !isEntity(camera.session, SENSOR_RE, false)
        || !isEntity(camera.diagnostic, SENSOR_RE, false)
        || (camera.name !== undefined && typeof camera.name !== "string")) {
        throw new Error("each camera requires a camera entity and unavailable binary sensor");
      }
      return { ...camera };
    });
    if (new Set(cameras.map((camera) => camera.camera)).size !== cameras.length) {
      throw new Error("cameras must not contain duplicate camera entities");
    }
    this._config = {
      cameras: this._autoDiscover ? discoverCameras(this._hass) : cameras,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._autoDiscover && this._config) {
      this._config.cameras = discoverCameras(hass);
    }
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return this._config ? Math.max(2, Math.ceil(this._config.cameras.length / 2) * 2) : 2;
  }

  connectedCallback() {
    if (this._built) {
      return;
    }
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    const style = create("style", "", root);
    style.textContent = STYLE;
    this._card = create("ha-card", "", root);
    const heading = create("div", "heading", this._card);
    this._title = create("h2", "", heading);
    this._count = create("span", "", heading);
    this._empty = create("p", "empty", this._card);
    this._grid = create("div", "grid", this._card);
    this._render();
  }

  _state(entityId) {
    return this._hass && entityId ? this._hass.states[entityId] : undefined;
  }

  _stateLabel(state, t) {
    if (!state || state.state === "unknown" || state.state === "unavailable") {
      return t("statusUnknown");
    }
    return typeof this._hass.formatEntityState === "function"
      ? this._hass.formatEntityState(state)
      : state.state;
  }

  _render() {
    if (!this._built || !this._config) {
      return;
    }
    const language = this._hass && (this._hass.locale?.language || this._hass.language);
    const t = translator(language);
    this._title.textContent = t("statusTitle");
    const hasCameras = this._config.cameras.length > 0;
    this._empty.hidden = hasCameras;
    this._empty.textContent = hasCameras ? "" : t("statusEmpty");
    this._grid.hidden = !hasCameras;
    let online = 0;
    while (this._grid.firstChild) {
      this._grid.removeChild(this._grid.firstChild);
    }
    for (const camera of this._config.cameras) {
      const cameraState = this._state(camera.camera);
      const availability = this._state(camera.unavailable);
      const unavailable = availability && availability.state;
      const status = unavailable === "off" ? "ok" : unavailable === "on" ? "problem" : "unknown";
      if (status === "ok") {
        online++;
      }
      const tile = create("section", "camera", this._grid);
      const heading = create("h3", "", tile);
      heading.textContent = camera.name
        || cameraState?.attributes?.friendly_name
        || camera.camera;
      this._addRow(tile, t("statusAvailability"), t(status === "ok"
        ? "statusOnline"
        : status === "problem" ? "statusOffline" : "statusUnknown"), status);
      if (camera.session) {
        const sessionState = this._state(camera.session);
        const sessionClass = !sessionState || sessionState.state === "unavailable"
          ? "unknown"
          : sessionState.state === "supervising"
            ? "ok"
            : sessionState.state === "failed" ? "problem" : "unknown";
        this._addRow(
          tile,
          t("statusSession"),
          this._stateLabel(sessionState, t),
          sessionClass,
        );
      }
      if (camera.diagnostic) {
        const diagnosticState = this._state(camera.diagnostic);
        const diagnosticClass = !diagnosticState || diagnosticState.state === "unavailable"
          ? "unknown"
          : diagnosticState.state === "healthy" ? "ok" : "problem";
        this._addRow(
          tile,
          t("statusDiagnostic"),
          this._stateLabel(diagnosticState, t),
          diagnosticClass,
        );
      }
    }
    this._count.textContent = hasCameras
      ? `${online}/${this._config.cameras.length} ${t("statusAvailableCount")}`
      : "";
  }

  _addRow(parent, label, value, stateClass = "") {
    const row = create("div", "row", parent);
    create("span", "label", row).textContent = label;
    create("span", stateClass, row).textContent = value;
  }
}

if (!customElements.get("netvue-camera-status-card")) {
  customElements.define("netvue-camera-status-card", NetvueCameraStatusCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "netvue-camera-status-card",
  name: "Netvue Camera Status",
  description: "Availability, session and diagnostic summary for Netvue cameras",
});
