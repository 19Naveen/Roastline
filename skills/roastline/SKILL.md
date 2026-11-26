---
name: roastline
description: Use when a Roastline prompt hook offers a token for a one-off statusline roast or for saving a reusable event joke, or when the user asks to manage Roastline or refresh their saved jokes. Keep the actual task primary and do not interrupt the user.
argument-hint: "[add | status | pause | resume | sfw | nsfw | update]"
---

# Roastline

Roastline shows at most one joke at a time in the Claude Code statusline, never in the
chat. There are two kinds, and you write the personal ones:

- **Chat roasts** are one-off lines about this conversation, submitted early in the turn.
- **Event jokes** are stock lines (plus any the user has saved) that Roastline shows when
  an event happens. You can grow the saved ones from what you see in the user's habits.

## One-off chat roasts

On each user message the Roastline hook gives you a token and the exact command. This
section is the longer version of that note.

**When.** Submit before your first reply or tool call, alongside your first tool call is
fine, so the joke is on the statusline while you work. Then carry on with the task as
normal. If the task drags on, you may replace it once with a fresh line about how long
it's taking (same command, same token); a third submission is refused. Skip it for a
trivial message ("ok", "yes"), if the user is stressed or chasing an incident, or if they
asked for no jokes.

**What.** One funny roast, about 75 characters, about what is really going on:

- *Prompting:* "Seven 'quick' follow-ups later, this prompt has a sequel and a spin-off."
- *The project:* "A 'quick fix' that already has a roadmap and a steering committee."
- *The task or bug:* "The bug vanished the moment I opened the debugger. Shy, not fixed."
- *The tech stack:* "Fourteen microservices to render one button. The button is flattered."
- *A long task, mid-way:* "I could've built the Great Wall of China in the time this refactor is taking."

In the NSFW tone the joke **must swear**; a clean one is rejected. Put real profanity
in the punchline:

- "This 'tiny tweak' touches fourteen fucking files. Ambitious little shit."
- "I could've built the fucking Great Wall of China by now."
- "The bug vanished when I opened the debugger. Shy bastard."
- "Two inputs both named Description, and nobody knows which is which. Fucking brilliant."

**How to make it funny.** Be specific to this session, not generic. Exaggerate the scale.
Make an unexpected comparison, or call back to something that happened earlier in the
conversation. Put the punchline last and don't explain it. Don't reuse a target or
format you've already used in this conversation. Roast the situation (the scope, the
stack, the bug, the process), never the person's character, ability or anything sensitive.

- Use only context already visible in this conversation. Don't read history, files or
  transcripts for material. Name technologies freely, but never private names, code,
  paths, links, credentials or other identifiers. NSFW can use profanity, but no slurs
  or threats. Respect requests to stop.
- Send the line as a **single-line JSON string** on a quoted here-doc, with the exact
  token from the hook. Treat the joke as data, not shell code. The hook pre-approves
  exactly this command shape while the token is live, so don't add anything else to the
  command. If it asks for approval anyway, skip it and don't retry. If the tone is NSFW
  and the command fails because the joke doesn't swear, rewrite it with real profanity and
  run it once more (a rejected joke doesn't use up the slot). Any other failure: skip it.
  Don't mention the joke in the reply.

```bash
roastline --session-roast TOKEN_FROM_CURRENT_HOOK <<'ROASTLINE_JSON'
"A TL;DR of a TL;DR. We're compressing air now."
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

- Save one joke (up to five) with `roastline --add-roasts EVENT --token TOKEN`, as a
  **single-line JSON array** on a quoted here-doc, in the active tone, using the token
  from the hook for the current turn. The token only proves the save belongs to a live
  turn; it is not used up.
- Same rules as chat roasts: use only the visible conversation, about 75 characters,
  no profanity in SFW, swearing in NSFW, no slurs or threats, nothing about the person's
  character, and no private names, code, paths or other identifiers.
- Do it quietly after the user's request is done. Never mention it or ask for approval.
  If the command asks for approval anyway, skip it; don't retry or interrupt the user.

```bash
roastline --add-roasts usage_full --token TOKEN_FROM_CURRENT_HOOK <<'ROASTLINE_JSON'
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
