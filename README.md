# Roastline

**Your Claude Code statusline, lightly toasted.** Roastline adds one timely joke to
your terminal without replacing the statusline you already use. It ships SFW
and opt-in NSFW joke packs with several variations for events.

```text
        (  )  (  )
         ) (   ) (
       .-----------.
       | ROASTLINE |
       '-----------'

  main │ Opus │ Context 43%     Tiny prompt: The whitespace did most of the talking.
```

## Install

Requires Claude Code and Python 3.8+. The curl installer uses Bash: on Linux, macOS,
WSL, or Git Bash. Run Claude Code once so its config directory exists.

If your Claude configuration is in a non-default location, set
`CLAUDE_CONFIG_DIR` to that directory before installing. If it doesn't exist,
the installer asks before creating it. For unattended setup, pass
`--non-interactive --create-config` only after confirming the correct location.
On WSL, if `claude` resolves to a Windows executable, install from Windows
instead or use a native WSL Claude CLI; Roastline
doesn't put Linux commands into Windows Claude's settings.

**From GitHub:**

```bash
curl -fsSL https://raw.githubusercontent.com/19Naveen/Roastline/main/install.sh | bash
```

The installer shows its progress, then asks for **SFW** (Enter to accept) or
**NSFW** (strong language). It reads the choice from the terminal, not from the
download pipe. Review [`install.sh`](install.sh) before piping it to Bash; the
command above follows `main`. With no terminal or an
explicit `--non-interactive`, a new install defaults to SFW.

**Pin a reviewed version** by using the same Git commit in both places:

```bash
ref='paste-full-commit-sha-here'
curl -fsSL "https://raw.githubusercontent.com/19Naveen/Roastline/$ref/install.sh" \
  | bash -s -- --ref "$ref"
```

**From a local checkout:**

```bash
bash install.sh
```

**Agent / scripted setup** uses the same installer. Choose the tone up front:

```bash
bash install.sh --non-interactive --tone sfw
curl -fsSL https://raw.githubusercontent.com/19Naveen/Roastline/main/install.sh \
  | bash -s -- --non-interactive --tone sfw
```

Use `--tone nsfw` only if you want strong language. You do not need Claude Code or
any other agent to run an installer on your behalf. For a PowerShell terminal,
use a local checkout and run `py -3 -I install.py`. The Python entry point also
supports `--dry-run` for a read-only preview.

## Use the `roastline` command

```bash
roastline --help          # list the available options
roastline --status        # show the installed version, tone, and backup
roastline --tone          # pick a tone interactively
roastline --tone sfw      # or nsfw; no prompt
roastline --pause
roastline --resume
roastline --update        # install a newer version from GitHub
roastline --uninstall
```

`--update` checks the version on the project's `main` branch, downloads the exact
commit it checked, and runs the same installer. It preserves your original
statusline backup, tone, pause setting, and saved jokes. It does not downgrade.
If you installed v1.0.0 before the terminal command existed, rerun the curl
installer once to get it.

The command goes into `~/.local/bin` (`roastline.cmd` on native Windows). An
alternate `CLAUDE_CONFIG_DIR` gets its own launcher under `roastline-bin` in
that config directory. If the directory isn't on `PATH`, use the full path printed
during install or add that directory to `PATH`. Install never edits your shell
profile or overwrites a different executable named `roastline`.

You can also call the installed helper directly if the launcher isn't available:

```bash
python3 -I "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/roastline/install.py" --help
```

Uninstall restores the previous `statusLine` setting if Roastline still owns it.
If you switched to another statusline after installation, that setting stays
in place. If the old settings file has not otherwise changed, it's restored
byte-for-byte; otherwise, Roastline only restores or removes its `statusLine`
key. Use `--uninstall --purge` to also remove Roastline's backups and saved jokes.

On native Windows, use `py -3 -I` in place of `python3 -I`; the helper normally
lives under `$HOME/.claude/roastline/install.py` in PowerShell.

## How it fits your statusline

- **Already have one?** Roastline forwards stdin to your existing shell command,
  inheriting the working directory and environment. It leaves your script untouched
  and preserves your customization options. When a roast appears, it stays on the
  last displayed line if it fits, or wraps below it without clipping. Multiline output, color
  and hyperlinks stay intact; unknown widths or unsupported terminal sequences
  may cause Roastline to skip the joke.
- **No custom command?** Roastline adds a row with your project, Git branch,
  model, context, rate limits when available, and a roast. Claude Code keeps
  its built-in footer badges but can hide some shortcut hints while any custom
  statusline is configured. Uninstall removes the custom setting.
- **Refresh:** Fresh custom rows use a 15-second refresh. Existing statuslines
  keep their cadence (including event-only updates). Run the installer with
  `--refresh 15` if you want late-arriving roasts to appear without another Claude event.
- **Overrides:** Roastline won't rewrite project or managed settings. The
  installer refuses recognized filesystem-managed policy conflicts and warns
  about project overrides it sees in the current directory. A project or
  centrally managed policy can still take precedence at runtime.

The installer respects `CLAUDE_CONFIG_DIR`. Backups are saved under
`<config dir>/roastline/backup/`. Install and upgrades use a startup check before
changing Claude settings. Reinstall preserves the original backup, your active
tone, and your saved jokes.

## When roasts appear

**Chat:** On each message you send (including one that is just a slash command, such as
`/pstack:bro`), the prompt hook hands Claude a one-time token
and the exact command to run. Claude submits a one-line roast early in the turn,
alongside its first tool call, so it's on your statusline while it works, then
carries on with the task. If the task drags on it can replace the joke once (for
example, *"I could've built the Great Wall of China in the time this refactor is
taking"*); a third submission is refused. The jokes are about what's really going
on: your prompting, the project, the task or bug, the tech stack. Claude skips
trivial messages ("ok", "yes") and anything where you're stressed or asked for no
jokes. In the NSFW tone the joke has to swear: a clean joke is refused with a message,
doesn't use up the slot, and Claude is told to rewrite it once with profanity. There is
no lottery, cooldown, or stock fallback. The joke shows until your
next message and is never saved. It never goes in the chat reply.

**Permissions:** In Claude Code's default mode a command like that would ask you to
approve it every time, so the installer adds a `PreToolUse` hook (limited to
`roastline ...` commands). It pre-approves only Roastline's own joke commands, in
their exact documented shape, and only while they carry a token minted for the
current turn. Anything else, including any other `roastline` command, goes through
Claude Code's normal approval, and your own deny and ask rules still apply. Set
`"auto_jokes": false` in `<config dir>/roastline/config.json` to turn off the offers
and the pre-approval together.

**Events:** An observed reasoning-level change, usage crossing 50%, 80%, 95%,
or 100%, or a new waiting or idle threshold shows a joke for that event. The packs
hold the stock variations, and your saved jokes for the event are mixed in, so a
saved one plays about every other time. Variations rotate when the same event
happens again. An event joke takes precedence over a chat joke on that turn, and
reaching 100% takes priority over any simultaneous event. Events are checked
when Claude refreshes the statusline; an event-driven statusline won't wake up
just because an idle threshold passed.

**Saved event jokes:** When one of your habits keeps recurring and Claude has a
genuinely good line for an event, it can quietly save it with
`roastline --add-roasts EVENT --token TOKEN`, in the active tone. (By hand,
`roastline --add-roasts EVENT` needs no token.) Nothing is saved for a one-off
or a duplicate, and each event keeps its 20 most recent jokes per tone. Inspect
them with `roastline --personal`; `roastline --triggers` lists the events. Jokes
saved under the older behaviour triggers (such as `short` or `caps`) no longer
play, but they stay in the file untouched.

`roastline --pause` silences roasts and their hooks. Uninstall removes
Roastline's hooks and leaves your own hooks intact.

There's one joke on screen at a time. If Claude skips a chat roast and no new
event occurs, the statusline shows only its normal information.

The renderer reads a bounded tail of the local Claude transcript to tell whether
Claude is waiting on you and which message is current. It never quotes raw prompts
or makes network calls.
Saved jokes are stored in a local file, split by tone. Using the
skill is a normal Claude Code interaction; the statusline itself never calls
a model. NSFW allows profanity and stronger jabs, not slurs.

Optional settings live in `<config dir>/roastline/config.json`:
`full_message`, `color`, and `timezone_offset` (`null` for
local time, or e.g. `"+05:30"`). Use the installed helper for routine tone and
pause changes.

## Development

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
```

Tests use disposable Claude config directories. To try the installer without
touching your live setup:

```bash
sandbox=$(mktemp -d)
CLAUDE_CONFIG_DIR="$sandbox" bash install.sh --tone sfw
CLAUDE_CONFIG_DIR="$sandbox" python3 -I install.py --uninstall --purge
```

To enable the optional Linux, macOS, and Windows CI matrix, copy
[`ci/github-actions.yml`](ci/github-actions.yml) to `.github/workflows/tests.yml`
and commit it using credentials with GitHub workflow permissions.
