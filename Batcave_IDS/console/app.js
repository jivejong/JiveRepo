/*
 * Batcave console frontend (Phase 8, docs/06 Track B, docs/08).
 *
 * Vanilla JS, no framework, no build step - a static file server (or opening
 * index.html directly) is enough. This file is an INPUT DEVICE for the
 * console backend (services/console/app.py), which itself drives the same
 * StageMachine (services/simulator/machine.py) the headless autopilot does.
 * Nothing here computes gating, probability, or outcomes - it only renders
 * what the backend returns and forwards the player's clicks.
 *
 * The one thing this file must never do, under any screen, including a
 * future Phase 10 finale: call `requestFullscreen` or otherwise take over the
 * browser viewport. The #simulation-frame chrome in index.html stays visible
 * at all times; this script only ever replaces the contents of
 * #screen-root.
 */

const API_BASE = "http://localhost:8090";

const screenRoot = document.getElementById("screen-root");

async function api(path, options) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch (_err) {
      /* non-JSON error body, keep statusText */
    }
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children || []) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function mount(...nodes) {
  screenRoot.replaceChildren(...nodes);
}

/* ---------------------------------------------------------------------- */
/* Screen: Welcome (Lex Luthor)                                            */
/* ---------------------------------------------------------------------- */

// Stage 0 is narrative only (docs/08's "Welcome — Lex Luthor" section, and
// its correction: there is no stage-0 row in fct_stage_progression, and
// there must never be one - techniques.csv has zero stage-0 entries and
// assert_stage_monotonic's own comment confirms the attempt log contains
// stages 1-4 only). Each bullet cites a real MITRE ATT&CK reconnaissance
// (TA0043) technique ID in prose, nothing more.
const RECON_BULLETS = [
  ["Enumerated every Wayne Enterprises subdomain and dangling DNS record", "T1590"],
  ["Pulled six years of WHOIS history and three abandoned registrar accounts", "T1596"],
  [
    "Scraped the R&D division's job postings and reconstructed their internal tech stack from the required skills",
    "T1593",
  ],
  ["Harvested 4,100 employee email addresses from conference attendee lists", "T1589"],
  ["Cross-referenced those against every credential dump since 2013", "T1597"],
  ["Identified eleven employees who reuse passwords and two who post their badge photos", "T1593"],
  [
    "Mapped the applied sciences building's contractor list through public procurement filings",
    "T1591",
  ],
  ["Socially engineered a facilities vendor into confirming their HVAC control platform", "T1598"],
  ["Fingerprinted the perimeter: open ports, TLS certificates, server banners, WAF vendor", "T1595"],
  ["Located a forgotten staging environment with directory listing enabled", "T1594"],
  ["Read the metadata off every PDF on the public site, including author names and file paths", "T1592"],
  [
    "Established which of the seventeen Gotham ISPs routes traffic that vanishes into no registered building",
    "T1590",
  ],
  ["Determined that the vanishing traffic terminates somewhere under Wayne Manor", "T1590"],
];

function renderWelcome() {
  const list = el(
    "ul",
    { class: "recon-list" },
    RECON_BULLETS.map(([text, attid]) =>
      el("li", {}, [text, " ", el("span", { class: "attid" }, [`(${attid})`])])
    )
  );

  mount(
    el("div", { class: "panel" }, [
      el("h1", {}, ["Incoming transmission"]),
      el("p", { class: "luthor-note" }, [
        "This is your old friend Lex Luthor.\n\n" +
          "I figured I'd slum around Gotham and do the Rogues Gallery a favor. Consider it charity. " +
          "You people have been throwing yourselves at that cave for decades with crowbars and clown " +
          "makeup, and not one of you thought to simply look first.\n\n" +
          "So I looked. Here is what I have already done for you:",
      ]),
      list,
      el("p", { class: "luthor-note" }, [
        "I would finish the job myself, but I am busy making money and fighting a man who can move " +
          "planets. You have a hammer and a grudge. That should be enough.\n\n" +
          "I left you this application. Try not to embarrass me.",
      ]),
      el("div", { class: "luthor-signature" }, ["— L.L."]),
      el("button", { class: "btn", onclick: renderVillainSelect }, ["Begin intrusion →"]),
    ])
  );
}

/* ---------------------------------------------------------------------- */
/* Screen: Villain select                                                  */
/* ---------------------------------------------------------------------- */

function statBar(label, value) {
  return el("div", { class: "stat-row" }, [
    el("span", { class: "label" }, [label]),
    el("div", { class: "stat-bar" }, [el("span", { style: `width:${value}%` })]),
    el("span", {}, [String(value)]),
  ]);
}

function villainCard(v) {
  const card = el(
    "div",
    { class: "villain-card", onclick: () => startSession(v.slug) },
    [
      el("h3", {}, [v.name]),
      el("div", { class: "archetype" }, [v.archetype]),
      statBar("INT", v.stats.intelligence),
      statBar("STR", v.stats.strength),
      statBar("SPD", v.stats.speed),
      statBar("DUR", v.stats.durability),
      statBar("PWR", v.stats.power),
      statBar("CMB", v.stats.combat),
      el(
        "div",
        { class: "stage-reach" },
        v.stage_reach.map((r) =>
          el("div", { class: `cell${r.available === 0 ? " locked" : ""}` }, [
            `S${r.stage}: ${r.available}/${r.total}`,
          ])
        )
      ),
    ]
  );
  return card;
}

async function renderVillainSelect() {
  mount(el("div", { class: "panel" }, ["Loading the Rogues Gallery…"]));
  let villains;
  try {
    villains = await api("/api/villains");
  } catch (err) {
    return renderApiError(err, renderVillainSelect);
  }

  mount(
    el("div", { class: "panel" }, [
      el("h2", {}, ["Choose your villain"]),
      el("p", {}, [
        "Stat bars are the six powerstats from the vendored roster. The row under each card shows " +
          "how many techniques that villain can reach at each stage, computed live from the same " +
          "gating rules the stage machine enforces — a fully-locked stage means this villain cannot " +
          "get past it no matter what you pick.",
      ]),
      el("div", { class: "villain-grid" }, villains.map(villainCard)),
      el("p", { class: "roster-note" }, [
        "Clayface and Mad Hatter are absent from the vendored character dataset. Man-Bat is present " +
          "in the dataset but excluded here by choice.",
      ]),
    ])
  );
}

async function startSession(slug) {
  mount(el("div", { class: "panel" }, ["Establishing session…"]));
  let session;
  try {
    session = await api("/api/session", {
      method: "POST",
      body: JSON.stringify({ villain_slug: slug }),
    });
  } catch (err) {
    return renderApiError(err, renderVillainSelect);
  }
  renderStage(session, []);
}

/* ---------------------------------------------------------------------- */
/* Screen: Stage terminal                                                  */
/* ---------------------------------------------------------------------- */

// Descriptive only (docs/08's "Parameter controls" section, and docs/07's
// correction): writes into the attempt event's `parameters` field as
// metadata about what the player chose. Has no effect on
// computed_probability or the traffic a technique generates.
const AGGRESSION_OPTIONS = ["measured", "aggressive", "reckless"];

function counterTile(label, value, cls) {
  return el("div", { class: `counter${cls ? ` ${cls}` : ""}` }, [
    el("div", { class: "label" }, [label]),
    el("div", { class: "value" }, [String(value)]),
  ]);
}

function renderCounters(counters) {
  const toleranceCls =
    counters.remaining_failure_tolerance <= 1
      ? "danger"
      : counters.remaining_failure_tolerance <= 3
        ? "warn"
        : "";
  const detectedCls = counters.detected_tally > 0 ? "warn" : "";
  return el("div", { class: "counters" }, [
    counterTile("Attempts", `${counters.attempt_seq} / ${counters.max_attempts}`),
    counterTile("Requests sent", counters.requests_sent),
    counterTile("Noise", counters.noise_generated),
    counterTile("Elapsed (s)", counters.elapsed_s),
    counterTile(
      "Tolerance left",
      `${counters.remaining_failure_tolerance} / ${counters.failure_tolerance}`,
      toleranceCls
    ),
    counterTile("Detected", counters.detected_tally, detectedCls),
  ]);
}

function logLine(entry) {
  const cls = `outcome-${entry.outcome}`;
  return el("div", { class: "line" }, [
    el("span", { class: cls }, [
      `[stage ${entry.stage}] ${entry.decision} ${entry.technique_id} (${entry.attack_id}) → ` +
        `roll ${entry.roll.toFixed(2)} vs p=${entry.computed_probability.toFixed(2)} → ${entry.outcome.toUpperCase()}` +
        (entry.noise_generated ? ` (noise ${entry.noise_generated})` : ""),
    ]),
  ]);
}

function techniqueRow(session, t, locked) {
  const attemptFn = () => submitAttempt(session, t.technique_id, aggressionSelectValue(t.technique_id));
  const controls = locked
    ? el("span", { class: "lock-reason" }, [
        t.failing_gates
          .map((g) => `needs ${g.stat} ≥ ${g.required} (has ${g.have})`)
          .join(", "),
      ])
    : el("div", { class: "param-row" }, [
        el(
          "select",
          { id: `aggr-${t.technique_id}` },
          AGGRESSION_OPTIONS.map((o) => el("option", { value: o }, [o]))
        ),
        el("button", { class: "btn", onclick: attemptFn }, ["Attempt"]),
      ]);

  return el("li", { class: locked ? "locked" : "" }, [
    el("div", {}, [
      el("div", { class: "name" }, [t.display_name]),
      el("div", { class: "meta" }, [
        `${t.attack_id} · observability: ${t.observability} · ${t.detection_signature}`,
      ]),
    ]),
    controls,
  ]);
}

function aggressionSelectValue(techniqueId) {
  const node = document.getElementById(`aggr-${techniqueId}`);
  return node ? node.value : "measured";
}

let logHistory = [];

function renderStage(session, previousLog) {
  logHistory = previousLog;

  if (session.finished) {
    return renderRunComplete(session);
  }

  const stage = session.stage;
  mount(
    el("div", { class: "panel" }, [
      el("div", { class: "stage-header" }, [
        el("h2", {}, [`Stage ${stage.stage_num}: ${stage.stage_name}`]),
        el("span", {}, [session.villain_slug]),
      ]),
      renderCounters(session.counters),
      el("h3", {}, ["Techniques"]),
      el(
        "ul",
        { class: "technique-list" },
        [
          ...stage.available.map((t) => techniqueRow(session, t, false)),
          ...stage.locked.map((t) => techniqueRow(session, t, true)),
        ]
      ),
      el("h3", {}, ["Log"]),
      el(
        "div",
        { id: "log-panel" },
        logHistory.length
          ? logHistory.map(logLine)
          : [el("div", { class: "line" }, ["(no attempts yet)"])]
      ),
    ])
  );

  const panel = document.getElementById("log-panel");
  if (panel) panel.scrollTop = panel.scrollHeight;
}

async function submitAttempt(session, techniqueId, aggression) {
  const buttons = screenRoot.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  let result;
  try {
    result = await api(`/api/session/${session.console_session_id}/attempt`, {
      method: "POST",
      body: JSON.stringify({ technique_id: techniqueId, parameters: { aggression } }),
    });
  } catch (err) {
    return renderApiError(err, () => renderStage(session, logHistory));
  }
  const nextLog = [...logHistory, result.event];
  renderStage(result.session, nextLog);
}

/* ---------------------------------------------------------------------- */
/* Screen: Run complete                                                    */
/* ---------------------------------------------------------------------- */

function renderRunComplete(session) {
  const cls = session.run_outcome === "cleared" ? "result-cleared" : "result-stalled";
  mount(
    el("div", { class: "panel" }, [
      el("h2", { class: cls }, [
        session.run_outcome === "cleared" ? "Run complete: CLEARED" : "Run complete: STALLED",
      ]),
      el("p", {}, [
        `${session.villain_slug} reached stage ${session.max_stage_reached} of 4.`,
      ]),
      renderCounters(session.counters),
      el("p", {}, [
        "Bat bot's probing conversation (Phase 9) and the Batcomputer's counterstrike readout " +
          "(Phase 10) land later in Track B. This build ends the interactive run here.",
      ]),
      el("button", { class: "btn", onclick: renderVillainSelect }, ["Play again"]),
    ])
  );
}

/* ---------------------------------------------------------------------- */
/* Errors                                                                   */
/* ---------------------------------------------------------------------- */

function renderApiError(err, retry) {
  mount(
    el("div", { class: "panel" }, [
      el("div", { class: "error-banner" }, [
        `Console backend error (${err.status || "network"}): ${err.message}. Is \`make console\` ` +
          "running, and is the honeypot/Redpanda stack up (\`make dev-up\`)?",
      ]),
      el("button", { class: "btn secondary", onclick: retry }, ["Retry"]),
    ])
  );
}

renderWelcome();
