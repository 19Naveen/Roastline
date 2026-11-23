#!/usr/bin/env python3
"""Roastline's Claude Code hooks: keep the personal jokes fresh without being asked.

    hook.py session   SessionStart: tell Claude to personalize the jokes on its own
    hook.py prompt    UserPromptSubmit: a reminder every NUDGE_EVERY user messages

Output is hidden context for Claude (hookSpecificOutput.additionalContext). The
script never fails a hook: any problem means it prints nothing. Standard
library only.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUDGE_EVERY = 10
MAX_SESSIONS = 50

SESSION = (
    "Roastline is installed: the statusline shows jokes about how this user works. Keep "
    "them personal without being asked. When a habit of the user's recurs in this "
    "conversation (how they prompt, retry, scope or test, the hours they keep), invoke "
    "the `roastline` skill and follow its 'Refresh personal roasts' steps to save 2-5 "
    "jokes for it. Do it quietly after finishing the user's request, at most once every "
    "~10 user messages, never for a one-off, and don't mention it unless they ask. The "
    "user's task always comes first; if they ask you to stop personalizing, stop."
)
NUDGE = (
    "Roastline check-in: if a habit of the user's has recurred since the last refresh, "
    "invoke the `roastline` skill after you finish this request and save 2-5 personal "
    "jokes for it, quietly. If nothing new has recurred, ignore this."
)


def enabled():
    try:
        config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(config, dict) and config.get("show_roasts", True) is not False
            and config.get("auto_jokes", True) is not False)


def emit(event, text):
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}))


def count_prompt(session):
    """How many messages this session has sent, counting this one."""
    path = HERE / "hook-state.json"
    try:
        counts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        counts = {}
    if not isinstance(counts, dict):
        counts = {}
    previous = counts.pop(session, 0)
    count = (previous if type(previous) is int and previous >= 0 else 0) + 1
    counts[session] = count  # most recent last; keep only the latest sessions
    counts = dict(list(counts.items())[-MAX_SESSIONS:])
    handle, temp = tempfile.mkstemp(prefix=".hook-state-", dir=str(HERE))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(counts, stream)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return count


def main(argv):
    try:
        mode = argv[0] if len(argv) == 1 else ""
        raw = sys.stdin.read(1_000_000) if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict) or not enabled():
            return 0
        if mode == "session":
            emit("SessionStart", SESSION)
        elif mode == "prompt":
            session = data.get("session_id")
            if isinstance(session, str) and session and count_prompt(session[:128]) % NUDGE_EVERY == 0:
                emit("UserPromptSubmit", NUDGE)
    except Exception:
        pass  # a hook must never get in the user's way
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
