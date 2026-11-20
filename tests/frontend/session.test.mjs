import assert from "node:assert/strict";
import { test } from "node:test";
import { HEALTHY_RESET_MS, NetvueSession, RecoveryPolicy } from "../../custom_components/netvue_local/frontend/session.js";
import { FakeClock } from "./helpers.mjs";

test("policy: 1 s, 2 s, 4 s then null", () => {
  const policy = new RecoveryPolicy();
  assert.deepEqual([policy.failed(), policy.failed(), policy.failed(), policy.failed(), policy.failed()], [1000, 2000, 4000, null, null]);
  policy.reset();
  assert.equal(policy.failed(), 1000);
});

test("policy: counter resets only after 30 s of healthy playback", () => {
  let now = 0;
  const policy = new RecoveryPolicy({ now: () => now });
  policy.failed();
  policy.failed();
  policy.live();
  now += HEALTHY_RESET_MS - 1;
  assert.equal(policy.failed(), 4000);
  policy.live();
  now += HEALTHY_RESET_MS;
  assert.equal(policy.failed(), 1000);
});

function harness() {
  const clock = new FakeClock();
  const conns = [];
  const states = [];
  const streams = [];
  const ptts = [];
  const session = new NetvueSession({
    entity: "camera.test",
    timers: clock.api,
    now: clock.now,
    onState: (s) => states.push(s),
    onStream: (s) => streams.push(s),
    onPtt: (p) => ptts.push(p),
    connectionFactory: (opts) => {
      const conn = {
        opts,
        started: 0,
        closed: 0,
        start() {
          this.started++;
        },
        close() {
          this.closed++;
        },
      };
      conns.push(conn);
      return conn;
    },
  });
  return { clock, conns, states, streams, ptts, session, names: () => states.map((s) => s.state) };
}

const recoverable = { code: "ice_failed", message: "", recoverable: true };

test("a recoverable failure reconnects with 1 s, 2 s, 4 s then stays in error; Retry restarts", () => {
  const h = harness();
  h.session.start();
  h.conns[0].opts.onLive();
  h.conns[0].opts.onFailed(recoverable);
  assert.equal(h.conns[0].closed, 1);
  assert.equal(h.conns.length, 1);
  h.clock.advance(999);
  assert.equal(h.conns.length, 1);
  h.clock.advance(1);
  assert.equal(h.conns.length, 2);
  h.conns[1].opts.onFailed(recoverable);
  h.clock.advance(2000);
  assert.equal(h.conns.length, 3);
  h.conns[2].opts.onFailed(recoverable);
  h.clock.advance(3999);
  assert.equal(h.conns.length, 3);
  h.clock.advance(1);
  assert.equal(h.conns.length, 4);
  h.conns[3].opts.onFailed(recoverable);
  h.clock.advance(60000);
  assert.equal(h.conns.length, 4);
  assert.equal(h.states.at(-1).state, "error");
  assert.equal(h.clock.pending, 0);
  h.session.retry();
  assert.equal(h.conns.length, 5);
  assert.equal(h.names().at(-1), "connecting");
});

test("non-recoverable errors never retry", () => {
  const h = harness();
  h.session.start();
  h.conns[0].opts.onFailed({ code: "netvue_webrtc_busy", message: "", recoverable: false });
  h.clock.advance(60000);
  assert.equal(h.conns.length, 1);
  assert.equal(h.states.at(-1).state, "error");
  assert.equal(h.states.at(-1).error.code, "netvue_webrtc_busy");
});

test("healthy playback for 30 s restores the full budget", () => {
  const h = harness();
  h.session.start();
  for (let round = 0; round < 3; round++) {
    const conn = h.conns.at(-1);
    conn.opts.onLive();
    h.clock.advance(HEALTHY_RESET_MS);
    conn.opts.onFailed(recoverable);
    h.clock.advance(1000);
    assert.equal(h.conns.length, round + 2);
  }
});

test("stop closes the connection, cancels a pending retry and clears stream and ptt", () => {
  const h = harness();
  h.session.start();
  h.conns[0].opts.onFailed(recoverable);
  h.session.stop();
  h.clock.advance(60000);
  assert.equal(h.conns.length, 1);
  assert.equal(h.clock.pending, 0);
  h.session.start();
  h.session.stop();
  assert.equal(h.conns[1].closed, 1);
  assert.equal(h.streams.at(-1), null);
  assert.equal(h.ptts.at(-1), null);
  assert.equal(h.states.at(-1).state, "stopped");
});

test("callbacks of a superseded connection are ignored", () => {
  const h = harness();
  h.session.start();
  const old = h.conns[0];
  h.session.retry();
  assert.equal(old.closed, 1);
  old.opts.onFailed(recoverable);
  old.opts.onLive();
  old.opts.onStream({});
  h.clock.advance(10000);
  assert.equal(h.conns.length, 2);
  assert.ok(!h.names().includes("live"));
});
