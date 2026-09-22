# Buffer Overflow BBS

An interactive portfolio site disguised as a late-1980s bulletin board system.

The core experience—layout, styling, state management, animation, keyboard and mouse interaction, synthesized audio, a puzzle, navigation, and a Pine-inspired mail composer—lives in a **46,991-byte HTML file** with no framework, package manager, build step, or runtime dependency.

> A complete interactive website experience in less source code than many individual modules in a React application.

That is the engineering point of this project. React is useful when a product needs React; this experience did not. The implementation demonstrates that I can choose an architecture based on the problem instead of reaching automatically for a framework.

## Experience

The site behaves like a session rather than a conventional landing page:

1. A keypress answers the connection prompt and starts a 12-second modem handshake.
2. A Commodore 64-inspired boot sequence types itself onto the screen.
3. The visitor chooses keyboard or mouse navigation.
4. A `SYS 64738` puzzle guards access to the system.
5. A demoscene-style crack intro combines raster effects, stars, scrolling text, and synthesized SID-style music.
6. The BBS menu opens portfolio destinations, a Pine-style contact screen, and a playable arcade.

Keyboard mode treats menu letters as commands. Mouse mode turns the same destinations into hyperlinks and restores the system cursor. The chosen mode is preserved when moving between the BBS and the arcade.

## What It Demonstrates

- A multi-stage interface driven by a small client-side state machine
- Progressive text rendering and asynchronous sequence control
- Dual keyboard and mouse interaction models
- Browser audio restrictions handled through an intentional user gesture
- Web Audio API oscillators for beeps and SID-inspired music
- Responsive CRT presentation using handwritten CSS
- Safe DOM construction for dynamic menus and game instructions
- Query-string state persistence across pages
- Same-origin iframe integration for playable games
- A shared Escape-key contract across independently implemented games
- Clear separation between configurable content and application behavior

Everything is plain HTML, CSS, and JavaScript. There is no virtual DOM, router, component library, CSS framework, bundler, transpiler, or dependency installation.

## Size Budget

Approximate sizes at the time of writing:

| Component | Size | Role |
| --- | ---: | --- |
| `index.html` | 46 KB | Complete BBS shell, styles, state, navigation, audio logic, puzzle, and Pine interface |
| `games.html` | 10 KB | Arcade catalog, instructions, and embedded game player |
| `arcade-return.js` | 0.5 KB | Shared Escape-key return behavior for every game |
| `handshake.mp3` | 96 KB | Deliberately separate modem recording; not included in the source-code comparison |

The arcade games are separate portfolio implementations and are not included in the BBS shell's size claim.

## Arcade

The Games option opens an in-page arcade launcher. Each title has a short description and a controls screen before launch, based on its accompanying README where available.

- Asteroids
- Breakout
- Centipede
- Galaxian
- Space Invaders
- Missile Command
- Pac-Man

Games run inside the arcade page. Press **Escape** from any title to return directly to the BBS main menu.

## Pine-Style Contact Screen

Contact the SYSOP opens a compose screen modeled after the Pine email client. It supports mouse interaction, tab navigation, `Ctrl+G` help, `Ctrl+X` send, and `Escape` to cancel.

**SMTP is intentionally not configured.** This is a static portfolio site, and adding a mail server would introduce credentials, abuse handling, spam controls, data retention, and operational infrastructure that do not improve the interface demonstration. When no contact email is configured, Send directs the visitor to the public LinkedIn contact channel instead.

The empty `contactEmail` setting is therefore deliberate—not an unfinished integration. If an email address is supplied later, the site delegates composition to the visitor's local mail client with a `mailto:` URL; it still does not operate an SMTP service.

## Run Locally

No installation or build is required. Serve the directory with any static web server:

```powershell
cd projects/BBS_Website
python -m http.server 8000
```

Then open [http://localhost:8000](http://localhost:8000).

Serving over HTTP is recommended because the arcade embeds same-origin game pages. The main BBS can also be opened directly from `index.html` in most modern browsers.

## Configuration

External destinations are centralized near the top of the script in `index.html` beneath this marker:

```javascript
// ============================================================
// LINKS: UPDATE EXTERNAL URLS HERE
// ============================================================
```

Update `LINK_CONFIG` to change LinkedIn, GitHub, research, contact, or video destinations. Games are intentionally routed to the local arcade rather than configured as an external link.

## Project Structure

```text
BBS_Website/
├── index.html          # Main BBS experience
├── games.html          # Arcade catalog, instructions, and player
├── arcade-return.js    # Shared Escape-to-menu behavior
├── handshake.mp3       # Modem connection audio
├── links.txt           # Menu-content reference
├── asteroids/
├── breakout/
├── centipede/
├── galaxian/
├── invaders/
├── missle/
└── pacman/
```

## Design Notes

The interface is intentionally desktop-first and keyboard-forward because the constraints are part of the fiction: it is presented as a BBS terminal, not a generic responsive marketing template. Small screens receive a compact set of external portfolio links instead of a compromised version of the terminal sequence.

The visual system uses a C64-inspired palette, scanlines, phosphor glow, screen curvature, and restrained animation. The result aims to feel specific and authored while remaining understandable to visitors who never used an actual BBS.
