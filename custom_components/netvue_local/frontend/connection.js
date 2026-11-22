// One RTCPeerConnection for video, camera audio and push-to-talk.
// It negotiates exactly once: createOffer/setLocalDescription are never called again.

import { describeError } from "./signalling.js";
import { MIC_CONSTRAINTS, PttController } from "./ptt.js";

export const PTT_CHANNEL = "netvue-ptt";
export const LIMITS = {
  answerMs: 25000,
  iceMs: 15000,
  disconnectedMs: 5000,
  stallMs: 10000,
  stallCheckMs: 1000,
};

export function defaultGetUserMedia() {
  const devices = globalThis.navigator && navigator.mediaDevices;
  if (!devices || !devices.getUserMedia) {
    const err = new Error("microphone unavailable");
    err.name = globalThis.isSecureContext === false ? "InsecureContext" : "NotSupportedError";
    return Promise.reject(err);
  }
  return devices.getUserMedia(MIC_CONSTRAINTS);
}

export class NetvueConnection {
  constructor(options) {
    this._o = {
      getUserMedia: defaultGetUserMedia,
      createPeerConnection: (config) => new RTCPeerConnection(config),
      createStream: () => new MediaStream(),
      now: () => Date.now(),
      isHidden: () => false,
      getPlaybackTime: () => undefined,
      onStream: () => {},
      onLive: () => {},
      onFailed: () => {},
      onPtt: () => {},
      onPttChange: () => {},
      onPttError: () => {},
      ...options,
    };
    this._timers = this._o.timers;
    this._pc = null;
    this._ptt = null;
    this._stream = null;
    this._unsub = null;
    this._sessionId = null;
    this._localQueue = [];
    this._remoteQueue = [];
    this._remoteReady = false;
    this._live = false;
    this._closed = false;
    this._answerTimer = null;
    this._iceTimer = null;
    this._disconnectTimer = null;
    this._stallTimer = null;
  }

  get ptt() {
    return this._ptt;
  }

  async start() {
    const o = this._o;
    let config;
    try {
      config = await o.signalling.getClientConfig(o.entity);
    } catch (err) {
      this._fail(describeError(err));
      return;
    }
    if (this._closed) {
      return;
    }
    const pc = (this._pc = o.createPeerConnection((config && config.configuration) || {}));
    pc.onicecandidate = (event) => this._onLocalCandidate(event);
    pc.oniceconnectionstatechange = () => this._evaluate();
    pc.onconnectionstatechange = () => this._evaluate();
    pc.ontrack = (event) => this._onTrack(event);
    pc.addTransceiver("video", { direction: "recvonly" });
    const audio = pc.addTransceiver("audio", { direction: "sendrecv" });
    const channel = pc.createDataChannel(PTT_CHANNEL, { ordered: true });
    this._ptt = new PttController({
      channel,
      sender: audio.sender,
      getUserMedia: o.getUserMedia,
      timers: this._timers,
      onChange: o.onPttChange,
      onError: o.onPttError,
    });
    o.onPtt(this._ptt);
    try {
      const offer = await pc.createOffer();
      if (this._closed) {
        return;
      }
      await pc.setLocalDescription(offer);
      if (this._closed) {
        return;
      }
      this._answerTimer = this._timers.setTimeout(() => {
        this._answerTimer = null;
        this._fail({ code: "no_answer", message: "", recoverable: true });
      }, LIMITS.answerMs);
      const subscription = o.signalling.offer(o.entity, offer.sdp, (event) =>
        this._onEvent(pc, event),
      );
      this._unsub = subscription;
      subscription.then(undefined, (err) => {
        if (!this._closed) {
          this._fail(describeError(err));
        }
      });
    } catch (err) {
      if (!this._closed) {
        this._fail(describeError(err));
      }
    }
  }

  close() {
    if (this._closed) {
      return;
    }
    this._closed = true;
    for (const name of ["_answerTimer", "_iceTimer", "_disconnectTimer"]) {
      if (this[name] !== null) {
        this._timers.clearTimeout(this[name]);
        this[name] = null;
      }
    }
    if (this._stallTimer !== null) {
      this._timers.clearInterval(this._stallTimer);
      this._stallTimer = null;
    }
    if (this._ptt) {
      this._ptt.dispose();
    }
    if (this._unsub) {
      Promise.resolve(this._unsub)
        .then((unsubscribe) => unsubscribe())
        .catch(() => {});
      this._unsub = null;
    }
    const pc = this._pc;
    if (this._stream) {
      for (const track of this._stream.getTracks()) {
        track.stop();
      }
      this._stream = null;
    }
    if (pc) {
      pc.onicecandidate = null;
      pc.oniceconnectionstatechange = null;
      pc.onconnectionstatechange = null;
      pc.ontrack = null;
      pc.close();
      this._pc = null;
    }
    this._localQueue = [];
    this._remoteQueue = [];
  }

  _fail(error) {
    if (this._closed) {
      return;
    }
    this.close();
    this._o.onFailed(error);
  }

  _onTrack(event) {
    if (this._closed) {
      return;
    }
    const first = this._stream === null;
    if (first) {
      this._stream = this._o.createStream();
    }
    this._stream.addTrack(event.track);
    if (first) {
      this._o.onStream(this._stream);
    }
  }

  _onLocalCandidate(event) {
    const candidate = event.candidate;
    if (this._closed || !candidate || !candidate.candidate) {
      return;
    }
    const init = typeof candidate.toJSON === "function" ? candidate.toJSON() : candidate;
    if (this._sessionId === null) {
      this._localQueue.push(init);
    } else {
      this._sendCandidate(init);
    }
  }

  _sendCandidate(init) {
    const o = this._o;
    Promise.resolve(o.signalling.addCandidate(o.entity, this._sessionId, init)).catch(() => {});
  }

  async _onEvent(pc, event) {
    if (this._closed || this._pc !== pc || !event) {
      return;
    }
    switch (event.type) {
      case "session":
        this._sessionId = event.session_id;
        for (const init of this._localQueue.splice(0)) {
          this._sendCandidate(init);
        }
        break;
      case "answer":
        await this._applyAnswer(pc, event.answer);
        break;
      case "candidate":
        await this._onRemoteCandidate(pc, event.candidate);
        break;
      case "error":
        this._fail(describeError({ code: event.code, message: event.message }));
        break;
    }
  }

  async _applyAnswer(pc, sdp) {
    if (this._remoteReady || typeof sdp !== "string") {
      return;
    }
    try {
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch (err) {
      this._fail({ code: "connect_failed", message: String((err && err.message) || ""), recoverable: true });
      return;
    }
    if (this._closed || this._pc !== pc) {
      return;
    }
    this._remoteReady = true;
    if (this._answerTimer !== null) {
      this._timers.clearTimeout(this._answerTimer);
      this._answerTimer = null;
    }
    if (!this._live && this._iceTimer === null) {
      this._iceTimer = this._timers.setTimeout(() => {
        this._iceTimer = null;
        this._fail({ code: "no_connection", message: "", recoverable: true });
      }, LIMITS.iceMs);
    }
    for (const init of this._remoteQueue.splice(0)) {
      await this._addRemote(pc, init);
    }
    this._evaluate();
  }

  async _onRemoteCandidate(pc, candidate) {
    // An empty candidate is end-of-candidates: nothing to add.
    if (!candidate || typeof candidate.candidate !== "string" || candidate.candidate === "") {
      return;
    }
    const init =
      candidate.sdpMid != null || candidate.sdpMLineIndex != null
        ? candidate
        : { candidate: candidate.candidate, sdpMid: "0" };
    if (!this._remoteReady) {
      this._remoteQueue.push(init);
      return;
    }
    await this._addRemote(pc, init);
  }

  async _addRemote(pc, init) {
    try {
      await pc.addIceCandidate(init);
    } catch {
      // A single unusable candidate must not drop a working connection.
    }
  }

  _evaluate() {
    const pc = this._pc;
    if (this._closed || !pc) {
      return;
    }
    const ice = pc.iceConnectionState;
    const state = pc.connectionState;
    if (ice === "failed" || state === "failed") {
      this._fail({ code: "ice_failed", message: "", recoverable: true });
      return;
    }
    if (ice === "disconnected" || state === "disconnected") {
      if (this._disconnectTimer === null) {
        this._disconnectTimer = this._timers.setTimeout(() => {
          this._disconnectTimer = null;
          this._fail({ code: "disconnected", message: "", recoverable: true });
        }, LIMITS.disconnectedMs);
      }
      return;
    }
    if (this._disconnectTimer !== null) {
      this._timers.clearTimeout(this._disconnectTimer);
      this._disconnectTimer = null;
    }
    if ((ice === "connected" || ice === "completed") && !this._live) {
      this._live = true;
      if (this._iceTimer !== null) {
        this._timers.clearTimeout(this._iceTimer);
        this._iceTimer = null;
      }
      this._watchStall();
      this._o.onLive();
    }
  }

  _watchStall() {
    const o = this._o;
    let lastTime = o.getPlaybackTime();
    let lastProgress = o.now();
    this._stallTimer = this._timers.setInterval(() => {
      const now = o.now();
      const time = o.getPlaybackTime();
      if (o.isHidden() || (typeof time === "number" && time !== lastTime)) {
        lastTime = time;
        lastProgress = now;
      } else if (now - lastProgress >= LIMITS.stallMs) {
        this._fail({ code: "stalled", message: "", recoverable: true });
      }
    }, LIMITS.stallCheckMs);
  }
}
