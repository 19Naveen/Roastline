import importlib.util
import json
import os
import re
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

    def test_slash_commands_and_accepted_suggestions_are_turns_but_internal_rows_are_not(self):
        slash = "<command-message>pstack:bro</command-message>\n<command-name>/pstack:bro</command-name>"
        human = {"promptSource": None, "origin": {"kind": "human"}, "turnOrigin": "human"}

        def current(**extra):
            self.candidates(message("user", slash, uuid="a", **extra))
            return r.current_prompt(r.transcript_rows(str(self.transcript)))

        self.assertIsNotNone(current(**human))  # a slash command is recognised as a turn
        self.assertIsNotNone(current(promptSource="suggestion_accepted"))
        for name, extra in {
            "local command like /voice": {"promptSource": None},
            "task notification": {"promptSource": "system", "origin": {"kind": "task-notification"},
                                  "turnOrigin": "task_notification"},
            "sdk host message": {"promptSource": "sdk", "origin": None, "turnOrigin": "host_synthetic"},
            "skill text for the model": {"promptSource": None, "isMeta": True, **human},
            "tool result": {"promptSource": None, "turnOrigin": None},
        }.items():
            self.assertIsNone(current(**extra), name)

    def test_slash_command_turn_accepts_a_roast_and_clears_the_previous_jokes(self):
        # The real shapes: a typed turn that worked, then "/pstack:bro" whose row has no promptSource.
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        typed = message("user", "check why my description is wrong", 60, uuid="typed-1")
        first = r.offer_roast({**data, "prompt": "check why my description is wrong"}, CONFIG, self.root, NOW - 60)
        self.candidates(typed)
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 60))
        self.assertTrue(r.submit_roast(first, "A first-turn joke.", CONFIG, self.root, NOW - 59))
        self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 58), ("", "A first-turn joke."))
        slash = message("user", "<command-message>pstack:bro</command-message>\n<command-name>/pstack:bro</command-name>",
                        0, uuid="slash-1", promptSource=None, origin={"kind": "human"}, turnOrigin="human")

        for order in ("hook first, then the row", "row first, then the hook"):
            with self.subTest(order):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                self.transcript = self.root / "session.jsonl"
                data = {"session_id": "s", "transcript_path": str(self.transcript)}
                token = r.offer_roast({**data, "prompt": "check why my description is wrong"}, CONFIG, self.root, NOW - 60)
                self.candidates(typed)
                r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 60)
                self.assertTrue(r.submit_roast(token, "A first-turn joke.", CONFIG, self.root, NOW - 59))
                if order.startswith("hook"):
                    # Claude Code ran the hook, then wrote the row ~10 ms later (as in the real log).
                    token = r.offer_roast({**data, "prompt": "/pstack:bro"}, CONFIG, self.root, NOW - 0.01)
                    self.candidates(typed, slash)
                else:
                    self.candidates(typed, slash)
                    token = r.offer_roast({**data, "prompt": "/pstack:bro"}, CONFIG, self.root, NOW + 0.04)
                # The old joke is gone as soon as the slash turn exists, and a new one is accepted.
                self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 1))
                self.assertTrue(r.submit_roast(token, "Two boxes called Description. Nobody can tell which.",
                                               CONFIG, self.root, NOW + 2))
                self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 3),
                                 ("", "Two boxes called Description. Nobody can tell which."))

    def test_offer_is_confirmed_by_timing_when_the_logged_text_differs_but_not_when_it_is_far_off(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        for gap, accepted in ((2, True), (9, True), (30, False)):
            with self.subTest(gap=gap):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                self.transcript = self.root / "session.jsonl"
                data = {"session_id": "s", "transcript_path": str(self.transcript)}
                token = r.offer_roast({**data, "prompt": "look at [Image #1] please"}, CONFIG, self.root, NOW)
                # The logged text has an image block and different text; the row lands `gap` seconds later.
                self.candidates(message("user", [{"type": "text", "text": "look at this"}, {"type": "image"}],
                                        -gap, uuid="row"))
                self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + gap))
                self.assertEqual(r.submit_roast(token, "A joke about the screenshot.", CONFIG, self.root, NOW + gap),
                                 accepted)

    def test_image_turn_after_previous_prompt_falls_out_of_transcript_tail(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        old = message("user", "Previous task", 60, uuid="old")
        self.candidates(old)
        config = {**CONFIG, "tone": "nsfw"}
        r.visible_roast(data, config, self.root, PACKS, NOW - 60)
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "system", "data": "x" * 5000}) + "\n")
        image = message("user", [{"type": "text", "text": "Improve this"},
                                 {"type": "image"}], uuid="new")
        with patch.object(r, "TAIL_BYTES", 4096):
            self.assertIsNone(r.current_prompt(r.transcript_rows(str(self.transcript))))
            token = r.offer_roast({**data, "prompt": "Improve this [Image #1]"}, config, self.root, NOW - 0.1)
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(image) + "\n")
            line = "It's close, so now we polish the shit out of it. Story of my damn life, Morty."
            self.assertTrue(r.submit_roast(token, line, config, self.root, NOW + 1))
            self.assertEqual(r.visible_roast(data, config, self.root, PACKS, NOW + 2), ("", line))

    def test_missing_previous_prompt_never_accepts_an_unverifiable_turn(self):
        for scenario in ("two prompts", "replaced transcript", "rewritten prefix",
                         "oversized append", "incomplete append"):
            with self.subTest(scenario=scenario):
                root = Path(tempfile.mkdtemp(dir=self.temp.name))
                transcript = root / "turn.jsonl"
                data = {"session_id": "s", "transcript_path": str(transcript)}
                old = message("user", "Previous task", 60, uuid="old")
                transcript.write_text(json.dumps(old) + "\n", encoding="utf-8")
                r.visible_roast(data, CONFIG, root, PACKS, NOW - 60)
                with transcript.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"type": "system", "data": "x" * 5000}) + "\n")
                with patch.object(r, "TAIL_BYTES", 4096):
                    token = r.offer_roast({**data, "prompt": "Improve this"}, CONFIG, root, NOW)
                    if scenario == "replaced transcript":
                        transcript.rename(root / "original.jsonl")
                    if scenario == "rewritten prefix":
                        original_size = transcript.stat().st_size
                        hidden = (json.dumps(message("user", "Other", uuid="other")) + "\n").encode()
                        transcript.write_bytes(hidden + b" " * (original_size - len(hidden) - 1) + b"\n")
                    with transcript.open("a", encoding="utf-8") as stream:
                        if scenario == "two prompts":
                            stream.write(json.dumps(message("user", "Other", uuid="other")) + "\n")
                        if scenario == "oversized append":
                            stream.write(json.dumps({"type": "system", "data": "x" * 5000}) + "\n")
                        stream.write(json.dumps(message("user", "Improve this", uuid="new")) + "\n")
                        if scenario == "incomplete append":
                            stream.write('{"type":')
                    self.assertFalse(r.submit_roast(token, "A joke about the task.", CONFIG, root, NOW + 1))

    def test_prompt_arriving_during_checkpoint_is_claimed(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        self.candidates(message("user", "Previous task", 60, uuid="old"))
        r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 60)
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "system", "data": "x" * 5000}) + "\n")
        capture = r.transcript_checkpoint

        def checkpoint_after_prompt(path):
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(message("user", "Improve this", uuid="new")) + "\n")
            return capture(path)

        with patch.object(r, "TAIL_BYTES", 4096), patch.object(r, "transcript_checkpoint", checkpoint_after_prompt):
            token = r.offer_roast({**data, "prompt": "Improve this"}, CONFIG, self.root, NOW)
            self.assertTrue(r.submit_roast(token, "A joke about the task.", CONFIG, self.root, NOW + 1))

    def test_two_prompts_arriving_during_checkpoint_are_not_misattributed(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        self.candidates(message("user", "Previous task", 60, uuid="old"))
        r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 60)
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "system", "data": "x" * 5000}) + "\n")
        capture = r.transcript_checkpoint

        def checkpoint_after_prompts(path):
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(message("user", "Improve this", uuid="one")) + "\n")
                stream.write(json.dumps(message("user", "Different task", uuid="two")) + "\n")
            return capture(path)

        with patch.object(r, "TAIL_BYTES", 4096), patch.object(r, "transcript_checkpoint", checkpoint_after_prompts):
            self.assertIsNone(r.offer_roast({**data, "prompt": "Improve this"}, CONFIG, self.root, NOW))

    def test_partial_prompt_at_checkpoint_can_be_completed(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        self.candidates(message("user", "Previous task", 60, uuid="old"))
        r.visible_roast(data, CONFIG, self.root, PACKS, NOW - 60)
        row = (json.dumps(message("user", "Improve this", uuid="new")) + "\n").encode()
        with self.transcript.open("ab") as stream:
            stream.write(json.dumps({"type": "system", "data": "x" * 5000}).encode() + b"\n")
            stream.write(row[:len(row) // 2])
        with patch.object(r, "TAIL_BYTES", 4096):
            token = r.offer_roast({**data, "prompt": "Improve this"}, CONFIG, self.root, NOW)
            with self.transcript.open("ab") as stream:
                stream.write(row[len(row) // 2:])
            self.assertTrue(r.submit_roast(token, "A joke about the task.", CONFIG, self.root, NOW + 1))

    def test_rejected_hook_invalidates_previous_turn_token(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        first = message("user", "First task", uuid="one")
        self.candidates(first)
        token = r.offer_roast({**data, "prompt": "First task"}, CONFIG, self.root, NOW)
        self.assertTrue(r.live_token(token, self.root, NOW))
        self.candidates(first, message("user", "Second task", -1, uuid="two"),
                        message("user", "Third task", -2, uuid="three"))
        self.assertIsNone(r.offer_roast({**data, "prompt": "Third task"}, CONFIG, self.root, NOW + 2))
        command = "roastline --add-roasts idle --token %s <<'ROASTLINE_JSON'\n[\"A line.\"]\nROASTLINE_JSON" % token
        self.assertFalse(r.live_token(token, self.root, NOW + 2))
        self.assertFalse(r.approve_command(command, CONFIG, self.root, NOW + 2))

    def test_hook_running_after_the_row_claims_it_even_when_the_text_differs(self):
        # A message with an image: the logged text differs from the hook's, and the hook is a bit late.
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        self.candidates(message("user", [{"type": "text", "text": "look at this"}, {"type": "image"}],
                                5, uuid="row"))
        token = r.offer_roast({**data, "prompt": "look at [Image #1] please"}, CONFIG, self.root, NOW)
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW))
        self.assertTrue(r.submit_roast(token, "A joke about the screenshot.", CONFIG, self.root, NOW + 1))
        # A second hook call for the same row is a duplicate, not a new slot.
        self.assertIsNone(r.offer_roast({**data, "prompt": "look at [Image #1] please"}, CONFIG, self.root, NOW + 2))

    def test_a_queued_second_message_still_gets_its_own_slot(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        first = message("user", "Fix the build", 3, uuid="one")
        self.candidates(first)
        token_a = r.offer_roast({**data, "prompt": "Fix the build"}, CONFIG, self.root, NOW)
        self.assertTrue(r.submit_roast(token_a, "A joke about the build.", CONFIG, self.root, NOW))
        # While Claude is busy the user queues another message; its hook runs before its row is logged.
        token_b = r.offer_roast({**data, "prompt": "and update the docs"}, CONFIG, self.root, NOW + 2)
        self.assertIsNotNone(token_b)
        self.assertNotEqual(token_b, token_a)
        self.candidates(first, message("user", "and update the docs", -2, uuid="two", promptSource="queued"))
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW + 3))  # the old joke is gone
        self.assertTrue(r.submit_roast(token_b, "Docs too? The scope is breeding.", CONFIG, self.root, NOW + 3))

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
                    self.assertTrue(r.submit_roast(token, joke, CONFIG, self.root, now))  # one replacement
                    self.assertFalse(r.submit_roast(token, joke, CONFIG, self.root, now))  # but no more
                rows.append(message("assistant", "One sentence."))
                self.candidates(*rows)
                r.finish_roast(data, self.root, now)
                expected = None if i == 2 else ("", joke)
                self.assertEqual(r.visible_roast(data, CONFIG, self.root, PACKS, now), expected)
                self.assertFalse(r.submit_roast(token, joke, CONFIG, self.root, now))
                self.assertIsNone(r.visible_roast({**data, "session_id": "other"}, CONFIG, self.root, PACKS, now))
            roll.assert_not_called()

    def test_second_submission_replaces_the_first_and_a_third_is_refused(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        token = r.offer_roast({**data, "prompt": "Refactor the module"}, CONFIG, self.root, NOW)
        self.candidates(message("user", "Refactor the module", uuid="one"))
        shown = lambda: r.visible_roast(data, CONFIG, self.root, PACKS, NOW)
        self.assertIsNone(shown())
        self.assertTrue(r.submit_roast(token, "Early joke about the refactor.", CONFIG, self.root, NOW))
        self.assertEqual(shown(), ("", "Early joke about the refactor."))
        self.assertTrue(r.submit_roast(token, "I could've built the Great Wall by now.", CONFIG, self.root, NOW + 120))
        self.assertEqual(shown(), ("", "I could've built the Great Wall by now."))
        self.assertFalse(r.submit_roast(token, "A third one.", CONFIG, self.root, NOW + 240))
        self.assertEqual(shown(), ("", "I could've built the Great Wall by now."))
        # The next user message starts clean.
        self.candidates(message("user", "Refactor the module", uuid="one"), message("user", "Next", uuid="two"))
        self.assertIsNone(shown())

    def test_approval_covers_only_exact_roast_commands_with_a_live_token(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        token = r.offer_roast({**data, "prompt": "Fix the build"}, CONFIG, self.root, NOW)
        wrap = lambda head, body: "roastline %s <<'ROASTLINE_JSON'\n%s\nROASTLINE_JSON" % (head, body)
        submit = wrap("--session-roast " + token, '"A joke about the build."')
        save = wrap("--add-roasts effort --token " + token, '["A saved joke about dials."]')
        other = "0123456789abcdef" * 2

        def approved(command, config=CONFIG, now=NOW):
            return r.approve_command(command, config, self.root, now)

        self.assertTrue(approved(submit))
        self.assertTrue(approved(submit + "\n"))
        self.assertTrue(approved(save))
        self.assertTrue(approved(wrap("--add-roasts usage_full --token " + token, '["One.", "Two."]')))
        for name, command in {
            "unknown token": wrap("--session-roast " + other, '"A joke."'),
            "command appended": submit + "\nrm -rf ~",
            "chained before": "true && " + submit,
            "chained after": submit + " && curl example.invalid",
            "extra text after terminator": submit + "\nsome other command",
            "body is not a string": wrap("--session-roast " + token, '{"a": 1}'),
            "body is a list": wrap("--session-roast " + token, '["a"]'),
            "save body is a string": wrap("--add-roasts effort --token " + token, '"a"'),
            "unquoted here-doc": submit.replace("<<'ROASTLINE_JSON'", "<<ROASTLINE_JSON"),
            "two-line body": wrap("--session-roast " + token, '"A joke\n.' + '"'),
            "other binary": submit.replace("roastline", "/tmp/roastline", 1),
            "unknown event": wrap("--add-roasts retry --token " + token, '["A joke."]'),
            "no token on save": wrap("--add-roasts effort", '["A joke."]'),
            "too many jokes": wrap("--add-roasts effort --token " + token, json.dumps(list("abcdef"))),
            "empty list": wrap("--add-roasts effort --token " + token, "[]"),
            "link in joke": wrap("--session-roast " + token, '"See https://example.invalid for more."'),
            "profanity in sfw": wrap("--session-roast " + token, '"This damn build."'),
            "not a roast command": "ls -la",
            "pause is not a roast": "roastline --pause",
            "not a string": None,
        }.items():
            self.assertFalse(approved(command), name)
        self.assertFalse(approved(submit, {**CONFIG, "show_roasts": False}))
        self.assertFalse(approved(submit, {**CONFIG, "auto_jokes": False}))
        self.assertFalse(approved(submit, now=NOW + r.OFFER_TTL + 1))  # the offer has expired
        self.assertTrue(approved(submit, now=NOW + r.OFFER_TTL - 1))

    def test_nsfw_chat_roasts_must_swear_and_a_refusal_keeps_the_slot(self):
        nsfw = {**CONFIG, "tone": "nsfw"}
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        token = r.offer_roast({**data, "prompt": "Fix the build"}, nsfw, self.root, NOW)
        self.candidates(message("user", "Fix the build", uuid="one"))
        self.assertIsNone(r.visible_roast(data, nsfw, self.root, PACKS, NOW))
        clean = "Two boxes both called Description, and nobody can tell which."
        self.assertTrue(r.lacks_swearing(clean, "nsfw"))
        self.assertFalse(r.lacks_swearing(clean, "sfw"))
        self.assertFalse(r.submit_roast(token, clean, nsfw, self.root, NOW))  # too clean for NSFW
        self.assertIsNone(r.visible_roast(data, nsfw, self.root, PACKS, NOW))
        for line in ("This half-assed spec has two Description boxes.", "Two Description boxes? What the hell.",
                     "Two boxes both called Description. Fucking brilliant."):
            self.assertFalse(r.lacks_swearing(line, "nsfw"), line)
        # The refusal didn't use anything up: both of the slot's submissions are still available.
        self.assertTrue(r.submit_roast(token, "Two boxes both called Description. Fucking brilliant.", nsfw, self.root, NOW))
        self.assertTrue(r.submit_roast(token, "Still reading. The Great Wall is jealous as hell.", nsfw, self.root, NOW))
        self.assertFalse(r.submit_roast(token, "A third fucking one.", nsfw, self.root, NOW))
        for word in ("class", "assignment", "pass", "hello", "shell", "assess"):
            self.assertTrue(r.lacks_swearing("The " + word + " module.", "nsfw"), word)  # no false positives

    def test_the_nudge_is_a_companion_in_four_moods_and_matches_the_tone(self):
        spec = importlib.util.spec_from_file_location("roastline_hook", ROOT / "src/hook.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        for tone in r.TONES:
            nudge = hook.NUDGE.format(token="a" * 32, tone=tone, tone_rule=hook.TONE_RULES[tone],
                                      examples=hook.EXAMPLES[tone], save_tone=hook.SAVE_TONE[tone],
                                      on_failure=hook.ON_FAILURE[tone],
                                      character_rule=hook.character_rule(None))
            examples = dict(re.findall(r'(ROAST|COMFORT|CHEER|JOKE) "(.+?)"(?: /|$)', hook.EXAMPLES[tone]))
            self.assertEqual(set(examples), {"ROAST", "COMFORT", "CHEER", "JOKE"}, tone)  # one of each mood
            for mood, line in examples.items():
                self.assertTrue(r.valid_line(line, tone, personal=True), line)
                self.assertFalse(r.lacks_swearing(line, tone), line)
                # NSFW swears in every mood, comfort included; SFW never does.
                self.assertEqual(bool(r.PROFANITY.search(line)), tone == "nsfw", (tone, mood, line))
            for mood in ("ROAST", "COMFORT", "CHEER", "JOKE"):
                self.assertIn("- %s " % mood, nudge)  # each mood is explained, not only exampled
            self.assertIn("sassy best friend", nudge)
            self.assertIn("Never mock someone who's struggling", nudge)
            self.assertNotIn("stressed", nudge)  # comfort, not skipping, is how a struggling user is met
            self.assertIn("a cheer", nudge)  # a later replacement can be a cheer when work lands
            if tone == "nsfw":
                self.assertIn("every message MUST swear, even a comforting one", nudge)
                self.assertIn("rewrite it with real profanity", nudge)
            else:
                self.assertNotIn("swear", nudge.lower())
                self.assertFalse(r.PROFANITY.search(nudge))
            self.assertIn("--session-roast " + "a" * 32, nudge)

    def test_stock_event_packs_include_warm_companion_lines_in_both_tones(self):
        warm = {"idle": "Still there?", "idle_long": "Welcome back", "question_ghosted": "No pressure",
                "usage_critical": "wrap", "usage_full": "Rest up"}
        for tone in r.TONES:
            pack = r.load_pack(PACKS / (tone + ".json"), tone)
            for trigger, fragment in warm.items():
                hit = [line for line in pack[trigger] if fragment.lower() in line.lower() or
                       (trigger == "usage_full" and "rest" in line.lower()) or
                       (trigger == "idle" and "no rush" in line.lower())]
                self.assertTrue(hit, (tone, trigger))

    def test_approval_follows_the_active_tone(self):
        nsfw = {**CONFIG, "tone": "nsfw"}
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        token = r.offer_roast({**data, "prompt": "Fix the build"}, nsfw, self.root, NOW)
        command = "roastline --session-roast %s <<'ROASTLINE_JSON'\n\"This damn build.\"\nROASTLINE_JSON" % token
        self.assertTrue(r.approve_command(command, nsfw, self.root, NOW))  # swearing is fine in nsfw
        self.assertFalse(r.approve_command(command, CONFIG, self.root, NOW))  # and not in sfw

    def test_a_used_up_token_is_no_longer_approved(self):
        data = {"session_id": "s", "transcript_path": str(self.transcript)}
        token = r.offer_roast({**data, "prompt": "Fix the build"}, CONFIG, self.root, NOW)
        self.candidates(message("user", "Fix the build", uuid="one"))
        command = "roastline --session-roast %s <<'ROASTLINE_JSON'\n\"A joke.\"\nROASTLINE_JSON" % token
        self.assertTrue(r.approve_command(command, CONFIG, self.root, NOW))
        self.assertIsNone(r.visible_roast(data, CONFIG, self.root, PACKS, NOW))  # confirms the turn
        self.assertTrue(r.submit_roast(token, "A joke.", CONFIG, self.root, NOW))
        self.assertTrue(r.approve_command(command, CONFIG, self.root, NOW))  # one use left
        self.assertTrue(r.submit_roast(token, "Another.", CONFIG, self.root, NOW))
        self.assertFalse(r.approve_command(command, CONFIG, self.root, NOW))
        before = (self.root / "turn-state.json").read_bytes()
        r.live_token(token, self.root, NOW)
        self.assertEqual((self.root / "turn-state.json").read_bytes(), before)  # checking never writes

    def test_state_lock_waits_for_a_busy_neighbour_instead_of_failing(self):
        import threading
        import time
        held, release = threading.Event(), threading.Event()

        def hold():
            with r.turn_state(self.root):
                held.set()
                release.wait(2)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(held.wait(2))
        threading.Timer(0.15, release.set).start()
        started = time.monotonic()
        self.assertFalse(r.live_token("0" * 32, self.root, NOW))
        self.assertGreater(time.monotonic() - started, 0.1)  # it waited its turn
        thread.join()

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
