import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install as installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="roastline 'safe ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / "claude config"
        self.config.mkdir()
        self.settings = self.config / "settings.json"
        self.home = self.config / "roastline"
        self.helper = self.home / "install.py"
        self.skill = self.config / "skills/roastline/SKILL.md"
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.config), "NO_COLOR": "1",
                    "COLUMNS": "160", "HOME": str(self.directory), "USERPROFILE": str(self.directory)}
        self.env.pop("ROASTLINE_BOOTSTRAPPED", None)
        self.env.pop("ROASTLINE_SRC", None)

    def call(self, *args, helper=False, payload=None, success=True):
        proc = subprocess.run(
            [sys.executable, "-I", str(self.helper if helper else ROOT / "install.py"), *args],
            env=self.env, cwd=self.directory, input=payload or b"", capture_output=True, timeout=20,
            start_new_session=os.name == "posix",
        )
        self.assertEqual(proc.returncode == 0, success, (proc.stdout, proc.stderr))
        return proc

    def config_value(self):
        return json.loads(self.settings.read_bytes())

    def install(self, *args):
        return self.call("--non-interactive", *args)

    def archive(self, fixture, extra=False):
        name = "Roastline-" + installer.VERSION
        with tarfile.open(fixture, "w:gz") as archive:
            top = tarfile.TarInfo(name)
            top.type = tarfile.DIRTYPE
            archive.addfile(top)
            for filename in ("install.sh", *installer.BUNDLE_FILES):
                archive.add(ROOT / filename, arcname=name + "/" + filename)
            if extra:
                bonus = tarfile.TarInfo(name + "/../../attempted-side-effect")
                bonus.size = 4
                archive.addfile(bonus, io.BytesIO(b"oops"))

    @property
    def launcher(self):
        return self.config / "roastline-bin" / installer.LAUNCHER_NAME

    def test_fresh_install_and_uninstall_remove_only_new_setting(self):
        self.install()
        self.assertIn("statusLine", self.config_value())
        self.assertTrue(self.launcher.is_file())
        self.assertFalse((self.directory / ".local/bin" / installer.LAUNCHER_NAME).exists())
        self.assertIn("name: roastline", self.skill.read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.home / "config.json").read_bytes())["tone"], "sfw")
        self.assertTrue(self.call("--status", helper=True).stdout)
        self.call("--uninstall", helper=True)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.skill.exists())
        self.assertTrue(any((self.home / "backup").iterdir()))

    def test_hooks_for_occasional_roasts_leave_other_hooks_alone(self):
        theirs = {"matcher": "startup", "hooks": [{"type": "command", "command": "bash herdr.sh session"}]}
        original = json.dumps({"hooks": {"SessionStart": [theirs]}, "x": 1}, indent=2).encode() + b"\n"
        self.settings.write_bytes(original)
        self.install()
        upgrade = self.config_value()
        old_roastline = {"hooks": [{"type": "command", "command": "python roastline/hook.py session"}]}
        upgrade["hooks"]["SessionStart"].append(old_roastline)
        self.settings.write_text(json.dumps(upgrade, indent=2) + "\n", encoding="utf-8")
        self.install()  # reinstalling never duplicates Roastline's hooks
        hooks = self.config_value()["hooks"]
        self.assertEqual(hooks["SessionStart"], [theirs])
        self.assertEqual(len(hooks["UserPromptSubmit"]), 1)
        self.assertEqual(len(hooks["Stop"]), 1)
        self.assertIn("hook.py", hooks["Stop"][0]["hooks"][0]["command"])
        self.assertTrue((self.home / "hook.py").is_file())
        self.assertIn(b"Occasional session roasts enabled", self.install().stdout)
        self.call("--uninstall", helper=True)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertFalse((self.home / "hook.py").exists())

    def test_hook_script_adds_hidden_context_and_stays_quiet_when_paused(self):
        self.install()
        hook = self.home / "hook.py"

        def run(mode, payload):
            driver = ("import secrets, runpy, sys; secrets.randbelow = lambda _: 0; "
                      "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
            proc = subprocess.run([sys.executable, "-I", "-c", driver, str(hook), mode], input=payload,
                                  capture_output=True, timeout=10, env=self.env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return proc.stdout

        transcript = self.directory / "turn.jsonl"
        payload = json.dumps({"session_id": "a", "transcript_path": str(transcript),
                              "prompt": "Shorten the summary again"}).encode()
        offered = json.loads(run("prompt", payload))["hookSpecificOutput"]
        self.assertEqual(offered["hookEventName"], "UserPromptSubmit")
        token = offered["additionalContext"].split("using the token ")[1].split(".")[0]
        self.assertEqual(run("prompt", payload), b"")
        transcript.write_text(json.dumps({
            "type": "user", "promptSource": "typed", "uuid": "user-1",
            "message": {"content": "Shorten the summary again"},
        }) + "\n", encoding="utf-8")
        line = b'"Now with 30 percent less summary."'
        self.call("--session-roast", token, helper=True, payload=line)
        self.call("--session-roast", token, helper=True, payload=line, success=False)
        self.assertFalse((self.home / "personal.json").exists())
        release = json.loads((self.home / "state.json").read_bytes())["release"]
        renderer = self.home / "releases" / release / "roastline.py"

        def render():
            return subprocess.run([sys.executable, "-I", str(renderer)], input=payload, env=self.env,
                                  check=True, capture_output=True, timeout=10).stdout

        self.assertIn(b"Now with 30 percent less summary.", render())
        self.assertEqual(run("prompt", b"not json"), b"")
        self.assertEqual(run("bogus", b"{}"), b"")
        self.call("--pause", helper=True)
        self.assertEqual(run("prompt", payload), b"")
        self.call("--resume", helper=True)
        self.assertNotIn(b"Now with 30 percent less summary.", render())

    def test_missing_config_requires_explicit_consent_in_headless_mode(self):
        missing = self.directory / "missing profile"
        self.env["CLAUDE_CONFIG_DIR"] = str(missing)
        result = self.call("--non-interactive", success=False)
        self.assertIn(b"--create-config", result.stderr)
        self.assertFalse(missing.exists())
        self.call("--dry-run")
        self.assertFalse(missing.exists())
        self.call("--non-interactive", "--create-config", "--tone", "sfw")
        self.assertTrue((missing / "settings.json").exists())
        subprocess.run([sys.executable, "-I", str(missing / "roastline/install.py"), "--uninstall"],
                       env=self.env, check=True, timeout=10, capture_output=True)
        self.assertFalse((missing / "settings.json").exists())

    @unittest.skipUnless(os.name == "posix", "Windows CLI detection is for WSL")
    def test_windows_claude_executable_is_not_wrapped_from_wsl(self):
        windows = self.directory / "claude.exe"
        windows.write_bytes(b"not executed")
        with patch.dict(os.environ, self.env), patch.object(installer.shutil, "which", return_value=str(windows)):
            self.assertEqual(installer.main(["--non-interactive", "--create-config"]), 1)
        self.assertEqual(list(self.config.iterdir()), [])

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX pseudoterminal required")
    def test_missing_config_can_be_created_through_piped_installer(self):
        self.env["CLAUDE_CONFIG_DIR"] = str(self.directory / "new config")
        script = shlex.quote(str(ROOT / "install.sh"))
        output = self.in_terminal("cat " + script + " | bash -s -- --tone sfw",
                                  b"Create this directory", b"y\n")
        self.assertIn(b"Created Claude config directory", output)
        self.assertTrue((self.directory / "new config/settings.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX launcher")
    def test_normal_profile_installs_a_command_callable_from_path(self):
        config = self.directory / ".claude"
        config.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(config)
        bin_dir = self.directory / ".local/bin"
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.install()
        launcher = bin_dir / "roastline"
        self.assertTrue(os.access(launcher, os.X_OK))

        def command(*flags):
            result = subprocess.run(["roastline", *flags], cwd=self.directory, env=self.env,
                                    capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            return result.stdout

        output = command("--help")
        self.assertIn(b"usage: roastline", output)
        for flag in (b"--tone", b"--update", b"--uninstall"):
            self.assertIn(flag, output)
        command("--tone", "nsfw")
        self.assertIn(b"NSFW", command("--status"))
        self.assertIn(b"NSFW", command("--tone", "--non-interactive"))
        command("--uninstall")
        self.assertFalse(launcher.exists())
        self.assertFalse((config / "settings.json").exists())

    def test_unowned_launcher_is_not_replaced_or_removed(self):
        self.launcher.parent.mkdir(parents=True)
        custom = b"existing tool with the same name"
        self.launcher.write_bytes(custom)
        self.install()
        self.assertEqual(self.launcher.read_bytes(), custom)
        self.call("--uninstall", helper=True)
        self.assertEqual(self.launcher.read_bytes(), custom)

    def test_modified_launcher_is_left_untouched_on_uninstall(self):
        self.install()
        custom = b"locally modified launcher"
        self.launcher.write_bytes(custom)
        self.call("--uninstall", "--purge", helper=True)
        self.assertEqual(self.launcher.read_bytes(), custom)

    def test_installed_helper_cannot_uninstall_a_different_profile(self):
        self.install()
        alternate = self.directory / "another config"
        alternate.mkdir()
        other_settings = alternate / "settings.json"
        prior = b'{"statusLine":{"type":"command","command":"echo somebody else"}}'
        other_settings.write_bytes(prior)
        self.env["CLAUDE_CONFIG_DIR"] = str(alternate)
        self.call("--uninstall", helper=True)
        self.assertFalse(self.settings.exists())
        self.assertEqual(other_settings.read_bytes(), prior)

    def test_reinstall_keeps_first_backup_tone_and_original_bytes(self):
        original = b'{\r\n\t"statusLine": {"type":"command","padding":3,"command":"echo custom"},\r\n\t"a":25\r\n}\r\n'
        self.settings.write_bytes(original)
        self.install("--tone", "nsfw")
        backup = next((self.home / "backup").iterdir())
        self.assertEqual(backup.read_bytes(), original)
        self.install()
        self.assertEqual(list((self.home / "backup").iterdir()), [backup])
        self.assertEqual(self.config_value()["statusLine"]["padding"], 3)
        self.assertNotIn("refreshInterval", self.config_value()["statusLine"])
        self.assertIn(b"NSFW", self.call("--status", helper=True).stdout)
        self.call("--uninstall", helper=True)
        self.assertEqual(self.settings.read_bytes(), original)

    def test_reinstall_after_uninstall_backs_up_current_user_choice(self):
        self.settings.write_text('{"statusLine":{"type":"command","command":"echo A"}}', encoding="utf-8")
        self.install()
        self.call("--uninstall", helper=True)
        new = b'{"statusLine":{"type":"command","command":"echo B"}}'
        self.settings.write_bytes(new)
        self.install()
        self.call("--uninstall", helper=True)
        self.assertEqual(self.settings.read_bytes(), new)
        self.assertEqual(len(list((self.home / "backup").iterdir())), 2)

    def test_non_statusline_changes_survive_uninstall(self):
        self.settings.write_text('{"statusLine":{"type":"command","command":"echo old"},"x":1}', encoding="utf-8")
        self.install("--refresh", "12")
        after = self.config_value()
        self.assertEqual(after["statusLine"]["refreshInterval"], 12)
        after["x"] = 2
        after["new"] = {"true": True}
        self.settings.write_text(json.dumps(after), encoding="utf-8")
        self.call("--uninstall", helper=True)
        self.assertEqual(self.config_value(), {"statusLine": {"type": "command", "command": "echo old"},
                                               "x": 2, "new": {"true": True}})

    def test_user_replacement_and_modified_skill_are_left_untouched(self):
        self.install()
        other = b'{"statusLine":{"type":"command","command":"echo another"}}'
        self.settings.write_bytes(other)
        self.skill.write_text("My custom skill notes", encoding="utf-8")
        self.call("--non-interactive", success=False)
        self.call("--uninstall", "--purge", helper=True)
        self.assertEqual(self.settings.read_bytes(), other)
        self.assertEqual(self.skill.read_text(encoding="utf-8"), "My custom skill notes")
        self.assertFalse(self.home.exists())

    def test_dry_run_and_invalid_settings_do_not_touch_files(self):
        proc = self.call("--dry-run")
        self.assertIn(b"Would add a statusline", proc.stdout)
        self.assertEqual(list(self.config.iterdir()), [])
        for invalid in (b"{invalid", b'{"same":1,"same":2}', b"[]", b'{"outOfRange":1e999}',
                        b'{"statusLine":{"type":"future"}}', b'{"disableAllHooks":true}'):
            self.settings.write_bytes(invalid)
            self.call("--non-interactive", success=False)
            self.assertEqual(self.settings.read_bytes(), invalid)
            self.assertEqual(list(self.config.iterdir()), [self.settings])

    def test_skill_name_collision_aborts_without_overwriting(self):
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("existing skill", encoding="utf-8")
        self.call("--non-interactive", success=False)
        self.assertEqual(self.skill.read_text(encoding="utf-8"), "existing skill")
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.home.exists())

    def test_startup_check_failure_keeps_statusline_and_never_activates(self):
        old = b'{"statusLine":{"type":"command","command":"echo keepme"}}'
        self.settings.write_bytes(old)
        with patch.dict(os.environ, self.env), patch.object(installer, "project_warning"), patch.object(
            installer.subprocess, "run"
        ) as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=3, stdout=b"")
            code = installer.main(["--non-interactive"])
        self.assertEqual(code, 1)
        self.assertEqual(self.settings.read_bytes(), old)
        self.assertFalse(self.skill.exists())
        self.assertFalse((self.home / "state.json").exists())
        self.assertEqual(list((self.home / "releases").iterdir()), [])

    def test_failed_commit_rolls_back_staged_artifacts(self):
        original = b'{"statusLine":{"type":"command","command":"echo original"}}'
        self.settings.write_bytes(original)
        with patch.dict(os.environ, self.env), patch.object(
            installer.Home, "change_settings", side_effect=installer.InstallError("changed")
        ), patch.object(installer, "project_warning"):
            code = installer.main(["--non-interactive"])
        self.assertEqual(code, 1)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.launcher.exists())
        self.assertFalse((self.home / "state.json").exists())
        for path in (self.home / "releases", self.home / "backup"):
            self.assertEqual(list(path.iterdir()), [])

    def test_managed_statusline_is_not_changed(self):
        managed = self.directory / "managed.json"
        managed.write_text('{"statusLine":{"type":"command","command":"echo managed"}}', encoding="utf-8")
        with patch.dict(os.environ, self.env), patch.object(installer, "managed_sources", return_value=[managed]):
            code = installer.main(["--non-interactive"])
        self.assertEqual(code, 1)
        self.assertEqual(list(self.config.iterdir()), [])

    def test_personal_bank_requires_approved_json_and_is_separate_per_tone(self):
        self.install()
        clean = b'["The spec is having a rerun.", "Your prompt came with a replay button."]'
        self.call("--add-roasts", "retry", helper=True, payload=clean)
        self.call("--add-roasts", "retry", helper=True, payload=clean)
        self.call("--add-roasts", "retry", helper=True, success=False,
                  payload=b'["No.", "Email user@example.com."]')
        self.call("--add-roasts", "retry", helper=True, success=False,
                  payload=b'["No.", "This damn prompt."]')
        self.call("--tone", "nsfw", helper=True)
        strong = b'["This damn prompt.", "That stupid button did not work twice."]'
        self.call("--add-roasts", "retry", helper=True, payload=strong)
        bank = json.loads((self.home / "personal.json").read_bytes())
        self.assertEqual(bank, {"sfw": {"retry": json.loads(clean)}, "nsfw": {"retry": json.loads(strong)}})
        self.call("--pause", helper=True)
        self.assertIn(b"paused", self.call("--status", helper=True).stdout)
        self.call("--resume", "--tone", "sfw", helper=True)
        self.assertIn(b"SFW", self.call("--status", helper=True).stdout)

    def test_replace_personal_cli(self):
        self.install()
        self.call("--pet", "cat", helper=True, success=False)  # pets were removed
        payload = b'{"retry":["The prompt has a sequel.", "The send button wants royalties."]}'
        self.call("--replace-personal", helper=True, payload=payload)
        bank = json.loads(self.call("--personal", helper=True).stdout)
        self.assertEqual(bank["roasts"]["sfw"], json.loads(payload))
        self.call("--replace-personal", helper=True, payload=b'{"retry":["repeat","repeat"]}', success=False)
        self.assertEqual(json.loads(self.call("--personal", helper=True).stdout), bank)
        self.assertNotIn(b"Examples:", self.call("--help", helper=True).stdout)

    def test_update_uses_resolved_commit_and_preserves_user_state(self):
        original = b'{"statusLine":{"type":"command","command":"echo keep"}}'
        self.settings.write_bytes(original)
        self.install("--tone", "nsfw")
        self.call("--pause", helper=True)
        self.call("--add-roasts", "retry", helper=True,
                  payload=b'["The spec is having a rerun.", "Your prompt came with a replay button."]')
        bank = (self.home / "personal.json").read_bytes()
        state_path = self.home / "state.json"
        previous = json.loads(state_path.read_bytes())
        self.launcher.unlink()
        previous.pop("launcher")
        previous["version"] = "1.0.0"
        state_path.write_text(json.dumps(previous), encoding="utf-8")
        fixture = self.directory / "update.tgz"
        self.archive(fixture, extra=True)
        commit = "abcdef0123456789abcdef0123456789abcdef01"
        meta = json.dumps({"object": {"sha": commit}}).encode()
        with patch.dict(os.environ, self.env), patch.object(
            installer, "fetch", side_effect=[meta, fixture.read_bytes()]
        ) as fetch:
            code = installer.main(["--update"])
        self.assertEqual(code, 0)
        self.assertEqual([item.args[0] for item in fetch.call_args_list], [
            "https://api.github.com/repos/19Naveen/Roastline/git/ref/heads/main",
            "https://codeload.github.com/19Naveen/Roastline/tar.gz/" + commit,
        ])
        current = json.loads(state_path.read_bytes())
        self.assertEqual(current["version"], installer.VERSION)
        self.assertEqual(current["backup"], previous["backup"])
        self.assertEqual((self.home / "personal.json").read_bytes(), bank)
        self.assertTrue(self.launcher.exists())
        self.assertFalse((self.directory / "attempted-side-effect").exists())
        options = json.loads((self.home / "config.json").read_bytes())
        self.assertEqual(options["tone"], "nsfw")
        self.assertFalse(options["show_roasts"])
        self.call("--uninstall", helper=True)
        self.assertEqual(self.settings.read_bytes(), original)

    def test_update_leaves_settings_untouched_if_current_or_download_fails(self):
        self.install()
        raw = self.settings.read_bytes()
        state = (self.home / "state.json").read_bytes()
        fixture = self.directory / "update.tgz"
        self.archive(fixture)
        meta = json.dumps({"object": {"sha": "1" * 40}}).encode()
        for replies, expected in (([meta, fixture.read_bytes()], 0), ([meta, b"not an archive"], 1),
                                  ([b'{"object":{"sha":"not-a-sha"}}'], 1)):
            with self.subTest(expected=expected), patch.dict(os.environ, self.env), patch.object(
                installer, "fetch", side_effect=replies
            ), patch.object(installer.subprocess, "run") as run:
                self.assertEqual(installer.main(["--update"]), expected)
                run.assert_not_called()
            self.assertEqual(self.settings.read_bytes(), raw)
            self.assertEqual((self.home / "state.json").read_bytes(), state)

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX Bash required")
    def test_wrapper_forwards_stdin_environment_cwd_and_returncode(self):
        child = self.directory / "original script.py"
        child.write_text("""import os, sys
from pathlib import Path
Path('received').write_bytes(sys.stdin.buffer.read())
sys.stdout.buffer.write(b'first\\n' + os.environ['ROASTLINE_TEST_SEGMENT'].encode() + b'\\n')
sys.exit(int(os.environ.get('ROASTLINE_TEST_EXIT', '0')))
""", encoding="utf-8")
        command = "{} {}".format(shlex.quote(sys.executable), shlex.quote(str(child)))
        original = {"statusLine": {"type": "command", "command": command, "padding": 2,
                                   "refreshInterval": 29, "hideVimModeIndicator": True}}
        self.settings.write_text(json.dumps(original), encoding="utf-8")
        self.install()
        self.assertEqual(child.read_text(encoding="utf-8").splitlines()[0], "import os, sys")
        applied = self.config_value()["statusLine"]
        self.assertEqual({**applied, "command": command}, original["statusLine"])
        payload = b'{"session_id": "wrapper-test", "effort": {"level": "medium"}}  \n'
        env = {**self.env, "ROASTLINE_TEST_SEGMENT": "existing"}

        def run(**extra):
            return subprocess.run(["bash", "-c", applied["command"]], input=payload, env={**env, **extra},
                                  cwd=self.directory, capture_output=True, timeout=6)

        proc = run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith(b"first\nexisting"))
        self.assertEqual((self.directory / "received").read_bytes(), payload)
        self.assertEqual(proc.stdout, b"first\nexisting\n")
        payload = payload.replace(b"medium", b"high")
        self.assertIn(b"Effort high: ", run().stdout.splitlines()[1])
        for extra in ({"COLUMNS": "8"}, {"COLUMNS": ""}, {"ROASTLINE_TEST_EXIT": "7"}):
            proc = run(**extra)
            self.assertEqual(proc.stdout, b"first\nexisting\n")
            self.assertEqual(proc.returncode, int(extra.get("ROASTLINE_TEST_EXIT", "0")))
        (self.home / "config.json").write_text("garbage", encoding="utf-8")
        self.assertEqual(run().stdout, b"first\nexisting\n")

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX Bash required")
    def test_piped_bootstrap_headless_and_release_bundle(self):
        # Fake only the download boundary; use the real tar reader and installer.
        fixture = self.directory / "release.tgz"
        commit = "0123456789abcdef0123456789abcdef01234567"
        self.archive(fixture, extra=True)
        binary = self.directory / "curl"
        binary.write_text("""#!/bin/bash
set -eu
while [[ $# -gt 2 ]]; do
  case "$1" in
    "https://codeload.github.com/19Naveen/Roastline/tar.gz/$MOCK_REF") found=1;;
  esac
  shift
done
[[ ${found:-} == 1 && "$1" == -o ]]
cp -- "$MOCK_ARCHIVE" "$2"
""", encoding="utf-8")
        binary.chmod(0o755)
        env = {**self.env, "PATH": str(self.directory) + os.pathsep + self.env["PATH"],
               "MOCK_ARCHIVE": str(fixture)}
        for args, archive_ref in (([], "refs/heads/main"), (["--ref", commit], commit)):
            with self.subTest(archive_ref=archive_ref):
                proc = subprocess.run(["bash", "-s", "--", *args], cwd=self.directory,
                                      env={**env, "MOCK_REF": archive_ref},
                                      input=(ROOT / "install.sh").read_bytes(), capture_output=True,
                                      timeout=15, start_new_session=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                version = ("Downloading Roastline v" + installer.VERSION).encode()
                for msg in (b"ROASTLINE", version, b"Tone: SFW", b"Startup check passed"):
                    self.assertIn(msg, proc.stdout)
                self.assertNotIn(b"Roast level", proc.stderr)
        self.assertFalse((self.directory / "attempted-side-effect").exists())

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX pseudoterminal required")
    def test_piped_bootstrap_can_ask_for_tone_without_consuming_script_stdin(self):
        script = shlex.quote(str(ROOT / "install.sh"))
        output = self.in_terminal("cat " + script + " | bash", b"Roast level [1]:", b"2\n")
        self.assertIn(b"Tone: NSFW", output)
        self.assertEqual(json.loads((self.home / "config.json").read_bytes())["tone"], "nsfw")
        output = self.in_terminal(shlex.quote(str(self.launcher)) + " --tone", b"Roast level [2]:", b"1\n")
        self.assertIn(b"Roasts enabled (SFW)", output)
        self.assertEqual(json.loads((self.home / "config.json").read_bytes())["tone"], "sfw")

    def in_terminal(self, command, prompt, answer):
        import errno
        import pty
        import select
        import signal
        import time

        pid, terminal = pty.fork()
        if pid == 0:
            os.chdir(self.directory)
            os.execvpe("bash", ["bash", "-c", command],
                       {**self.env, "ROASTLINE_SRC": str(ROOT)})
        output = bytearray()
        answered = False
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                readable, _, _ = select.select([terminal], [], [], max(0, deadline - time.monotonic()))
                if not readable:
                    self.fail("installer hung: {!r}".format(output))
                try:
                    chunk = os.read(terminal, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
                if prompt in output and not answered:
                    os.write(terminal, answer)
                    answered = True
            else:
                self.fail("installer timed out: {!r}".format(output))
            _, status = os.waitpid(pid, 0)
            pid = None
            self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, output)
        finally:
            os.close(terminal)
            if pid is not None:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
        self.assertTrue(answered, output)
        return bytes(output)


if __name__ == "__main__":
    unittest.main()
