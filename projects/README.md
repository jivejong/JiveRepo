# 🛠️ Projects

Working code — things built to be used, not to demonstrate a pattern. Each project is self-contained and carries its own setup instructions where setup is non-trivial.

> **Moved:** the four Streamlit agentic AI apps (Agentic Poet, Agentic Snacks, NoCap, Spouse Approval) now live in [`/agentic_AI`](../agentic_AI/). This folder is the non-agentic build work.

| Project                              | What it is                                    | Stack                             | State                    |
| ------------------------------------ | --------------------------------------------- | --------------------------------- | ------------------------ |
| [`Chord_Charts/`](./Chord_Charts/)   | Chord chart manager for live performance      | Node + Express + SQLite, React PWA | Built, needs assembly    |
| [`BBS_Website/`](./BBS_Website/)     | Interactive portfolio site as a 1980s BBS     | Vanilla HTML/CSS/JS, zero deps     | Runs as-is               |

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

The landing site for the [Buffer Overflow](https://www.youtube.com/@BufferOverflow-v2j) channel, built as a Commodore 64 bulletin board. The complete core shell is 46 KB of handwritten HTML, CSS, and JavaScript—no build step, runtime dependencies, or framework. See the [project README](./BBS_Website/README.md) for the architecture and size breakdown.

It is a scripted sequence rather than a page, and it commits to the bit:

1. **Boot** — POST beeps and a boot sequence, typed line by line.
2. **Input selection** — keyboard or mouse. Moving the mouse triggers a `MOUSE INPUT DETECTED` interstitial: *"This is a BBS. We use keyboards here. Mouse support indicates you may have joined computing after 1995. We don't judge. Much."*
3. **Crack intro** — a full demoscene tribute: rainbow raster bars, star field, scroller, and SID-style music synthesized live through the Web Audio API.
4. **The `SYS 64738` puzzle** — a gate before the menu. The answer is the C64 warm-reset command; the site accepts `SYS 64738`, `SYS64738`, or `64738`, cycles through dismissive responses on wrong guesses, and relents with the answer after three attempts.
5. **Main menu** — live portfolio links, a Pine-inspired contact composer, an embedded seven-game arcade, sound controls, and a Goodbye command that hangs up the modem.

The Pine-style contact screen is intentionally front-end only. SMTP is deliberately not configured on this static portfolio site; Send falls back to the public LinkedIn contact channel.

**The interface is CSS, browser APIs, and one small media asset.** The C64 palette, scanlines, CRT curvature, phosphor glow, and flicker-on are handwritten; beeps and the SID-style loop are synthesized through Web Audio, while the opening modem handshake uses a dedicated MP3 recording.

The full terminal experience is desktop-first by design. Small screens receive a compact set of real portfolio links rather than a compromised version of the keyboard-driven BBS sequence.

---

## Conventions

**Self-contained.** No shared build, no workspace root, no cross-project imports. Each project stands alone and is copied out intact.

**Documented where it adds context.** [`Chord_Charts/`](./Chord_Charts/README.md) covers assembly and setup; [`BBS_Website/`](./BBS_Website/README.md) explains the deliberately small architecture, interaction model, and design decisions.

**Secrets stay out.** The chart tagger reads `ANTHROPIC_API_KEY` from the environment at invocation — never committed, never in a config file.
