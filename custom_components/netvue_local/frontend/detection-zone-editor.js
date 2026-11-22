const FULL_IMAGE = [[0, 0], [1, 0], [1, 1], [0, 1]];
const NEW_ZONE = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]];
const MAX_ZONES = 32;
const MAX_VERTICES = 32;

function rectanglePoints(left, top, right, bottom) {
  return [[left, top], [right, top], [right, bottom], [left, bottom]];
}

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
      button, select, input { min-height:40px; padding:6px 12px; border:1px solid var(--divider-color, #999);
        border-radius:8px; background:var(--card-background-color, #fff); color:inherit; font:inherit; }
      .stage { position:relative; margin:12px auto; width:fit-content; max-width:100%; line-height:0; }
      img { display:block; max-width:100%; max-height:65dvh; }
      canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:crosshair; }
      [hidden] { display:none !important; }
    </style>
    <p data-help></p>
    <div class="controls">
      <label><span data-zone-label></span> <select data-zone></select></label>
      <button data-add type="button"></button>
      <label><span data-name-label></span> <input data-name maxlength="80"></label>
      <button data-polygon type="button"></button>
      <button data-finish type="button" hidden></button>
      <button data-full type="button"></button>
      <button data-clear type="button"></button>
    </div>
    <div class="stage" hidden><img alt=""><canvas></canvas></div>
    <p data-status role="status"></p>`;
    const t = this._t;
    root.querySelector("[data-help]").textContent = t("zoneHelp");
    root.querySelector("[data-zone-label]").textContent = t("zoneLabel");
    root.querySelector("[data-name-label]").textContent = t("zoneName");
    this._select = root.querySelector("select");
    this._name = root.querySelector("[data-name]");
    this._name.addEventListener("input", () => {
      const id = this._select.value;
      const name = this._name.value.trim();
      this.zones[id] = { name, points: this.zones[id].points };
      this._select.selectedOptions[0].textContent = name || t("zoneUnnamed");
      this._draw();
    });
    this._select.addEventListener("change", () => this._selectZone(this._select.value));
    const add = root.querySelector("[data-add]");
    add.textContent = t("zoneAdd");
    add.addEventListener("click", () => {
      if (Object.keys(this.zones).length >= MAX_ZONES) return;
      const id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
        (byte) => byte.toString(16).padStart(2, "0")).join("");
      this.zones[id] = { name: "", points: NEW_ZONE };
      this._refreshOptions(id);
      this._name.focus();
    });
    this._polygonButton = root.querySelector("[data-polygon]");
    this._polygonButton.addEventListener("click", () => {
      this._polygon = this._polygon ? null : [];
      this._draw();
    });
    this._finish = root.querySelector("[data-finish]");
    this._finish.textContent = t("zoneFinish");
    this._finish.addEventListener("click", () => {
      if (!this._polygon || this._polygon.length < 3) return;
      this._setPoints(this._polygon);
      this._polygon = null;
      this._draw();
    });
    const full = root.querySelector("[data-full]");
    full.textContent = t("zoneFull");
    full.addEventListener("click", () => {
      this._setPoints(FULL_IMAGE);
      this._polygon = null;
      this._draw();
    });
    const clear = root.querySelector("[data-clear]");
    clear.textContent = t("zoneClear");
    clear.addEventListener("click", () => {
      delete this.zones[this._select.value];
      this._refreshOptions(Object.keys(this.zones)[0]);
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
    this._canvas.addEventListener("pointerdown", (event) => this._startDrawing(event));
    this._canvas.addEventListener("pointermove", (event) => {
      if (this._drawing?.id === event.pointerId) this._draw(this._draftPoints(event));
    });
    this._canvas.addEventListener("pointerup", (event) => {
      if (this._drawing?.id !== event.pointerId) return;
      const points = this._draftPoints(event);
      this._drawing = null;
      if (points) this._setPoints(points);
      this._draw();
    });
    this._canvas.addEventListener("lostpointercapture", () => {
      if (this._drawing) {
        this._drawing = null;
        this._draw();
      }
    });
    this._refreshOptions(Object.keys(this.zones)[0]);
    this._loadPreview();
  }

  disconnectedCallback() {
    this._observer?.disconnect();
  }

  _startDrawing(event) {
    const zone = this.zones[this._select.value];
    if (event.button !== 0 || this._drawing || !zone) return;
    const start = this._point(event);
    if (this._polygon) {
      if (this._polygon.length < MAX_VERTICES) this._polygon.push(start);
      this._draw();
      return;
    }
    this._canvas.setPointerCapture(event.pointerId);
    const points = zone.points;
    const { width, height } = this._canvas;
    const vertex = points.findIndex(([x, y]) =>
      Math.hypot((x - start[0]) * width, (y - start[1]) * height) <= 12);
    this._drawing = { id: event.pointerId, start, points, vertex };
  }

  _refreshOptions(id) {
    this._select.replaceChildren(...Object.entries(this.zones).map(([key, zone]) =>
      new Option(zone.name || this._t("zoneUnnamed"), key)));
    this.shadowRoot.querySelector("[data-add]").disabled = this._select.options.length >= MAX_ZONES;
    this._selectZone(id);
  }

  _selectZone(id) {
    const zone = this.zones[id];
    this._select.value = id || "";
    this._polygon = null;
    this._drawing = null;
    this._name.value = zone?.name ?? "";
    for (const control of [this._select, this._name, this._polygonButton,
      this.shadowRoot.querySelector("[data-full]"), this.shadowRoot.querySelector("[data-clear]")]) {
      control.disabled = !zone;
    }
    this._draw();
  }

  _setPoints(points) {
    const id = this._select.value;
    this.zones[id] = { name: this._name.value.trim(), points };
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

  _draftPoints(event) {
    const { start, points, vertex } = this._drawing;
    const end = this._point(event);
    if (vertex >= 0) return points.map((point, index) => index === vertex ? end : point);
    const [x1, y1] = start;
    const [x2, y2] = end;
    if (x1 === x2 || y1 === y2) return undefined;
    return rectanglePoints(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2));
  }

  _resize() {
    if (this._stage.hidden) return;
    const bounds = this._image.getBoundingClientRect();
    this._canvas.width = bounds.width;
    this._canvas.height = bounds.height;
    this._draw();
  }

  _draw(draft) {
    this._polygonButton.textContent = this._t(this._polygon ? "zoneCancel" : "zonePolygon");
    this._finish.hidden = !this._polygon;
    this._finish.disabled = (this._polygon?.length || 0) < 3;
    if (this._stage.hidden) return;
    const context = this._canvas.getContext("2d");
    const { width, height } = this._canvas;
    context.clearRect(0, 0, width, height);
    const selected = this._select.value;
    const selectedPoints = selected ? this._polygon ?? draft ?? this.zones[selected].points : [];
    const status = selectedPoints.length ? "zoneCustomSelected" : "zoneUndefined";
    this._status.textContent = this._t(this._polygon ? "zonePolygonHelp" : status);
    for (const [id, zone] of Object.entries(this.zones)) {
      const active = id === selected;
      const points = active ? selectedPoints : zone.points;
      if (!points.length) continue;
      const open = active && this._polygon;
      context.fillStyle = active ? "rgba(3,169,244,.18)" : "transparent";
      context.strokeStyle = active ? "#03a9f4" : "#fff";
      context.lineWidth = active ? 3 : 1;
      context.beginPath();
      context.moveTo(points[0][0] * width, points[0][1] * height);
      for (const [x, y] of points.slice(1)) context.lineTo(x * width, y * height);
      if (!open) {
        context.closePath();
        if (active) context.fill();
      }
      context.stroke();
      if (active) {
        context.fillStyle = "#03a9f4";
        for (const [x, y] of points) context.fillRect(x * width - 3, y * height - 3, 6, 6);
      }
    }
  }
}

if (!customElements.get("netvue-detection-zone-editor")) {
  customElements.define("netvue-detection-zone-editor", NetvueDetectionZoneEditor);
}
