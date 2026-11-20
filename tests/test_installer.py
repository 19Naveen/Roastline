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

    def test_fresh_install_and_uninstall_remove_only_new_setting(self):
        self.install()
        self.assertIn("statusLine", self.config_value())
        self.assertIn("name: roastline", self.skill.read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.home / "config.json").read_bytes())["tone"], "sfw")
        self.assertTrue(self.call("--status", helper=True).stdout)
        self.call("--uninstall", helper=True)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.skill.exists())
        self.assertTrue(any((self.home / "backup").iterdir()))

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
        payload = b'{"effort": {"level": "medium"}}  \n'
        env = {**self.env, "ROASTLINE_TEST_SEGMENT": "existing"}

        def run(**extra):
            return subprocess.run(["bash", "-c", applied["command"]], input=payload, env={**env, **extra},
                                  cwd=self.directory, capture_output=True, timeout=6)

        proc = run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith(b"first\nexisting"))
        self.assertEqual((self.directory / "received").read_bytes(), payload)
        self.assertIn(b": ", proc.stdout.splitlines()[1])
        for extra in ({"COLUMNS": "12"}, {"COLUMNS": ""}, {"ROASTLINE_TEST_EXIT": "7"}):
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
        with tarfile.open(fixture, "w:gz") as archive:
            top = tarfile.TarInfo("Roastline-1.0.0")
            top.type = tarfile.DIRTYPE
            archive.addfile(top)
            archive.add(ROOT / "src", arcname="Roastline-1.0.0/src")
            for filename in ("install.sh", "install.py", "config.default.json", "skills/roastline/SKILL.md",
                             "tests/fixtures/statusline_input_sample.json"):
                archive.add(ROOT / filename, arcname="Roastline-1.0.0/" + filename)
            bonus = tarfile.TarInfo("Roastline-1.0.0/../../attempted-side-effect")
            bonus.size = 4
            archive.addfile(bonus, io.BytesIO(b"oops"))
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
                for msg in (b"ROASTLINE", b"Downloading Roastline v1.0.0", b"Tone: SFW", b"Startup check passed"):
                    self.assertIn(msg, proc.stdout)
                self.assertNotIn(b"Roast level", proc.stderr)
        self.assertFalse((self.directory / "attempted-side-effect").exists())

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX pseudoterminal required")
    def test_piped_bootstrap_can_ask_for_tone_without_consuming_script_stdin(self):
        import errno
        import pty
        import select
        import signal
        import time

        pid, terminal = pty.fork()
        if pid == 0:
            os.chdir(self.directory)
            script = shlex.quote(str(ROOT / "install.sh"))
            os.execvpe("bash", ["bash", "-c", "cat " + script + " | bash"],
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
                if b"Roast level [1]:" in output and not answered:
                    os.write(terminal, b"2\n")
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
        self.assertIn(b"Tone: NSFW", output)
        self.assertEqual(json.loads((self.home / "config.json").read_bytes())["tone"], "nsfw")


if __name__ == "__main__":
    unittest.main()
