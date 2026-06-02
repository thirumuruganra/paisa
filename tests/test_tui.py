from __future__ import annotations

from datetime import date

from textual.color import Color
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button

from paisa.ai.ollama_client import OllamaUnavailableError
from paisa.categorization.matcher import CategorizationService
from paisa.config import Settings
from paisa.db.migrations import initialize_database
from paisa.db.queries import ensure_category, get_category_id, insert_imported_statement, list_transactions
from paisa.models import CategorizationDecision, ImportedFile, TransactionInput
from paisa.tui.app import DEFAULT_LOGO, PaisaTuiApp


async def _seed_transactions(temp_db_path) -> None:
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")
    income_id = await get_category_id(temp_db_path, "Income")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="hash-1", filename="seed.pdf", bank="HDFC"),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 1),
                narration="UPI/P2M/ZOMATO",
                merchant_name="Zomato",
                amount=350.0,
                type="DEBIT",
                category_id=food_id,
            ),
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 2),
                narration="SALARY CREDIT",
                merchant_name="Employer",
                amount=50000.0,
                type="CREDIT",
                category_id=income_id,
            ),
        ],
    )


async def _seed_expense_analytics_transactions(temp_db_path) -> None:
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")
    investment_id = await ensure_category(temp_db_path, "Investments")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="analytics-hdfc", filename="analytics-hdfc.pdf", bank="HDFC"),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 10),
                narration="UPI/P2M/CAFE",
                merchant_name="Cafe",
                amount=300.0,
                type="DEBIT",
                category_id=food_id,
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 12),
                narration="SIP",
                merchant_name="Broker H",
                amount=2000.0,
                type="DEBIT",
                category_id=investment_id,
            ),
        ],
    )

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="analytics-icici", filename="analytics-icici.pdf", bank="ICICI"),
        [
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 7),
                narration="UPI/P2M/ZOMATO",
                merchant_name="Zomato",
                amount=450.0,
                type="DEBIT",
                category_id=food_id,
            ),
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 15),
                narration="SIP",
                merchant_name="Broker I",
                amount=1500.0,
                type="DEBIT",
                category_id=investment_id,
            ),
        ],
    )


async def test_app_boots_and_shortcuts_switch_views(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_view == "overview"
        assert app.query_one("Header") is not None
        assert list(app.query("#sidebar")) == []
        assert app.query_one("#overview-logo").styles.color == Color.parse("#f5f7ff")
        assert "888888ba" in str(app.query_one("#overview-logo").render())
        assert app.query_one("#summary-balance").region.height <= 8

        await pilot.press("3")
        await pilot.pause()
        assert app.active_view == "ledger"

        await pilot.press("4")
        await pilot.pause()
        assert app.active_view == "categories"

        await pilot.press("5")
        await pilot.pause()
        assert app.active_view == "ask-ai"


async def test_ai_recategorize_binding_only_available_on_ledger(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert "r" not in app.active_bindings

        await pilot.press("3")
        await pilot.pause()
        assert "r" in app.active_bindings

        await pilot.press("2")
        await pilot.pause()
        assert "r" not in app.active_bindings


async def test_overview_view_reports_scrollable_overflow_on_small_viewport(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        overview = app.query_one("#view-overview")
        assert overview.max_scroll_y > 0


async def test_overview_recent_transactions_are_visible_when_data_exists(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        await pilot.pause()

        table = app.query_one("#overview-recent")
        assert table.row_count == 2
        assert table.display is True
        assert table.size.height > 1


async def test_ledger_rows_load_from_database(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        table = app.query_one("#ledger-table")
        assert table.row_count == 2


async def test_ledger_search_filters_without_database_roundtrip(monkeypatch, temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)

    call_count = 0

    async def tracked_list_transactions(db_path):
        nonlocal call_count
        call_count += 1
        from paisa.db.queries import list_transactions as real_list_transactions

        return await real_list_transactions(db_path)

    monkeypatch.setattr("paisa.tui.screens.ledger.list_transactions", tracked_list_transactions)

    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert call_count == 1

        search = app.query_one("#ledger-search")
        search.value = "zomato"
        await pilot.pause()

        table = app.query_one("#ledger-table")
        assert table.row_count == 1
        assert call_count == 1


async def test_ledger_edit_persists_transaction_changes(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        app.query_one("#ledger-search").value = "zomato"
        await pilot.pause()

        table = app.query_one("#ledger-table")
        table.focus()
        await pilot.press("enter")
        await pilot.pause()

        app.screen.query_one("#edit-merchant").value = "Zomato Gold"
        await pilot.press("enter")
        await pilot.pause()

    transactions = await list_transactions(temp_db_path)
    updated = next(transaction for transaction in transactions if transaction.type == "DEBIT")
    assert updated.merchant_name == "Zomato Gold"
    assert updated.category_name == "Food"


async def test_ledger_edit_escape_discards_changes(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        app.query_one("#ledger-search").value = "zomato"
        await pilot.pause()

        table = app.query_one("#ledger-table")
        table.focus()
        await pilot.press("enter")
        await pilot.pause()

        app.screen.query_one("#edit-merchant").value = "Discard Me"
        await pilot.press("escape")
        await pilot.pause()

    transactions = await list_transactions(temp_db_path)
    updated = next(transaction for transaction in transactions if transaction.type == "DEBIT")
    assert updated.merchant_name == "Zomato"


async def test_ledger_search_escape_blurs_search_input(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        search = app.query_one("#ledger-search")
        table = app.query_one("#ledger-table")
        search.focus()
        await pilot.pause()

        assert search.has_focus is True
        await pilot.press("escape")
        await pilot.pause()

        assert search.has_focus is False
        assert table.has_focus is True


async def test_ask_ai_handles_ollama_unavailable_gracefully(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)

    class OfflineChatService:
        async def answer_question(self, question: str):
            raise OllamaUnavailableError("Ollama unavailable at http://127.0.0.1:11434. Start Ollama locally and retry.")

    app = PaisaTuiApp(
        Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"),
        chat_service=OfflineChatService(),
    )

    async with app.run_test() as pilot:
        await pilot.press("5")
        await pilot.pause()

        input_area = app.query_one("#ask-ai-input")
        input_area.load_text("What did I spend this month?")
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await pilot.pause()

        status = app.query_one("#ask-ai-status")
        assert "Ollama unavailable" in str(status.render())
        assert len(list(app.query(".ask-ai-error"))) == 1


async def test_ask_ai_ctrl_j_sends_message_when_terminal_cannot_distinguish_ctrl_enter(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)

    class OfflineChatService:
        async def answer_question(self, question: str):
            raise OllamaUnavailableError("Ollama unavailable at http://127.0.0.1:11434. Start Ollama locally and retry.")

    app = PaisaTuiApp(
        Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"),
        chat_service=OfflineChatService(),
    )

    async with app.run_test() as pilot:
        await pilot.press("5")
        await pilot.pause()

        input_area = app.query_one("#ask-ai-input")
        input_area.load_text("Show latest spend")
        await pilot.press("ctrl+j")
        await pilot.pause()
        await pilot.pause()

        status = app.query_one("#ask-ai-status")
        assert "Ollama unavailable" in str(status.render())
        assert len(list(app.query(".ask-ai-error"))) == 1


async def test_ask_ai_escape_blurs_textarea(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("5")
        await pilot.pause()

        input_area = app.query_one("#ask-ai-input")
        send_button = app.query_one("#ask-ai-send")
        input_area.focus()
        await pilot.pause()

        assert input_area.has_focus is True
        await pilot.press("escape")
        await pilot.pause()

        assert input_area.has_focus is False
        assert send_button.has_focus is True


async def test_ask_ai_view_scrolls_as_full_page(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("5")
        await pilot.pause()

        view = app.query_one("#view-ask-ai", VerticalScroll)
        messages = app.query_one("#ask-ai-messages", Vertical)

        assert view.styles.overflow_y == "auto"
        assert messages.styles.min_height.value == 5


async def test_command_palette_actions_route_correctly(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+k")
        await pilot.pause()

        palette_input = app.screen.query_one("#command-palette-input")
        palette_input.value = "ask ai"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.active_view == "ask-ai"

        await pilot.press("ctrl+k")
        await pilot.pause()

        palette_input = app.screen.query_one("#command-palette-input")
        palette_input.value = "zomato"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.active_view == "ledger"
        assert app.query_one("#ledger-search").value == "zomato"


async def test_ledger_edit_can_create_category_inline(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        app.query_one("#ledger-search").value = "zomato"
        await pilot.pause()

        table = app.query_one("#ledger-table")
        table.focus()
        await pilot.press("enter")
        await pilot.pause()

        app.screen.query_one("#edit-new-category").value = "Dining Out"
        await pilot.click("#edit-save")
        await pilot.pause()

    transactions = await list_transactions(temp_db_path)
    updated = next(transaction for transaction in transactions if transaction.type == "DEBIT")
    assert updated.category_name == "Dining Out"
    assert await get_category_id(temp_db_path, "Dining Out") > 0

    decision = await CategorizationService(temp_db_path, similarity_threshold=0.9).categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 3),
            narration="UPI/P2M/ZOMATO GOLD",
            merchant_name="Zomato Gold",
            amount=420.0,
            type="DEBIT",
        )
    )
    assert decision.category_name == "Dining Out"


async def test_ledger_edit_keeps_selected_row_after_category_change(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    misc_id = await get_category_id(temp_db_path, "Misc")
    travel_id = await get_category_id(temp_db_path, "Travel")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="hash-keep-row", filename="keep-row.pdf", bank="HDFC"),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 5),
                narration="UPI/P2M/ALPHA STORE",
                merchant_name="Alpha Store",
                amount=120.0,
                type="DEBIT",
                category_id=misc_id,
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 4),
                narration="UPI/P2M/CAFE CENTRAL",
                merchant_name="Cafe Central",
                amount=240.0,
                type="DEBIT",
                category_id=misc_id,
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 3),
                narration="UPI/P2M/GAMMA MART",
                merchant_name="Gamma Mart",
                amount=360.0,
                type="DEBIT",
                category_id=misc_id,
            ),
        ],
    )

    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        table = app.query_one("#ledger-table")
        table.focus()
        table.move_cursor(row=1)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        app.screen.query_one("#edit-category-select").value = travel_id
        await pilot.click("#edit-save")
        await pilot.pause()

        assert table.cursor_row == 1
        assert table.get_row_at(1)[1] == "Cafe Central"


async def test_categories_screen_can_add_category(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()

        app.query_one("#categories-name").value = "Learning"
        await pilot.click("#categories-add")
        await pilot.pause()

        assert app.query_one("#categories-table").row_count >= 1

    assert await get_category_id(temp_db_path, "Learning") > 0


async def test_ledger_ai_recategorizes_visible_misc_debits(monkeypatch, temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    misc_id = await get_category_id(temp_db_path, "Misc")
    food_id = await get_category_id(temp_db_path, "Food")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="hash-2", filename="misc.pdf", bank="HDFC"),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 3),
                narration="UPI/P2M/ZOMATO GOLD",
                merchant_name="Zomato Gold",
                amount=420.0,
                type="DEBIT",
                category_id=misc_id,
            ),
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 4),
                narration="SALARY CREDIT",
                merchant_name="Employer",
                amount=50000.0,
                type="CREDIT",
                category_id=await get_category_id(temp_db_path, "Income"),
            ),
        ],
    )

    async def fake_categorize_transaction(self, transaction, resolver=None):
        return CategorizationDecision(
            category_id=food_id,
            category_name="Food",
            confidence=0.95,
            source="merchant-example",
        )

    monkeypatch.setattr(
        "paisa.tui.screens.ledger.CategorizationService.categorize_transaction",
        fake_categorize_transaction,
    )

    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()

        app.query_one("#ledger-search").value = "misc"
        await pilot.pause()

        app.query_one("#view-ledger").action_ai_recategorize_visible()
        await pilot.pause()

    transactions = await list_transactions(temp_db_path)
    updated = next(transaction for transaction in transactions if transaction.merchant_name == "Zomato Gold")
    assert updated.category_name == "Food"


async def test_empty_states_show_polished_copy(temp_db_path, tmp_path):
    await initialize_database(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Import PDFs" in str(app.query_one("#overview-empty").render())

        await pilot.press("2")
        await pilot.pause()
        assert "No expense transactions yet" in str(app.query_one("#expenses-empty").render())
        assert app.query_one("#expenses-category-chart").display is False


async def test_phase_three_charts_render_when_transactions_exist(temp_db_path, tmp_path):
    await _seed_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.pause()

        overview_chart = app.query_one("#overview-balance-chart")
        assert overview_chart.display is True
        assert app.query_one("#overview-chart-empty").display is False

        await pilot.press("2")
        await pilot.pause()

        expenses_chart = app.query_one("#expenses-category-chart")
        assert expenses_chart.display is True
        assert app.query_one("#expenses-empty").display is False
        assert app.query_one("#expenses-status").region.width > 72
        assert app.query_one("#expenses-status").styles.text_wrap == "nowrap"
        assert app.query_one("#expenses-status").styles.text_overflow == "ellipsis"
        assert (
            "1 debit transactions, 1 expense categories, tracked spend: Rs 350.00."
            in str(app.query_one("#expenses-status").render())
        )


async def test_expenses_filters_refresh_status_and_investments(temp_db_path, tmp_path):
    await _seed_expense_analytics_transactions(temp_db_path)
    app = PaisaTuiApp(Settings(db_path=temp_db_path, statements_dir=tmp_path / "statements"))

    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()

        expenses_view = app.query_one("#view-expenses")

        assert app.query_one("#expenses-timeframe-this_month", Button).variant == "primary"
        assert app.query_one("#expenses-bank-all", Button).variant == "primary"
        assert app.query_one("#expenses-grouping-category", Button).variant == "primary"
        assert "This Month • All Banks" in str(app.query_one("#expenses-status").render())
        assert app.query_one("#expenses-category-chart").display is True
        assert app.query_one("#expenses-investments-chart").display is True

        await pilot.click("#expenses-bank-HDFC")
        await pilot.pause()

        assert app.query_one("#expenses-category-chart").display is False
        assert app.query_one("#expenses-empty").display is True
        assert "no non-investment expense transactions" in str(app.query_one("#expenses-status").render()).lower()
        assert app.query_one("#expenses-investments-chart").display is False
        assert app.query_one("#expenses-investments-empty").display is True
        assert app.query_one("#expenses-bank-HDFC", Button).variant == "primary"

        await pilot.click("#expenses-timeframe-last_3_months")
        await pilot.pause()

        assert app.query_one("#expenses-category-chart").display is True
        assert app.query_one("#expenses-empty").display is False
        assert app.query_one("#expenses-investments-chart").display is True
        assert app.query_one("#expenses-investments-empty").display is False
        assert "Last 3 Months • HDFC" in str(app.query_one("#expenses-status").render())
        assert app.query_one("#expenses-timeframe-last_3_months", Button).variant == "primary"

        await pilot.click("#expenses-grouping-bank")
        await pilot.pause()

        assert app.query_one("#expenses-category-chart").display is True
        assert app.query_one("#expenses-empty").display is False
        assert app.query_one("#expenses-grouping-bank", Button).variant == "primary"


def test_logo_fallback_contains_ascii_art():
    assert "888888ba" in DEFAULT_LOGO
