## Scope discipline

- Source of truth is SPEC.md. Build only requirements listed in section 3.
- Every code change must trace to an R-ID. Mention the R-ID in the commit message.
- Respect the robustness tier in section 2. Do not add error handling,
  validation, logging, retries, config options, feature flags, or
  abstractions beyond what that tier and the acceptance criteria call for.
- Do not refactor or reformat code outside the files you must touch.
- Do not add dependencies without asking.
- If the spec is ambiguous, contradictory, or silent on something you need,
  stop and ask. Do not guess.
- If you think something is missing, add it to a "Proposed" list in your
  report. Do not build it.
- Anything in sections 4 and 5 is off-limits.
- Finish by reporting: R-IDs completed, checks run and results, deviations
  from the spec, proposals.
