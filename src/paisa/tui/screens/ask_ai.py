from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static, TextArea

from paisa.ai import ChatService, ChatServiceError, OllamaRequestError, OllamaUnavailableError, SqlGuardError


class AskAiView(VerticalScroll):
    BINDINGS = [
        Binding("ctrl+enter", "send_message", "Send"),
        Binding("ctrl+j", "send_message", "Send", show=False),
    ]

    def __init__(self, db_path: Path, chat_service: ChatService | None = None, **kwargs) -> None:
        super().__init__(classes="screen-view", **kwargs)
        self.db_path = db_path
        self._chat_service = chat_service or ChatService(db_path)

    def compose(self) -> ComposeResult:
        yield Static("Ask AI", classes="section-title")
        yield Static(
            "Local-only finance questions. Ctrl+Enter sends when terminal supports it. Ctrl+J always sends. Ollama must run on this machine.",
            id="ask-ai-status",
            classes="placeholder-copy placeholder-box",
        )
        yield Vertical(id="ask-ai-messages")
        yield Static(
            "Ask about balances, merchants, categories, or monthly totals after imports complete.",
            id="ask-ai-empty",
            classes="placeholder-copy placeholder-box",
        )
        yield TextArea("", id="ask-ai-input", soft_wrap=True, show_line_numbers=False)
        with Horizontal(id="ask-ai-actions"):
            yield Button("Send", id="ask-ai-send", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ask-ai-send":
            self.action_send_message()

    def on_key(self, event: events.Key) -> None:
        composer = self.query_one("#ask-ai-input", TextArea)
        if event.key == "escape" and composer.has_focus:
            self.query_one("#ask-ai-send", Button).focus()
            event.stop()
            return

        if composer.has_focus and event.key in {"ctrl+enter", "ctrl+j"}:
            self.action_send_message()
            event.stop()

    def action_send_message(self) -> None:
        self.run_worker(self._send_message(), exclusive=True)

    async def _send_message(self) -> None:
        composer = self.query_one("#ask-ai-input", TextArea)
        question = composer.text.strip()
        if not question:
            self._set_status("Type a finance question first.", error=True)
            return

        self._append_message("user", question)
        composer.load_text("")
        self._set_pending(True)
        self._set_status("Thinking locally with Ollama...", error=False)

        try:
            answer = await self._chat_service.answer_question(question)
        except (OllamaUnavailableError, OllamaRequestError) as exc:
            self._append_message("assistant", str(exc), error=True)
            self._set_status(str(exc), error=True)
        except (ChatServiceError, SqlGuardError, ValueError) as exc:
            self._append_message("assistant", str(exc), error=True)
            self._set_status(str(exc), error=True)
        else:
            self._append_message("assistant", answer.answer)
            status = f"Ran local SQL: {answer.sql}"
            if answer.result.truncated:
                status += " (truncated to 50 rows)"
            self._set_status(status)
        finally:
            self._set_pending(False)

    def _append_message(self, role: str, content: str, error: bool = False) -> None:
        bubble_classes = "ask-ai-message ask-ai-error" if error else "ask-ai-message"
        bubble = Static(content, classes=bubble_classes, markup=False)
        row_classes = "ask-ai-row ask-ai-user-row" if role == "user" else "ask-ai-row ask-ai-assistant-row"
        row = Horizontal(bubble, classes=row_classes)

        messages = self.query_one("#ask-ai-messages", Vertical)
        messages.mount(row)
        messages.display = True
        self.query_one("#ask-ai-empty", Static).display = False
        self.scroll_end(animate=False)

    def focus_input(self) -> None:
        self.query_one("#ask-ai-input", TextArea).focus()

    def _set_pending(self, pending: bool) -> None:
        composer = self.query_one("#ask-ai-input", TextArea)
        send_button = self.query_one("#ask-ai-send", Button)
        composer.disabled = pending
        send_button.disabled = pending

    def _set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#ask-ai-status", Static)
        status.update(message)
        status.styles.color = "#ff6b6b" if error else "#9aa4b2"
