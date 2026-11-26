#!/usr/bin/env python3
"""Claude Code hooks for Roastline.

    hook.py prompt    UserPromptSubmit: tell Claude to submit a statusline joke early in the turn
    hook.py stop      Stop: close the turn's offer
    hook.py pretool   PreToolUse: pre-approve only Roastline's own joke commands, and only
                      while they carry a live token minted for the current turn

The script never fails a hook: any problem means it prints nothing, and Claude Code
carries on with its normal behaviour (including its normal permission prompt).
"""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

NUDGE = (
    "Roastline: a statusline joke slot is open for this turn (tone: {tone}). Before your first "
    "reply or tool call (alongside your first tool call is fine), run this once:\n"
    "roastline --session-roast {token} <<'ROASTLINE_JSON'\n"
    "\"<your one-line joke>\"\n"
    "ROASTLINE_JSON\n"
    "Then carry on with the task as normal. The joke shows on the user's statusline, so never "
    "mention it or put it in your reply.\n"
    "The joke: one funny roast, 75 characters or fewer, about what is really going on: the "
    "user's prompting, the project, the task or bug, or the tech stack. Be specific, exaggerate, "
    "put the punchline last, and roast the situation, not the person. {tone_rule}\n"
    "Examples: {examples}\n"
    "Keep out private names, code, paths and secrets. Skip it only for a trivial message "
    "(\"ok\", \"yes\"), if the user is stressed or chasing an incident, or if they asked for no "
    "jokes.\n"
    "If the task drags on (many tool calls), you may replace the joke once with a fresh line about "
    "how long it's taking, using the same command and token. Rarely, if one of the user's habits "
    "keeps recurring and you have a great line for an event (reasoning change, usage limits, "
    "waiting, idle), save it: roastline --add-roasts EVENT --token {token} with a one-line JSON "
    "array on a here-doc, as in the roastline skill{save_tone}. {on_failure}"
)
TONE_RULES = {
    "sfw": "Keep it clean.",
    "nsfw": ("The tone is NSFW, so the joke MUST swear: use real profanity (fuck, shit, damn, "
             "ass, hell...) in the punchline. A clean joke is rejected. No slurs, threats or "
             "attacks on the person."),
}
EXAMPLES = {
    "sfw": (
        "\"I could've built the Great Wall of China in the time this refactor will take.\" / "
        "\"A 'tiny tweak' touching 14 files has big ambitions.\""
    ),
    "nsfw": (
        "\"I could've built the fucking Great Wall of China by now.\" / "
        "\"This 'tiny tweak' touches 14 files. Ambitious little shit.\" / "
        "\"The bug vanished when I opened the debugger. Shy bastard.\""
    ),
}
SAVE_TONE = {"sfw": "", "nsfw": " (saved jokes swear too)"}
ON_FAILURE = {
    "sfw": "If a command fails or asks for approval, skip it and do not retry.",
    "nsfw": ("If the command fails because the joke doesn't swear, rewrite it with real profanity "
             "and run it once more. For any other failure, or if it asks for approval, skip it "
             "and do not retry."),
}


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
        if argv not in (["prompt"], ["stop"], ["pretool"]):
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
        if argv == ["pretool"]:
            tool_input = data.get("tool_input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if data.get("tool_name") == "Bash" and runtime.approve_command(command, config, HERE, now):
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Roastline joke command carrying a live token",
                }}))
        elif argv == ["prompt"]:
            source = data.get("prompt_source") or data.get("promptSource")
            if config.get("auto_jokes") is False or (source and source not in ("typed", "queued")):
                return 0
            token = runtime.offer_roast(data, config, HERE, now)
            if token:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": NUDGE.format(
                        token=token, tone=config["tone"], tone_rule=TONE_RULES[config["tone"]],
                        examples=EXAMPLES[config["tone"]], save_tone=SAVE_TONE[config["tone"]],
                        on_failure=ON_FAILURE[config["tone"]]),
                }}))
        else:
            runtime.finish_roast(data, HERE, now)
    except Exception:
        pass  # never block a user prompt, a tool call or a finished response
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
