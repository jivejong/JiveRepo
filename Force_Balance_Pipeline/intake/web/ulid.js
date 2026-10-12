// A ULID (doc 02): 48-bit millisecond timestamp + 80 random bits, 26 Crockford base32 characters. The browser makes one per report
// at submit time; it is the report's idempotency key, and the server derives the event_id from it (intake_core.event_id_for).
// The server accepts exactly this shape: first character 0-7, the rest Crockford (intake_core._ULID_RE).

export const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
export const ULID_RE = /^[0-7][0-9A-HJKMNP-TV-Z]{25}$/;

/** n random bytes from the platform's cryptographic generator. */
export function cryptoRandomBytes(n) {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  return bytes;
}

/**
 * @param {number} nowMs  milliseconds since the epoch (an integer below 2**48)
 * @param {(n: number) => Uint8Array} randomBytes  returns n random bytes
 */
export function newUlid(nowMs, randomBytes = cryptoRandomBytes) {
  if (!Number.isSafeInteger(nowMs) || nowMs < 0 || nowMs >= 2 ** 48) {
    throw new RangeError("a ULID timestamp is an integer number of milliseconds below 2**48");
  }
  const noise = randomBytes(10);
  if (!noise || noise.length !== 10) throw new RangeError("randomBytes(10) must return 10 bytes");
  const chars = new Array(26);
  let time = nowMs;
  for (let i = 9; i >= 0; i--) {
    chars[i] = CROCKFORD[time % 32];
    time = Math.floor(time / 32);
  }
  let bits = 0n;
  for (const byte of noise) bits = (bits << 8n) | BigInt(byte);
  for (let i = 25; i >= 10; i--) {
    chars[i] = CROCKFORD[Number(bits & 31n)];
    bits >>= 5n;
  }
  return chars.join("");
}
