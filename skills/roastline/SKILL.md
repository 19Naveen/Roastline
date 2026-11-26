---
name: roastline
description: Use when a Roastline prompt hook offers a token for a one-off statusline roast or for saving a reusable event joke, or when the user asks to manage Roastline or refresh their saved jokes. Keep the actual task primary and do not interrupt the user.
argument-hint: "[add | status | pause | resume | sfw | nsfw | update]"
---

# Roastline

Roastline shows at most one joke at a time in the Claude Code statusline, never in the
chat. There are two kinds, and you write the personal ones:

- **Chat roasts** are one-off lines about this conversation. You decide if one is earned.
- **Event jokes** are stock lines (plus any the user has saved) that Roastline shows when
  an event happens. You can grow the saved ones from what you see in the user's habits.

## One-off chat roasts

Use this workflow only for a fresh token from the Roastline hook on the current turn.
The token means a roast is available. You decide if the conversation earned one.
Do the user's work first. If there's a specific, funny moment in the conversation,
write one short punchline about the behavior, not the person. Repeatedly asking for a
shorter version of an already short answer may be funny; asking for a summary once
is not. If the joke feels forced, skip it.

- Use only context already visible in this conversation. Respect requests to stop.
  Don't read history, files or transcripts for material.
- Use the tone in the hook, keep the line to about 75 characters, and omit any label.
  NSFW can use profanity, but no slurs, threats, or attacks on sensitive traits.
  Don't quote prompts, code, names, links, paths, credentials, or other identifiers.
- Send the line as a **JSON string on stdin**, using the exact token from the hook.
  Treat the joke as data, not shell code. Submit at most once and do not retry a
  rejected or expired token. Skip if the tool isn't available or needs approval;
  never interrupt the user for a joke. Don't mention it in the reply.

```bash
roastline --session-roast TOKEN_FROM_CURRENT_HOOK <<'ROASTLINE_JSON'
"A TL;DR of a TL;DR. We’re compressing air now."
ROASTLINE_JSON
```

A chat roast is never saved. It disappears at the user's next message.

## Saving event jokes

Event jokes make Roastline's stock lines personal. When a habit of the user's has
really recurred in this conversation and you have a genuinely good line for one of the
events below, save it. Roastline then mixes it in with the stock lines the next time
that event happens. A one-off, a forced line or a repeat of something already saved is
a reason to save nothing.

| Event | When it shows |
|---|---|
| `effort` | the user changes the reasoning level |
| `usage_half`, `usage_high`, `usage_critical`, `usage_full` | context, 5-hour or weekly usage reaches 50%, 80%, 95%, 100% |
| `question_wait`, `question_late`, `question_ghosted` | your question is still unanswered after 1, 5, 30 minutes |
| `idle`, `idle_long` | the session sits idle for 15, 60 minutes |

Write the punchline about the event, shaped by the habit. For example, if "one more
thing" keeps growing the scope, a `usage_full` line could be *"The AI survived the
task. 'One more thing' is what killed it."* Don't write the reason or a label; Roastline
adds it.

- Save one joke (up to five) with `roastline --add-roasts EVENT`, as a **JSON array
  on stdin**, in the active tone. Use `roastline --status` to read the tone.
- Same rules as chat roasts: use only the visible conversation, keep it to about 75
  characters, no profanity in SFW, no slurs or threats in NSFW, nothing about the
  person's character, and no quotes, names, links, paths, code or other identifiers.
- Do it quietly after the user's request is done. Never mention it, never ask for
  approval, and treat the joke as data, not shell code. If the command fails or needs
  approval, skip it; don't retry or interrupt the user.

```bash
roastline --add-roasts usage_full <<'ROASTLINE_JSON'
["The AI survived the task. 'One more thing' is what killed it."]
ROASTLINE_JSON
```

Roastline keeps the 20 most recent saved jokes per event and tone, and ignores
duplicates of stock or saved lines.

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
- Never edit Claude settings, the package, or the saved jokes directly. If the
  helper is missing, explain how to install Roastline.

## Refresh saved jokes on request

Use this workflow only when the user explicitly asks to add, refresh, or preview their
saved jokes. Preview-only requests do not save. Automatic chat roasts never touch the
saved jokes.

1. Read the active tone with `--status` and list the events with `--triggers`.
2. Read the saved jokes with `roastline --personal`. For each event you'll change,
   keep still-relevant distinct jokes, remove duplicates and clearly stale ones, and
   add fresh ones grounded in the conversation. Don't call a joke stale just because
   its habit hasn't appeared in this conversation. If you can't read the saved jokes,
   don't replace them.
3. Write distinct one-line punchlines under the same rules as above.
4. Save the complete revised list for the active tone with `roastline --replace-personal`,
   as a JSON object mapping **events** to arrays of lines on stdin. Include every
   unchanged event from what you read. Jokes saved for triggers from older Roastline
   versions are no longer played; the helper keeps them untouched, so leave them out.
   Use a single-quoted here-doc delimiter that isn't in the JSON, and only report
   success if the helper confirms it.

```bash
roastline --replace-personal <<'ROASTLINE_JSON'
{"effort": ["Your reasoning dial has more mood swings than the roadmap."]}
ROASTLINE_JSON
```

This example assumes `effort` is the only event with saved jokes. Include all the others
when constructing a real replacement.
