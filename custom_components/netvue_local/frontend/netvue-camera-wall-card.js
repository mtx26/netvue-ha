const CAMERA_RE = /^camera\.[a-z0-9_]+$/;
const STYLE = `
:host { display: block; }
.wall { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 16px; }
`;

export class NetvueCameraWallCard extends HTMLElement {
  static getStubConfig(hass, entities) {
    const cameras = (entities || Object.keys((hass && hass.states) || {}))
      .filter((entity) => entity.startsWith("camera."));
    const netvue = cameras.filter((entity) => hass && hass.states[entity]?.attributes?.brand === "Netvue");
    const selected = (netvue.length ? netvue : cameras).slice(0, 2);
    return {
      type: "custom:netvue-camera-wall-card",
      entities: selected.length ? selected : ["camera.netvue"],
    };
  }

  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._cards = [];
    this._built = false;
  }

  setConfig(config) {
    if (!config || !Array.isArray(config.entities) || config.entities.length === 0
      || config.entities.some((entity) => typeof entity !== "string" || !CAMERA_RE.test(entity))
      || new Set(config.entities).size !== config.entities.length) {
      throw new Error("entities: one or more distinct camera entities are required");
    }
    this._config = { entities: [...config.entities] };
    if (this._built) {
      this._render();
    }
  }

  set hass(hass) {
    this._hass = hass;
    for (const card of this._cards) {
      card.hass = hass;
    }
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return this._config ? Math.ceil(this._config.entities.length / 2) * 4 : 4;
  }

  connectedCallback() {
    this._build();
  }

  _build() {
    if (this._built) {
      return;
    }
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);
    const card = document.createElement("ha-card");
    root.appendChild(card);
    this._grid = document.createElement("div");
    this._grid.className = "wall";
    card.appendChild(this._grid);
    this._render();
  }

  _render() {
    if (!this._built || !this._config) {
      return;
    }
    while (this._grid.firstChild) {
      this._grid.removeChild(this._grid.firstChild);
    }
    this._cards = this._config.entities.map((entity) => {
      const card = document.createElement("netvue-camera-card");
      card.setConfig({ entity });
      if (this._hass) {
        card.hass = this._hass;
      }
      this._grid.appendChild(card);
      return card;
    });
  }
}

if (!customElements.get("netvue-camera-wall-card")) {
  customElements.define("netvue-camera-wall-card", NetvueCameraWallCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "netvue-camera-wall-card",
  name: "Netvue Camera Wall",
  description: "Responsive multi-camera grid using the Netvue camera card",
});
