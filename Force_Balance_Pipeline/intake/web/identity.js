// The reporter's browser identity: a random 4-16 character [a-z0-9] string kept in localStorage (doc 02: web-a3f2). It is not a
// login. The server uses it only to label the report's source and to count the per-reporter daily cap, so a person who clears their
// storage gets a new one. Every storage access is wrapped: localStorage can throw (private windows, blocked site data), and the page
// must still work, with an identity that lasts for the session.

import { cryptoRandomBytes } from "./ulid.js";

export const REPORTER_RE = /^[a-z0-9]{4,16}$/;
export const REPORTER_KEY = "fbp.reporter_id";
export const REPORTER_LENGTH = 12;
const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

/** A uniformly random identity (rejection sampling, so no character is more likely than another). */
export function newReporterId(randomBytes = cryptoRandomBytes) {
  let id = "";
  while (id.length < REPORTER_LENGTH) {
    for (const byte of randomBytes(REPORTER_LENGTH * 2)) {
      if (byte < 252 && id.length < REPORTER_LENGTH) id += ALPHABET[byte % 36];
    }
  }
  return id;
}

/**
 * @param {() => Storage} getStorage  returns localStorage; may throw
 * @returns {{reporterId: () => string, persistent: () => boolean}}
 */
export function createIdentity({ getStorage, randomBytes = cryptoRandomBytes }) {
  let cached = null;
  let persistent = false;
  return {
    reporterId() {
      if (cached) return cached;
      let storage = null;
      try {
        storage = getStorage();
      } catch (e) {
        storage = null;
      }
      let stored = null;
      try {
        stored = storage ? storage.getItem(REPORTER_KEY) : null;
      } catch (e) {
        stored = null;
      }
      if (typeof stored === "string" && REPORTER_RE.test(stored)) {
        cached = stored;
        persistent = true;
        return cached;
      }
      cached = newReporterId(randomBytes);
      try {
        storage.setItem(REPORTER_KEY, cached);
        persistent = true;
      } catch (e) {
        persistent = false;
      }
      return cached;
    },
    persistent() {
      return persistent;
    },
  };
}
