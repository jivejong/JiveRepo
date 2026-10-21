// Guardrail overrides of a deployment row (doc 06 "Constraint layer": every rejection is appended to guardrail_overrides). The agent
// writes a JSON array of {"proposal": {jedi_ids, starship_id, rationale} or null, "reasons": [...]}. Pure and tolerant: anything else
// gives an empty list or empty fields, never a throw.

const strings = (value) => (Array.isArray(value) ? value.filter((v) => typeof v === "string") : []);

/** [{reasons: [string], proposalJedi: [string], proposalShip: string or null}] */
export function summarizeOverrides(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const entry = item && typeof item === "object" ? item : {};
    const proposal = entry.proposal && typeof entry.proposal === "object" ? entry.proposal : {};
    return {
      reasons: strings(entry.reasons),
      proposalJedi: strings(proposal.jedi_ids),
      proposalShip: typeof proposal.starship_id === "string" ? proposal.starship_id : null,
    };
  });
}
