# Demo runbook (for the day 6 video)

Goal: show in about 3 minutes that Errand builds real automations from plain English,
that a human approves each one, that it repairs itself, and that the Sprite keeps it
contained. Use the public URL in a clean browser window, logged in.

## Before you record

1. Log in: run the login-link command and open the link.
2. Make sure the cron job calls `/tick` (Settings shows nothing for this; check the log).
3. Discard old drafts that you do not want on screen.
4. Keep a terminal open in the errand folder for `scripts/egress.sh`.

## Scene 1: a new errand (about 60 s)

1. On the home page, type: "Every weekday at 8:00, email me the top 5 Hacker News
   stories with one line on each."
2. Show the steps as they appear: connectors, peek, write, dry run.
3. Open "Last dry run": the email text is there, with "Email was not done".
4. Point at the proposal: when it runs, what it uses, the time limit.
5. Click Approve and install. Show the version and the checkpoint.

## Scene 2: the Sprite keeps it contained (about 45 s)

1. Ask for: "Every Monday at 09:00, email me the Wikipedia featured article of the day."
2. If en.wikipedia.org is already allowed, remove it first: `scripts/egress.sh sync`
   after you delete it from `egress_allow` (or record this scene before you allow it).
3. Errand stops and gives the command. Say: the Sprite cannot change its own network
   policy, so the agent cannot get around this.
4. Run the command in the terminal, reply "continue", and show the proposal.

## Scene 3: self-repair (about 45 s)

1. Send a payload that the star alert does not expect, for example a JSON list, to its
   webhook URL with curl.
2. The run fails. Errand starts a fix by itself and emails a link.
3. Open the fix: the run output, the new code, the passed dry run. Approve and update.
4. Send a real GitHub star payload. The run passes and the email goes out.

## Scene 4: undo (about 20 s)

1. On the errand page, show Versions and Roll back.
2. In Settings, show the checkpoints that Errand saved before each change.

## What to say in the submission (1-2 sentences)

Errand turns plain English into small automations that run on your own Sprite: an agent
writes the script against the Sprites connectors, tests it with a dry run, and installs
it only after you approve. When a run fails, it prepares a fix, and the Sprite's network
policy keeps every errand to the sites you allowed.
