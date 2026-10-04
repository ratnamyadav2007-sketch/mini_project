import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window ??= { dispatchEvent() {} };

const { getState, resetState, setState, subscribe } = await import("../js/store.js");

test("stores user, active member, and family and notifies subscribers", () => {
  const changes = [];
  const unsubscribe = subscribe(state => changes.push(state));
  const next = setState({
    currentUser: { id: "u1" },
    activeMember: { id: "p1" },
    family: { id: "f1" },
  });

  assert.deepEqual(next, {
    currentUser: { id: "u1" },
    activeMember: { id: "p1" },
    family: { id: "f1" },
  });
  assert.equal(changes.length, 1);
  assert.deepEqual(getState(), next);

  unsubscribe();
  resetState();
  assert.equal(changes.length, 1);
});
