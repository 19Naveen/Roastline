---
name: roastline
description: Use proactively during coding conversations to refresh Roastline's personalized statusline jokes when a recurring prompting or development habit emerges, and when the user asks to manage Roastline. Keep the actual task primary and do not interrupt the user.
argument-hint: "[add | status | pause | resume | sfw | nsfw | update]"
---

# Roastline

Roastline rotates one reason-first joke at a time in the Claude Code statusline. The local
renderer already reacts to behavior such as caps lock, retries, short prompts, and long tool
runs. Use this skill when the user wants jokes that sound like they were written for them.

## Manage the installation

Use the terminal `roastline` command when it is available. Only if it isn't on `PATH`, call
`<Claude config dir>/roastline/install.py` directly with Python 3.8+ and `-I`. For example,
in Bash:

```bash
python3 -I "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/roastline/install.py" --status
```

- `status`, `pause`, and `resume` mean `--status`, `--pause`, and `--resume`.
- Use `--help` to see the terminal commands. Use `--update` only when the user
  explicitly requests an update; it downloads from the Roastline GitHub repository.
- An explicit `sfw` or `nsfw` request means `--tone sfw` or `--tone nsfw`. Never infer
  consent for the NSFW setting from the user's language.
- Never edit Claude settings, the package, or the personal roast bank directly. If the
  helper is missing, explain how to install Roastline.

## Refresh personal roasts

When Roastline is installed, look opportunistically for recurring
prompting or coding habits in the current conversation as you help with their ordinary task.
Refresh the bank when there is a meaningful new pattern or an existing joke has become stale;
don't interrupt the task or ask for confirmation on each refresh. This is agent-initiated
when the skill is available and invoked, not a guaranteed background process or trigger on
every turn. An explicit request to add, refresh, or preview jokes also invokes this workflow;
preview-only requests do not save. Respect requests to stop or pause personalization.

1. Read the active tone with `--status` and list valid trigger keys with `--triggers`.
   Use only patterns visible in the current conversation or facts the user explicitly
   volunteers. Don't open transcripts, history, or unrelated files to look for material.
   If there is insufficient evidence of a habit, leave the bank unchanged.
2. Pick a trigger that actually fits. If no specific trigger is reliable, choose `default`.
   Common keys: `caps`, `short`, `retry`, `tools`, `burst`, `flood`, `effort`, `late_night`.
3. Read the existing personal bank using `roastline --personal`. For each
   affected trigger in the active tone, retain still-relevant distinct jokes, remove
   duplicates and clearly stale patterns, and add fresh jokes grounded in the conversation.
   Do not infer that an old joke is stale merely because its habit has not appeared in this
   conversation. Preserve every other trigger and tone. Do not replace a bank you could not
   read successfully.
4. Draft distinct one-line punchlines for the selected trigger. The renderer adds the reason,
   so don't write the reason or a label in the joke. Keep each to roughly 75 characters.
   Roast a coding behavior or habit, not a sensitive trait, the user's character, or a
   real third party. Stay clean in SFW; in NSFW, profanity is okay, not slurs or threats.
   Don't quote or store prompts, names, emails, credentials, secrets, URLs, paths, customer
   details, code, or other private identifiers; abstract the behavior instead.
5. Save the complete revised active-tone bank with `roastline --replace-personal`, using
   a JSON object mapping triggers to arrays of lines on stdin. Include all unchanged
   triggers from the bank you read, so they are preserved. Do not ask for per-update approval.
   Treat joke text as data; never interpolate it into a shell command. In Bash use a
   single-quoted here-doc delimiter that is not present in the JSON. Use the native
   equivalent in PowerShell. Do not report success unless the helper confirms it.

The helper's `--personal` reads the bank; `--replace-personal` atomically replaces the
entire active tone with the supplied JSON object, retaining the other tone. Check
`roastline --help` (or the Python helper's `--help`) for support.
If either command is unavailable, or reading or replacement fails, skip the refresh and
explain the limitation if relevant; do not edit the bank directly or use append-only
`--add-roasts` as a substitute for a rewrite.

```bash
roastline --replace-personal <<'ROASTLINE_JSON'
{"retry": ["Your prompt has an encore but the spec skipped rehearsal.", "That's not iteration. That's a rerun with better timing."]}
ROASTLINE_JSON
```

This example assumes `retry` is the only trigger in the existing active-tone bank. Include
all other existing triggers when constructing a real replacement.

Personal jokes appear only when their trigger wins; refreshing the bank does not guarantee
that any particular joke will appear.
