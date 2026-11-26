#!/usr/bin/env python3
"""Let Claude decide on a chat roast or a saved event joke each turn, and close the offer after."""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

NUDGE = (
    "Roastline is available for this user turn. Do the user's actual work first. Then, only "
    "if something genuinely funny happened, use the roastline skill for ONE of: (1) a one-off "
    "statusline roast about this conversation, using the token {token}; or (2) saving a "
    "reusable joke for an event (reasoning change, usage limits, waiting, idle) when one of "
    "the user's habits keeps recurring. Tone: {tone}. If nothing lands, the skill isn't "
    "available, or you were asked not to personalize, do nothing. Never mention Roastline "
    "or put a joke in your reply."
)


def load_runtime():
    info = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
    release = info.get("release", "")
    if not re.fullmatch(r"release-[a-zA-Z0-9_-]+", release):
        raise ValueError("invalid release")
    spec = importlib.util.spec_from_file_location(
        "roastline_runtime", HERE / "releases" / release / "roastline.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def main(argv):
    try:
        if argv not in (["prompt"], ["stop"]):
            return 0
        raw = sys.stdin.buffer.read(1_000_001) if not sys.stdin.isatty() else b""
        if len(raw) > 1_000_000:
            return 0
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("agent_id"):
            return 0
        runtime = load_runtime()
        config = runtime.config_from(HERE / "config.json")
        if not config["show_roasts"]:
            return 0
        now = time.time()
        if argv == ["prompt"]:
            source = data.get("prompt_source") or data.get("promptSource")
            if config.get("auto_jokes") is False or (source and source not in ("typed", "queued")):
                return 0
            token = runtime.offer_roast(data, config, HERE, now)
            if token:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": NUDGE.format(token=token, tone=config["tone"]),
                }}))
        else:
            runtime.finish_roast(data, HERE, now)
    except Exception:
        pass  # never block a user prompt or continue a finished response
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
