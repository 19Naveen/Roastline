#!/usr/bin/env python3
"""Install or manage Roastline without editing any other Claude setting."""

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime


VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
TONES = ("sfw", "nsfw")
LIMIT = 4_000_000
BANNER = r"""
         (  )  (  )
          ) (   ) (
        .-----------.
        | ROASTLINE |
        '-----------'
        Your statusline. Lightly toasted.
"""


class InstallError(Exception):
    pass


def message(label, text):
    print("  [{}] {}".format(label, text), flush=True)


def raw_file(path, limit=LIMIT):
    if path.is_symlink():
        raise InstallError("Refusing to change a symlink: {}".format(path))
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise InstallError("Not a regular file: {}".format(path))
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(raw) > limit:
        raise InstallError("File is too large: {}".format(path))
    return raw


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def reject_constant(value):
    raise ValueError("invalid JSON constant " + value)


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("out-of-range JSON number")
    return number


def object_json(raw, path):
    if raw is None:
        return {}
    try:
        obj = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object,
                         parse_constant=reject_constant, parse_float=finite_float)
        if isinstance(obj, dict):
            return obj
    except (ValueError, RecursionError):
        pass
    raise InstallError("Invalid JSON object in {}. No settings were overwritten.".format(path))


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def settings_bytes(obj, original):
    if original is None:
        return json_bytes(obj)
    text = original.decode("utf-8-sig")
    eol = "\r\n" if "\r\n" in text else "\n"
    indent = re.search(r'(?m)^([ \t]+)"', text)
    style = indent.group(1) if indent else (2 if "\n" in text.strip() else None)
    rendered = json.dumps(obj, ensure_ascii=True, indent=style).replace("\n", eol)
    if text.endswith("\n"):
        rendered += eol
    return rendered.encode("utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8")


def write_atomic(path, raw, mode=None):
    if path.is_symlink():
        raise InstallError("Refusing to change a symlink: {}".format(path))
    if raw is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    fd, filename = tempfile.mkstemp(prefix=".roastline-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            if mode is not None:
                os.chmod(filename, mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def file_mode(path, fallback=0o600):
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return fallback


@contextmanager
def config_lock(config_dir):
    path = config_dir / ".roastline-install.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise InstallError("Another Roastline operation is running (or a stale lock exists): {}".format(path))
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def private_dir(path):
    if path.is_symlink():
        raise InstallError("Refusing to use a symlinked directory: {}".format(path))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def managed_sources():
    if os.name == "nt":
        base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ClaudeCode"
    elif sys.platform == "darwin":
        base = Path("/Library/Application Support/ClaudeCode")
    else:
        base = Path("/etc/claude-code")
    yield base / "managed-settings.json"
    yield from (base / "managed-settings.d").glob("*.json")


def managed_guard():
    # This only sees filesystem-managed policies, not all enterprise sources.
    for path in managed_sources():
        data = object_json(raw_file(path), path)
        if ("statusLine" in data or data.get("disableAllHooks") is True
                or data.get("strictPluginOnlyCustomization") is True
                or (isinstance(data.get("strictPluginOnlyCustomization"), list)
                    and "skills" in data["strictPluginOnlyCustomization"])):
            raise InstallError("A managed Claude policy controls the statusline or skills: {}".format(path))


def project_warning(directory, config_dir):
    for root in (directory, *directory.parents):
        settings_dir = root / ".claude"
        if settings_dir in (Path.home() / ".claude", config_dir):
            continue
        for name in ("settings.local.json", "settings.json"):
            path = settings_dir / name
            try:
                data = object_json(raw_file(path), path)
                if "statusLine" in data or data.get("disableAllHooks") is True:
                    message("NOTE", "Project statusline may override Roastline here: {}".format(path))
                    return
            except (OSError, InstallError):
                continue


def resolve_shell():
    if os.name != "nt":
        binary = shutil.which("bash")
        if not binary:
            raise InstallError("Bash is required to match Claude Code's statusline shell.")
        return {"kind": "bash", "executable": str(Path(binary).absolute())}
    override = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    git = shutil.which("git.exe")
    paths = [Path(git).parent.parent / "bin" / "bash.exe"] if git else []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(env):
            parent = Path(os.environ[env])
            paths.append(parent / ("Programs" if env == "LOCALAPPDATA" else "") / "Git/bin/bash.exe")
    if override:
        if not Path(override).is_file():
            raise InstallError("CLAUDE_CODE_GIT_BASH_PATH does not point to a file.")
        paths.insert(0, Path(override))
    for candidate in paths:
        if candidate.is_file():
            return {"kind": "bash", "executable": str(candidate.resolve())}
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise InstallError("Could not locate Git Bash or PowerShell for the statusline.")
    return {"kind": "powershell", "executable": powershell}


def python_path():
    return Path(getattr(sys, "_base_executable", None) or sys.executable).absolute()


def shell_command(shell, *parts):
    words = [part.as_posix() if isinstance(part, Path) and os.name == "nt" else str(part) for part in parts]
    if shell["kind"] == "powershell":
        return "& " + " ".join("'" + word.replace("'", "''") + "'" for word in words)
    return " ".join(shlex.quote(word) for word in words)


def prompt_tone(non_interactive):
    if non_interactive:
        return "sfw"
    stream = sys.stdin if sys.stdin.isatty() else None
    try:
        if stream is None:
            stream = open("CONIN$" if os.name == "nt" else "/dev/tty", encoding="utf-8")
    except OSError:
        return "sfw"
    try:
        if os.name == "posix":
            try:
                if os.tcgetpgrp(stream.fileno()) != os.getpgrp():
                    return "sfw"  # a background job must not stop waiting for terminal input
            except OSError:
                return "sfw"
        print("  Choose how hot to make it: [1] SFW (clean)  [2] NSFW (strong language)", file=sys.stderr)
        while True:
            print("  Roast level [1]: ", end="", file=sys.stderr, flush=True)
            answer = stream.readline().strip().lower()
            if answer in ("", "1", "sfw"):
                return "sfw"
            if answer in ("2", "nsfw"):
                return "nsfw"
            print("  Pick 1 or 2. The toaster can wait.", file=sys.stderr)
    finally:
        if stream is not sys.stdin:
            stream.close()


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("roastline_runtime", str(path))
    module = importlib.util.module_from_spec(spec)
    # Do not write __pycache__ in the source checkout or an installed payload.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class Home:
    def __init__(self):
        self.config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser().resolve()
        self.settings = self.config / "settings.json"
        self.root = self.config / "roastline"
        self.releases = self.root / "releases"
        self.backups = self.root / "backup"
        self.state = self.root / "state.json"
        self.options = self.root / "config.json"
        self.personal = self.root / "personal.json"
        self.skills = self.config / "skills"
        self.skill_dir = self.skills / "roastline"
        self.skill = self.skill_dir / "SKILL.md"

    def check_paths(self, include_skill=True):
        paths = [self.root, self.releases, self.backups]
        if include_skill:
            paths.extend((self.skills, self.skill_dir))
        for path in paths:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise InstallError("Expected a regular directory at {}".format(path))

    def read_settings(self):
        raw = raw_file(self.settings)
        return raw, object_json(raw, self.settings)

    def read_state(self):
        raw = raw_file(self.state)
        if raw is None:
            return None
        state = object_json(raw, self.state)
        if (state.get("schema") != 1 or type(state.get("original_present")) is not bool
                or type(state.get("settings_existed")) is not bool
                or not isinstance(state.get("applied"), dict)
                or not self.mentions_wrapper(state["applied"])
                or not re.fullmatch(r"release-[a-zA-Z0-9_-]+", str(state.get("release")))
                or not re.fullmatch(r"settings-[a-zA-Z0-9_-]+\.json", str(state.get("backup")))
                or not re.fullmatch(r"[a-f0-9]{64}", str(state.get("skill_sha")))):
            raise InstallError("Unrecognized installer state: {}".format(self.state))
        return state

    def mentions_wrapper(self, status):
        command = status.get("command") if isinstance(status, dict) else None
        return (isinstance(command, str) and "roastline.py" in command
                and (str(self.releases) in command or self.releases.as_posix() in command
                     or bool(re.search(r"roastline[/\\]releases[/\\]release-", command))))

    def owned(self, status, state):
        return bool(state and (status == state["applied"] or
                               (state.get("previous") is not None and status == state["previous"])))

    def original_is_current(self, current, state):
        return (("statusLine" in current) == state["original_present"]
                and current.get("statusLine") == state.get("original_statusline"))

    def change_settings(self, old, new):
        if raw_file(self.settings) != old:
            raise InstallError("Claude settings changed during installation. Try again.")
        write_atomic(self.settings, new, file_mode(self.settings))


def original_options(settings, state):
    if state:
        present, original = state["original_present"], state["original_statusline"]
    else:
        present, original = "statusLine" in settings, settings.get("statusLine")
    if present:
        if (not isinstance(original, dict) or original.get("type") != "command"
                or not isinstance(original.get("command"), str)
                or not original["command"].strip() or "\x00" in original["command"]
                or len(original["command"].encode("utf-8")) > 32_000):
            raise InstallError("This existing statusLine cannot be wrapped safely. Left it untouched.")
        padding = original.get("padding", 0)
        if type(padding) is not int or padding < 0:
            raise InstallError("This existing statusLine has unsupported padding. Left it untouched.")
    return present, original


def preflight(home):
    if not home.config.is_dir():
        raise InstallError("No Claude Code config directory at {}. Start Claude Code first.".format(home.config))
    home.check_paths()
    managed_guard()
    old, settings = home.read_settings()
    if settings.get("disableAllHooks") is True:
        raise InstallError("Claude Code disables custom statuslines when disableAllHooks is true. Left it unchanged.")
    state = home.read_state()
    status = settings.get("statusLine")
    if state and not home.owned(status, state) and not home.original_is_current(settings, state):
        raise InstallError("The statusline changed since Roastline was installed. Uninstall before reinstalling.")
    if not state and home.mentions_wrapper(status):
        raise InstallError("Found a Roastline command without its original backup. Refusing to overwrite it.")
    if (home.config / "commands/roastline.md").exists() or (home.skill_dir / ".claude-plugin").exists():
        raise InstallError("An existing /roastline command or plugin is in the way. Left it untouched.")
    skill = raw_file(home.skill)
    if skill is not None and (not state or sha(skill) != state["skill_sha"]):
        raise InstallError("An existing /roastline skill is not owned by this installer. Left it untouched.")
    return old, settings, state, original_options(settings, state)


def stage_release(home, integration, source=HERE):
    release = Path(tempfile.mkdtemp(prefix="release-", dir=str(home.releases)))
    try:
        (release / "roasts").mkdir()
        shutil.copyfile(source / "src/roastline.py", release / "roastline.py")
        shutil.copyfile(source / "config.default.json", release / "config.default.json")
        for tone in TONES:
            shutil.copyfile(source / "src/roasts" / (tone + ".json"), release / "roasts" / (tone + ".json"))
        write_atomic(release / "integration.json", json_bytes(integration))
        sample = (source / "tests/fixtures/statusline_input_sample.json").read_bytes()
        check = subprocess.run(
            [str(python_path()), "-I", str(release / "roastline.py"), "--smoke"],
            input=sample, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6,
        )
        if check.returncode or not check.stdout.strip():
            raise InstallError("Startup check failed. Your Claude settings are unchanged.")
        return release
    except BaseException:
        shutil.rmtree(release)
        raise


def install(home, args):
    old, settings, state, (present, original) = preflight(home)
    project_warning(Path.cwd(), home.config)
    if args.dry_run:
        message("PLAN", "Would {} at {}".format("wrap the existing statusline" if present else
                                                "add a statusline above Claude's footer", home.settings))
        message("PLAN", "Would install both 100-line packs and the global /roastline skill")
        return
    shell = resolve_shell()
    runtime = load_runtime(HERE / "src/roastline.py")
    if runtime.VERSION != VERSION:
        raise InstallError("Bundled renderer version does not match the installer.")
    skill = (HERE / "skills/roastline/SKILL.md").read_bytes()
    with config_lock(home.config):
        old, settings, state, (present, original) = preflight(home)
        config = runtime.config_from(home.options if home.options.exists() else HERE / "config.default.json")
        tone = args.tone or (config["tone"] if home.options.exists() else prompt_tone(args.non_interactive))
        config["tone"] = tone
        message("OK", "Tone: {}".format(tone.upper()))
        private_dir(home.root)
        private_dir(home.releases)
        private_dir(home.backups)
        integration = {"original_command": original["command"] if present else None,
                       "padding": original.get("padding", 0) if present else 0, "shell": shell}
        message("..", "Staging the renderer and checking both roast packs")
        release = stage_release(home, integration)
        applied = dict(original) if present else {"type": "command", "refreshInterval": 15}
        applied["command"] = shell_command(shell, python_path(), "-I", release / "roastline.py")
        refresh = args.refresh or (state or {}).get("refresh")
        if refresh is not None:
            applied["refreshInterval"] = refresh
        backup_name = state["backup"] if state else "settings-{}-{}.json".format(
            datetime.now().strftime("%Y%m%d-%H%M%S"), release.name[8:])
        next_state = {
            "schema": 1, "version": VERSION, "backup": backup_name, "release": release.name,
            "original_present": present, "original_statusline": original,
            "settings_existed": state["settings_existed"] if state else old is not None,
            "skill_sha": sha(skill), "applied": applied, "refresh": refresh,
            "previous": settings.get("statusLine") if home.owned(settings.get("statusLine"), state) else None,
        }
        with ExitStack() as rollback:
            rollback.callback(shutil.rmtree, release)

            def save(path, contents):
                current = raw_file(path)
                mode = file_mode(path)
                rollback.callback(write_atomic, path, current, mode)
                write_atomic(path, contents, mode)

            if not state:
                save(home.backups / backup_name, old if old is not None else b"{}\n")
            elif raw_file(home.backups / backup_name) is None:
                raise InstallError("Original settings backup is missing. Left your statusline untouched.")
            message("OK", "Original settings backed up" if not state else "Original backup retained")
            private_dir(home.skills)
            private_dir(home.skill_dir)
            save(home.skill, skill)
            save(home.root / "install.py", Path(__file__).read_bytes())
            save(home.options, json_bytes(config))
            save(home.state, json_bytes(next_state))
            home.change_settings(old, settings_bytes({**settings, "statusLine": applied}, old))
            rollback.pop_all()
    message("OK", "Global /roastline skill installed")
    message("OK", "Startup check passed; {}".format("existing command preserved" if present else
                                                    "Roastline statusline enabled"))
    if present and "refreshInterval" not in applied:
        message("NOTE", "Kept event-driven refresh. Pass --refresh 15 at install for idle-time updates.")
    if not present:
        message("NOTE", "Claude adds a custom row and may hide some built-in shortcut hints until uninstall.")
    print("\n  Try /roastline in Claude Code. Uninstall with:\n  {}".format(
        shell_command(shell, python_path(), "-I", home.root / "install.py", "--uninstall")), flush=True)


def uninstall(home, args):
    home.check_paths()
    state = home.read_state()
    if not state:
        raise InstallError("No Roastline installation receipt. Nothing was removed.")
    if args.dry_run:
        _, settings = home.read_settings()
        message("PLAN", "Would {} and remove the owned skill".format(
            "restore the original statusline" if home.owned(settings.get("statusLine"), state)
            else "keep your current statusline"))
        return
    with config_lock(home.config):
        state = home.read_state()
        if not state:
            raise InstallError("No Roastline installation receipt. Nothing was removed.")
        old, settings = home.read_settings()
        current = settings.get("statusLine")
        if home.owned(current, state):
            backup_path = home.backups / state["backup"]
            backup = raw_file(backup_path)
            if backup is None:
                raise InstallError("Missing settings backup. Nothing was removed.")
            before = object_json(backup, backup_path)
            updated = dict(settings)
            if state["original_present"]:
                updated["statusLine"] = state["original_statusline"]
            else:
                updated.pop("statusLine", None)
            # Restore exact bytes only if the file still matches what the installer wrote.
            if updated == before and old == settings_bytes(
                {**before, "statusLine": current}, backup if state["settings_existed"] else None
            ):
                restored = backup if state["settings_existed"] else None
            else:
                restored = settings_bytes(updated, old)
            home.change_settings(old, restored)
            message("OK", "Original statusline restored")
        elif home.mentions_wrapper(current):
            raise InstallError("Your edited statusline still calls Roastline. Restore its original setting before removing files.")
        else:
            message("OK", "Current statusline left alone")
        skill = None if home.skill.is_symlink() else raw_file(home.skill)
        if skill is not None and sha(skill) == state["skill_sha"]:
            home.skill.unlink()
            try:
                home.skill_dir.rmdir()
            except OSError:
                pass
            message("OK", "Removed the global skill")
        elif skill is not None or home.skill.is_symlink():
            message("NOTE", "Kept the user-modified skill")
        write_atomic(home.state, None)
        if args.purge:
            shutil.rmtree(home.root)
            message("OK", "Removed Roastline files, backups and saved jokes")
        else:
            message("OK", "Backups and saved jokes kept at {}".format(home.root))


def status(home):
    home.check_paths(include_skill=False)
    state = home.read_state()
    if state is None:
        message("INFO", "Roastline is not installed")
        return
    _, settings = home.read_settings()
    enabled = home.owned(settings.get("statusLine"), state)
    options = object_json(raw_file(home.options), home.options)
    message("INFO", "Roastline {} • {} • {}".format(
        state.get("version", "?"), "configured" if enabled else "not the current user statusline",
        "paused" if options.get("show_roasts") is False else str(options.get("tone", "sfw")).upper()))
    message("INFO", "Original settings backup: {}".format(home.backups / state["backup"]))


def manage(home, args):
    home.check_paths(include_skill=False)
    if not home.read_state():
        raise InstallError("Roastline is not installed.")
    with config_lock(home.config):
        state = home.read_state()
        if not state:
            raise InstallError("Roastline is not installed.")
        runtime = load_runtime(home.releases / state["release"] / "roastline.py")
        options = runtime.config_from(home.options)
        if args.add_roasts:
            trigger = args.add_roasts
            if trigger not in runtime.TRIGGERS:
                raise InstallError("Unknown trigger. Use --triggers to list available triggers.")
            data = sys.stdin.buffer.read(4097)
            lines = json.loads(data) if len(data) <= 4096 else None
            tone = options["tone"]
            if (not isinstance(lines, list) or not 2 <= len(lines) <= 5
                    or not all(runtime.valid_line(line, tone, personal=True) for line in lines)):
                raise InstallError("Supply 2–5 short, single-line jokes as a JSON array, in the active tone, without links or identifiers.")
            stock = runtime.load_pack(home.releases / state["release"] / "roasts" / (tone + ".json"), tone)
            bank = object_json(raw_file(home.personal, 512_000), home.personal)
            group = bank.setdefault(tone, {})
            if not isinstance(group, dict):
                raise InstallError("Invalid personalized roast bank.")
            saved = runtime.personal_lines(home.personal, tone, trigger)
            seen = {line.casefold() for line in saved + stock[trigger]}
            new = []
            for line in lines:
                if line.casefold() not in seen:
                    new.append(line)
                    seen.add(line.casefold())
            if not new:
                message("INFO", "Those jokes are already in rotation.")
                return
            group[trigger] = (saved + new)[-runtime.MAX_PERSONAL:]
            write_atomic(home.personal, json_bytes(bank))
            message("OK", "Saved {} {} roast(s) for {}".format(len(new), tone.upper(), trigger))
            return
        if args.tone:
            options["tone"] = args.tone
        if args.pause or args.resume:
            options["show_roasts"] = args.resume
        write_atomic(home.options, json_bytes(options))
        message("OK", "Roasts {} ({})".format("enabled" if options["show_roasts"] else "paused",
                                             options["tone"].upper()))


def positive(value):
    if not value.isdigit() or not 1 <= int(value) <= 3600:
        raise argparse.ArgumentTypeError("refresh must be between 1 and 3600 seconds")
    return int(value)


def main(argv=None):
    if sys.version_info < (3, 8):
        print("Roastline needs Python 3.8 or newer.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description="Install or manage Roastline for Claude Code")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    action.add_argument("--pause", action="store_true")
    action.add_argument("--resume", action="store_true")
    action.add_argument("--add-roasts", metavar="TRIGGER", help="read 2–5 approved jokes as JSON from stdin")
    action.add_argument("--triggers", action="store_true")
    parser.add_argument("--tone", choices=TONES)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--refresh", type=positive, metavar="SECONDS")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge", action="store_true", help="remove saved data and backups on uninstall")
    parser.add_argument("--version", action="version", version="Roastline " + VERSION)
    args = parser.parse_args(argv)
    if args.purge and not args.uninstall:
        parser.error("--purge requires --uninstall")
    manage_action = args.pause or args.resume or args.add_roasts
    bundle = (HERE / "src/roastline.py").is_file()
    if args.dry_run and (args.status or args.triggers or manage_action or (not bundle and not args.uninstall)):
        parser.error("--dry-run is for install and uninstall only")
    if args.refresh and (not bundle or args.uninstall or args.status or args.triggers or manage_action):
        parser.error("--refresh is only set from the installer")
    if args.tone and (args.status or args.triggers or args.uninstall or args.add_roasts):
        parser.error("--tone is for install, tone switching, pause or resume")
    home = Home()
    try:
        if args.status:
            status(home)
        elif args.uninstall:
            uninstall(home, args)
        elif args.triggers:
            home.check_paths(include_skill=False)
            state = None if bundle else home.read_state()
            path = HERE / "src/roastline.py" if bundle else (
                home.releases / state["release"] / "roastline.py" if state else None)
            if path is None:
                raise InstallError("Roastline is not installed.")
            print("\n".join(load_runtime(path).TRIGGERS))
        elif manage_action or not bundle and args.tone:
            manage(home, args)
        elif bundle:
            if os.environ.get("ROASTLINE_BOOTSTRAPPED") != "1":
                print(BANNER, flush=True)
            message("..", "Checking your Claude Code settings")
            install(home, args)
        else:
            parser.print_help()
        return 0
    except (InstallError, OSError, ValueError, KeyError, RecursionError, subprocess.TimeoutExpired) as exc:
        print("  [FAIL] {}".format(exc), file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\n  [STOP] Installation cancelled.", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
