const state = {
  currentUser: null,
  activeMember: null,
  family: null,
};

const listeners = new Set();

export function getState() {
  return { ...state };
}

export function setState(patch) {
  Object.assign(state, patch);
  const snapshot = getState();
  for (const listener of listeners) listener(snapshot);
  window.dispatchEvent(new CustomEvent("fieldnote:statechange", { detail: snapshot }));
  return snapshot;
}

export function subscribe(listener) {
  if (typeof listener !== "function") throw new TypeError("A state listener must be a function");
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetState() {
  return setState({ currentUser: null, activeMember: null, family: null });
}
