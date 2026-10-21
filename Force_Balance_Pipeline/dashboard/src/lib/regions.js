// Region order and grouping for the galaxy view (doc 07 Phase 7, panel 1; the ring order of agent/distance.py). Pure.

export const REGION_ORDER = [
  "Core Worlds",
  "Colonies",
  "Inner Rim",
  "Expansion Region",
  "Mid Rim",
  "Outer Rim",
  "Wild Space",
  "Unknown Regions",
];

/** [{region, planets}] in REGION_ORDER (empty regions left out); any other or null region goes in a final "Other" group. The server's
 *  order of planets is kept. */
export function groupByRegion(galaxyRows) {
  const groups = new Map(REGION_ORDER.map((r) => [r, []]));
  const other = [];
  for (const row of galaxyRows || []) {
    if (groups.has(row.region)) groups.get(row.region).push(row);
    else other.push(row);
  }
  const out = [];
  for (const [region, planets] of groups) if (planets.length > 0) out.push({ region, planets });
  if (other.length > 0) out.push({ region: "Other", planets: other });
  return out;
}
