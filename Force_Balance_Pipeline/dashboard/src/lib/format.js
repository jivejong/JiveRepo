// Date and number formatting for the dashboard (doc 07 Phase 7, display rules). Pure: no DOM, no fetch, no clock of its own.

const pad = (n) => String(n).padStart(2, "0");

function toDate(iso) {
  if (typeof iso !== "string" || iso === "") return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "YYYY-MM-DD HH:MM UTC", or "-" for a null or invalid input. */
export function formatUtc(iso) {
  const d = toDate(iso);
  if (!d) return "-";
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** "just now" under 60 s, "N min ago" under 60 min, "N h ago" under 48 h, else "N d ago"; "-" for a null or invalid input. */
export function formatAgo(nowMs, iso) {
  const d = toDate(iso);
  if (!d || typeof nowMs !== "number") return "-";
  const seconds = Math.max(0, Math.floor((nowMs - d.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/** A number with a fixed count of digits, or "-" for null, undefined or a non-number. */
export function formatNumber(x, digits = 2) {
  return typeof x === "number" && Number.isFinite(x) ? x.toFixed(digits) : "-";
}
