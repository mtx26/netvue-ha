// Push-to-talk state machine. One controller per peer connection: ids restart at 1
// and increase for every press, which is what the server uses to discard stale acks.

export const KEEPALIVE_MS = 500;
export const MIC_CONSTRAINTS = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  video: false,
};

const SERVER_ERRORS = new Set(["busy", "unavailable", "failed", "timeout", "unsupported"]);

export function micErrorCode(err) {
  switch (err && err.name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "mic_denied";
    case "InsecureContext":
      return "mic_insecure";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return "mic_missing";
    case "NotReadableError":
    case "AbortError":
      return "mic_busy";
    case "NotSupportedError":
      return "mic_unsupported";
    default:
      return "mic_failed";
  }
}

function stopStream(stream) {
  for (const track of stream ? stream.getTracks() : []) {
    track.stop();
  }
}

export class PttController {
  constructor({ channel, sender, getUserMedia, timers, onChange, onError }) {
    this._channel = channel;
    this._sender = sender;
    this._getUserMedia = getUserMedia;
    this._timers = timers;
    this._onChange = onChange || (() => {});
    this._onError = onError || (() => {});
    this._ready = false;
    this._closed = false;
    this._lastId = 0;
    this._active = 0;
    this._serverSeen = false;
    this._phase = "idle";
    this._stream = null;
    this._keepalive = null;
    this._listeners = [
      ["message", (event) => this.handleMessage(event.data)],
      ["close", () => this.connectionLost()],
      ["error", () => this.connectionLost()],
    ];
    for (const [type, handler] of this._listeners) {
      channel.addEventListener(type, handler);
    }
  }

  get snapshot() {
    return { ready: this._ready, pressed: this._active !== 0, phase: this._phase };
  }

  press() {
    if (this._closed || !this._ready || this._active !== 0) {
      return false;
    }
    const id = ++this._lastId;
    this._active = id;
    this._serverSeen = false;
    this._phase = "opening";
    if (!this._send({ t: "start", id })) {
      this._finish(false);
      this._onError("failed");
      return false;
    }
    this._keepalive = this._timers.setInterval(() => {
      if (!this._send({ t: "keepalive", id })) {
        this._finish(false);
        this._onError("failed");
      }
    }, KEEPALIVE_MS);
    this._emit();
    this._acquire(id);
    return true;
  }

  release() {
    this._finish(true);
  }

  // Data channel or peer connection gone: nothing can be sent any more.
  connectionLost() {
    const wasReady = this._ready;
    this._ready = false;
    this._finish(false);
    if (wasReady) {
      this._emit();
    }
  }

  dispose() {
    if (this._closed) {
      return;
    }
    this._finish(this._channel.readyState === "open");
    this._closed = true;
    this._ready = false;
    for (const [type, handler] of this._listeners) {
      this._channel.removeEventListener(type, handler);
    }
  }

  handleMessage(data) {
    let message;
    try {
      message = JSON.parse(data);
    } catch {
      return;
    }
    if (!message || typeof message !== "object") {
      return;
    }
    const id = message.id;
    if (!Number.isInteger(id) || id < 0 || id > this._lastId) {
      return;
    }
    // Anything older than the latest press belongs to a previous press.
    if (id !== 0 && id < this._lastId) {
      return;
    }
    if (message.t === "state") {
      this._handleState(message.state, id);
    } else if (message.t === "error" && typeof message.code === "string") {
      this._handleError(message.code, id);
    }
  }

  _handleState(state, id) {
    if (state === "listening") {
      if (id === 0) {
        this._ready = true;
        this._emit();
      } else if (id === this._active && this._serverSeen) {
        // The server ended this press by itself.
        this._finish(false);
      }
    } else if ((state === "opening" || state === "speaking") && id === this._active) {
      this._serverSeen = true;
      this._phase = state;
      this._emit();
    }
  }

  _handleError(code, id) {
    if (id !== this._active) {
      return;
    }
    this._finish(false);
    this._onError(SERVER_ERRORS.has(code) ? code : "failed");
  }

  async _acquire(id) {
    let stream;
    try {
      stream = await this._getUserMedia();
    } catch (err) {
      if (this._active === id) {
        this._finish(true);
        this._onError(micErrorCode(err));
      }
      return;
    }
    const track = stream && stream.getAudioTracks()[0];
    if (this._active !== id || !track) {
      stopStream(stream);
      if (this._active === id) {
        this._finish(true);
        this._onError("mic_missing");
      }
      return;
    }
    this._stream = stream;
    track.addEventListener("ended", () => {
      if (this._active === id) {
        this._finish(true);
        this._onError("mic_failed");
      }
    });
    try {
      await this._sender.replaceTrack(track);
    } catch {
      if (this._active === id) {
        this._finish(true);
        this._onError("mic_failed");
      } else if (this._stream === stream) {
        this._dropMic();
      }
      return;
    }
    if (this._active !== id) {
      // Released while replaceTrack was pending; the release already queued replaceTrack(null).
      stopStream(stream);
      if (this._stream === stream) {
        this._stream = null;
      }
    }
  }

  _finish(sendStop) {
    const id = this._active;
    if (id === 0) {
      return;
    }
    this._active = 0;
    this._phase = "idle";
    if (this._keepalive !== null) {
      this._timers.clearInterval(this._keepalive);
      this._keepalive = null;
    }
    if (sendStop) {
      this._send({ t: "stop", id });
    }
    this._dropMic();
    this._emit();
  }

  _dropMic() {
    stopStream(this._stream);
    this._stream = null;
    try {
      const done = this._sender.replaceTrack(null);
      if (done && done.catch) {
        done.catch(() => {});
      }
    } catch {
      // Sender already closed with its peer connection.
    }
  }

  _send(message) {
    if (this._channel.readyState !== "open") {
      return false;
    }
    try {
      this._channel.send(JSON.stringify(message));
      return true;
    } catch {
      return false;
    }
  }

  _emit() {
    this._onChange(this.snapshot);
  }
}
