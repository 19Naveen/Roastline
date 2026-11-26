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
        """Write a transcript; return the waiting/idle event its last row would trigger."""
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        parsed = r.transcript_rows(str(self.transcript))
        waiting = r.waiting_trigger(*parsed[-1], NOW) if parsed else None
        return dict([waiting]) if waiting else {}

    def test_each_pack_has_multiple_distinct_roasts_per_trigger(self):
        all_jokes = set()
        for tone in r.TONES:
            pack = r.load_pack(PACKS / (tone + ".json"), tone)
            jokes = {joke.casefold() for lines in pack.values() for joke in lines}
            self.assertEqual(set(r.TRIGGERS), set(pack))
            self.assertTrue(all(len(lines) >= 2 for lines in pack.values()))
            self.assertGreaterEqual(len(pack["usage_full"]), 4)
            self.assertFalse(all_jokes & jokes)
            all_jokes.update(jokes)

    def test_pack_loader_rejects_an_empty_trigger(self):
        pack = r.load_pack(PACKS / "sfw.json", "sfw")
        path = self.root / "pack.json"
        pack["effort"] = []
        path.write_text(json.dumps(pack), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "at least one line per trigger"):
            r.load_pack(path, "sfw")
        pack = r.load_pack(PACKS / "sfw.json", "sfw")
        pack["short"] = ["A line for a trigger that no longer exists."]
        path.write_text(json.dumps(pack), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not cover all triggers"):
            r.load_pack(path, "sfw")

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

    def test_only_typed_or_queued_user_rows_count_as_prompts(self):
        def current(*rows):
            self.candidates(*rows)
            return r.current_prompt(r.transcript_rows(str(self.transcript)))

        self.assertIsNotNone(current(message("user", "hi", uuid="a")))
        self.assertIsNotNone(current(message("user", "hi", uuid="a", promptSource="queued")))
        for extra in ({"isMeta": True}, {"isSidechain": True}, {"promptSource": "tool"}, {"promptSource": None}):
            self.assertIsNone(current(message("user", "hi", uuid="a", **extra)), extra)
        # Tool results and replies never advance the current prompt.
        rows = [message("user", "hi", uuid="a"), message("assistant", "ok"),
                {**message("user", [{"type": "tool_result"}]), "promptSource": None}]
        self.assertEqual(current(*rows), current(rows[0]))

    def test_malformed_transcript_fails_closed_and_torn_tail_is_safe(self):
        self.candidates(message("user", "SHORT", uuid="a"))
        path = str(self.transcript)
        with self.transcript.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertEqual(len(r.transcript_rows(path)), 1)  # a torn final write is skipped
        with self.transcript.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(r.transcript_rows(path), [])  # damage in the middle fails closed
        self.assertEqual(r.transcript_rows(str(self.root / "missing")), [])
        self.assertEqual(self.candidates({"type": "user", "message": {"content": [7]}}), {})

    def test_large_transcript_only_reads_its_tail(self):
        with self.transcript.open("wb") as stream:
            stream.seek(50_000_000)
            stream.write(b"\n" + json.dumps(message("user", "again", uuid="a")).encode() + b"\n")
        with patch.object(r, "TAIL_BYTES", 512):
            self.assertEqual(len(r.transcript_rows(str(self.transcript))), 1)

    def test_usage_thresholds_fire_once_when_crossed(self):
        def events(percent, record):
            return r.event_triggers({"rate_limits": {"five_hour": {"used_percentage": percent}}},
                                    [], record, NOW)

        for percent, expected in ((49.99, None), (50, "usage_half"), (80, "usage_high"),
                                  (95, "usage_critical"), (100, "usage_full")):
            found = events(percent, {"usage_levels": {"5h": 0}})
            self.assertEqual(found[0][0] if found else None, expected, percent)
        record = {}
        self.assertEqual(events(80, record), [])  # joining a session already at 80% is not a crossing
        self.assertEqual(events(80, record), [])  # and a steady value never fires
        self.assertEqual(events(96, record)[0][0], "usage_critical")
        self.assertEqual(events(97, record), [])
        self.assertEqual(r.event_triggers({"rate_limits": {"five_hour": {"used_percentage": 100}}}, [], {}, NOW)[0][0],
                         "usage_full")  # a session that opens full still gets its joke

    def test_hitting_the_limit_outranks_other_simultaneous_events(self):
        data = {"session_id": "s", "effort": {"level": "high"},
                "context_window": {"used_percentage": 99}}
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW))
        data["effort"]["level"] = "max"
        data["context_window"]["used_percentage"] = 100
        reason, joke = r.visible_roast(data, CONFIG, self.root, PACKS, NOW)
        self.assertEqual(reason, "Context 100%")
        self.assertIn(joke, r.load_pack(PACKS / "sfw.json", "sfw")["usage_full"])

    def test_event_jokes_mix_stock_and_saved_lines_per_tone(self):
        saved = {"sfw": {"effort": ["Saved clean line.", "Another clean line."]},
                 "nsfw": {"effort": ["Saved spicy line, damn it."]}}
        (self.root / "personal.json").write_text(json.dumps(saved), encoding="utf-8")
        for tone in r.TONES:
            stock = set(r.load_pack(PACKS / (tone + ".json"), tone)["effort"])
            mine = set(saved[tone]["effort"])
            indices = {}
            shown = [r.event_joke(self.root, PACKS, tone, "effort", indices) for _ in range(12)]
            self.assertTrue(set(shown) & stock and set(shown) & mine, tone)  # a real mixture
            self.assertLessEqual(set(shown), stock | mine)  # nothing from the other tone
            self.assertEqual(sum(line in mine for line in shown), 6)  # saved lines play every other time
        # With nothing saved, only the stock lines rotate, starting anywhere.
        stock = r.load_pack(PACKS / "sfw.json", "sfw")["effort"]
        indices = {}
        shown = [r.event_joke(self.root / "nothing", PACKS, "sfw", "effort", indices) for _ in range(4)]
        self.assertEqual(set(shown), set(stock))
        self.assertNotEqual(shown[0], shown[1])

    def test_saved_jokes_for_a_retired_trigger_never_play(self):
        saved = {"sfw": {"short": ["Old tiny prompt line."], "effort": ["Saved clean line."]}}
        (self.root / "personal.json").write_text(json.dumps(saved), encoding="utf-8")
        data = {"session_id": "s", "effort": {"level": "high"}}
        shown = set()
        r.visible_roast(data, CONFIG, self.root, PACKS, NOW)
        for level in ("max", "low", "high", "max", "low", "high"):
            data["effort"]["level"] = level
            shown.add(r.visible_roast(data, CONFIG, self.root, PACKS, NOW)[1])
        self.assertIn("Saved clean line.", shown)
        self.assertNotIn("Old tiny prompt line.", shown)
        self.assertNotIn("short", r.TRIGGERS)

    def test_roast_joins_a_statusline_with_nerd_font_icons(self):
        original = "\x1b[1m\u25c6 repo\x1b[0m \u2502 \uf073 11% (25/11 15:16)".encode()
        out = r.append_original(original, ("Effort high", "A roast."), 120, 0, CONFIG)
        self.assertIn("Effort high: A roast.", out.decode())
        self.assertTrue(out.startswith(original))
        for bad in ("\t", "\r", "\x1b[2K"):  # control codes still leave the line alone
            line = ("repo " + bad + " x").encode()
            self.assertEqual(r.append_original(line, ("Effort high", "A roast."), 120, 0, CONFIG), line)

    def test_settings_left_by_pet_versions_are_ignored(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"tone": "nsfw", "pet": "cat", "pet_file": "/tmp/frog.json"}), encoding="utf-8")
        config = r.config_from(path)
        self.assertEqual(config["tone"], "nsfw")
        self.assertNotIn("pet", config)
        self.assertNotIn("pet_file", config)

    def test_builtin_wraps_roast_at_many_widths_without_ellipses(self):
        data = json.loads((ROOT / "tests/fixtures/statusline_input_sample.json").read_bytes())
        data["model"]["display_name"] = "運" * 23 + "é"
        for width in (1, 8, 40, 80, 90, 120, 162, 220):
            for joke in (None, ("Tiny prompt", "A " + "漢" * 40 + " joke")):
                with self.subTest(width=width, joke=joke):
                    rendered = r.render_builtin(data, joke, width, {**CONFIG, "color": True})
                    if not joke:
                        self.assertNotIn("\n", rendered)
                    for row in rendered.splitlines():
                        self.assertLessEqual(r.display_width(row), max(0, width - 4))
                    if joke and width > 5:
                        self.assertEqual(rendered.count("漢"), 40)
                        self.assertTrue(rendered.endswith("joke\x1b[0m"))

    def test_wrapper_preserves_original_multiline_style_and_line_endings(self):
        line = "\x1b[32mbranch 界\x1b[0m \x1b]8;;https://example.com\x07link\x1b]8;;\x07"
        raw = ("line one\r\n" + line + "\r\n\r\n").encode()
        out = r.append_original(raw, ("Tiny prompt", "The whitespace did most of the talking."), 85, 2, CONFIG)
        self.assertTrue(out.startswith(("line one\r\n" + line).encode()))
        self.assertTrue(out.endswith(b"\r\n\r\n"))
        self.assertNotIn(b"Tiny prompt:", out)
        self.assertIn(b"The whitespace did most of the talking.", out)
        self.assertLessEqual(r.display_width(out.decode().splitlines()[1]), 77)

    def test_wrapper_passes_through_when_content_or_width_unknown(self):
        joke = ("Tiny prompt", "A fair bit of text")
        for out, columns in ((b"\xffnot UTF8", 200), (b"left\tvalue", 200), (b"a\x1b[2K", 200),
                             (b"existing", None), (b"", 80)):
            self.assertEqual(r.append_original(out, joke, columns, 0, CONFIG), out)

    def test_wrapper_moves_roast_below_full_row_and_wraps_the_whole_joke(self):
        original = b"metadata\r\n\x1b[35m" + b"X" * 29 + b"\x1b[0m\r\n\r\n"
        joke = ("5h 63%", "The usage bar isn't a drinking game, for goodness' sake.")
        out = r.append_original(original, joke, 34, 0, CONFIG).decode()
        rows = out.split("\r\n")
        self.assertEqual(rows[:2], original.decode().split("\r\n")[:2])
        self.assertEqual(rows[-2:], ["", ""])
        self.assertEqual(" ".join(r.SGR.sub("", row) for row in rows[2:-2]), r.format_roast(joke))
        self.assertTrue(all(r.display_width(row) <= 30 for row in rows))

    def test_claude_can_roast_on_consecutive_turns_or_stay_quiet(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        rows = []
        joke = "A summary of a summary. We're compressing air."
        with patch.object(r.secrets, "randbelow") as roll:
            for i in range(1, 5):
                now = NOW + i * 10
                text = "Summarize part " + str(i)
                token = r.offer_roast({**data, "prompt": text}, CONFIG, self.root, now)
                self.assertIsNotNone(token)
                self.assertIsNone(r.offer_roast({**data, "prompt": text}, CONFIG, self.root, now))
                rows.append(message("user", text, -i * 10, uuid="prompt-" + str(i)))
                self.candidates(*rows)
                self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, now))
                if i != 2:
                    self.assertFalse(r.submit_roast(token, "This is fucking broken.", CONFIG, self.root, now))
                    self.assertTrue(r.submit_roast(token, joke, CONFIG, self.root, now))
                    self.assertFalse(r.submit_roast(token, joke, CONFIG, self.root, now))
                rows.append(message("assistant", "One sentence."))
                self.candidates(*rows)
                r.finish_roast(data, self.root, now)
                expected = None if i == 2 else ("", joke)
                self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, now), expected)
                self.assertFalse(r.submit_roast(token, joke, CONFIG, self.root, now))
                self.assertIsNone(r.visible_roast({**data, "session_id": "other"}, CONFIG, self.root, PACKS, now))
            roll.assert_not_called()

    def test_unconfirmed_prompt_and_declined_chat_have_no_stock_fallback(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        ignored = r.offer_roast({**data, "prompt": "Scheduled task"}, CONFIG, self.root, NOW)
        internal = message("user", "Scheduled task", promptSource=None)
        self.candidates(internal, message("assistant", "Background response."))
        r.finish_roast(data, self.root, NOW)
        self.assertFalse(r.submit_roast(ignored, "A joke.", CONFIG, self.root, NOW))
        token = r.offer_roast({**data, "prompt": "fix"}, CONFIG, self.root, NOW + 5)
        user = message("user", "fix", -5, uuid="typed")
        self.candidates(internal, user)
        r.finish_roast(data, self.root, NOW + 5)
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 5))
        self.candidates(internal, user, message("assistant", "Fixed.", -6))
        r.finish_roast(data, self.root, NOW + 6)
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 6))
        self.assertFalse(r.submit_roast(token, "Too late.", CONFIG, self.root, NOW + 6))

    def test_effort_changes_rotate_stock_and_refreshes_keep_the_same_joke(self):
        data = {"session_id": "s", "effort": {"level": "high"}, "transcript_path": str(self.transcript)}
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW))
        data["effort"]["level"] = "max"
        first = r.visible_roast(data, CONFIG, self.root, PACKS, NOW)
        self.assertEqual(first[0], "Effort max")
        self.assertIn(first[1], r.load_pack(PACKS / "sfw.json", "sfw")["effort"])
        self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 1), first)
        data["effort"]["level"] = "low"
        second = r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 2)
        self.assertEqual(second[0], "Effort low")
        self.assertNotEqual(second[1], first[1])
        self.candidates(message("user", "Please fix the broken build."))
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 3))

    def test_late_token_cannot_target_the_next_user_turn(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        with patch.object(r.secrets, "randbelow", return_value=0):
            token = r.offer_roast({**data, "prompt": "Make it shorter"}, CONFIG, self.root, NOW)
        self.candidates(message("user", "Make it shorter", uuid="one"),
                        message("user", "Make it shorter", -10, uuid="two"))
        self.assertFalse(r.submit_roast(token, "Wrong turn.", CONFIG, self.root, NOW + 10))

    def test_usage_limit_crossings_trigger_stock_even_on_consecutive_turns(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        data["context_window"] = {"used_percentage": 99}
        data["rate_limits"] = {"five_hour": {"used_percentage": 99}}

        def show():
            return r.visible_roast(data, CONFIG, self.root, PACKS, NOW)

        self.assertIsNone(show())
        data["context_window"]["used_percentage"] = 100
        first = show()
        self.assertEqual(first[0], "Context 100%")
        self.assertEqual(show(), first)
        self.candidates(message("user", "Next message", uuid="next"))
        self.assertIsNone(show())
        data["rate_limits"]["five_hour"]["used_percentage"] = 100
        second = show()
        self.assertEqual(second[0], "5h 100%")
        self.assertNotEqual(second[1], first[1])

    def test_question_waiting_joke_only_changes_at_next_threshold(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        self.candidates(message("user", "Help me pick an approach.", uuid="prompt-1"),
                        message("assistant", "Which one?", uuid="reply"))
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 59))
        first = r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 60)
        self.assertEqual(first[0], "Question waiting 1m")
        self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 240), first)
        later = r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 300)
        self.assertEqual(later[0], "Question waiting 5m")

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
