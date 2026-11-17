"""Show Fleet's existing commands in a keyboard-first terminal interface.

Usage: dotfleet tui. Arrows and Tab move; Enter selects; Esc goes back; q quits.
Configure closes this interface and runs the existing enrollment wizard.
"""

import argparse
import os
from pathlib import Path
import sys

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import BUILTIN_THEMES, Theme
from textual.worker import WorkerCancelled, get_current_worker
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from render_run import human, render
import remote
import style
from tui_session import CoreSession

ROOT = Path(__file__).resolve().parents[1]
MENU = [
    ("status", "Status — saved choices and drift"),
    ("check", "Updates — check, then update"),
    ("diff", "Diff — pending file changes"),
    ("backups", "Backups — list and restore"),
    ("machines", "Machines — records and SSH actions"),
    ("doctor", "Doctor — check this machine"),
    ("configure", "Configure — open the wizard"),
]


def plain_theme():
    return "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"


def palette(name):
    code = int(style.COLORS[name][2:-1]) - 30
    return "ansi_" + ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")[code]


def styled(line, plain=False, diff=False):
    if plain:
        return Text(line)
    color = "CYAN" if line == style.RULE or " · " in line[:8] else style.line_palette(line, diff)
    return Text.from_ansi(style.COLORS[color] + line + style.CLEAR)


class Review(ModalScreen[str]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static("Review before apply", classes="heading")
            yield RichLog(id="review-text", wrap=True, highlight=False, min_width=1)
            yield Static(
                self.request["explanation"]
                + f" Type {self.request['word']} to proceed, or cancel. Press Enter.",
                id="confirm-help",
            )
            yield Static("Tab moves focus; arrows scroll the review. Esc cancels.", classes="keys")
            yield Input(placeholder=f"Type {self.request['word']} or cancel", id="confirmation")
            yield Static("", id="confirm-error")
            yield Button("Cancel", id="cancel")

    def on_mount(self):
        output = self.query_one("#review-text", RichLog)
        for line in render(self.request["record"], future=True).splitlines():
            output.write(styled(line, self.app.plain))
        output.scroll_home(animate=False)
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, event):
        answer = event.value.strip()
        if answer in (self.request["word"], "cancel"):
            self.dismiss(answer)
        else:
            self.query_one("#confirm-error", Static).update(
                "Not confirmed. Type the exact word shown above."
            )

    @on(Button.Pressed, "#cancel")
    def action_cancel(self):
        self.dismiss("cancel")


class ActionScreen(Screen):
    def __init__(self, action, machine=None):
        super().__init__()
        self.action = action
        self.machine = machine
        self.backups = []
        self.connections = []

    def compose(self) -> ComposeResult:
        title = "Updates" if self.action == "check" else self.action.capitalize()
        if self.machine:
            title += " · " + self.machine
        yield Static("Fleet · " + title, classes="heading")
        yield RichLog(id="output", wrap=True, highlight=False, min_width=1)
        yield Static("Working…", id="progress")
        yield OptionList(Option("Back", id="back"), id="actions")
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    def on_mount(self):
        self.app.perform_action(self, self.action)

    def options(self, result, action):
        options = self.query_one("#actions", OptionList)
        options.clear_options()
        if self.action == "check":
            options.add_option(Option("Check again", id="check"))
            options.add_option(
                Option(
                    "Update manager and content",
                    id="update",
                    disabled=action != "check" or result != 1,
                )
            )
        elif self.action == "backups":
            for index, backup in enumerate(self.backups):
                options.add_option(Option("Restore " + Path(backup).name, id=f"restore-{index}"))
        elif self.action == "machines":
            for index, machine in enumerate(self.connections):
                for action in remote.CAPABLE:
                    options.add_option(
                        Option(action.capitalize() + " · " + machine, id=f"remote-{index}-{action}")
                    )
        options.add_option(Option("Back", id="back"))
        options.disabled = False
        options.highlighted = 0
        options.focus()

    @on(OptionList.OptionSelected, "#actions")
    async def selected(self, event):
        choice = event.option.id
        if choice == "back":
            await self.app.action_back()
        elif choice.startswith("restore-"):
            self.app.perform_action(
                self, "rollback", self.backups[int(choice.removeprefix("restore-"))]
            )
        elif choice.startswith("remote-"):
            _, index, action = choice.split("-", 2)
            self.app.push_screen(ActionScreen(action, self.connections[int(index)]))
        else:
            self.app.perform_action(self, choice)


class FleetApp(App[str]):
    CSS_PATH = "tui.tcss"
    TITLE = "Fleet"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("escape", "back", "Back"), ("q", "quit", "Quit")]

    def __init__(self, session=None):
        super().__init__()
        self.session = session or CoreSession(ROOT)
        self.plain = plain_theme()
        self.rows = []
        self.busy = False
        self.changing = False
        self.exit_requested = False
        self.worker = None
        self.operation_worker = None
        colors = {key: "ansi_white" if self.plain else palette(key) for key in style.COLORS}
        self.register_theme(
            Theme(
                name="fleet-plain" if self.plain else "fleet",
                primary=colors["CYAN"],
                secondary=colors["BLUE"],
                accent=colors["MAGENTA"],
                warning=colors["YELLOW"],
                error=colors["RED"],
                success=colors["GREEN"],
                background="ansi_black",
                surface="ansi_black",
                panel="ansi_black",
                foreground="ansi_white",
                ansi=True,
                variables={
                    key: value.replace("ansi_bright_blue", "ansi_white") if self.plain else value
                    for key, value in BUILTIN_THEMES["ansi-dark"].variables.items()
                },
            )
        )
        self.theme = "fleet-plain" if self.plain else "fleet"

    def compose(self) -> ComposeResult:
        yield Static("Fleet · this machine", classes="heading")
        yield Static("Loading local state…", id="facts")
        yield OptionList(*(Option(label, id=action) for action, label in MENU), id="menu")
        yield Static("↑↓ / Tab move · Enter selects · Esc back · q quits", classes="keys")

    def on_mount(self):
        self.worker = self.load_dashboard()
        self.query_one("#menu", OptionList).focus()

    def render_facts(self):
        width = max(12, self.size.width - 2)
        lines = []
        for key, value in self.rows:
            text = key.ljust(11) + "  " + human(value, str(Path.home()))
            line = Text(text, no_wrap=True)
            line.truncate(width, overflow="ellipsis")
            lines.append(line.plain)
        self.screen_stack[0].query_one("#facts", Static).update(Text("\n".join(lines)))

    def on_resize(self):
        if self.is_mounted and self.rows:
            self.render_facts()

    @work
    async def load_dashboard(self):
        def event(message):
            if message["kind"] == "dashboard":
                self.rows = message["rows"]
                self.render_facts()

        errors = []
        try:
            result = await self.session.run("dashboard", errors.append, event, self.confirm)
            if result:
                self.screen_stack[0].query_one("#facts", Static).update(Text("\n".join(errors)))
        except Exception as error:
            self.screen_stack[0].query_one("#facts", Static).update(Text("Error: " + str(error)))

    async def confirm(self, request):
        return await self.push_screen_wait(Review(request))

    @on(OptionList.OptionSelected, "#menu")
    def open_action(self, event):
        if event.option.id == "configure":
            self.exit("configure")
        else:
            self.push_screen(ActionScreen(event.option.id))

    @work
    async def perform_action(self, screen, action, backup=None):
        if self.busy:
            return
        self.busy = True
        self.operation_worker = get_current_worker()
        self.changing = action in ("update", "rollback")
        output = screen.query_one("#output", RichLog)
        output.clear()
        screen.query_one("#actions", OptionList).disabled = True
        progress = screen.query_one("#progress", Static)
        progress.update(
            "Working on " + screen.machine + ". The target runs without prompts."
            if screen.machine
            else "Working… Changes require review."
            if self.changing
            else "Working…"
        )

        def event(message):
            if message["kind"] == "backups":
                screen.backups = message["items"]
            elif message["kind"] == "connections":
                screen.connections = message["items"]

        try:
            result = await self.session.run(
                action,
                lambda line: output.write(styled(line, self.plain, action == "diff")),
                event,
                self.confirm,
                backup,
                **({"machine": screen.machine} if screen.machine else {}),
            )
            normal = result == 0 or action in ("check", "diff") and result == 1
            progress.update(
                "Finished."
                if normal
                else "Stopped on " + screen.machine + ". Resolve the target error before retrying."
                if screen.machine
                else "Stopped. Read the error above. Configure returns to the terminal wizard."
            )
            if action == "doctor" and result == 1:
                progress.update("Checks finished. Follow the fixes above.")
            screen.options(result, action)
            output.scroll_home(animate=False)
        except Exception as error:
            output.write(styled("Error: " + str(error), self.plain))
            progress.update("Stopped. No confirmation was assumed.")
            screen.options(2, action)
        finally:
            self.busy = self.changing = False
            if self.exit_requested:
                self.exit()

    async def stop_read(self):
        if self.busy and not self.changing and self.operation_worker:
            self.operation_worker.cancel()
            try:
                await self.operation_worker.wait()
            except WorkerCancelled:
                pass

    async def action_back(self):
        if isinstance(self.screen, Review):
            self.screen.action_cancel()
            return
        if self.busy and self.changing:
            self.screen.query_one("#progress", Static).update(
                "Wait for this operation to finish. q closes afterward."
            )
            return
        await self.stop_read()
        if len(self.screen_stack) > 1:
            self.pop_screen()
            self.worker = self.load_dashboard()
        else:
            self.exit()

    async def action_quit(self):
        if self.busy and self.changing:
            self.exit_requested = True
            if isinstance(self.screen, Review):
                self.screen.action_cancel()
            else:
                self.screen.query_one("#progress", Static).update(
                    "Will close when this operation finishes."
                )
        else:
            await self.stop_read()
            self.exit()

    def _fatal_error(self):
        # Textual's default includes locals in a traceback. Keep private machine data off screen.
        self.panic(Text("Error: The terminal interface stopped. Plain Fleet commands still work."))


def main(argv=None):
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from command_display import current_command

        print(
            "Fleet TUI needs a terminal. Run " + current_command() + " --help for plain commands."
        )
        return 0
    app = FleetApp()
    result = app.run()
    if result == "configure":
        import command

        return style.run_cli(lambda: command.main(["configure"]), ROOT)
    return app.return_code or 0


if __name__ == "__main__":
    sys.exit(main())
