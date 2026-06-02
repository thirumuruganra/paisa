from __future__ import annotations

from datetime import date, datetime, timezone

import aiosqlite
import pytest

from paisa.db.migrations import DEFAULT_CATEGORIES, initialize_database
from paisa.db.queries import (
    delete_category,
    ensure_category,
    get_current_balance,
    get_dashboard_snapshot,
    get_expense_snapshot,
    get_monthly_investments,
    get_expense_totals_by_category,
    get_category_id,
    get_rolling_balance_series,
    insert_imported_statement,
    list_transaction_banks,
    list_categories,
    list_category_summaries,
    rename_category,
)
from paisa.models import ImportedFile, TransactionInput


async def test_initialize_database_creates_tables_and_categories(temp_db_path):
    await initialize_database(temp_db_path)

    async with aiosqlite.connect(temp_db_path) as db:
        tables = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        table_names = {row[0] for row in tables}
        categories = await db.execute_fetchall("SELECT name FROM categories ORDER BY name")

    assert {
        "categories",
        "categorization_examples",
        "merchant_category_rules",
        "parsed_files",
        "transactions",
    }.issubset(table_names)
    assert {row[0] for row in categories} == set(DEFAULT_CATEGORIES)


async def test_category_management_queries_add_rename_and_delete_unused_categories(temp_db_path):
    await initialize_database(temp_db_path)

    categories_before = await list_categories(temp_db_path)
    assert any(name == "Misc" for _, name in categories_before)

    added_id = await ensure_category(temp_db_path, "Dining Out")
    summaries = await list_category_summaries(temp_db_path)
    added_summary = next(summary for summary in summaries if summary.category_id == added_id)
    assert added_summary.transaction_count == 0
    assert added_summary.rule_count == 0
    assert added_summary.example_count == 0

    await rename_category(temp_db_path, added_id, "Eating Out")
    renamed_id = await get_category_id(temp_db_path, "Eating Out")
    assert renamed_id == added_id

    await delete_category(temp_db_path, added_id)
    categories_after = await list_categories(temp_db_path)
    assert all(name != "Eating Out" for _, name in categories_after)


async def test_misc_category_cannot_be_renamed_or_deleted(temp_db_path):
    await initialize_database(temp_db_path)
    misc_id = await get_category_id(temp_db_path, "Misc")

    with pytest.raises(ValueError, match="Misc category cannot be renamed"):
        await rename_category(temp_db_path, misc_id, "Other")

    with pytest.raises(ValueError, match="Misc category cannot be deleted"):
        await delete_category(temp_db_path, misc_id)


async def test_deleted_default_category_does_not_reappear_after_reinitialize(temp_db_path):
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")

    await delete_category(temp_db_path, food_id)
    await initialize_database(temp_db_path)

    categories = await list_categories(temp_db_path)
    assert all(name != "Food" for _, name in categories)
    assert any(name == "Misc" for _, name in categories)


async def test_dashboard_snapshot_prefers_latest_running_balance_per_bank(temp_db_path):
    await initialize_database(temp_db_path)

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="hdfc-file",
            filename="hdfc.pdf",
            bank="HDFC",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 1),
                narration="Coffee",
                merchant_name="Coffee",
                amount=100.0,
                type="DEBIT",
                running_balance=900.0,
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 2),
                narration="Salary",
                merchant_name="Salary",
                amount=300.0,
                type="CREDIT",
                running_balance=1200.0,
            ),
        ],
    )

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="icici-file",
            filename="icici.pdf",
            bank="ICICI",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="ICICI",
                date=date(2026, 4, 3),
                narration="Refund",
                merchant_name="Refund",
                amount=100.0,
                type="CREDIT",
            )
        ],
    )

    snapshot = await get_dashboard_snapshot(temp_db_path, today=date(2026, 4, 30))

    assert snapshot.current_balance == 1300.0


async def test_analytics_queries_return_balance_series_and_category_breakdown(temp_db_path):
    await initialize_database(temp_db_path)

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="analytics-file",
            filename="analytics.pdf",
            bank="HDFC",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 27),
                narration="Salary",
                merchant_name="Employer",
                amount=50000.0,
                type="CREDIT",
                running_balance=50000.0,
                category_id=await _category_id(temp_db_path, "Income"),
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 29),
                narration="Swiggy",
                merchant_name="Swiggy",
                amount=400.0,
                type="DEBIT",
                running_balance=49600.0,
                category_id=await _category_id(temp_db_path, "Food"),
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 30),
                narration="Uber",
                merchant_name="Uber",
                amount=200.0,
                type="DEBIT",
                running_balance=49400.0,
                category_id=await _category_id(temp_db_path, "Travel"),
            ),
        ],
    )

    assert await get_current_balance(temp_db_path) == 49400.0

    rolling_balance = await get_rolling_balance_series(temp_db_path, today=date(2026, 4, 30), days=4)
    assert [point.day.isoformat() for point in rolling_balance] == [
        "2026-04-27",
        "2026-04-28",
        "2026-04-29",
        "2026-04-30",
    ]
    assert [point.balance for point in rolling_balance] == [50000.0, 50000.0, 49600.0, 49400.0]

    breakdown = await get_expense_totals_by_category(temp_db_path)
    assert [(item.category_name, item.total_amount) for item in breakdown] == [
        ("Food", 400.0),
        ("Travel", 200.0),
    ]


async def test_expense_aggregates_exclude_investment_categories(temp_db_path):
    await initialize_database(temp_db_path)
    await ensure_category(temp_db_path, "Investments")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="expense-filter-file",
            filename="expense-filter.pdf",
            bank="HDFC",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 1),
                narration="Swiggy",
                merchant_name="Swiggy",
                amount=400.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Food"),
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 3),
                narration="SIP",
                merchant_name="Broker",
                amount=5000.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Investments"),
            ),
        ],
    )

    dashboard = await get_dashboard_snapshot(temp_db_path, today=date(2026, 5, 31))
    expenses = await get_expense_snapshot(temp_db_path)
    breakdown = await get_expense_totals_by_category(temp_db_path)

    assert dashboard.expenses_this_month == 400.0
    assert expenses.debit_transaction_count == 1
    assert expenses.category_count == 1
    assert expenses.total_spend == 400.0
    assert [(item.category_name, item.total_amount) for item in breakdown] == [("Food", 400.0)]


async def test_filtered_expense_snapshot_supports_bank_grouping_and_monthly_investments(temp_db_path):
    await initialize_database(temp_db_path)
    await ensure_category(temp_db_path, "Investments")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="filtered-expenses-hdfc",
            filename="filtered-expenses-hdfc.pdf",
            bank="HDFC",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 3, 20),
                narration="Cafe",
                merchant_name="Cafe",
                amount=300.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Food"),
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 4, 5),
                narration="SIP",
                merchant_name="Broker H",
                amount=2000.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Investments"),
            ),
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 10),
                narration="Groceries",
                merchant_name="Fresh Mart",
                amount=400.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Groceries"),
            ),
        ],
    )

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(
            file_hash="filtered-expenses-icici",
            filename="filtered-expenses-icici.pdf",
            bank="ICICI",
            import_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        [
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 12),
                narration="Uber",
                merchant_name="Uber",
                amount=600.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Travel"),
            ),
            TransactionInput(
                bank="ICICI",
                date=date(2026, 5, 15),
                narration="SIP",
                merchant_name="Broker I",
                amount=5000.0,
                type="DEBIT",
                category_id=await _category_id(temp_db_path, "Investments"),
            ),
        ],
    )

    snapshot = await get_expense_snapshot(
        temp_db_path,
        timeframe="this_month",
        bank="all",
        grouping="category",
        current_day=date(2026, 5, 31),
    )

    assert snapshot.debit_transaction_count == 2
    assert snapshot.category_count == 2
    assert snapshot.total_spend == 1000.0
    assert [(item.label, item.total_amount) for item in snapshot.breakdown] == [
        ("Travel", 600.0),
        ("Groceries", 400.0),
    ]
    assert [(item.month, item.total_amount) for item in snapshot.monthly_investments] == [("2026-05", 5000.0)]

    hdfc_snapshot = await get_expense_snapshot(
        temp_db_path,
        timeframe="last_3_months",
        bank="HDFC",
        grouping="bank",
        current_day=date(2026, 5, 31),
    )

    assert hdfc_snapshot.debit_transaction_count == 2
    assert hdfc_snapshot.category_count == 2
    assert hdfc_snapshot.total_spend == 700.0
    assert [(item.label, item.total_amount) for item in hdfc_snapshot.breakdown] == [("HDFC", 700.0)]
    assert [(item.month, item.total_amount) for item in hdfc_snapshot.monthly_investments] == [("2026-04", 2000.0)]

    investments = await get_monthly_investments(
        temp_db_path,
        timeframe="last_3_months",
        bank="all",
        current_day=date(2026, 5, 31),
    )
    assert [(item.month, item.total_amount) for item in investments] == [
        ("2026-04", 2000.0),
        ("2026-05", 5000.0),
    ]
    assert await list_transaction_banks(temp_db_path) == ["HDFC", "ICICI"]


async def _category_id(temp_db_path, name: str) -> int:
    async with aiosqlite.connect(temp_db_path) as db:
        cursor = await db.execute("SELECT id FROM categories WHERE name = ?", (name,))
        row = await cursor.fetchone()
    return int(row[0])
