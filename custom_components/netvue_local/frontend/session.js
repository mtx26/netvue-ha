// Reconnection policy and the session that owns the current NetvueConnection.

import { NetvueConnection } from "./connection.js";

export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
export const HEALTHY_RESET_MS = 30000;

export class RecoveryPolicy {
  constructor({ delays = RETRY_DELAYS_MS, healthyMs = HEALTHY_RESET_MS,
    now = () => Date.now(), random = Math.random } = {}) {
    this._delays = delays;
    this._healthyMs = healthyMs;
    this._now = now;
    this._random = random;
    this.reset();
  }

  reset() {
    this._attempts = 0;
    this._healthySince = null;
  }

  live() {
    this._healthySince = this._now();
  }

  failed() {
    if (this._healthySince !== null && this._now() - this._healthySince >= this._healthyMs) {
      this._attempts = 0;
    }
    this._healthySince = null;
    const delay = this._delays[Math.min(this._attempts, this._delays.length - 1)];
    this._attempts = Math.min(this._attempts + 1, this._delays.length);
    return Math.round(delay * (0.75 + 0.25 * this._random()));
  }

  get attempts() {
    return this._attempts;
  }
}

// State events: connecting | live | reconnecting {attempt, delay} | error {error} | stopped
export class NetvueSession {
  constructor(options) {
    this._o = {
      connectionFactory: (opts) => new NetvueConnection(opts),
      now: () => Date.now(),
      onState: () => {},
      onStream: () => {},
      onPtt: () => {},
      ...options,
    };
    this._timers = this._o.timers;
    this._policy = new RecoveryPolicy({ now: this._o.now, random: this._o.random });
    this._conn = null;
    this._retryTimer = null;
    this._active = false;
  }

  get active() {
    return this._active;
  }

  start() {
    if (this._active) {
      return;
    }
    this._policy.reset();
    this._active = true;
    this._connect("connecting");
  }

  // Manual retry after a fatal error.
  retry() {
    this._teardown();
    this._policy.reset();
    this._active = true;
    this._connect("connecting");
  }

  stop() {
    const wasActive = this._active;
    this._active = false;
    this._teardown();
    if (wasActive) {
      this._o.onState({ state: "stopped" });
    }
  }

  _teardown() {
    if (this._retryTimer !== null) {
      this._timers.clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
    this._release();
  }

  _release() {
    const conn = this._conn;
    if (conn) {
      this._conn = null;
      conn.close();
      this._o.onPtt(null);
      this._o.onStream(null);
    }
  }

  _connect(state) {
    this._o.onState({ state });
    const o = this._o;
    const conn = o.connectionFactory({
      ...o,
      onStream: (stream) => this._current(conn) && o.onStream(stream),
      onLive: () => {
        if (this._current(conn)) {
          this._policy.live();
          o.onState({ state: "live" });
        }
      },
      onFailed: (error) => this._failed(conn, error),
      onPtt: (ptt) => this._current(conn) && o.onPtt(ptt),
    });
    this._conn = conn;
    conn.start();
  }

  _current(conn) {
    return this._conn === conn;
  }

  _failed(conn, error) {
    if (!this._current(conn)) {
      return;
    }
    this._release();
    const delay = error.recoverable ? this._policy.failed() : null;
    if (delay === null) {
      this._active = false;
      this._o.onState({ state: "error", error });
      return;
    }
    this._o.onState({ state: "reconnecting", attempt: this._policy.attempts, delay });
    this._retryTimer = this._timers.setTimeout(() => {
      this._retryTimer = null;
      if (this._active) {
        this._connect("reconnecting");
      }
    }, delay);
  }
}
