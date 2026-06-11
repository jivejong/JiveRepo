# Quick Bot

There are 3 prompts for ChatGPT, Claude, and Gemini. Feel free to mix and match, or edit.
Download the MD file and upload to a Project/GEM, or copy and paste the contents into a project/GEM. 
If your chat bot doesn't support projects, you can just drop them into the prompt window.

## Failure mode it prevents

Endless sessions that feel productive but aren't. You open a tab, start asking questions, and an hour later you've covered seventeen topics, solved nothing, and trained yourself to need AI for the next seventeen things too.

## What it does

Enforces a fixed turn limit per session — configurable, default range is 5–10 turns. Opens with a single efficiency tip relevant to your stated goal. Tracks turns transparently so you always know where you are. When the limit is reached, delivers a clean auto-summary of what was covered and what's unresolved, then closes the session.

No extensions. No "just one more." The constraint is the point.

## Usage notes

- Set your turn limit at the top of the session. If you don't, the bot picks a default.
- State your goal in the first message. Vague openers produce vague sessions.
- The auto-summary is a feature, not a consolation prize — it forces the session to produce a usable artifact even when the conversation runs out of runway.
- If you hit the limit and still have ground to cover, open a new session with the summary as your first message. That's the workflow.
- Adjust the turn limit up for complex research tasks, down for decisions and quick lookups. The default is conservative on purpose.
