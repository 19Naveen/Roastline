import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import roastline as r


NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
CONFIG = {**r.DEFAULTS, "timezone_offset": "+00:00", "color": False}
PACKS = ROOT / "src/roasts"


def message(role, content, ago=0, **values):
    row = {"type": role, "message": {"content": content},
           "timestamp": datetime.fromtimestamp(NOW - ago, timezone.utc).isoformat()}
    if role == "user":
        row["promptSource"] = "typed"
    return {**row, **values}


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcript = self.root / "session.jsonl"

    def candidates(self, *rows):
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return dict(r.conversation_triggers(str(self.transcript), NOW))

    def test_each_pack_has_100_distinct_roasts_and_covers_every_trigger(self):
        all_jokes = set()
        for tone in r.TONES:
            pack = r.load_pack(PACKS / (tone + ".json"), tone)
            jokes = {joke.casefold() for lines in pack.values() for joke in lines}
            self.assertEqual(set(r.TRIGGERS), set(pack))
            self.assertEqual(len(jokes), 100)
            self.assertFalse(all_jokes & jokes)
            all_jokes.update(jokes)

    def test_assistant_question_formats_and_thresholds(self):
        for delay, kind in ((60, "question_wait"), (300, "question_late"), (1800, "question_ghosted")):
            for content in ("Which approach?", [{"type": "text", "text": "Which approach?"}],
                            [{"type": "tool_use", "name": "AskUserQuestion"}]):
                with self.subTest(delay=delay, content=content):
                    self.assertIn(kind, self.candidates(message("assistant", content, ago=delay)))
        self.assertEqual(self.candidates(message("assistant", "Which approach?", ago=59)), {})
        self.assertEqual(self.candidates(message("assistant", [
            {"type": "text", "text": "Which approach?"},
            {"type": "tool_use", "name": "Bash"},
        ], ago=3700)), {})

    def test_no_idle_when_user_answered_or_tool_is_pending(self):
        for delay, kind in ((900, "idle"), (3600, "idle_long")):
            self.assertIn(kind, self.candidates(message("assistant", "Done.", ago=delay)))
        self.assertEqual(self.candidates(message("assistant", "Which one?", ago=3600),
                                         message("user", "The first route")), {})
        self.assertEqual(self.candidates(message("assistant", "Done.", ago=899)), {})

    def test_prompt_signals_do_not_reuse_prompt_text(self):
        self.assertEqual(self.candidates(message("user", "TOKENLEAK42!"))["caps"], "Caps lock")
        self.assertEqual(self.candidates(message("user", "a1b2c3"))["short"], "Tiny prompt")
        self.assertIn("retry", self.candidates(message("user", " Rebuild  it ", 40),
                                               message("user", "rebuild it")))
        self.assertEqual(self.candidates(message("user", "TINYTEXT99", isMeta=True)), {})
        self.assertEqual(self.candidates(message("user", "TINYTEXT99", isSidechain=True)), {})
        self.assertEqual(self.candidates(message("user", "TINYTEXT99", promptSource="tool")), {})

    def test_prompt_frequency_and_session_thresholds(self):
        def prompts(number, age):
            return [message("user", "Coding request {}".format(index), ago=age)
                    for index in range(number)]
        self.assertNotIn("burst", self.candidates(*prompts(5, 0)))
        self.assertIn("burst", self.candidates(*prompts(6, 30)))
        self.assertIn("flood", self.candidates(*prompts(10, 599)))
        self.assertNotIn("burst", self.candidates(*prompts(6, 600)))
        self.assertIn("session_regular", self.candidates(*prompts(20, 900)))
        self.assertIn("session_marathon", self.candidates(*prompts(40, 900)))

    def test_tool_count_stops_at_last_prompt_and_deduplicates(self):
        calls = [{"type": "tool_use", "name": "Bash", "id": str(index)} for index in range(15)]
        older = [message("user", "Previous task", ago=5), message("assistant", calls, ago=3)]
        current = message("user", "Current task")
        self.assertNotIn("tools", self.candidates(*older, current, message("assistant", calls[:14] * 2)))
        self.assertIn("tools", self.candidates(*older, current, message("assistant", calls)))

    def test_malformed_transcript_fails_closed_and_torn_tail_is_safe(self):
        original = message("user", "SHORT")
        self.candidates(original)
        with self.transcript.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertIn("short", dict(r.conversation_triggers(str(self.transcript), NOW)))
        with self.transcript.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(r.conversation_triggers(str(self.transcript), NOW), [])
        self.assertEqual(r.conversation_triggers(str(self.root / "missing"), NOW), [])
        self.assertEqual(self.candidates({"type": "user", "message": {"content": [7]}}), {})

    def test_large_transcript_only_reads_its_tail(self):
        with self.transcript.open("wb") as stream:
            stream.seek(50_000_000)
            stream.write(b"\n" + json.dumps(message("user", "again")).encode() + b"\n")
        with patch.object(r, "TAIL_BYTES", 512):
            self.assertEqual(dict(r.conversation_triggers(str(self.transcript), NOW)), {"short": "Tiny prompt"})

    def test_usage_thresholds_and_trigger_priority(self):
        for percent, expected in ((49.99, None), (50, "usage_half"), (80, "usage_high"),
                                  (95, "usage_critical"), (100, "usage_full")):
            value = r.usage_trigger({"rate_limits": {"five_hour": {"used_percentage": percent}}})
            self.assertEqual(value[0] if value else None, expected)
        data = {"context_window": {"used_percentage": 100}, "effort": {"level": "high"}}
        self.assertEqual(r.choose_trigger(data, CONFIG, NOW), ("usage_full", "Context 100%"))
        self.candidates(message("assistant", "Will you answer?", ago=301))
        data["transcript_path"] = str(self.transcript)
        self.assertEqual(r.choose_trigger(data, CONFIG, NOW)[0], "question_late")
        self.assertEqual(r.choose_trigger({"effort": {"level": "max"}}, CONFIG, NOW)[0], "effort")
        self.assertEqual(r.choose_trigger({}, CONFIG, NOW)[0], "default")
        self.assertEqual(r.choose_trigger({}, CONFIG, NOW - 10 * 3600)[0], "late_night")

    def test_rotation_includes_only_detected_trigger_and_active_tone(self):
        personal = {"sfw": {"effort": ["Custom clean line.", "Another clean line."]},
                    "nsfw": {"effort": ["Custom spicy line, damn it.", "Another spicy line, damn it."]}}
        (self.root / "personal.json").write_text(json.dumps(personal), encoding="utf-8")
        data = {"session_id": "session", "effort": {"level": "max"},
                "transcript_path": str(self.transcript)}
        for tone in r.TONES:
            chosen = []
            for i in range(8):
                self.candidates(*(message("assistant", "Finished " + str(n)) for n in range(i + 1)))
                choice = r.choose_roast(data, {**CONFIG, "tone": tone}, self.root, PACKS, NOW)
                self.assertEqual(choice, r.choose_roast(data, {**CONFIG, "tone": tone}, self.root, PACKS, NOW + 300))
                chosen.append(choice)
            self.assertTrue(all(reason == "Effort max" for reason, _ in chosen))
            self.assertEqual(sum(line in personal[tone]["effort"] for _, line in chosen), 6)
            self.assertTrue(all(a != b for a, b in zip(chosen, chosen[1:])))
            other = "nsfw" if tone == "sfw" else "sfw"
            self.assertNotIn(("Effort max", personal[other]["effort"][0]), chosen)

    def test_pets_are_local_static_and_fail_safe(self):
        data = {"session_id": "session", "effort": {"level": "max"}}
        base = r.choose_roast(data, CONFIG, self.root, PACKS, NOW)
        for pet, face in (("cat", "(=^.^=)"), ("dog", "(U^.^U)"),
                          ("anime", "(^_^)"), ("person", "(:-)")):
            result = r.choose_roast(data, {**CONFIG, "pet": pet}, self.root, PACKS, NOW)
            self.assertEqual(result, (base[0], face + " " + base[1]))

    def test_one_line_with_and_without_roast_at_many_widths(self):
        data = json.loads((ROOT / "tests/fixtures/statusline_input_sample.json").read_bytes())
        data["model"]["display_name"] = "運" * 23 + "é"
        for width in (1, 8, 40, 80, 90, 120, 162, 220):
            for joke in (None, ("Tiny prompt", "A " + "漢" * 40 + " joke")):
                with self.subTest(width=width, joke=joke):
                    rendered = r.render_builtin(data, joke, width, {**CONFIG, "color": True})
                    self.assertNotIn("\n", rendered)
                    self.assertLessEqual(r.display_width(rendered), max(0, width - 4))

    def test_wrapper_preserves_original_multiline_style_and_line_endings(self):
        line = "\x1b[32mbranch 界\x1b[0m \x1b]8;;https://example.com\x07link\x1b]8;;\x07"
        raw = ("line one\r\n" + line + "\r\n\r\n").encode()
        out = r.append_original(raw, ("Tiny prompt", "The whitespace did most of the talking."), 85, 2, CONFIG)
        self.assertTrue(out.startswith(("line one\r\n" + line).encode()))
        self.assertTrue(out.endswith(b"\r\n\r\n"))
        self.assertIn(b"Tiny prompt: ", out)
        self.assertLessEqual(r.display_width(out.decode().splitlines()[1]), 77)

    def test_wrapper_passes_through_when_content_or_width_unknown(self):
        joke = ("Tiny prompt", "A fair bit of text")
        for out, columns in ((b"\xffnot UTF8", 200), (b"left\tvalue", 200), (b"a\x1b[2K", 200),
                             (b"A" * 100, 80), (b"existing", None), (b"", 80)):
            self.assertEqual(r.append_original(out, joke, columns, 0, CONFIG), out)

    def test_git_branch_reports_detached_commit_and_ahead_behind(self):
        with patch.object(r.subprocess, "run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=b"# branch.oid 0123456789abcdef\n# branch.head (detached)\n"
                                     b"# branch.ab +2 -12\n? untracked\n")
            self.assertEqual(r.git_branch("a path"), "0123456 ● ↑2 ↓12")
            self.assertEqual(run.call_args.args[0].count("status"), 1)

    def test_full_meter_keeps_sfw_tone_after_switching(self):
        config = {**CONFIG, "full_message": "DAMN FULL"}
        self.assertEqual(r.usage_bar(100, 3, config), "███ FULL")
        self.assertEqual(r.usage_bar(100, 3, {**config, "tone": "nsfw"}), "███ DAMN FULL")

    def test_no_invalid_stdin_crash(self):
        integration = {"original_command": None, "padding": 0}
        for invalid in (b"", b"[6]", b"{broken", b"{}", b"\xff"):
            self.assertEqual(r.render(invalid, integration, self.root), (0, b""))


if __name__ == "__main__":
    unittest.main()
