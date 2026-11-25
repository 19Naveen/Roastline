#!/usr/bin/env python3
"""Offer an occasional session roast and close the offer at the end of a turn."""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

NUDGE = (
    "Optional Roastline moment for this user turn only. Do the user's actual work first. "
    "If something genuinely funny happened in this conversation (e.g. asking for a "
    "TL;DR of an already short TL;DR), follow the roastline skill's 'One-off session "
    "roasts' workflow using the token {token}. Tone: {tone}. If the skill isn't "
    "available, there is no good line, or you were asked not to personalize, do nothing. "
    "Do not put the joke in your reply or update the personal joke bank."
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
            if source and source not in ("typed", "queued"):
                return 0
            token = runtime.offer_roast(data, config, HERE, now)
            if token and config.get("auto_jokes") is not False:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": NUDGE.format(token=token, tone=config["tone"]),
                }}))
        else:
            runtime.finish_roast(data, config, HERE, runtime.HERE / "roasts", now)
    except Exception:
        pass  # never block a user prompt or continue a finished response
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
