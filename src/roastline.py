#!/usr/bin/env python3
"""Local statusline renderer. An existing statusline always runs first."""

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone


VERSION = "1.2.0"
TONES = ("sfw", "nsfw")
DEFAULTS = {
    "tone": "sfw",
    "show_roasts": True,
    "rotation_seconds": 30,
    "full_message": "FULL",
    "color": True,
    "timezone_offset": None,
    "pet": "off",
}
TRIGGERS = {
    "question_wait": 8,
    "question_late": 9,
    "question_ghosted": 9,
    "usage_half": 1,
    "usage_high": 3,
    "usage_critical": 7,
    "usage_full": 8,
    "idle": 6,
    "idle_long": 7,
    "tools": 5,
    "caps": 5,
    "short": 4,
    "retry": 5,
    "burst": 3,
    "flood": 4,
    "late_night": 3,
    "session_regular": 1,
    "session_marathon": 2,
    "effort": 0,
    "default": -1,
}
HERE = Path(__file__).resolve().parent
TAIL_BYTES = 600_000
MAX_PERSONAL = 20  # per trigger and tone
MAX_ROAST_WIDTH = 100
SGR = re.compile(r"\x1b\[[0-9;:]*m")
LINK = re.compile(r"\x1b\]8;[^\x07\x1b\r\n]*;[^\x07\x1b\r\n]*(?:\x07|\x1b\\)")
PARTIAL = " ▏▎▍▌▋▊▉█"
RAINBOW = ((235, 80, 80), (245, 150, 60), (240, 215, 70),
           (90, 200, 110), (80, 150, 240), (170, 100, 230))
PRIVATE_LOOKING = re.compile(
    r"(?i)https?://|[^\s@]+@[^\s@]+\.[a-z]{2,}|\b[0-9]{7,}\b"
    r"|\b(?:sk-|gh[pousr]_|github_pat_|AKIA)[\w-]{8,}"
)
PROFANITY = re.compile(
    r"(?i)\b(?:fuck\w*|shit\w*|bitch\w*|asshole\w*|damn\w*|bastard\w*|"
    r"bullshit\w*|dumbass\w*|jackass\w*|badass\w*|piss\w*|crap\w*)\b"
)


def get(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def read_json(path, limit=1_000_000):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON file is too large")
    return json.loads(raw)


def timezone_for(offset):
    if offset is None:
        return None  # use the local timezone, including DST
    if not isinstance(offset, str) or not re.fullmatch(r"[+-](?:[01]\d|2[0-3]):[0-5]\d", offset):
        raise ValueError("timezone_offset must be null or ±HH:MM")
    minutes = int(offset[1:3]) * 60 + int(offset[4:])
    return timezone(timedelta(minutes=minutes if offset[0] == "+" else -minutes))


def safe_text(value):
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value if not unicodedata.category(char).startswith("C")
                   and char not in "\u2028\u2029")


def config_from(path):
    data = read_json(path, 16_384)
    if not isinstance(data, dict):
        raise ValueError("Roastline config must be a JSON object")
    config = {**DEFAULTS, **data}
    if config["tone"] not in TONES:
        raise ValueError("tone must be sfw or nsfw")
    if config["pet"] not in ("off", "cat", "dog", "anime", "person"):
        raise ValueError("invalid pet")
    if type(config["show_roasts"]) is not bool or type(config["color"]) is not bool:
        raise ValueError("show_roasts and color must be booleans")
    seconds = config["rotation_seconds"]
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError("rotation_seconds must be between 1 and 3600")
    message = config["full_message"]
    if (not isinstance(message, str) or not message.strip() or message != safe_text(message)
            or display_width(message) > 18):
        raise ValueError("full_message must be short, single-line text")
    timezone_for(config["timezone_offset"])
    return config


def cells(char):
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def display_width(text):
    return sum(map(cells, SGR.sub("", LINK.sub("", text))))


def truncate(text, width):
    if display_width(text) <= width:
        return text
    if width <= 0:
        return ""
    parts = []
    budget = width - 1
    for char in text:
        weight = cells(char)
        if weight > budget:
            break
        parts.append(char)
        budget -= weight
    return "".join(parts).rstrip() + "…"


def valid_line(text, tone, personal=False):
    return (isinstance(text, str) and 0 < len(text) <= 160 and text == text.strip()
            and text == safe_text(text) and 1 <= display_width(text) <= MAX_ROAST_WIDTH
            and (tone != "sfw" or not PROFANITY.search(text))
            and (not personal or not PRIVATE_LOOKING.search(text)))


def load_pack(path, tone):
    data = read_json(path, 64_000)
    if not isinstance(data, dict) or set(data) != set(TRIGGERS):
        raise ValueError("roast pack does not cover all triggers")
    seen = set()
    for lines in data.values():
        if not isinstance(lines, list) or len(lines) != 5:
            raise ValueError("roast packs must have five lines per trigger")
        for line in lines:
            if not valid_line(line, tone) or line.casefold() in seen:
                raise ValueError("invalid or duplicate line in roast pack")
            seen.add(line.casefold())
    return data


def personal_lines(path, tone, trigger):
    try:
        items = get(read_json(path, 512_000), tone, trigger)
        if isinstance(items, list):
            return [line for line in items[-MAX_PERSONAL:] if valid_line(line, tone, personal=True)]
    except (OSError, ValueError, RecursionError):
        pass
    return []


def numeric(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if math.isfinite(number):
                return number
        except OverflowError:
            pass
    return None


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except (ValueError, OverflowError, OSError):
            return None
    return numeric(value)


def reset_time(value, tz, weekly=False):
    value = timestamp(value)
    if value is None:
        return ""
    try:
        dt = datetime.fromtimestamp(value, tz)
    except (ValueError, OverflowError, OSError):
        return ""
    clock = "{}:{:02d}{}".format(dt.hour % 12 or 12, dt.minute, "am" if dt.hour < 12 else "pm")
    return dt.strftime("%a ") + clock if weekly else clock


def usage_fields(data):
    return tuple((name, number, reset) for name, value, reset in (
        ("Context", get(data, "context_window", "used_percentage"), None),
        ("5h", get(data, "rate_limits", "five_hour", "used_percentage"),
         get(data, "rate_limits", "five_hour", "resets_at")),
        ("Weekly", get(data, "rate_limits", "seven_day", "used_percentage"),
         get(data, "rate_limits", "seven_day", "resets_at")),
    ) for number in (numeric(value),) if number is not None and number >= 0)


def usage_trigger(data):
    candidates = usage_fields(data)
    if not candidates:
        return None
    name, percent, _ = max(candidates, key=lambda item: item[1])
    for threshold, key in ((100, "usage_full"), (95, "usage_critical"),
                           (80, "usage_high"), (50, "usage_half")):
        if percent >= threshold:
            return key, "{} {}%".format(name, min(100, int(percent)))
    return None


def bounded_rows(path):
    """Read a fixed tail, skip a torn final write, never scan a whole large transcript."""
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("not a regular transcript")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - TAIL_BYTES)
        stream.seek(max(0, offset - 1))
        blob = stream.read(TAIL_BYTES + bool(offset))
    if offset:
        blob = blob.partition(b"\n")[2]
    lines = blob.split(b"\n")
    rows = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            if index == len(lines) - 1:
                break
            raise
        if not isinstance(row, dict) or not isinstance(row.get("type"), str):
            raise ValueError("unrecognized transcript row")
        if row["type"] in ("user", "assistant") and not (row.get("isMeta") or row.get("isSidechain")):
            message = row.get("message")
            content = get(message, "content")
            if not isinstance(content, (list, str)):
                raise ValueError("unrecognized transcript message")
            if isinstance(content, list) and any(
                not isinstance(block, dict) or not isinstance(block.get("type"), str)
                or (block["type"] == "text" and not isinstance(block.get("text"), str))
                for block in content
            ):
                raise ValueError("unrecognized transcript block")
            rows.append((row, content))
    return rows


def blocks_of(content):
    return content if isinstance(content, list) else ()


def text_of(content):
    return content if isinstance(content, str) else " ".join(
        block["text"] for block in content if block["type"] == "text"
    )


def wait_label(seconds):
    mins = max(1, int(seconds // 60))
    return "{}h{:02d}m".format(*divmod(mins, 60)) if mins >= 60 else "{}m".format(mins)


def conversation_triggers(path, now):
    if not isinstance(path, str) or not path:
        return []
    try:
        rows = bounded_rows(path)
    except (OSError, ValueError, RecursionError):
        return []
    if not rows:
        return []
    candidates = []
    prompts = [(i, row, c) for i, (row, c) in enumerate(rows)
               if row["type"] == "user" and row.get("promptSource") == "typed"]
    row, content = rows[-1]
    tools = [block for block in blocks_of(content) if block["type"] == "tool_use"]
    asked = any(block.get("name") == "AskUserQuestion" for block in tools)
    running = any(block.get("name") != "AskUserQuestion" for block in tools)
    ts = timestamp(row.get("timestamp"))
    age = now - ts if ts is not None else 0
    if row["type"] == "assistant" and not running:
        question = asked or text_of(content).rstrip().endswith("?")
        if question and age >= 60:
            key = "question_ghosted" if age >= 1800 else "question_late" if age >= 300 else "question_wait"
            candidates.append((key, "Question waiting " + wait_label(age)))
        elif not question and age >= 900:
            key = "idle_long" if age >= 3600 else "idle"
            candidates.append((key, "Idle " + wait_label(age)))
    if not prompts:
        return candidates
    index, _, last = prompts[-1]
    calls, ids = 0, set()
    for event, value in rows[index + 1:]:
        if event["type"] == "assistant":
            for block in blocks_of(value):
                if block["type"] == "tool_use" and block.get("name") != "AskUserQuestion":
                    tool_id = block.get("id")
                    if not isinstance(tool_id, str) or not tool_id or tool_id not in ids:
                        calls += 1
                        if isinstance(tool_id, str) and tool_id:
                            ids.add(tool_id)
    if calls >= 15:
        candidates.append(("tools", "{} tool calls this turn".format(calls)))
    prompt = text_of(last).strip()
    alpha = sum(char.isalpha() for char in prompt)
    if alpha >= 8 and sum(char.isupper() for char in prompt) / alpha > 0.8:
        candidates.append(("caps", "Caps lock"))
    if 0 < len(prompt) <= 6:
        candidates.append(("short", "Tiny prompt"))
    normalized = " ".join(prompt.casefold().split())
    if normalized and len(prompts) >= 2:
        previous = " ".join(text_of(prompts[-2][2]).casefold().split())
        if previous == normalized:
            candidates.append(("retry", "Same prompt twice"))
    recent = 0
    for _, event, _ in prompts:
        sent = timestamp(event.get("timestamp"))
        if sent is not None and 0 <= now - sent < 600:
            recent += 1
    if recent >= 6:
        candidates.append(("flood" if recent >= 10 else "burst", "{} prompts in 10m".format(recent)))
    # On large transcripts these are lower bounds, not misleading exact totals.
    if len(prompts) >= 20:
        marathon = len(prompts) >= 40
        candidates.append(("session_marathon" if marathon else "session_regular",
                           "{}+ prompts this session".format(40 if marathon else 20)))
    return candidates


def choose_trigger(data, config, now):
    candidates = conversation_triggers(data.get("transcript_path"), now)
    usage = usage_trigger(data)
    if usage:
        candidates.append(usage)
    if datetime.fromtimestamp(now, timezone_for(config["timezone_offset"])).hour < 5:
        candidates.append(("late_night", "Late-night session"))
    effort = get(data, "effort", "level")
    if effort in ("low", "medium", "high", "xhigh", "max"):
        candidates.append(("effort", "Effort " + effort))
    candidates.append(("default", "Claude Code"))
    return max(candidates, key=lambda candidate: TRIGGERS[candidate[0]])


def choose_roast(data, config, root, pack_dir, now):
    trigger, reason = choose_trigger(data, config, now)
    tone = config["tone"]
    pack = load_pack(pack_dir / (tone + ".json"), tone)
    stock = pack[trigger]
    personal = list(dict.fromkeys(personal_lines(root / "personal.json", tone, trigger)))
    session = get(data, "session_id")
    seed = (session[:256] if isinstance(session, str) else "") + ":" + trigger + ":" + tone
    offset = int.from_bytes(hashlib.sha256(seed.encode("utf-8", "replace")).digest()[:8], "big")
    # Conversation rows, rather than wall-clock time, drive rotation. Refreshes
    # with no new message therefore don't randomly change the joke.
    try:
        rows = bounded_rows(data.get("transcript_path")) if data.get("transcript_path") else []
    except (OSError, ValueError, RecursionError, TypeError):
        rows = []
    if rows:
        index = len(rows)
    else:
        prompt_id = data.get("prompt_id")
        index = int.from_bytes(hashlib.sha256(str(prompt_id or "").encode()).digest()[:8], "big")
    turn = index + offset
    if len(personal) == 1:
        # With one personal joke, alternate rather than show the same joke twice.
        joke = personal[0] if turn % 2 else stock[(turn // 2) % len(stock)]
    elif personal and turn % 4 != 0:
        joke = personal[(turn - turn // 4 - 1) % len(personal)]
    else:
        joke = stock[(turn // 4) % len(stock)]
    pet = config.get("pet", "off")
    if pet != "off":
        try:
            spec = importlib.util.spec_from_file_location("roastline_pets", str(HERE / "pets.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            joke = module.decorate(joke, pet)
        except (OSError, ImportError, ValueError, AttributeError):
            # Older updaters stage only files known to their own version.
            face = {"cat": "(=^.^=)", "dog": "(U^.^U)", "anime": "(^_^)", "person": "(:-)"}.get(pet)
            if face:
                joke = face + " " + joke
    return reason, joke


def format_roast(roast, room=None):
    if not roast:
        return ""
    reason, joke = roast
    prefix = reason + ": "
    if room is None:
        return prefix + joke
    room -= display_width(prefix)
    if room < min(12, display_width(joke)):
        return ""
    return prefix + truncate(joke, room)


def color(text, code, config):
    return "\x1b[{}m{}\x1b[0m".format(code, text) if text and config["color"] else text


def append_original(original, roast, columns, padding, config):
    """Return unchanged bytes if there's any doubt that the addition fits."""
    if not roast or not columns or not original:
        return original
    try:
        text = original.decode("utf-8")
    except UnicodeError:
        return original
    # Keep line endings, trailing blank lines, and all original ANSI/OSC bytes.
    rows = re.split(r"(\r?\n)", text)
    for index in range(len(rows) - 1, -1, -2):
        line = rows[index]
        plain = SGR.sub("", LINK.sub("", line))
        if not plain.strip():
            if line and line != plain:  # unmodelled cross-line styles
                return original
            continue
        if safe_text(plain) != plain:  # tabs, cursor movement, carriage return, etc.
            return original
        links = LINK.findall(line)
        if links and not (links[-1] in ("\x1b]8;;\x07", "\x1b]8;;\x1b\\")):
            return original
        available = columns - 4 - 2 * padding - display_width(line)
        if "\ufe0f" in plain:
            available -= plain.count("\ufe0f")  # emoji presentation can widen glyphs
        display = format_roast(roast, available - 2)
        if not display:
            return original
        gap = " " * max(2, available - display_width(display))
        reset = "\x1b[0m" if SGR.search(text) else ""
        rows[index] = line + reset + gap + color(display, "3;95", config)
        return "".join(rows).encode("utf-8")
    return original


def run_original(integration, payload):
    command = integration["original_command"]
    shell = integration["shell"]
    args = [shell["executable"]]
    if shell["kind"] == "powershell":
        args += ["-NoProfile", "-NonInteractive", "-Command", command]
    else:
        args += ["-c", command]
    # Do not accelerate, re-parse, or impose a new timeout on the user's command.
    return subprocess.run(args, input=payload, stdout=subprocess.PIPE)


def git_branch(cwd):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v2", "--branch"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=0.025,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    if result.returncode:
        return ""
    head, oid, changes, dirty = "", "", "", False
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("# branch.head "):
            head = line[14:]
        elif line.startswith("# branch.oid "):
            oid = line[13:20]
        elif line.startswith("# branch.ab "):
            counts = line[12:].split()
            for count, marker, arrow in zip(counts, "+-", "↑↓"):
                if count.startswith(marker) and count[1:].isdigit() and count[1:] != "0":
                    changes += " " + arrow + count[1:]
        elif line and not line.startswith("#"):
            dirty = True
    return safe_text((oid if head == "(detached)" else head) + (" ●" if dirty else "") + changes) if head else ""


def token_label(value):
    if value >= 1_000_000:
        return "{:.1f}".format(value / 1_000_000).rstrip("0").rstrip(".") + "M"
    return "{}k".format(round(value / 1000)) if value >= 1000 else str(int(value))


def usage_bar(percent, width, config):
    eighths = round(min(percent, 100) * width * 8 / 100)
    bars = []
    for index in range(width):
        level = min(8, max(0, eighths - index * 8))
        rgb = RAINBOW[round(index * (len(RAINBOW) - 1) / max(1, width - 1))]
        fg = "38;2;{};{};{}".format(*rgb)
        if level == 0:
            bars.append(color("█" if config["color"] else "░", "38;2;58;58;58", config))
        elif level == 8:
            bars.append(color("█", fg, config))
        else:
            bars.append(color(PARTIAL[level], fg + ";48;2;58;58;58", config))
    full = config["full_message"]
    if config["tone"] == "sfw" and PROFANITY.search(full):
        full = DEFAULTS["full_message"]
    label = full if percent >= 100 else str(int(percent)) + "%"
    return "".join(bars) + " " + label


def render_builtin(data, roast, columns, config):
    workspace = get(data, "workspace", "current_dir") or data.get("cwd")
    project = get(data, "workspace", "project_dir") or workspace
    project = Path(project) if isinstance(project, str) and project else None
    directory = safe_text("~" if project == Path.home() else (project.name or str(project)) if project else "")
    git = git_branch(workspace) if isinstance(workspace, str) and workspace else ""
    model = safe_text(get(data, "model", "display_name"))
    effort = safe_text(get(data, "effort", "level"))
    fast = data.get("fast_mode") is True
    tz = timezone_for(config["timezone_offset"])
    usage = [(name, percent, reset_time(reset, tz, name == "Weekly"))
             for name, percent, reset in usage_fields(data)]
    total = numeric(get(data, "context_window", "context_window_size"))
    counts = get(data, "context_window", "current_usage")
    used = sum(numeric(get(counts, field)) or 0 for field in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    ))
    tokens = token_label(used) + "/" + token_label(total) if total and total > 0 and 0 <= used < math.inf else ""

    def segments(width, details, resets, show_dir, show_effort):
        fields = []
        if directory and show_dir:
            fields.append(color(directory, "34", config))
        if git:
            fields.append(color(git, "35", config))
        if model:
            label = model + (" " + effort if effort and show_effort else "") + (" ⚡" if fast else "")
            fields.append(color(label, "36", config))
        for name, percent, reset in usage:
            detail = tokens if details and name == "Context" else ""
            if resets and reset:
                detail = "↻" + reset
            fields.append(name + " " + usage_bar(percent, width, config) + (" " + detail if detail else ""))
        return fields

    levels = (
        (8, True, True, True, True), (6, False, True, True, True),
        (5, False, True, False, True), (4, False, False, False, True),
        (3, False, False, False, False),
    )
    left = ""
    if columns is None:
        left = " │ ".join(segments(*levels[0]))
        display = format_roast(roast)
        return left + (" ┊ " if left and display else "") + color(display, "3;95", config)
    room = max(0, columns - 4)
    display = format_roast(roast)
    for index, level in enumerate(levels):
        current = (" │ " if index < 2 else "│").join(segments(*level))
        width = display_width(current)
        if width <= room:
            left = current
            if not display or width + (2 if width else 0) + display_width(display) <= room:
                break
    else:
        if not left:
            # Never slice through an ANSI sequence.
            last = "│".join(segments(*levels[-1])) or directory
            left = truncate(SGR.sub("", last), room)
    width = display_width(left)
    display = format_roast(roast, room - width - (2 if width else 0))
    if not display:
        return left
    gap = " " * max(2, room - width - display_width(display)) if left else ""
    return left + gap + color(display, "3;95", config)


def terminal_columns():
    try:
        columns = int(os.environ.get("COLUMNS", ""))
        return min(columns, 1000) if columns > 0 else None
    except ValueError:
        return None


def render(payload, integration, root, pack_dir=HERE / "roasts"):
    original = None
    if integration["original_command"] is not None:
        original = run_original(integration, payload)
        if original.returncode:
            return original.returncode if original.returncode > 0 else 128 - original.returncode, original.stdout
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or not data:
            return 0, original.stdout if original is not None else b""
        try:
            config = config_from(root / "config.json")
        except (OSError, ValueError, RecursionError):
            config = {**DEFAULTS, "show_roasts": False}
        config["color"] = config["color"] and "NO_COLOR" not in os.environ
        columns = terminal_columns()
        roast = None
        if config["show_roasts"] and (original is None or (columns and original.stdout)):
            try:
                roast = choose_roast(data, config, root, pack_dir, time.time())
            except (OSError, ValueError, RecursionError, OverflowError):
                pass
        if original is not None:
            out = append_original(original.stdout, roast, columns, integration["padding"], config)
        else:
            out = render_builtin(data, roast, columns, config).encode("utf-8")
        return 0, out
    except Exception:
        # The optional renderer must not interfere with an existing command's output.
        return 0, original.stdout if original is not None else b""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 1 and argv[0] == "--smoke":
            # Explicitly never run the installed user's original command in a smoke test.
            config = config_from(HERE / "config.default.json")
            for tone in TONES:
                load_pack(HERE / "roasts" / (tone + ".json"), tone)
            data = json.loads(sys.stdin.buffer.read())
            text = render_builtin(data, None, 120, config)
            if not text or "\n" in text:
                return 1
            sys.stdout.buffer.write(text.encode("utf-8"))
            return 0
        if argv:
            return 2
        integration = read_json(HERE / "integration.json", 65_536)
        status, out = render(sys.stdin.buffer.read(), integration, HERE.parents[1])
        sys.stdout.buffer.write(out)
        return status
    except (OSError, ValueError, KeyError, RecursionError):
        return 1 if argv else 0


if __name__ == "__main__":
    sys.exit(main())
