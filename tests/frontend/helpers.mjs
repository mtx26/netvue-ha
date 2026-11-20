export class FakeClock {
  constructor() {
    this.time = 0;
    this.next = 1;
    this.timers = new Map();
    this.setTimeout = (fn, ms) => this._add(fn, ms, false);
    this.setInterval = (fn, ms) => this._add(fn, ms, true);
    this.clearTimeout = (id) => this.timers.delete(id);
    this.clearInterval = (id) => this.timers.delete(id);
    this.now = () => this.time;
  }

  get pending() {
    return this.timers.size;
  }

  get api() {
    return {
      setTimeout: this.setTimeout,
      setInterval: this.setInterval,
      clearTimeout: this.clearTimeout,
      clearInterval: this.clearInterval,
    };
  }

  _add(fn, ms, repeat) {
    const id = this.next++;
    this.timers.set(id, { fn, at: this.time + ms, ms, repeat });
    return id;
  }

  advance(ms) {
    const end = this.time + ms;
    for (;;) {
      let best = null;
      for (const [id, timer] of this.timers) {
        if (timer.at <= end && (best === null || timer.at < best[1].at)) {
          best = [id, timer];
        }
      }
      if (best === null) {
        break;
      }
      const [id, timer] = best;
      this.time = timer.at;
      if (timer.repeat) {
        timer.at += timer.ms;
      } else {
        this.timers.delete(id);
      }
      timer.fn();
    }
    this.time = end;
  }
}

export const flush = () => new Promise((resolve) => setImmediate(resolve));

export class FakeChannel {
  constructor() {
    this.readyState = "open";
    this.sent = [];
    this.listeners = new Map();
  }

  addEventListener(type, fn) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), fn]);
  }

  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn));
  }

  get listenerCount() {
    return [...this.listeners.values()].reduce((n, list) => n + list.length, 0);
  }

  send(text) {
    this.sent.push(JSON.parse(text));
  }

  messages(type) {
    return this.sent.filter((m) => m.t === type);
  }

  serverSend(message) {
    for (const fn of this.listeners.get("message") || []) {
      fn({ data: JSON.stringify(message) });
    }
  }

  close() {
    this.readyState = "closed";
    for (const fn of this.listeners.get("close") || []) {
      fn({});
    }
  }
}

export function fakeTrack(id = "mic") {
  const track = {
    id,
    kind: "audio",
    stopped: false,
    listeners: {},
    stop() {
      this.stopped = true;
    },
    addEventListener(type, fn) {
      this.listeners[type] = fn;
    },
  };
  return track;
}

export function fakeStream(...tracks) {
  return {
    tracks,
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === "audio"),
    addTrack(track) {
      tracks.push(track);
    },
  };
}

// getUserMedia whose results the test resolves or rejects by hand.
export function manualMic() {
  const calls = [];
  const getUserMedia = () =>
    new Promise((resolve, reject) => {
      calls.push({ resolve, reject });
    });
  return { getUserMedia, calls };
}

export function fakeSender() {
  const sender = {
    calls: [],
    replaceTrack(track) {
      sender.calls.push(track === null ? null : track.id);
      return Promise.resolve();
    },
  };
  return sender;
}

export class FakePC {
  constructor(config) {
    this.config = config;
    this.createOfferCalls = 0;
    this.setLocalCalls = 0;
    this.remoteDescriptions = [];
    this.candidates = [];
    this.transceivers = [];
    this.channels = [];
    this.closed = false;
    this.iceConnectionState = "new";
    this.connectionState = "new";
    this.sender = fakeSender();
  }

  addTransceiver(kind, init) {
    const t = { kind, init, sender: kind === "audio" ? this.sender : fakeSender() };
    this.transceivers.push(t);
    return t;
  }

  createDataChannel(label, init) {
    const channel = new FakeChannel();
    channel.label = label;
    channel.init = init;
    this.channels.push(channel);
    return channel;
  }

  async createOffer() {
    this.createOfferCalls++;
    return { type: "offer", sdp: "v=0 offer" };
  }

  async setLocalDescription() {
    this.setLocalCalls++;
  }

  async setRemoteDescription(desc) {
    this.remoteDescriptions.push(desc);
  }

  async addIceCandidate(candidate) {
    this.candidates.push(candidate);
  }

  close() {
    this.closed = true;
  }

  setState(ice, connection = ice) {
    this.iceConnectionState = ice;
    this.connectionState = connection;
    this.oniceconnectionstatechange?.();
    this.onconnectionstatechange?.();
  }
}

export function fakeSignalling() {
  const s = {
    pcs: [],
    offers: [],
    candidates: [],
    unsubscribed: 0,
    configCalls: 0,
    failConfig: null,
    rejectOffer: null,
    async getClientConfig() {
      s.configCalls++;
      if (s.failConfig) {
        throw s.failConfig;
      }
      return { configuration: { iceServers: [] } };
    },
    offer(entity, sdp, callback) {
      const record = { entity, sdp, emit: callback };
      s.offers.push(record);
      if (s.rejectOffer) {
        return Promise.reject(s.rejectOffer);
      }
      return Promise.resolve(() => {
        s.unsubscribed++;
      });
    },
    async addCandidate(entity, sessionId, candidate) {
      s.candidates.push({ entity, sessionId, candidate });
    },
    get last() {
      return s.offers[s.offers.length - 1];
    },
  };
  return s;
}

export function connectionOptions(extra = {}) {
  const clock = extra.clock || new FakeClock();
  const signalling = extra.signalling || fakeSignalling();
  const pcs = [];
  const mic = extra.mic || manualMic();
  let playback = 0;
  const events = { failed: [], live: 0, streams: [], ptt: [], pttErrors: [], pttChanges: [] };
  const options = {
    entity: "camera.test",
    signalling,
    timers: clock.api,
    now: clock.now,
    getUserMedia: mic.getUserMedia,
    createPeerConnection: (config) => {
      const pc = new FakePC(config);
      pcs.push(pc);
      return pc;
    },
    createStream: () => fakeStream(),
    getPlaybackTime: () => ++playback,
    onFailed: (e) => events.failed.push(e),
    onLive: () => events.live++,
    onStream: (s) => events.streams.push(s),
    onPtt: (p) => events.ptt.push(p),
    onPttChange: (s) => events.pttChanges.push(s),
    onPttError: (c) => events.pttErrors.push(c),
    ...extra.options,
  };
  return { options, clock, signalling, pcs, mic, events };
}
