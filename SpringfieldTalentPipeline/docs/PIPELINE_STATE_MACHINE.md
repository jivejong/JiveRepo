# Pipeline State Machine

## Stages

```
Sourced → Screening → Interviewing → Offer → Hired
                                    ↘
                    (any of the above) → Rejected
                    (any of the above) → Withdrawn
```

`Rejected` and `Withdrawn` are reachable from any active stage (`Sourced`, `Screening`, `Interviewing`, `Offer`) — a candidate can drop out or be rejected at any point. `Hired` is only reachable from `Offer`. There is no path backward (no `Interviewing → Screening`) — a mis-transition should be corrected by an admin action/note, not by allowing arbitrary backward moves that would make the audit trail meaningless.

## Allowed transitions

| From | Allowed To |
|---|---|
| `Sourced` | `Screening`, `Rejected`, `Withdrawn` |
| `Screening` | `Interviewing`, `Rejected`, `Withdrawn` |
| `Interviewing` | `Offer`, `Rejected`, `Withdrawn` |
| `Offer` | `Hired`, `Rejected`, `Withdrawn` |
| `Hired` | *(terminal)* |
| `Rejected` | *(terminal)* |
| `Withdrawn` | *(terminal)* |

## Implementation: Spring Statemachine

Use **Spring Statemachine** rather than hand-rolling the transition table in an `if`/`switch` block — it's a real, well-documented library in the Spring ecosystem and a legitimate thing to point to as "I used the standard tool for this problem" in a portfolio.

### Version constraint this creates (confirmed 2026-09-09)

Choosing Spring Statemachine is what pins the whole project to Spring Boot 3, and that is worth
being explicit about rather than discovering in Phase 2:

- Statemachine's newest release is **4.0.2** (2026-06-11), built against Spring Framework 6.2 /
  Boot 3.5. There is no Framework 7 / Boot 4 compatible release.
- Boot **3.5.16** (2026-06-25) is the last OSS release of the 3.5 line; free support ended
  2026-06-30, so the branch receives no further free security patches.

So Boot 3.5.16 is the newest version that can satisfy this doc's requirement. Dependency
resolution is clean — Statemachine's own Boot 3.5.15 references all upgrade to 3.5.16 with no split
versions — but the trade is real and has two exits if it ever matters: hand-roll the transition
table and move to Boot 4, or wait for a Framework 7 Statemachine build. For a local,
non-internet-facing portfolio demo, staying put is the reasonable call. Revisit if that changes.

- Define states as an enum matching the stage list above.
- Define events as the transition triggers (e.g. `ADVANCE`, `REJECT`, `WITHDRAW`).
- Configure allowed transitions per the table above; Spring Statemachine rejects an attempted transition that isn't configured, which is exactly the validation this needs — no custom guard logic required for the basic linear+terminal shape here.
- On every successful transition, write a `StageTransition` row (see `DATA_MODEL.md`) from a state machine listener — keeps the audit log automatically in sync with actual transitions rather than relying on every call site to remember to log it.

## Endpoint behavior

`POST /api/applications/{id}/transition` takes a target stage (or event name) and:
1. Loads the application's current state into a state machine instance.
2. Attempts the transition.
3. On success: persists the new `currentStage`, writes the `StageTransition` row, returns 200.
4. On rejection (invalid transition): returns 409 with the current stage and the actual allowed next stages, so the client can show a useful error rather than a bare failure.
