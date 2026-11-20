import assert from "node:assert/strict";
import { test } from "node:test";
import { KEEPALIVE_MS, PttController } from "../../custom_components/netvue_local/frontend/ptt.js";
import { FakeChannel, FakeClock, fakeSender, fakeStream, fakeTrack, flush, manualMic } from "./helpers.mjs";

function setup() {
  const clock = new FakeClock();
  const channel = new FakeChannel();
  const sender = fakeSender();
  const mic = manualMic();
  const errors = [];
  const changes = [];
  const ptt = new PttController({
    channel,
    sender,
    getUserMedia: mic.getUserMedia,
    timers: clock.api,
    onChange: (s) => changes.push(s),
    onError: (c) => errors.push(c),
  });
  channel.serverSend({ t: "state", state: "listening", id: 0 });
  return { clock, channel, sender, mic, errors, changes, ptt };
}

test("press is refused until the server announces listening", () => {
  const clock = new FakeClock();
  const channel = new FakeChannel();
  const ptt = new PttController({ channel, sender: fakeSender(), getUserMedia: manualMic().getUserMedia, timers: clock.api });
  assert.equal(ptt.press(), false);
  assert.deepEqual(channel.sent, []);
});

test("press sends start, mic ready replaces the track, release sends stop and clears everything", async () => {
  const { clock, channel, sender, mic, ptt } = setup();
  assert.equal(ptt.press(), true);
  assert.deepEqual(channel.sent, [{ t: "start", id: 1 }]);
  const track = fakeTrack("m1");
  const stream = fakeStream(track);
  mic.calls[0].resolve(stream);
  await flush();
  assert.deepEqual(sender.calls, ["m1"]);
  clock.advance(KEEPALIVE_MS * 3);
  assert.equal(channel.messages("keepalive").length, 3);
  assert.deepEqual(channel.messages("keepalive")[0], { t: "keepalive", id: 1 });
  ptt.release();
  assert.deepEqual(channel.sent.at(-1), { t: "stop", id: 1 });
  assert.equal(sender.calls.at(-1), null);
  assert.equal(track.stopped, true);
  assert.equal(clock.pending, 0);
  const before = channel.sent.length;
  clock.advance(5000);
  assert.equal(channel.sent.length, before);
});

test("a getUserMedia resolved after release never reaches the sender and its tracks are stopped", async () => {
  const { sender, mic, ptt, channel } = setup();
  ptt.press();
  ptt.release();
  const track = fakeTrack("late");
  mic.calls[0].resolve(fakeStream(track));
  await flush();
  assert.equal(track.stopped, true);
  assert.ok(!sender.calls.includes("late"));
  assert.deepEqual(channel.messages("stop"), [{ t: "stop", id: 1 }]);
});

test("a stale mic of press 1 cannot be used by press 2", async () => {
  const { sender, mic, ptt } = setup();
  ptt.press();
  ptt.release();
  ptt.press();
  const stale = fakeTrack("one");
  const fresh = fakeTrack("two");
  mic.calls[0].resolve(fakeStream(stale));
  mic.calls[1].resolve(fakeStream(fresh));
  await flush();
  assert.equal(stale.stopped, true);
  assert.equal(fresh.stopped, false);
  assert.deepEqual(sender.calls.filter((c) => c !== null), ["two"]);
});

test("replaceTrack pending during release ends with replaceTrack(null) and stopped tracks", async () => {
  const { sender, mic, ptt } = setup();
  let finish;
  sender.replaceTrack = (track) => {
    sender.calls.push(track === null ? null : track.id);
    return track === null ? Promise.resolve() : new Promise((resolve) => (finish = resolve));
  };
  ptt.press();
  const track = fakeTrack("slow");
  mic.calls[0].resolve(fakeStream(track));
  await flush();
  ptt.release();
  finish();
  await flush();
  assert.equal(sender.calls.at(-1), null);
  assert.equal(track.stopped, true);
});

test("rapid press/release cycles use strictly increasing ids and leave no timer", async () => {
  const { clock, channel, mic, ptt } = setup();
  for (let i = 0; i < 50; i++) {
    assert.equal(ptt.press(), true);
    assert.equal(ptt.press(), false);
    ptt.release();
    ptt.release();
  }
  const ids = channel.messages("start").map((m) => m.id);
  assert.deepEqual(ids, Array.from({ length: 50 }, (_, i) => i + 1));
  assert.equal(channel.messages("stop").length, 50);
  assert.equal(clock.pending, 0);
  for (const call of mic.calls) {
    call.resolve(fakeStream(fakeTrack()));
  }
  await flush();
});

test("microphone denied reports a mic error, sends stop and keeps the controller usable", async () => {
  const { mic, ptt, errors, channel, sender } = setup();
  ptt.press();
  mic.calls[0].reject(Object.assign(new Error("no"), { name: "NotAllowedError" }));
  await flush();
  assert.deepEqual(errors, ["mic_denied"]);
  assert.deepEqual(channel.messages("stop"), [{ t: "stop", id: 1 }]);
  assert.equal(ptt.snapshot.pressed, false);
  assert.equal(ptt.snapshot.ready, true);
  assert.equal(sender.calls.at(-1), null);
  assert.equal(ptt.press(), true);
});

test("insecure context and missing devices map to dedicated codes", async () => {
  for (const [name, code] of [["InsecureContext", "mic_insecure"], ["NotFoundError", "mic_missing"], ["Boom", "mic_failed"]]) {
    const { mic, ptt, errors } = setup();
    ptt.press();
    mic.calls[0].reject(Object.assign(new Error(), { name }));
    await flush();
    assert.deepEqual(errors, [code]);
  }
});

for (const code of ["busy", "unavailable", "failed", "timeout", "unsupported"]) {
  test(`server error ${code} stops the local mic and keeps the connection`, async () => {
    const { mic, ptt, errors, channel, sender } = setup();
    ptt.press();
    const track = fakeTrack();
    mic.calls[0].resolve(fakeStream(track));
    await flush();
    channel.serverSend({ t: "error", code, id: 1 });
    channel.serverSend({ t: "state", state: "listening", id: 1 });
    assert.deepEqual(errors, [code]);
    assert.equal(track.stopped, true);
    assert.equal(sender.calls.at(-1), null);
    assert.equal(ptt.snapshot.pressed, false);
    assert.equal(ptt.snapshot.ready, true);
    assert.equal(channel.readyState, "open");
  });
}

test("server states drive the opening/speaking phase and stale acks are ignored", () => {
  const { ptt, channel, errors } = setup();
  ptt.press();
  assert.equal(ptt.snapshot.phase, "opening");
  channel.serverSend({ t: "state", state: "speaking", id: 1 });
  assert.equal(ptt.snapshot.phase, "speaking");
  ptt.release();
  channel.serverSend({ t: "state", state: "speaking", id: 1 });
  assert.equal(ptt.snapshot.phase, "idle");
  ptt.press();
  channel.serverSend({ t: "state", state: "speaking", id: 1 });
  channel.serverSend({ t: "error", code: "busy", id: 1 });
  assert.equal(ptt.snapshot.phase, "opening");
  assert.deepEqual(errors, []);
});

test("a late ack after release cannot re-enable the mic", async () => {
  const { ptt, channel, mic, sender } = setup();
  ptt.press();
  ptt.release();
  channel.serverSend({ t: "state", state: "opening", id: 1 });
  channel.serverSend({ t: "state", state: "speaking", id: 1 });
  mic.calls[0].resolve(fakeStream(fakeTrack("x")));
  await flush();
  assert.equal(ptt.snapshot.pressed, false);
  assert.ok(!sender.calls.includes("x"));
});

test("the server ending a press by itself releases the mic", async () => {
  const { ptt, channel, mic, sender } = setup();
  ptt.press();
  mic.calls[0].resolve(fakeStream(fakeTrack("a")));
  await flush();
  channel.serverSend({ t: "state", state: "speaking", id: 1 });
  channel.serverSend({ t: "state", state: "listening", id: 1 });
  assert.equal(ptt.snapshot.pressed, false);
  assert.equal(sender.calls.at(-1), null);
  assert.equal(channel.messages("stop").length, 0);
});

test("data channel close releases immediately and disables push-to-talk", async () => {
  const { ptt, channel, clock, mic, sender } = setup();
  ptt.press();
  const track = fakeTrack();
  mic.calls[0].resolve(fakeStream(track));
  await flush();
  channel.close();
  assert.equal(ptt.snapshot.pressed, false);
  assert.equal(ptt.snapshot.ready, false);
  assert.equal(track.stopped, true);
  assert.equal(sender.calls.at(-1), null);
  assert.equal(clock.pending, 0);
  assert.equal(ptt.press(), false);
});

test("a failing channel send ends the press", () => {
  const { ptt, channel, errors, clock } = setup();
  ptt.press();
  channel.send = () => {
    throw new Error("closed");
  };
  clock.advance(KEEPALIVE_MS);
  assert.equal(ptt.snapshot.pressed, false);
  assert.deepEqual(errors, ["failed"]);
  assert.equal(clock.pending, 0);
});

test("dispose releases, detaches listeners and ignores later messages", () => {
  const { ptt, channel, clock } = setup();
  ptt.press();
  ptt.dispose();
  assert.deepEqual(channel.sent.at(-1), { t: "stop", id: 1 });
  assert.equal(channel.listenerCount, 0);
  assert.equal(clock.pending, 0);
  assert.equal(ptt.press(), false);
});

test("malformed or unknown messages are ignored", () => {
  const { ptt, channel } = setup();
  ptt.press();
  channel.listeners.get("message")[0]({ data: "not json" });
  channel.serverSend({ t: "state", state: "speaking", id: 99 });
  channel.serverSend({ t: "state", state: "speaking", id: "1" });
  channel.serverSend(null);
  assert.equal(ptt.snapshot.phase, "opening");
});
