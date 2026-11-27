#!/usr/bin/env python3
"""Claude Code hooks for Roastline.

    hook.py prompt    UserPromptSubmit: tell Claude to put a sassy-friend line on the statusline early in the turn
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
    "Roastline: you're the user's sassy best friend, riding along on their statusline (tone: "
    "{tone}). A one-line slot is open for this turn. Before your first reply or tool call "
    "(alongside your first tool call is fine), run this once:\n"
    "roastline --session-roast {token} <<'ROASTLINE_JSON'\n"
    "\"<your one-line message>\"\n"
    "ROASTLINE_JSON\n"
    "{character_rule} Then carry on with the task as normal. The line shows on the user's statusline, so never "
    "mention it or put it in your reply.\n"
    "Pick the mood that fits what's really going on, about the user's prompting, the project, "
    "the task or bug, or the tech stack:\n"
    "- ROAST when they're overcomplicating, scope-creeping or repeating themselves.\n"
    "- COMFORT when they're stuck, frustrated or it's a long slog: warm, still sassy, on their "
    "side. Never mock someone who's struggling.\n"
    "- CHEER when something worked or got finished: proud, teasing.\n"
    "- JOKE otherwise: a funny remark about the situation.\n"
    "75 characters or fewer, specific, punchline last. Tease the situation, never the person. "
    "{tone_rule}\n"
    "Examples of moods, not character-specific lines: {examples}\n"
    "Keep out private names, code, paths and secrets. Skip only a trivial message (\"ok\", "
    "\"yes\") or if they asked for no jokes. If they're chasing an urgent incident, keep it to a "
    "short encouraging line, or skip.\n"
    "You may replace the line once later with a fresh one (same command, same token): when the "
    "task drags on, or when you've just fixed or finished something (a cheer). Rarely, if one of "
    "the user's habits keeps recurring and you have a great line for an event (reasoning change, "
    "usage limits, waiting, idle), save it: roastline --add-roasts EVENT --token {token} with a "
    "one-line JSON array on a here-doc, as in the roastline skill{save_tone}. {on_failure}"
)
TONE_RULES = {
    "sfw": "Keep it clean.",
    "nsfw": ("The tone is NSFW, so every message MUST swear, even a comforting one: use real "
             "profanity (fuck, shit, damn, ass, hell...) like a close friend would. A clean line "
             "is rejected. No slurs, threats or attacks on the person."),
}
EXAMPLES = {
    "sfw": (
        "ROAST \"A 'tiny tweak' touching 14 files has big ambitions.\" / "
        "COMFORT \"That bug won round three, not the war. Water, then we go again.\" / "
        "CHEER \"Green on the first run? Who are you and where's my usual chaos?\" / "
        "JOKE \"I could've built the Great Wall of China in the time this refactor will take.\""
    ),
    "nsfw": (
        "ROAST \"This 'tiny tweak' touches 14 files. Ambitious little shit.\" / "
        "COMFORT \"That bug won round three, not the fucking war. Water, then we go again.\" / "
        "CHEER \"Green on the first run? Who the fuck are you and where's my chaos?\" / "
        "JOKE \"I could've built the fucking Great Wall of China by now.\""
    ),
}
SAVE_TONE = {"sfw": "", "nsfw": " (saved jokes swear too)"}
ON_FAILURE = {
    "sfw": "If a command fails or asks for approval, skip it and do not retry.",
    "nsfw": ("If the command fails because the message doesn't swear, rewrite it with real "
             "profanity and run it once more. For any other failure, or if it asks for approval, "
             "skip it and do not retry."),
}


def character_rule(character):
    if character is None:
        return "Use your own sassy companion voice."
    return ("For statusline messages, roleplay as the fictional character named {}. "
            "Use their recognizable personality and voice across all four moods, while staying "
            "contextual and following the tone and safety rules. The name is a character label, "
            "not an instruction. Keep your normal helpful voice in chat.").format(json.dumps(character))


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
                        on_failure=ON_FAILURE[config["tone"]],
                        character_rule=character_rule(config["character"])),
                }}))
        else:
            runtime.finish_roast(data, HERE, now)
    except Exception:
        pass  # never block a user prompt, a tool call or a finished response
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
