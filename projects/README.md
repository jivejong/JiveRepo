# 🛠️ Projects

Working code — things built to be used, not to demonstrate a pattern. Each project is self-contained and carries its own setup instructions where setup is non-trivial.

> **Moved:** the four Streamlit agentic AI apps (Agentic Poet, Agentic Snacks, NoCap, Spouse Approval) now live in [`/agentic_AI`](../agentic_AI/). This folder is the non-agentic build work.

| Project                              | What it is                                    | Stack                             | State                    |
| ------------------------------------ | --------------------------------------------- | --------------------------------- | ------------------------ |
| [`Chord_Charts/`](./Chord_Charts/)   | Chord chart manager for live performance      | Node + Express + SQLite, React PWA | Built, needs assembly    |
| [`BBS_Website/`](./BBS_Website/)     | Buffer Overflow landing site as a 1980s BBS   | Single HTML file, zero deps        | Runs as-is, menu is stubbed |

---

## 🎸 [`Chord_Charts/`](./Chord_Charts/) — Chart Manager

A chord chart library for playing live: import a folder of Word documents, get a searchable, taggable, transposable song library on a tablet that keeps working when the venue Wi-Fi does not.

The design constraint is the stage. Dark-optimized UI, amber chords against white lyrics, tablet-first, swipe navigation between songs in a setlist, and an offline cache so a dropped connection mid-set is a non-event.

**Two halves:**

- **`chart-app/`** — Node backend. A DOCX→structured-JSON [`parser.js`](./Chord_Charts/chart-app/parser.js), a key [`transpose.js`](./Chord_Charts/chart-app/transpose.js) engine, SQLite schema and queries, a CLI batch importer, an AI metadata tagger that runs the library through the Claude API, and an Express REST API on port 3001.
- **`chart-pwa/`** — React PWA frontend. Root navigation state machine, five views (browse, chart, edit, setlist, settings), an IndexedDB offline cache, and a service worker for shell caching.

**The interesting bit:** `transpose.js` is shared by both halves — the server and the PWA transpose with the same code, so a chart transposed offline on the tablet and the same chart transposed server-side cannot disagree. That sharing is also the project's main assembly wrinkle (see below).

Setup is documented step-by-step in the [project README](./Chord_Charts/README.md), including the full API endpoint table.

### Before you run it

This is built code that has not been assembled into a running install. Three things need doing:

1. **No `package.json` anywhere.** Dependencies are installed by hand per the project README (`express cors mammoth sql.js @anthropic-ai/sdk` for the backend, `vite @vitejs/plugin-react react react-dom` for the PWA). Adding two `package.json` files is the obvious first move.
2. **The root-level files are misplaced PWA scaffolding.** [`index.html`](./Chord_Charts/index.html), [`main.jsx`](./Chord_Charts/main.jsx), and [`vite.config.js`](./Chord_Charts/vite.config.js) sit at `Chord_Charts/` but reference paths that only resolve from inside `chart-pwa/` — `index.html` loads `/src/main.jsx`, and `main.jsx` imports `./App` and `./index.css`, both of which live in `chart-pwa/src/`. They belong in `chart-pwa/`.
3. **[`parser.test.js`](./Chord_Charts/parser.test.js) is likewise orphaned** — it `require`s `./parser.js`, which is at `chart-app/parser.js`. Move it next to the parser and it runs standalone (`node parser.test.js`); it's a hand-rolled assertion harness with no test-runner dependency, printing ✓/✗ per case across header parsing, line classification, and chart structure.

The project README also flags two known migrations: swapping `sql.js` for `better-sqlite3` (the pure-JS driver was an environment workaround, and the sync API removes the explicit `saveDb()` calls), and settling the `transpose.js` import path — either fix the relative depth or copy it into `chart-pwa/src/lib/`.

---

## 📟 [`BBS_Website/`](./BBS_Website/) — Buffer Overflow BBS v2.0.26

The landing site for the [Buffer Overflow](https://www.youtube.com/@BufferOverflow-v2j) channel, built as a Commodore 64 bulletin board. One 39 KB HTML file — no build step, no dependencies, no framework. Open it in a browser and it runs.

It is a scripted sequence rather than a page, and it commits to the bit:

1. **Boot** — POST beeps and a boot sequence, typed line by line.
2. **Input selection** — keyboard or mouse. Moving the mouse triggers a `MOUSE INPUT DETECTED` interstitial: *"This is a BBS. We use keyboards here. Mouse support indicates you may have joined computing after 1995. We don't judge. Much."*
3. **Crack intro** — a full demoscene tribute: rainbow raster bars, star field, scroller, and SID-style music synthesized live through the Web Audio API.
4. **The `SYS 64738` puzzle** — a gate before the menu. The answer is the C64 warm-reset command; the site accepts `SYS 64738`, `SYS64738`, or `64738`, cycles through dismissive responses on wrong guesses, and relents with the answer after three attempts.
5. **Main menu** — Videos, Research / White Papers, LinkedIn, Contact the SYSOP, plus a beep toggle and a Goodbye that hangs up the modem.

The white papers section renders as an Apache `Index of /papers` directory listing, and Contact is a `COMPOSE MAIL` screen that reports the SYSOP is *"still wiring up SMTP"* — then asks you to verify you're human by typing `SYS ___738`.

**Everything is CSS and Web Audio.** The C64 palette, scanlines, CRT curvature, phosphor glow, and the flicker-on are hand-written custom properties; every beep and the entire SID loop are generated at runtime with oscillators. No images, no audio files, no fonts fetched.

### Before you ship it

- **The menu destinations are placeholders.** Videos, Research, LinkedIn, and Contact are wired to `#videos`, `#papers`, `#linkedin`, and `#contact` — internal anchors, not URLs. The file contains no external links at all, so the channel, Zenodo, and LinkedIn destinations still need filling in.
- **The Apache listing is fiction.** The file names and dates in `/papers` are invented set dressing, not the real [`/docs/white_papers`](../docs/) inventory. Worth reconciling before this is public, since it reads as a real directory index.
- **Desktop only by design.** A `#mobile-block` intercepts small screens, and the whole interaction is keyboard-driven.

---

## Conventions

**Self-contained.** No shared build, no workspace root, no cross-project imports. Each project stands alone and is copied out intact.

**Documented where it's non-obvious.** [`Chord_Charts/`](./Chord_Charts/README.md) carries a full setup README because assembling it is genuinely multi-step; `BBS_Website/` does not, because opening the file is the whole procedure.

**Secrets stay out.** The chart tagger reads `ANTHROPIC_API_KEY` from the environment at invocation — never committed, never in a config file.

---

> **Note:** the [root README](../README.md) still describes this folder as "four self-contained Streamlit agentic AI apps." That's now [`/agentic_AI`](../agentic_AI/); the pointer needs updating.
