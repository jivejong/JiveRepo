# South Park: Town Hall Debate

A Streamlit multi-agent debate simulator. The user selects two characters and a number of rounds; a generated town-hall topic, character arguments, three independent judge evaluations, and a final announcement are produced step by step.

## Debate orchestration

1. Mayor McDaniels generates a family-friendly, absurd topic.
2. One contender calls heads or tails. The winner chooses FOR or AGAINST, while the loser takes the opposite stance and opens the debate.
3. Chef, Mr. Mackey, and Wendy independently assess the transcript.
4. Terrance and Phillip synthesize the judges' conclusions into the final announcement.

The character roster contains more than 25 scoped persona prompts. System prompts are distinct by role: combatant, moderator, judge, and announcer. A completed debate is saved in session state and can be re-rendered without another model request.

## Cost model and telemetry

Each debate uses `5 + (2 x rounds)` Gemini calls: one new topic for the debate, two arguments per round, three judges, and one announcer. Before a run starts, the app checks that the whole debate fits the remaining quota.

The sidebar displays live OpenTelemetry-backed information for every model call, including agent, role, latency, status, and token counts. The current implementation uses an in-memory exporter for that display.

## Setup

```powershell
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
streamlit run app.py
```

Set `GEMINI_API_KEY` and strong access codes in the copied file. The app uses `gemini-3.1-flash-lite`. Keep the real secrets file local or in your deployment's protected secret store.

## Access and quota behavior

The access-code screen precedes client and telemetry initialization. The configured `owner` identity is unlimited; other identities use `limits.max_billable_operations_per_session` (default: 20). The app reserves one quota unit per model call and blocks overlapping calls.

The quota is intentionally session-scoped for a private demo; a new browser session can receive a new allowance. It is not a replacement for user accounts, persistent auditing, revocation, rate limiting, or provider-side spend controls.

## Project layout

```text
app.py                  debate orchestration, Gemini calls, and live telemetry UI
security.py             access gate, capacity checks, and session controls
requirements.txt        Streamlit, Gemini, and OpenTelemetry dependencies
tests/test_security.py  unit coverage for access and quota behavior
.streamlit/             local secrets and the tracked safe template
```

## Concepts demonstrated

- Multi-agent role separation and transcript handoffs
- Deterministic call budgeting for a variable-length workflow
- Session-safe rendering that avoids duplicate inference
- In-app OpenTelemetry visibility for agent calls
