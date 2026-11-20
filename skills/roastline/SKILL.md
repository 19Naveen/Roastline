---
name: roastline
description: Use when the user asks for personalized statusline roasts, wants to add or preview jokes based on their coding habits, or wants to check, pause, resume, or change the tone of Roastline. Do not turn routine coding requests into roasts unless the user asks.
argument-hint: "[add | status | pause | resume | sfw | nsfw]"
---

# Roastline

Roastline rotates one reason-first joke at a time in the Claude Code statusline. The local
renderer already reacts to behavior such as caps lock, retries, short prompts, and long tool
runs. Use this skill when the user wants jokes that sound like they were written for them.

## Manage the installation

The installed helper is `<Claude config dir>/roastline/install.py`. The config directory is
`CLAUDE_CONFIG_DIR` when set, or `~/.claude` otherwise. Use Python 3.8+ and call it with `-I`.
For example, in Bash:

```bash
python3 -I "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/roastline/install.py" --status
```

- `status`, `pause`, and `resume` mean `--status`, `--pause`, and `--resume`.
- An explicit `sfw` or `nsfw` request means `--tone sfw` or `--tone nsfw`. Never infer
  consent for the NSFW setting from the user's language.
- Never edit Claude settings, the package, or the personal roast bank directly. If the
  helper is missing, explain how to install Roastline.

## Add several personal roasts

1. Read the active tone with `--status` and list the valid trigger keys with `--triggers`.
   Use only patterns visible in the current conversation or facts the user explicitly
   volunteers. Don't open transcripts, history, or unrelated files to look for material.
2. Pick a trigger that actually fits. If no specific trigger is reliable, choose `default`.
   Common keys: `caps`, `short`, `retry`, `tools`, `burst`, `flood`, `effort`, `late_night`.
3. Draft 2–5 distinct one-line punchlines for that trigger. The renderer adds the reason,
   so don't write the reason or a label in the joke. Keep each to roughly 75 characters.
   Roast a coding behavior or habit, not a sensitive trait, the user's character, or a
   real third party. Stay clean in SFW; in NSFW, profanity is okay, not slurs or threats.
   Don't quote prompts, names, emails, credentials, URLs, paths, customer details, or code.
4. Show the exact lines and ask whether to save them. If the user approves, JSON-encode
   the list and pass it on stdin to `--add-roasts <trigger>`. Treat joke text as data;
   never interpolate it into a shell command. In Bash use a single-quoted here-doc
   delimiter that is not present in the JSON. Use the native equivalent in PowerShell.

```bash
python3 -I "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/roastline/install.py" --add-roasts retry <<'ROASTLINE_JSON'
["Your prompt has an encore but the spec skipped rehearsal.", "That's not iteration. That's a rerun with better timing."]
ROASTLINE_JSON
```

The helper validates, deduplicates, and saves the approved jokes locally under the active
tone. They appear only when their trigger wins. Don't claim a joke was saved until the
helper reports success.
