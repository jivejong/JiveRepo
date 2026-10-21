// Countdown text for the deployment log (doc 07 Phase 7, display rules: the countdown targets released_at; the arrival is decided_at plus
// the ETA). Pure: the caller passes the clock.

/** "in N min" under 60 min (rounded up), "in N h M min" under 48 h, "in N d" beyond; "done" once the time has passed (or is exactly now);
 *  "-" for a null or invalid input. */
export function countdown(nowMs, iso) {
  if (typeof iso !== "string" || iso === "" || typeof nowMs !== "number") return "-";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "-";
  const diff = t - nowMs;
  if (diff <= 0) return "done";
  const totalMinutes = Math.ceil(diff / 60000);
  if (totalMinutes < 60) return `in ${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  if (hours < 48) return `in ${hours} h ${totalMinutes % 60} min`;
  return `in ${Math.floor(hours / 24)} d`;
}
