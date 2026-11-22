import assert from "node:assert/strict";
import { test } from "node:test";
import { LIMITS, NetvueConnection } from "../../custom_components/netvue_local/frontend/connection.js";
import { connectionOptions, fakeStream, fakeTrack, flush } from "./helpers.mjs";

async function started(extra) {
  const ctx = connectionOptions(extra);
  const conn = new NetvueConnection(ctx.options);
  await conn.start();
  await flush();
  return { ...ctx, conn, pc: ctx.pcs[0] };
}

async function negotiated(extra) {
  const ctx = await started(extra);
  ctx.signalling.last.emit({ type: "session", session_id: "S1" });
  ctx.signalling.last.emit({ type: "answer", answer: "v=0 answer" });
  await flush();
  return ctx;
}

test("the offer is one video recvonly, one audio sendrecv and the netvue-ptt channel", async () => {
  const { pc, signalling } = await started();
  assert.deepEqual(pc.transceivers.map((t) => [t.kind, t.init.direction]), [
    ["video", "recvonly"],
    ["audio", "sendrecv"],
  ]);
  assert.equal(pc.channels.length, 1);
  assert.equal(pc.channels[0].label, "netvue-ptt");
  assert.deepEqual(pc.channels[0].init, { ordered: true });
  assert.equal(signalling.offers.length, 1);
  assert.equal(signalling.last.sdp, "v=0 offer");
  assert.equal(signalling.last.entity, "camera.test");
});

test("local candidates before the session event are buffered then sent; end-of-candidates is ignored", async () => {
  const { pc, signalling } = await started();
  pc.onicecandidate({ candidate: { candidate: "candidate:1", toJSON: () => ({ candidate: "candidate:1", sdpMid: "0", sdpMLineIndex: 0 }) } });
  pc.onicecandidate({ candidate: null });
  pc.onicecandidate({ candidate: { candidate: "", toJSON: () => ({ candidate: "" }) } });
  assert.equal(signalling.candidates.length, 0);
  signalling.last.emit({ type: "session", session_id: "S1" });
  assert.deepEqual(signalling.candidates.map((c) => [c.sessionId, c.candidate.candidate]), [["S1", "candidate:1"]]);
  pc.onicecandidate({ candidate: { candidate: "candidate:2", toJSON: () => ({ candidate: "candidate:2", sdpMid: "0" }) } });
  assert.equal(signalling.candidates.length, 2);
});

test("remote candidates arriving before the answer are applied after it, in order", async () => {
  const { pc, signalling } = await started();
  const emit = signalling.last.emit;
  emit({ type: "session", session_id: "S1" });
  emit({ type: "candidate", candidate: { candidate: "candidate:a", sdpMid: "0", sdpMLineIndex: 0 } });
  emit({ type: "candidate", candidate: { candidate: "candidate:b" } });
  emit({ type: "candidate", candidate: { candidate: "" } });
  await flush();
  assert.equal(pc.candidates.length, 0);
  emit({ type: "answer", answer: "v=0 answer" });
  await flush();
  assert.deepEqual(pc.remoteDescriptions, [{ type: "answer", sdp: "v=0 answer" }]);
  assert.deepEqual(pc.candidates.map((c) => c.candidate), ["candidate:a", "candidate:b"]);
  assert.equal(pc.candidates[1].sdpMid, "0");
  emit({ type: "candidate", candidate: { candidate: "candidate:c", sdpMid: "0" } });
  await flush();
  assert.equal(pc.candidates.length, 3);
});

test("tracks build one stream that is delivered once", async () => {
  const { pc, events } = await negotiated();
  pc.ontrack({ track: fakeTrack("v") });
  pc.ontrack({ track: fakeTrack("a") });
  assert.equal(events.streams.length, 1);
  assert.equal(events.streams[0].getTracks().length, 2);
});

test("live is reported once ICE connects and the connection never renegotiates across many PTT cycles", async () => {
  const { pc, events, mic, signalling, clock } = await negotiated();
  pc.ontrack({ track: fakeTrack("v") });
  const stream = events.streams[0];
  pc.setState("connected");
  assert.equal(events.live, 1);
  const channel = pc.channels[0];
  channel.serverSend({ t: "state", state: "listening", id: 0 });
  for (let i = 1; i <= 30; i++) {
    assert.equal(events.ptt[0].press(), true);
    channel.serverSend({ t: "state", state: "speaking", id: i });
    mic.calls[i - 1].resolve(fakeStream(fakeTrack(`m${i}`)));
    await flush();
    clock.advance(1200);
    events.ptt[0].release();
  }
  assert.equal(pc.createOfferCalls, 1);
  assert.equal(pc.setLocalCalls, 1);
  assert.equal(signalling.offers.length, 1);
  assert.equal(pc.remoteDescriptions.length, 1);
  assert.equal(pc.closed, false);
  assert.equal(events.streams.length, 1);
  assert.equal(events.streams[0], stream);
  assert.equal(events.failed.length, 0);
  assert.deepEqual(pc.transceivers.map((t) => t.init.direction), ["recvonly", "sendrecv"]);
  assert.equal(channel.messages("start").length, 30);
});

test("failure paths report a recoverable error and close first", async () => {
  const cases = [
    ["ice failed", (ctx) => ctx.pc.setState("failed"), "ice_failed"],
    ["connection failed", (ctx) => ctx.pc.setState("connected", "failed"), "ice_failed"],
    ["answer timeout", (ctx) => ctx.clock.advance(LIMITS.answerMs), "no_answer"],
    ["signalling error", (ctx) => ctx.signalling.last.emit({ type: "error", code: "netvue_webrtc_failed", message: "x" }), "netvue_webrtc_failed"],
  ];
  for (const [name, trigger, code] of cases) {
    const ctx = await started();
    trigger(ctx);
    assert.equal(ctx.events.failed.length, 1, name);
    assert.equal(ctx.events.failed[0].code, code, name);
    assert.equal(ctx.events.failed[0].recoverable, true, name);
    assert.equal(ctx.pc.closed, true, name);
    assert.equal(ctx.clock.pending, 0, name);
  }
});

test("a delayed answer leaves the ICE connection its full deadline", async () => {
  const { clock, signalling, pc, events, conn } = await started();
  clock.advance(20000);
  assert.equal(events.failed.length, 0);
  signalling.last.emit({ type: "session", session_id: "slow" });
  signalling.last.emit({ type: "answer", answer: "v=0 answer" });
  await flush();
  clock.advance(14000);
  assert.equal(events.failed.length, 0);
  pc.setState("connected");
  assert.equal(events.live, 1);
  conn.close();
});

test("busy is not recoverable", async () => {
  const ctx = await started();
  ctx.signalling.last.emit({ type: "error", code: "netvue_webrtc_busy", message: "x" });
  assert.equal(ctx.events.failed[0].recoverable, false);
});

test("subscription rejection of an unavailable entity is fatal, a lost connection is not", async () => {
  const fatal = connectionOptions();
  fatal.signalling.rejectOffer = { code: "webrtc_offer_failed", message: "nope" };
  await new NetvueConnection(fatal.options).start();
  await flush();
  assert.equal(fatal.events.failed[0].recoverable, false);
  const transient = connectionOptions();
  transient.signalling.rejectOffer = { code: 3 };
  await new NetvueConnection(transient.options).start();
  await flush();
  assert.equal(transient.events.failed[0].recoverable, true);
  const config = connectionOptions();
  config.signalling.failConfig = { code: "not_found" };
  await new NetvueConnection(config.options).start();
  assert.equal(config.events.failed[0].recoverable, false);
  assert.equal(config.pcs.length, 0);
});

test("disconnected for 5 s fails, a recovery before that does not", async () => {
  const ctx = await negotiated();
  ctx.pc.setState("connected");
  ctx.pc.setState("disconnected");
  ctx.clock.advance(LIMITS.disconnectedMs - 1);
  ctx.pc.setState("connected");
  ctx.clock.advance(LIMITS.disconnectedMs * 2);
  assert.equal(ctx.events.failed.length, 0);
  ctx.pc.setState("disconnected");
  ctx.clock.advance(LIMITS.disconnectedMs);
  assert.equal(ctx.events.failed[0].code, "disconnected");
});

test("ICE that never connects after the answer fails", async () => {
  const ctx = await negotiated();
  ctx.clock.advance(LIMITS.iceMs);
  assert.equal(ctx.events.failed[0].code, "no_connection");
});

test("a video that does not advance for 10 s is a stall; progress or a hidden page is not", async () => {
  let time = 0;
  let hidden = false;
  const ctx = await started({ options: { getPlaybackTime: () => time, isHidden: () => hidden } });
  ctx.signalling.last.emit({ type: "answer", answer: "a" });
  await flush();
  ctx.pc.setState("connected");
  for (let i = 0; i < 30; i++) {
    time += 1;
    ctx.clock.advance(1000);
  }
  assert.equal(ctx.events.failed.length, 0);
  hidden = true;
  ctx.clock.advance(60000);
  assert.equal(ctx.events.failed.length, 0);
  hidden = false;
  ctx.clock.advance(LIMITS.stallMs + LIMITS.stallCheckMs);
  assert.equal(ctx.events.failed[0].code, "stalled");
});

test("close tears everything down: unsubscribe, pc, timers, mic, listeners", async () => {
  const ctx = await negotiated();
  ctx.pc.setState("connected");
  ctx.pc.ontrack({ track: fakeTrack("v") });
  const channel = ctx.pc.channels[0];
  channel.serverSend({ t: "state", state: "listening", id: 0 });
  ctx.conn.ptt.press();
  const track = fakeTrack("m");
  ctx.mic.calls[0].resolve(fakeStream(track));
  await flush();
  ctx.conn.close();
  await flush();
  assert.equal(ctx.signalling.unsubscribed, 1);
  assert.equal(ctx.pc.closed, true);
  assert.equal(ctx.pc.onicecandidate, null);
  assert.equal(track.stopped, true);
  assert.equal(channel.listenerCount, 0);
  assert.equal(ctx.clock.pending, 0);
  ctx.conn.close();
  assert.equal(ctx.signalling.unsubscribed, 1);
  assert.equal(ctx.events.failed.length, 0);
});

test("closing before the config arrives creates no peer connection", async () => {
  const ctx = connectionOptions();
  const conn = new NetvueConnection(ctx.options);
  const starting = conn.start();
  conn.close();
  await starting;
  assert.equal(ctx.pcs.length, 0);
});
