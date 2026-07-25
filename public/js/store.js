const initialState = {
  state: null,
  evaluation: null,
  events: [],
  notificationDiagnostics: null,
  updater: null,
  loading: true,
  error: "",
  simulation: false,
};

let value = { ...initialState };
const listeners = new Set();

export function getStore() {
  return value;
}

export function setStore(patch) {
  value = { ...value, ...patch };
  for (const listener of listeners) {
    listener(value);
  }
  return value;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetStore() {
  value = { ...initialState };
  for (const listener of listeners) {
    listener(value);
  }
}
