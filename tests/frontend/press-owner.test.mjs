import assert from "node:assert/strict";
import { test } from "node:test";
import { KEYBOARD, PressOwner } from "../../custom_components/netvue_local/frontend/press-owner.js";

test("a second pointer cannot take over or cancel the first press", () => {
  const owner = new PressOwner();
  assert.equal(owner.begin(1), true);
  assert.equal(owner.begin(2), false);
  assert.equal(owner.end(2), false);
  assert.equal(owner.active, true);
  assert.equal(owner.end(1), true);
  assert.equal(owner.active, false);
});

test("keyboard during a pointer press is ignored, and its keyup does not release", () => {
  const owner = new PressOwner();
  owner.begin(1);
  assert.equal(owner.begin(KEYBOARD), false);
  assert.equal(owner.end(KEYBOARD), false);
  assert.equal(owner.end(1), true);
});

test("pointer during a keyboard press is ignored", () => {
  const owner = new PressOwner();
  owner.begin(KEYBOARD);
  assert.equal(owner.begin(7), false);
  assert.equal(owner.end(7), false);
  assert.equal(owner.end(KEYBOARD), true);
});

test("clear frees the owner so a new press can start", () => {
  const owner = new PressOwner();
  owner.begin(1);
  owner.clear();
  assert.equal(owner.begin(2), true);
});
