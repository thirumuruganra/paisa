from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from paisa.models import CategorizationPrompt, CategorizationResolution


def _format_candidates(prompt: CategorizationPrompt) -> str:
    if not prompt.candidate_categories:
        return "No strong learned candidates yet."
    return "\n".join(
        f"{candidate.category_name} ({candidate.score:.2f}, {candidate.source})"
        for candidate in prompt.candidate_categories[:5]
    )


class CategorizationReviewModal(ModalScreen[CategorizationResolution | None]):
    BINDINGS = [
        Binding("enter", "confirm", show=False),
        Binding("escape", "skip", show=False),
    ]

    def __init__(self, prompt: CategorizationPrompt) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        category_options = [(name, category_id) for category_id, name in self.prompt.category_options]
        suggestion = self.prompt.suggested_category_name or "No suggestion"
        with Vertical(id="categorization-review-modal"):
            yield Static("Review Category", classes="section-title")
            yield Static(self.prompt.merchant_name or self.prompt.narration, id="categorization-review-title")
            yield Static(self.prompt.narration, id="categorization-review-narration")
            yield Static(
                f"Suggested: {suggestion} at {self.prompt.confidence:.2f} confidence.",
                id="categorization-review-suggestion",
            )
            if self.prompt.reason:
                yield Static(self.prompt.reason, id="categorization-review-reason")
            yield Static(_format_candidates(self.prompt), id="categorization-review-candidates")
            yield Select(category_options, prompt="Choose category", id="categorization-review-select")
            yield Static("", id="categorization-review-error")
            with Horizontal(id="categorization-review-actions"):
                yield Button("Skip", id="categorization-review-skip")
                yield Button("Confirm", id="categorization-review-confirm", variant="primary")

    def on_mount(self) -> None:
        select = self.query_one("#categorization-review-select", Select)
        if self.prompt.suggested_category_id is not None:
            select.value = self.prompt.suggested_category_id
        self.query_one("#categorization-review-select", Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "categorization-review-skip":
            self.dismiss(CategorizationResolution(skipped=True))
            return
        if event.button.id == "categorization-review-confirm":
            self._confirm()

    def action_confirm(self) -> None:
        self._confirm()

    def action_skip(self) -> None:
        self.dismiss(CategorizationResolution(skipped=True))

    def _confirm(self) -> None:
        value = self.query_one("#categorization-review-select", Select).value
        if value == Select.BLANK:
            self.query_one("#categorization-review-error", Static).update("Pick a category or skip.")
            return
        self.dismiss(CategorizationResolution(category_id=int(value), skipped=False))