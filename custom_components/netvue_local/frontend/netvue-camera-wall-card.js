const CAMERA_RE = /^camera\.[a-z0-9_]+$/;
const STYLE = `
:host { display: block; }
.wall { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 16px; }
.wall:fullscreen { display: block; width: 100vw; height: 100vh; height: 100dvh; background: #000; }
.wall:fullscreen > netvue-camera-card:not([wall-active]) { display: none; }
`;

export class NetvueCameraWallCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("netvue-camera-wall-card-editor");
  }

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
    this._activeCard = null;
    this._onFullscreenChange = () => this._updateFullscreen();
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
    document.addEventListener("fullscreenchange", this._onFullscreenChange);
  }

  disconnectedCallback() {
    document.removeEventListener("fullscreenchange", this._onFullscreenChange);
  }

  _isFullscreen() {
    return this.shadowRoot.fullscreenElement === this._grid;
  }

  async _openFullscreen(card) {
    this._activeCard = card;
    await this._grid.requestFullscreen();
  }

  _navigate(direction) {
    if (!this._isFullscreen() || this._cards.length < 2) {
      return;
    }
    this._activeCard._releasePtt();
    const index = this._cards.indexOf(this._activeCard);
    this._activeCard = this._cards[(index + direction + this._cards.length) % this._cards.length];
    this._updateFullscreen();
  }

  _updateFullscreen() {
    const fullscreen = this._isFullscreen();
    for (const card of this._cards) {
      card.toggleAttribute("wall-active", fullscreen && card === this._activeCard);
      card._render();
    }
    if (!fullscreen) {
      this._activeCard = null;
    }
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
    const fullscreen = this._isFullscreen();
    const activeEntity = this._activeCard?._config.entity;
    if (fullscreen && this._activeCard) {
      this._activeCard._releasePtt();
    }
    while (this._grid.firstChild) {
      this._grid.removeChild(this._grid.firstChild);
    }
    this._cards = this._config.entities.map((entity) => {
      const card = document.createElement("netvue-camera-card");
      card._wall = this;
      card.setConfig({ entity });
      if (this._hass) {
        card.hass = this._hass;
      }
      this._grid.appendChild(card);
      return card;
    });
    if (fullscreen) {
      this._activeCard = this._cards.find((card) => card._config.entity === activeEntity) || this._cards[0];
      this._updateFullscreen();
    }
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
