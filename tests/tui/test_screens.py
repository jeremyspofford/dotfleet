"""Drive the terminal screens with Textual's headless keyboard and mouse pilot.

Usage: python3 -m unittest discover -s tests/tui -v in the test environment.
"""

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from common import toml
import command
import tui_core
import ui_channel
import updates
from render_run import render
from tui import ActionScreen, FleetApp, Review, styled
from textual.widgets import Input, OptionList, RichLog, Static


class FixtureSession:
    """Use the actual dispatcher; mock only external core operations in tests."""

    async def run(self, action, output, event, confirm, backup=None):
        loop = asyncio.get_running_loop()

        def send(message, reply=False):
            if reply:
                answer = asyncio.run_coroutine_threadsafe(confirm(message), loop).result(10)
                return answer
            loop.call_soon_threadsafe(event, message)

        def execute():
            captured = io.StringIO()
            with (
                patch.object(ui_channel, "send", side_effect=send),
                contextlib.redirect_stdout(captured),
            ):
                result = tui_core.dispatch(action, backup)
            for line in captured.getvalue().splitlines():
                loop.call_soon_threadsafe(output, line)
            return result

        return await asyncio.to_thread(execute)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.profile = {
            "domain": "personal",
            "domain_chosen": True,
            "git_name": "Example Person",
            "git_email": "example@example.invalid",
            "pins": {},
            "groups": {"helpers": True},
            "content_repo": "https://github.com/example/dotfiles",
        }
        profile = self.home / ".config/fleet/profile.toml"
        profile.parent.mkdir(parents=True)
        profile.write_text(toml(self.profile))
        backup = self.home / "fleet-backups/20261116-140000"
        backup.mkdir(parents=True)
        (backup / "MANIFEST.json").write_text("[]")
        (backup / "RUN.json").write_text('{"result":"SUCCESS"}')
        (self.home / "VERSION").write_text("0.2.0\n")
        self.record = json.loads((ROOT / "tests/fixtures/run-results/changed.json").read_text())
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for context in (
            patch.dict(os.environ, {"HOME": str(self.home), "TERM": "xterm-256color"}),
            patch.object(tui_core, "ROOT", self.home),
            patch.object(tui_core.socket, "gethostname", return_value="example-hub"),
            patch.object(tui_core, "commit", return_value="abcdef0123456789" * 2 + "abcdef01"),
            patch.object(command, "status", return_value=""),
            patch.object(command, "pin_rows", return_value=[]),
            patch.object(
                command,
                "checkout_state",
                return_value="abcdef01 · 0 behind last fetched upstream · clean",
            ),
            patch("command_display.shutil.which", return_value=None),
        ):
            self.stack.enter_context(context)

    async def settle(self, pilot, app):
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def test_dashboard_from_store_at_80_columns(self):
        app = FleetApp(FixtureSession())
        async with app.run_test(size=(80, 24)) as pilot:
            await self.settle(pilot, app)
            facts = app.query_one("#facts", Static).render().plain
            for text in (
                "example-hub",
                "personal · chosen",
                self.profile["content_repo"],
                "abcdef01",
                "0.2.0",
                "not checked",
                "2026-11-16 14:00:00",
                "Drift        none",
            ):
                self.assertIn(text, facts)
            self.assertEqual(app.screen.max_scroll_y, 0)
            self.assertLessEqual(app.query_one(".keys").region.bottom, 24)
            # Capture actual compositor cells, including selection and padding.
            lines = [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]
            text = "\n".join(lines).rstrip() + "\n"
            golden = ROOT / "tests/fixtures/tui/dashboard-80x24.txt"
            self.assertEqual(text, golden.read_text())

    async def test_updates_call_check_and_update_cores_with_review(self):
        def check(home, profile, display=True):
            print("Manager: 3 commits behind @ abcdef01")
            print("Content: up to date @ abcdef01")
            return 1

        def update(home):
            if ui_channel.review(self.record):
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(updates, "check", side_effect=check) as checked,
            patch.object(command, "update", side_effect=update) as updated,
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.settle(pilot, app)
                await pilot.press("down", "enter")
                await self.settle(pilot, app)
                self.assertEqual(checked.call_count, 1)
                self.assertIsInstance(app.screen, ActionScreen)
                self.assertFalse(
                    app.screen.query_one("#actions", OptionList).get_option("update").disabled
                )
                await pilot.press("down", "enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                await pilot.press(*"confirm", "enter")
                await self.settle(pilot, app)
                self.assertEqual(updated.call_count, 1)
                text = "\n".join(line.text for line in app.screen.query_one(RichLog).lines)
                self.assertIn("6 · Next steps", text)
                self.assertIn("fleet update", text)

    async def test_rollback_requires_exact_typed_word_and_escape_cancels(self):
        restored = []

        def restore(args, home):
            if command.confirm("restore", "Restore these files.", self.record):
                restored.append(args.backup)
                print(render(self.record), end="")
            return 0

        app = FleetApp(FixtureSession())
        with (
            patch.object(ui_channel, "active", return_value=True),
            patch.object(command, "restore_command", side_effect=restore),
        ):
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                await pilot.press("down", "down", "down", "enter")
                await self.settle(pilot, app)
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, Review)
                await pilot.press("enter", "r", "enter")
                self.assertEqual(restored, [])
                self.assertIsInstance(app.screen, Review)
                await pilot.press("escape")
                await self.settle(pilot, app)
                self.assertEqual(restored, [])
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press(*"restore", "enter")
                await self.settle(pilot, app)
                self.assertEqual(len(restored), 1)

    async def test_phone_width_keyboard_and_plain_themes(self):
        for environment in ({"NO_COLOR": ""}, {"TERM": "dumb"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                app = FleetApp(FixtureSession())
                async with app.run_test(size=(32, 24)) as pilot:
                    await self.settle(pilot, app)
                    self.assertEqual(app.theme, "fleet-plain")
                    self.assertEqual(styled("  + ~/.new", app.plain).spans, [])
                    for widget in app.screen.query("Static, OptionList"):
                        self.assertLessEqual(widget.region.right, 32)
                    await pilot.press("enter")
                    await self.settle(pilot, app)
                    self.assertIsInstance(app.screen, ActionScreen)
                    await pilot.press("tab", "escape")
                    await self.settle(pilot, app)
                    self.assertEqual(len(app.screen_stack), 1)
                    await pilot.press("q")

    async def test_configure_closes_the_interface(self):
        app = FleetApp(FixtureSession())
        async with app.run_test() as pilot:
            await self.settle(pilot, app)
            await pilot.press(*(["down"] * 6), "enter")
        self.assertEqual(app.return_value, "configure")

    async def test_diff_and_read_only_screens_keep_core_output(self):
        app = FleetApp(FixtureSession())
        with patch.object(
            command,
            "preview",
            side_effect=lambda *args: (
                print("No differences. Your files match fixture @ abcdef01.") or 0
            ),
        ) as preview:
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                # Mouse and keyboard activate the same menu options.
                menu = app.query_one("#menu", OptionList)
                menu.highlighted = 2
                await pilot.press("enter")
                await self.settle(pilot, app)
                self.assertEqual(preview.call_count, 1)
                lines = [line.text for line in app.screen.query_one(RichLog).lines]
                self.assertEqual(lines, ["No differences. Your files match fixture @ abcdef01."])
                await pilot.press("escape")
                await self.settle(pilot, app)
                await pilot.click("#menu", offset=(3, 1))
                await self.settle(pilot, app)
                self.assertIsInstance(app.screen, ActionScreen)
                self.assertEqual(app.screen.action, "status")

    async def test_failed_check_never_offers_update(self):
        app = FleetApp(FixtureSession())
        with (
            patch.object(updates, "check", return_value=2),
            patch.object(command, "update") as update,
        ):
            async with app.run_test() as pilot:
                await self.settle(pilot, app)
                await pilot.press("down", "enter")
                await self.settle(pilot, app)
                self.assertTrue(
                    app.screen.query_one("#actions", OptionList).get_option("update").disabled
                )
                self.assertFalse(update.called)

    async def test_review_wraps_at_phone_width_without_hidden_columns(self):
        app = FleetApp(FixtureSession())
        async with app.run_test(size=(32, 24)) as pilot:
            await self.settle(pilot, app)
            await app.push_screen(
                Review(
                    {
                        "record": self.record,
                        "word": "restore",
                        "explanation": "Restore these files.",
                    }
                )
            )
            await pilot.pause()
            output = app.screen.query_one("#review-text", RichLog)
            self.assertEqual(output.max_scroll_x, 0)
            self.assertLessEqual(output.region.right, 32)
            await pilot.press("escape")
            self.assertEqual(len(app.screen_stack), 1)

    async def test_escape_and_quit_cancel_a_running_read(self):
        cancelled = asyncio.Event()

        class SlowCheck(FixtureSession):
            async def run(self, action, *args, **kwargs):
                if action == "check":
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                else:
                    return await super().run(action, *args, **kwargs)

        app = FleetApp(SlowCheck())
        async with app.run_test() as pilot:
            await self.settle(pilot, app)
            await pilot.press("down", "enter")
            await pilot.pause()
            self.assertTrue(app.busy)
            await pilot.press("escape")
            await self.settle(pilot, app)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(len(app.screen_stack), 1)
            cancelled.clear()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("q")
            self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
