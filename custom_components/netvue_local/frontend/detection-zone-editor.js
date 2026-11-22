const ZONE_NAMES = ["entrance", "doorstep", "garden", "driveway"];
const FULL_IMAGE = [0, 0, 1, 1];

class NetvueDetectionZoneEditor extends HTMLElement {
  configure(t, zones, hass, entityId) {
    this._t = t;
    this._hass = hass;
    this._entityId = entityId;
    this.zones = { ...zones };
  }

  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      .controls { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
      button, select { min-height:40px; padding:6px 12px; border:1px solid var(--divider-color, #999);
        border-radius:8px; background:var(--card-background-color, #fff); color:inherit; font:inherit; }
      .stage { position:relative; margin:12px auto; width:fit-content; max-width:100%; line-height:0; }
      img { display:block; max-width:100%; max-height:65dvh; }
      canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:crosshair; }
      [hidden] { display:none !important; }
    </style>
    <p data-help></p>
    <div class="controls">
      <label><span data-zone-label></span> <select data-zone></select></label>
      <button data-full type="button"></button>
      <button data-clear type="button"></button>
    </div>
    <div class="stage" hidden><img alt=""><canvas></canvas></div>
    <p data-status role="status"></p>`;
    const t = this._t;
    root.querySelector("[data-help]").textContent = t("zoneHelp");
    root.querySelector("[data-zone-label]").textContent = t("zoneLabel");
    this._select = root.querySelector("select");
    this._select.replaceChildren(...ZONE_NAMES.map((name) => new Option(t(`zone_${name}`), name)));
    this._select.value = ZONE_NAMES.find((name) => Object.hasOwn(this.zones, name)) || "entrance";
    this._select.addEventListener("change", () => this._draw());
    const full = root.querySelector("[data-full]");
    full.textContent = t("zoneFull");
    full.addEventListener("click", () => {
      this.zones[this._select.value] = [...FULL_IMAGE];
      this._draw();
    });
    const clear = root.querySelector("[data-clear]");
    clear.textContent = t("zoneClear");
    clear.addEventListener("click", () => {
      delete this.zones[this._select.value];
      this._draw();
    });
    this._status = root.querySelector("[data-status]");
    this._stage = root.querySelector(".stage");
    this._image = root.querySelector("img");
    this._canvas = root.querySelector("canvas");
    this._observer = new ResizeObserver(() => this._resize());
    this._observer.observe(this._image);
    this._image.addEventListener("load", () => {
      this._stage.hidden = false;
      this._resize();
    });
    this._image.addEventListener("error", () => this._previewFailed());
    this._canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || this._drawing) return;
      this._canvas.setPointerCapture(event.pointerId);
      this._drawing = { id: event.pointerId, start: this._point(event) };
    });
    this._canvas.addEventListener("pointermove", (event) => {
      if (this._drawing?.id === event.pointerId) this._draw(this._rectangle(event));
    });
    this._canvas.addEventListener("pointerup", (event) => {
      if (this._drawing?.id !== event.pointerId) return;
      const rect = this._rectangle(event);
      this._drawing = null;
      if (rect[2] > rect[0] && rect[3] > rect[1]) this.zones[this._select.value] = rect;
      this._draw();
    });
    this._canvas.addEventListener("lostpointercapture", () => {
      if (this._drawing) {
        this._drawing = null;
        this._draw();
      }
    });
    this._loadPreview();
  }

  disconnectedCallback() {
    this._observer?.disconnect();
  }

  async _loadPreview() {
    this._status.textContent = this._t("settingsLoading");
    const path = `/api/camera_proxy/${encodeURIComponent(this._entityId)}`;
    try {
      const picture = this._hass.states?.[this._entityId]?.attributes?.entity_picture;
      const source = picture || (await this._hass.callWS({ type: "auth/sign_path", path })).path;
      if (this.isConnected) this._image.src = source;
    } catch {
      if (this.isConnected) this._previewFailed();
    }
  }

  _previewFailed() {
    this._stage.hidden = true;
    this._status.textContent = this._t("zoneImageError");
  }

  _point(event) {
    const bounds = this._canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    ];
  }

  _rectangle(event) {
    const [x1, y1] = this._drawing.start;
    const [x2, y2] = this._point(event);
    return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
  }

  _resize() {
    if (this._stage.hidden) return;
    const bounds = this._image.getBoundingClientRect();
    this._canvas.width = bounds.width;
    this._canvas.height = bounds.height;
    this._draw();
  }

  _draw(draft) {
    if (this._stage.hidden) return;
    const context = this._canvas.getContext("2d");
    const { width, height } = this._canvas;
    context.clearRect(0, 0, width, height);
    const selected = draft || this.zones[this._select.value];
    this._status.textContent = this._t(selected
      ? selected.every((value, index) => value === FULL_IMAGE[index]) ? "zoneFullSelected" : "zoneCustomSelected"
      : "zoneUndefined");
    for (const [name, zone] of Object.entries({ ...this.zones, [this._select.value]: selected })) {
      if (!zone) continue;
      const [left, top, right, bottom] = zone;
      const active = name === this._select.value;
      context.fillStyle = active ? "rgba(3,169,244,.18)" : "transparent";
      context.strokeStyle = active ? "#03a9f4" : "#fff";
      context.lineWidth = active ? 3 : 1;
      context.fillRect(left * width, top * height, (right - left) * width, (bottom - top) * height);
      context.strokeRect(left * width, top * height, (right - left) * width, (bottom - top) * height);
    }
  }
}

if (!customElements.get("netvue-detection-zone-editor")) {
  customElements.define("netvue-detection-zone-editor", NetvueDetectionZoneEditor);
}
