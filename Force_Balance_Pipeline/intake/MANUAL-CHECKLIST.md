# Manual browser check of the web intake page

What this proves: the page, the browser queue and the intake server work together in a real browser, including the failure paths that
the automated tests only simulate. It runs ONLY against a throwaway broker container and the STUB model. No model call, no key, no
quota, and nothing reaches the live broker or production bronze.

You run it and paste the results (the table at the end) back to me. Allow about 45 minutes; step 17 is optional and takes about 13.

## Rules for this run

- Use only the page the script prints (`http://127.0.0.1:8081/`). Do not point the page, or any report, at the live intake or broker.
- The stub model answers by keyword: a dark side name (Vader, Sith, Darth...) scores 0.95; Force talk (Jedi, lightsaber, force...) 0.70;
  anything else 0.04. Words in square brackets are triggers: `[stub:503]` `[stub:bad]` `[stub:blocked]` `[stub:slow]` `[stub:html]`.
- Each reporter may file **4 accepted reports** per day in this run (the real limit is 20). A report the server refuses or fails does
  not count. When a step says "new identity", run this in the browser console and reload:
  `localStorage.removeItem("fbp.reporter_id"); location.reload()`
- The queue sends one report at a time, oldest first. A report that is waiting to be retried holds back the reports filed after it.
  Steps that leave a report waiting are in a private window (steps 14-17), which has its own queue and identity.
- Keep a tally of "Report filed" messages (accepted reports). Step 18 checks it against what landed.
- DevTools: F12. Network tab -> throttling menu -> "Offline" / "No throttling". Application tab -> IndexedDB -> fbp-intake -> queue;
  Local Storage -> `http://127.0.0.1:8081`.

## Setup

1. In a terminal at the repo's `Force_Balance_Pipeline` folder:

       edge\.venv\Scripts\python.exe intake\manual_check.py

   (Optional first: `... intake\manual_check.py --self-test` drives the same setup over HTTP for a minute and cleans up.)
2. The banner must say: page `http://127.0.0.1:8081/`, throwaway container `fbp-e2e-manual` on port `18885`, model "the STUB". If it
   shows any other broker or port, press Ctrl+C and tell me. Leave the terminal visible: it prints `[landed] ...` for each file the
   bridge writes (a few seconds after each accepted report).
3. In your browser: open `http://127.0.0.1:8081/`, then DevTools -> Application -> Storage -> "Clear site data" for this origin, and
   reload. (An earlier run can leave a waiting report in the browser's IndexedDB.)

## Steps

Mark each step PASS or FAIL in the table at the end, with a few words on anything unexpected.

### A. The page and a report

**1. Page.** Reload. Expect: the planet list has 60 planets in alphabetical order after "Choose a planet"; the counter reads `0 / 8000`;
the text "Choose a planet to see its recent reports." and "Nothing is waiting to be sent."; DevTools Console has no errors and no
"Content Security Policy" messages. Network tab: every request goes to `127.0.0.1:8081` (the page, `/static/main.js` and the other
modules, `style.css`, `/api/sectors`, `/api/reports?sector_id=`). Click the first request (`/`) -> Headers: `Content-Security-Policy:
default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri
'none'; form-action 'self'`, and `Cache-Control: no-store`.

**2. A dark side report.** Pick Coruscant, type `Saw Darth Vader walking through the market`, press Send report. Expect: the message
"Report filed."; a "Report filed" panel with Relevance 0.95, Midichlorian density 26400 ppm, Kyber resonance 30.0, Dark side activity
85.0, and "Why: manual-check stub: a named dark side figure"; the text box is empty; the recent list for Coruscant shows your text with
`relevance 0.95 | dark side 85.0`; the terminal prints a `[landed]` line within about 5 seconds. Application -> IndexedDB -> queue is empty;
Local Storage has `fbp.reporter_id` = 12 characters of a-z and 0-9. (Tally: 1.)

**3. The other scores.** Send `The weather was mild and the market was busy` (expect relevance 0.04, 1500 ppm, 12.0, 5.0) and
`A Jedi with a lightsaber on the bridge` (expect 0.70, 9000 ppm, 70.0, 20.0). Both appear in the recent list, newest first. (Tally: 3.)

**4. Planets and reload.** Choose Tatooine: the recent list says "No reports for this planet yet." Choose Coruscant: your three reports
are back. Reload: the planet resets to "Choose a planet", and `fbp.reporter_id` is unchanged.

**5. Checks before sending.** With no planet chosen and some text: "Choose a planet first." With a planet and only spaces: "Describe what
you saw first." Run `copy("a".repeat(9000))` in the console, paste into the box: the box holds 8000 characters and the counter reads
`8000 / 8000`. Nothing is sent in any of these (Network tab shows no `/api/report` request).

### B. Offline

**6. Offline queue.** Network -> "Offline". Send `Saw Darth Vader while the network was down`. Expect: the text box empties at once; the
message "The report could not be sent yet. It is saved on this device and will be tried again in 5 seconds (attempt 1 of 5)."; the "On
this device" panel: "1 report is waiting to be sent. Next attempt in ..." and an entry "coruscant: queued"; IndexedDB has one entry
with `attempts` 1. Wait about 40 seconds: the message moves to "attempt 2 of 5" (in 30 seconds). Set throttling back to "No
throttling": within about 30 seconds the report is sent ("Report filed.", the panel shows "Nothing is waiting to be sent.", the
IndexedDB entry is gone, a `[landed]` line appears) and it is in the recent list **once**. (Tally: 4. The cap for this identity is now
used up.)

**New identity** (console one-liner above, then reload).

### C. Text handling

**7. Hostile text.** Send exactly: `<img src=x onerror=alert(1)> <script>alert(2)</script> "quotes" & [stub:html]`. Expect: no alert box
appears; the recent list shows the text literally (angle brackets visible); the confirmation's "Why:" line shows the HTML text
literally too (`<b>bold</b> <img src=x onerror=alert(1)> ...`) and nothing is bold; Elements tab: no `<img>` or `<script>` element
inside `#confirmation` or `#recent-list`. (Tally: 5.)

**8. Unicode and lines.** In the console run `copy("café — Coruscant\n🌑\n日本語")` and paste into the box (three lines: an accent and an
em dash, a moon emoji, Japanese characters); the counter should read `22 / 8000` (the emoji counts as one character, each line break as one). Send it. Expect:
the recent list shows all three lines, in order, nothing garbled. (Tally: 6.)

### D. Refusals

**9. A report the model refuses.** Send `This will be refused [stub:blocked]`. Expect: the message "This report could not be assessed. Try
describing what you saw in different words."; the panel shows an entry "coruscant: dropped" with a Dismiss button and **no** retry
(the terminal shows no new `[landed]` line; `intake.log` in the folder the banner printed has one `report refused: blocked_by_model`
line for it, not several; the Network tab shows exactly one `/api/report` request). Click Dismiss: the entry goes. (Not tallied.)

### E. A slow server, two tabs

**10. The 60 second timeout.** Send `A slow answer [stub:slow]`. Expect: nothing happens for 60 seconds (the box is empty and Send is
enabled again at once); at 60 seconds the Network tab shows the request cancelled and the message "The report could not be sent yet ...
(attempt 1 of 5)"; about 5 seconds later (the server finishes at 65 seconds) the next request returns 200 and the page shows "Report
filed." with the same values as the stub gives for text without a keyword (0.04). Exactly **one** `[landed]` line for it. The terminal or
`intake.log` may show a connection error when the server tried to answer the cancelled request; that is expected. (Tally: 7.)

**11. Two tabs.** Open a second tab on the same page. In tab 1 set "Offline" and send `Saw Darth Vader, two tabs`. Switch to tab 2 (online; switching
tabs makes the page look at the shared queue) and wait up to 30 seconds. Expect: tab 2's panel shows the waiting report and then sends
it (tab 2 shows "Report filed."); set tab 1 back to "No throttling" and switch to it: its panel ends as "Nothing is waiting to be sent."
and **no second** copy of the report is filed (one `[landed]` line, one entry in the recent list). (Tally: 8.)

**New identity.**

### F. Broker down, and the daily cap

**12. The broker stops.** In another terminal: `docker stop fbp-e2e-manual`. Send `Saw Darth Vader, broker down`. Expect: "The report
could not be sent yet ... in 15 seconds (attempt 1 of 5)" (the server answered 503 and the page waited at least its Retry-After).
Within the next 30 seconds run `docker start fbp-e2e-manual`. Expect: within about two minutes "Report filed.", a `[landed]` line, and the
report once in the recent list. (Tally: 9. This identity has 1 of 4.)

**13. The daily cap.** Send `A report about nothing in particular`, then `The Force is strong here`, then `Another quiet day`
(these are this identity's 2nd, 3rd and 4th). Then send a fifth, `One report too many`. Expect: the message "You have filed as many
reports as are allowed today. Try again tomorrow. It stays on this device and will be sent in about N hours." (N = hours until the next
UTC midnight); the panel: "1 report is waiting to be sent. Next attempt in about N hours." with "coruscant: queued"; no Dismiss
button; IndexedDB `attempts` is 0 (a refusal for the daily cap is not an attempt). No `[landed]` line for it. (Tally: 12.)

### G. A private window (fresh identity and queue)

Open a private/incognito window on `http://127.0.0.1:8081/`.

**14. It works.** Choose Coruscant and send `Saw Darth Vader in a private window`. Expect "Report filed." In DevTools the
`fbp.reporter_id` differs from the normal window's. Note what the "On this device" panel says: if the browser blocks IndexedDB in private
windows it reads "This browser cannot keep reports between visits: closing this tab would lose a waiting report."; write down which
you saw and the browser name. (Tally: 13.)

**15. The model is unavailable.** Send `The model is down [stub:503]`. Expect: "The report could not be sent yet ... in 30 seconds
(attempt 1 of 5)" (the server's Retry-After of 30 seconds is longer than the page's own 5 second step); "1 report is waiting";
after 30 seconds "attempt 2 of 5" (in 30 seconds), then "attempt 3 of 5" (in 2 minutes).

**16. A waiting report survives a reload.** While step 15's report is waiting (and only if step 14 showed IndexedDB working), reload the
page. Expect: the panel still shows "1 report is waiting to be sent", the entry still shows its last message (for example "attempt 2
of 5"), and the retries carry on from that count: the next message names the next number, not attempt 1.

**17. (Optional, about 13 minutes) Giving up.** Leave step 15's report waiting. Expect attempt 4 two minutes after attempt 3, attempt 5
ten minutes after that, and then the message "This report could not be assessed." with the entry "coruscant: failed" and a Dismiss
button; no further requests (Network tab). Click Dismiss. Close the private window.

### H. Audit and clean-up

**18. What landed.** In PowerShell (use the "Landing folder" from the banner):

    $landing = "<the Landing folder from the banner>"
    Get-ChildItem -Recurse $landing -Filter *.ndjson | Get-Content | Measure-Object -Line
    Get-ChildItem -Recurse $landing -Filter *.ndjson | Get-Content | ForEach-Object { ($_ | ConvertFrom-Json).event_id } | Group-Object | Where-Object Count -gt 1

Expect: the line count equals your tally of "Report filed" messages (13 if you did every step once), and the second command prints
nothing (no event_id landed twice).

**19. Clean-up.** Ctrl+C in the terminal. Expect "removed container fbp-e2e-manual". `docker ps -a` shows no `fbp-e2e-manual`. In the
browser, clear site data for `http://127.0.0.1:8081` again (a report waiting for tomorrow, from step 13, would otherwise be sent by the
next run). Confirm `force-mosquitto` was never stopped: `docker ps` still lists it as Up.

## Results (paste this table back)

Browser and version: ______   Date: ______   Number of "Report filed" messages (tally): ______

| Step | PASS / FAIL | What you saw (anything unexpected) |
|------|-------------|------------------------------------|
| 1 Page, CSP header | | |
| 2 Dark side report | | |
| 3 Other scores | | |
| 4 Planets, reload | | |
| 5 Checks before sending | | |
| 6 Offline queue | | |
| 7 Hostile text | | |
| 8 Unicode and lines | | |
| 9 Refused report | | |
| 10 60 second timeout | | |
| 11 Two tabs | | |
| 12 Broker stops | | |
| 13 Daily cap | | |
| 14 Private window | | |
| 15 Model unavailable | | |
| 16 Reload while waiting | | |
| 17 Giving up (optional) | | |
| 18 Landing audit (line count; duplicate event_ids) | | |
| 19 Clean-up | | |
