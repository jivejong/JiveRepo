// The only file that touches the real browser objects; app.js gets them as arguments.

import { startApp } from "./app.js";
import { cryptoRandomBytes } from "./ulid.js";

function safeIndexedDb() {
  try {
    return window.indexedDB || null;
  } catch (e) {
    return null;
  }
}

startApp({
  document,
  fetchFn: (url, init) => fetch(url, init),
  indexedDB: safeIndexedDb(),
  getStorage: () => window.localStorage,
  now: () => Date.now(),
  randomBytes: cryptoRandomBytes,
  setTimeoutFn: (fn, ms) => setTimeout(fn, ms),
  clearTimeoutFn: (id) => clearTimeout(id),
  // one tab sends at a time (two tabs share one queue)
  lock: navigator.locks ? (fn) => navigator.locks.request("fbp-intake-queue", fn) : null,
  listenOnline: (fn) => window.addEventListener("online", fn),
  listenVisible: (fn) => document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && fn()),
});
