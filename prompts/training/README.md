# Training

Partners for learning a new domain, named after the machine-learning lifecycle
they loosely mirror: **pre-training**, **training**, **post-training**, and an
optional **project** phase. They aren't separate tutors that share a theme.
They're one pipeline with typed handoffs — and the pipeline forks and closes into
a loop.

> The ML names are a hook, not a literal claim. "Post-training" in ML means
> behavior-shaping (RLHF/fine-tuning); here it means retention and consolidation.
> The analogy earns its keep as a mental model for the _phases_ of learning, not
> as a precise mapping. Use it and don't overthink it.

## The partners

- **[Pre-training partner](./pre-training-partner.md)** — before you study. Maps
  the new domain onto what you already know, finds transfer points, designs a
  phased path. Does _not_ teach. → hands off to Training.
- **[Training partner](./training-partner.md)** — while you study or build.
  In-the-moment guidance that preserves friction and flags epistemic status
  (confirmed / inferred / simplified). Records what stayed shaky. → forks to
  Post-training _or_ Project.
- **[Post-training partner](./post-training-partner.md)** — after you finish.
  Builds the retrieval schedule that beats the forgetting curve, anchors learning
  by mapping it back outward, routes gaps back to Pre. → loops to Pre, or on to
  Project.
- **[Project partner](./project-partner.md)** _(optional)_ — apply it. Proposes
  2-3 projects scaled to what you learned, coaches the build with the friction
  intact, and records what the build revealed as shaky. → loops to Pre.

## The fork

The core pipeline is pre → training → post → (loop back to pre). The project
partner adds a fork after training, because learners retain differently:

- **The reflective path** — `training → post-training → pre` (next cycle).
  Consolidate by reflecting: extract mental models, build the retrieval schedule,
  map outward. Best for the conceptual learner who retains through structured
  reflection.
- **The applied path** — `training → project → pre` (next cycle). Consolidate by
  building: skip the reflection pass and cement the concept by using it. Best for
  the learner who retains through doing. The project _is_ the retention mechanism.
- **The full path** — `training → post-training → project → pre`. Reflect first,
  then apply. Slowest and most thorough: you get the retrieval schedule _and_ the
  build. Best when the material is hard or high-stakes enough to warrant both.

The project partner takes the same session record either fork feeds it, so it
plugs in after training or after post without changing shape. And nothing
exposes a shaky concept like having to use it — so the applied and full paths
often produce the sharpest input for the next cycle.

## Alternate loops at a glance

```
                    ┌─────────────── reflective ───────────────┐
                    │                                           │
 PRE ──▶ TRAINING ──┼──▶ POST-TRAINING ──────────────────▶ (loop to PRE)
                    │          │                                ▲
                    │          └──▶ PROJECT ──▶ (loop to PRE) ──┤
                    │                    ▲                      │
                    └──── applied ───────┘                      │
                                                                │
              full path: TRAINING ▶ POST ▶ PROJECT ▶ (loop to PRE)
```

Every arrow into PRE carries a return edge: what stayed shaky becomes next
cycle's starting point. You re-enter the loop from a higher floor each time.

## Why it's a loop, not a line

**The symmetry.** Pre-training and post-training are the same cognitive
operation reversed. Pre maps the _new_ domain onto your existing shelves to load
it in; post maps the _learned_ thing back out onto other shelves to anchor it.
Analogical transfer bookends the pipeline.

**The return edge.** No partner is a terminus. Post surfaces gaps; project
reveals gaps under application; both route back to pre as next cycle's input. The
retrieval schedule carries retention across the gap between cycles.

## The shared bet

Every partner refuses the same failure: doing the learning _for_ the learner.
Pre won't pre-chew the topic, training preserves friction on purpose, post hands
you a test to take rather than a summary to reread, and project coaches the build
without building it for you. Retention isn't built by being given good structure
once — it's built by being made to retrieve, and by being made to apply. The
system is designed around that, end to end.

## Handoff discipline (for maintainers)

The partners only form a pipeline if the seams are honored. Each prompt opens with
an **Initialization Protocol** that listens for a specific handoff block, and ends
by emitting one — a block wrapped in **strict XML tags** with structured,
human-legible fields that the learner pastes into the next bot. The tag names are
the wiring; each emitter's tag is exactly what the next partner's Initialization
Protocol listens for. The seams:

- Pre emits `<handoff_to_training>` (knows / concept map / path / first step) →
  Training listens for it.
- Training emits `<handoff_to_post_training>` (progress / shaky / corrected /
  stuck) → both Post _and_ Project listen for it (the fork).
- Post emits `<handoff_to_next>` (learned / revisit / next / carry) → both Pre
  (loop) _and_ Project listen for it.
- Project emits `<handoff_to_pre_training>` (built / applied / revealed-shaky /
  reachable / carry) → Pre listens for it (loop).

So Pre's Initialization Protocol listens for _either_ `<handoff_to_next>` (from
Post) _or_ `<handoff_to_pre_training>` (from Project) — the two arrows that close
the loop. Project listens for _either_ `<handoff_to_post_training>` (from
Training) _or_ `<handoff_to_next>` (from Post).

The blocks run standalone bots in separate sessions, so the tagged block _is_ the
transport between them. Keep both the tag names and the field names stable when
editing — the next bot keys on the tags to catch the handoff and on the fields to
read it.
