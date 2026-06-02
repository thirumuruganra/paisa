from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import aiosqlite

from paisa.db.connection import connect
from paisa.models import (
    CategoryExample,
    ExpenseBankFilter,
    ExpenseGrouping,
    ExpenseTimeframe,
    CategorySpend,
    CategorySummary,
    DailyBalancePoint,
    DashboardSnapshot,
    ExpenseSnapshot,
    ImportedFile,
    LedgerTransaction,
    MonthlyTotal,
    SpendBreakdown,
    TransactionInput,
)


_INVESTMENT_CATEGORY_PREFIX = "invest"


def _is_investment_category_sql(category_alias: str = "c") -> str:
    return f"LOWER({category_alias}.name) LIKE '{_INVESTMENT_CATEGORY_PREFIX}%'"


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _subtract_months(day: date, months: int) -> date:
    year = day.year
    month = day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return day.replace(year=year, month=month)


def _timeframe_start(
    timeframe: ExpenseTimeframe,
    current_day: date,
) -> date | None:
    if timeframe == "all_time":
        return None
    if timeframe == "this_month":
        return _month_start(current_day)
    if timeframe == "last_3_months":
        return _month_start(_subtract_months(current_day, 2))
    if timeframe == "this_year":
        return current_day.replace(month=1, day=1)
    raise ValueError(f"Unsupported expense timeframe: {timeframe}")


def _build_expense_filter_clause(
    *,
    timeframe: ExpenseTimeframe,
    bank: ExpenseBankFilter,
    current_day: date | None,
) -> tuple[str, tuple[object, ...]]:
    filters = ["t.type = 'DEBIT'", f"(c.id IS NULL OR NOT ({_is_investment_category_sql()}))"]
    params: list[object] = []

    if bank != "all":
        filters.append("t.bank = ?")
        params.append(bank)

    start_day = _timeframe_start(timeframe, current_day or date.today())
    if start_day is not None:
        filters.append("t.date >= ?")
        params.append(start_day.isoformat())

    return " AND ".join(filters), tuple(params)


def _build_investment_filter_clause(
    *,
    timeframe: ExpenseTimeframe,
    bank: ExpenseBankFilter,
    current_day: date | None,
) -> tuple[str, tuple[object, ...]]:
    filters = ["t.type = 'DEBIT'", _is_investment_category_sql()]
    params: list[object] = []

    if bank != "all":
        filters.append("t.bank = ?")
        params.append(bank)

    start_day = _timeframe_start(timeframe, current_day or date.today())
    if start_day is not None:
        filters.append("t.date >= ?")
        params.append(start_day.isoformat())

    return " AND ".join(filters), tuple(params)


async def get_category_id(db_path: Path, name: str) -> int:
    db = await connect(db_path)
    try:
        cursor = await db.execute("SELECT id FROM categories WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"Category not found: {name}")
        return int(row["id"])
    finally:
        await db.close()


async def get_category_name(db_path: Path, category_id: int) -> str:
    db = await connect(db_path)
    try:
        cursor = await db.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"Category not found: {category_id}")
        return str(row["name"])
    finally:
        await db.close()


async def is_file_parsed(db_path: Path, file_hash: str) -> bool:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            "SELECT 1 FROM parsed_files WHERE file_hash = ? LIMIT 1",
            (file_hash,),
        )
        return await cursor.fetchone() is not None
    finally:
        await db.close()


async def insert_imported_statement(
    db_path: Path,
    parsed_file: ImportedFile,
    transactions: list[TransactionInput],
    default_category_name: str = "Misc",
) -> int:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            "SELECT id FROM categories WHERE name = ?",
            (default_category_name,),
        )
        category_row = await cursor.fetchone()
        if category_row is None:
            raise LookupError(f"Category not found: {default_category_name}")
        default_category_id = int(category_row["id"])

        await db.execute("BEGIN")
        try:
            await db.execute(
                """
                INSERT INTO parsed_files (file_hash, filename, bank, import_date)
                VALUES (?, ?, ?, ?)
                """,
                (
                    parsed_file.file_hash,
                    parsed_file.filename,
                    parsed_file.bank,
                    parsed_file.import_date.isoformat(),
                ),
            )

            await db.executemany(
                """
                INSERT INTO transactions (
                        id, bank, date, narration, merchant_name, amount, type, running_balance, category_id
                )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        transaction.id,
                        transaction.bank,
                        transaction.date.isoformat(),
                        transaction.narration,
                        transaction.merchant_name,
                        transaction.amount,
                        transaction.type,
                        transaction.running_balance,
                        transaction.category_id or default_category_id,
                    )
                    for transaction in transactions
                ],
            )
        except Exception:
            await db.rollback()
            raise
        else:
            await db.commit()

    finally:
        await db.close()

    return len(transactions)


async def list_transactions(db_path: Path, limit: int | None = None) -> list[LedgerTransaction]:
    db = await connect(db_path)
    try:
        query = """
            SELECT
                t.id,
                t.bank,
                t.date,
                t.narration,
                t.merchant_name,
                t.amount,
                t.type,
                c.name AS category_name
            FROM transactions AS t
            JOIN categories AS c ON c.id = t.category_id
            ORDER BY t.date DESC, t.created_at DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_ledger_transaction(row) for row in rows]
    finally:
        await db.close()


async def get_dashboard_snapshot(db_path: Path, today: date | None = None) -> DashboardSnapshot:
    current_day = today or date.today()
    recent_transactions = await list_transactions(db_path, limit=10)
    return DashboardSnapshot(
        current_balance=await get_current_balance(db_path),
        income_this_month=await get_monthly_income(db_path, current_day),
        expenses_this_month=await get_monthly_expenses(db_path, current_day),
        recent_transactions=recent_transactions,
        rolling_balance=await get_rolling_balance_series(db_path, today=current_day),
    )


async def list_categories(db_path: Path) -> list[tuple[int, str]]:
    db = await connect(db_path)
    try:
        cursor = await db.execute("SELECT id, name FROM categories ORDER BY name COLLATE NOCASE")
        rows = await cursor.fetchall()
        return [(int(row["id"]), str(row["name"])) for row in rows]
    finally:
        await db.close()


async def list_category_summaries(db_path: Path) -> list[CategorySummary]:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT
                c.id AS category_id,
                c.name AS category_name,
                COALESCE(tx.transaction_count, 0) AS transaction_count,
                COALESCE(r.rule_count, 0) AS rule_count,
                COALESCE(e.example_count, 0) AS example_count
            FROM categories AS c
            LEFT JOIN (
                SELECT category_id, COUNT(*) AS transaction_count
                FROM transactions
                GROUP BY category_id
            ) AS tx ON tx.category_id = c.id
            LEFT JOIN (
                SELECT category_id, COUNT(*) AS rule_count
                FROM merchant_category_rules
                GROUP BY category_id
            ) AS r ON r.category_id = c.id
            LEFT JOIN (
                SELECT category_id, COUNT(*) AS example_count
                FROM categorization_examples
                GROUP BY category_id
            ) AS e ON e.category_id = c.id
            ORDER BY c.name COLLATE NOCASE
            """
        )
        rows = await cursor.fetchall()
        return [
            CategorySummary(
                category_id=int(row["category_id"]),
                category_name=str(row["category_name"]),
                transaction_count=int(row["transaction_count"]),
                rule_count=int(row["rule_count"]),
                example_count=int(row["example_count"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


async def get_current_balance(db_path: Path) -> float:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(
                COALESCE(
                    (
                        SELECT latest.running_balance
                        FROM transactions AS latest
                        WHERE latest.bank = banks.bank AND latest.running_balance IS NOT NULL
                        ORDER BY latest.date DESC, latest.created_at DESC
                        LIMIT 1
                    ),
                    (
                        SELECT COALESCE(SUM(
                            CASE fallback.type
                                WHEN 'CREDIT' THEN fallback.amount
                                ELSE -fallback.amount
                            END
                        ), 0)
                        FROM transactions AS fallback
                        WHERE fallback.bank = banks.bank
                    )
                )
            ), 0) AS current_balance
            FROM (
                SELECT DISTINCT bank
                FROM transactions
            ) AS banks
            """
        )
        row = await cursor.fetchone()
        return float(row["current_balance"])
    finally:
        await db.close()


async def get_monthly_income(db_path: Path, current_day: date | None = None) -> float:
    db = await connect(db_path)
    try:
        month_prefix = (current_day or date.today()).strftime("%Y-%m")
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS income_this_month
            FROM transactions
            WHERE type = 'CREDIT' AND substr(date, 1, 7) = ?
            """,
            (month_prefix,),
        )
        row = await cursor.fetchone()
        return float(row["income_this_month"])
    finally:
        await db.close()


async def get_monthly_expenses(db_path: Path, current_day: date | None = None) -> float:
    db = await connect(db_path)
    try:
        month_prefix = (current_day or date.today()).strftime("%Y-%m")
        cursor = await db.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS expenses_this_month
            FROM transactions AS t
            LEFT JOIN categories AS c ON c.id = t.category_id
            WHERE t.type = 'DEBIT'
              AND (c.id IS NULL OR NOT ({_is_investment_category_sql()}))
              AND substr(t.date, 1, 7) = ?
            """,
            (month_prefix,),
        )
        row = await cursor.fetchone()
        return float(row["expenses_this_month"])
    finally:
        await db.close()


async def get_recent_transactions(db_path: Path, limit: int = 10) -> list[LedgerTransaction]:
    return await list_transactions(db_path, limit=limit)


async def get_expense_totals_by_category(db_path: Path, limit: int = 7) -> list[CategorySpend]:
    return await get_expense_totals_by_category_filtered(
        db_path,
        limit=limit,
        timeframe="all_time",
        bank="all",
    )


async def get_expense_totals_by_category_filtered(
    db_path: Path,
    *,
    timeframe: ExpenseTimeframe,
    bank: ExpenseBankFilter,
    current_day: date | None = None,
    limit: int = 7,
) -> list[CategorySpend]:
    db = await connect(db_path)
    try:
        where_clause, params = _build_expense_filter_clause(
            timeframe=timeframe,
            bank=bank,
            current_day=current_day,
        )
        cursor = await db.execute(
            f"""
            SELECT
                c.id AS category_id,
                c.name AS category_name,
                COALESCE(SUM(t.amount), 0) AS total_amount
            FROM transactions AS t
            JOIN categories AS c ON c.id = t.category_id
            WHERE {where_clause}
            GROUP BY c.id, c.name
            HAVING total_amount > 0
            ORDER BY total_amount DESC, c.name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [
            CategorySpend(
                category_id=int(row["category_id"]),
                category_name=str(row["category_name"]),
                total_amount=float(row["total_amount"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


async def get_expense_totals_by_bank(
    db_path: Path,
    *,
    timeframe: ExpenseTimeframe,
    bank: ExpenseBankFilter,
    current_day: date | None = None,
) -> list[SpendBreakdown]:
    db = await connect(db_path)
    try:
        where_clause, params = _build_expense_filter_clause(
            timeframe=timeframe,
            bank=bank,
            current_day=current_day,
        )
        cursor = await db.execute(
            f"""
            SELECT
                t.bank AS label,
                COALESCE(SUM(t.amount), 0) AS total_amount
            FROM transactions AS t
            LEFT JOIN categories AS c ON c.id = t.category_id
            WHERE {where_clause}
            GROUP BY t.bank
            HAVING total_amount > 0
            ORDER BY total_amount DESC, t.bank COLLATE NOCASE ASC
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            SpendBreakdown(
                label=str(row["label"]),
                total_amount=float(row["total_amount"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


async def list_transaction_banks(db_path: Path) -> list[str]:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            "SELECT DISTINCT bank FROM transactions ORDER BY bank COLLATE NOCASE ASC"
        )
        rows = await cursor.fetchall()
        return [str(row["bank"]) for row in rows]
    finally:
        await db.close()


async def get_monthly_investments(
    db_path: Path,
    *,
    timeframe: ExpenseTimeframe,
    bank: ExpenseBankFilter,
    current_day: date | None = None,
) -> list[MonthlyTotal]:
    db = await connect(db_path)
    try:
        where_clause, params = _build_investment_filter_clause(
            timeframe=timeframe,
            bank=bank,
            current_day=current_day,
        )
        cursor = await db.execute(
            f"""
            SELECT
                substr(t.date, 1, 7) AS month,
                COALESCE(SUM(t.amount), 0) AS total_amount
            FROM transactions AS t
            JOIN categories AS c ON c.id = t.category_id
            WHERE {where_clause}
            GROUP BY substr(t.date, 1, 7)
            HAVING total_amount > 0
            ORDER BY month ASC
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            MonthlyTotal(
                month=str(row["month"]),
                total_amount=float(row["total_amount"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


async def get_rolling_balance_series(
    db_path: Path,
    today: date | None = None,
    days: int = 30,
) -> list[DailyBalancePoint]:
    if days <= 0:
        return []

    current_day = today or date.today()
    start_day = current_day - timedelta(days=days - 1)

    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT bank, date, type, amount, running_balance
            FROM transactions
            WHERE date <= ?
            ORDER BY bank ASC, date ASC, created_at ASC
            """,
            (current_day.isoformat(),),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    bank_rows: dict[str, list[tuple[date, str, float, float | None]]] = {}
    for row in rows:
        bank = str(row["bank"])
        bank_rows.setdefault(bank, []).append(
            (
                date.fromisoformat(str(row["date"])),
                str(row["type"]),
                float(row["amount"]),
                None if row["running_balance"] is None else float(row["running_balance"]),
            )
        )

    points: list[DailyBalancePoint] = []
    for offset in range(days):
        point_day = start_day + timedelta(days=offset)
        total_balance = 0.0
        for transactions in bank_rows.values():
            running_balance: float | None = None
            signed_total = 0.0
            for tx_day, tx_type, amount, tx_running_balance in transactions:
                if tx_day > point_day:
                    break
                signed_total += amount if tx_type == "CREDIT" else -amount
                if tx_running_balance is not None:
                    running_balance = tx_running_balance
            total_balance += running_balance if running_balance is not None else signed_total
        points.append(DailyBalancePoint(day=point_day, balance=total_balance))

    return points


async def get_merchant_rule(db_path: Path, merchant_key: str) -> tuple[int, str] | None:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT c.id AS category_id, c.name AS category_name
            FROM merchant_category_rules AS r
            JOIN categories AS c ON c.id = r.category_id
            WHERE r.merchant_key = ?
            """,
            (merchant_key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["category_id"]), str(row["category_name"])
    finally:
        await db.close()


async def list_category_examples(db_path: Path) -> list[CategoryExample]:
    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT
                e.merchant_key,
                e.sample_text,
                c.id AS category_id,
                c.name AS category_name
            FROM categorization_examples AS e
            JOIN categories AS c ON c.id = e.category_id
            ORDER BY e.created_at DESC, e.id DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            CategoryExample(
                merchant_key=str(row["merchant_key"]),
                sample_text=str(row["sample_text"]),
                category_id=int(row["category_id"]),
                category_name=str(row["category_name"]),
            )
            for row in rows
        ]
    finally:
        await db.close()


async def record_categorization_feedback(
    db_path: Path,
    merchant_key: str,
    sample_text: str,
    category_id: int,
    transaction_id: str | None = None,
) -> None:
    db = await connect(db_path)
    try:
        stored_transaction_id = transaction_id
        if transaction_id is not None:
            cursor = await db.execute(
                "SELECT 1 FROM transactions WHERE id = ? LIMIT 1",
                (transaction_id,),
            )
            if await cursor.fetchone() is None:
                stored_transaction_id = None

        await db.execute(
            """
            INSERT INTO categorization_examples (
                merchant_key,
                sample_text,
                category_id,
                source_transaction_id
            ) VALUES (?, ?, ?, ?)
            """,
            (merchant_key, sample_text, category_id, stored_transaction_id),
        )
        await db.execute(
            """
            INSERT INTO merchant_category_rules (
                merchant_key,
                category_id,
                source_transaction_id,
                updated_at
            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(merchant_key) DO UPDATE SET
                category_id = excluded.category_id,
                source_transaction_id = excluded.source_transaction_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (merchant_key, category_id, stored_transaction_id),
        )
        await db.commit()
    finally:
        await db.close()


async def ensure_category(db_path: Path, name: str) -> int:
    normalized_name = " ".join(name.split()).strip()
    if not normalized_name:
        raise ValueError("Category name cannot be empty")

    db = await connect(db_path)
    try:
        await db.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (normalized_name,))
        await db.commit()
    finally:
        await db.close()

    return await get_category_id(db_path, normalized_name)


async def rename_category(db_path: Path, category_id: int, new_name: str) -> None:
    normalized_name = " ".join(new_name.split()).strip()
    if not normalized_name:
        raise ValueError("Category name cannot be empty")

    db = await connect(db_path)
    try:
        cursor = await db.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"Category not found: {category_id}")

        current_name = str(row["name"])
        if current_name == "Misc":
            raise ValueError("Misc category cannot be renamed")

        try:
            await db.execute(
                "UPDATE categories SET name = ? WHERE id = ?",
                (normalized_name, category_id),
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"Category already exists: {normalized_name}") from exc
        await db.commit()
    finally:
        await db.close()


async def delete_category(db_path: Path, category_id: int) -> None:
    db = await connect(db_path)
    try:
        cursor = await db.execute("SELECT name FROM categories WHERE id = ?", (category_id,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError(f"Category not found: {category_id}")

        category_name = str(row["name"])
        if category_name == "Misc":
            raise ValueError("Misc category cannot be deleted")

        usage_cursor = await db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM transactions WHERE category_id = ?) AS transaction_count,
                (SELECT COUNT(*) FROM merchant_category_rules WHERE category_id = ?) AS rule_count,
                (SELECT COUNT(*) FROM categorization_examples WHERE category_id = ?) AS example_count
            """,
            (category_id, category_id, category_id),
        )
        usage = await usage_cursor.fetchone()
        if usage is None:
            raise LookupError(f"Category not found: {category_id}")

        if (
            int(usage["transaction_count"]) > 0
            or int(usage["rule_count"]) > 0
            or int(usage["example_count"]) > 0
        ):
            raise ValueError("Category is still linked to transactions or learned matches")

        await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        await db.commit()
    finally:
        await db.close()


async def update_transaction_details(
    db_path: Path,
    transaction_id: str,
    merchant_name: str,
    category_id: int,
) -> None:
    normalized_merchant_name = " ".join(merchant_name.split()).strip()
    if not normalized_merchant_name:
        raise ValueError("Merchant name cannot be empty")

    db = await connect(db_path)
    try:
        cursor = await db.execute(
            """
            UPDATE transactions
            SET merchant_name = ?, category_id = ?
            WHERE id = ?
            """,
            (normalized_merchant_name, category_id, transaction_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise LookupError(f"Transaction not found: {transaction_id}")
    finally:
        await db.close()


async def get_expense_snapshot(
    db_path: Path,
    *,
    timeframe: ExpenseTimeframe = "all_time",
    bank: ExpenseBankFilter = "all",
    grouping: ExpenseGrouping = "category",
    current_day: date | None = None,
) -> ExpenseSnapshot:
    db = await connect(db_path)
    try:
        where_clause, params = _build_expense_filter_clause(
            timeframe=timeframe,
            bank=bank,
            current_day=current_day,
        )
        cursor = await db.execute(
            f"""
            SELECT
                COUNT(*) AS debit_transaction_count,
                COUNT(DISTINCT category_id) AS category_count,
                COALESCE(SUM(amount), 0) AS total_spend
            FROM transactions AS t
            LEFT JOIN categories AS c ON c.id = t.category_id
            WHERE {where_clause}
            """,
            params,
        )
        row = await cursor.fetchone()

        if grouping == "bank":
            breakdown = await get_expense_totals_by_bank(
                db_path,
                timeframe=timeframe,
                bank=bank,
                current_day=current_day,
            )
        else:
            breakdown = [
                SpendBreakdown(label=item.category_name, total_amount=item.total_amount)
                for item in await get_expense_totals_by_category_filtered(
                    db_path,
                    timeframe=timeframe,
                    bank=bank,
                    current_day=current_day,
                )
            ]

        return ExpenseSnapshot(
            debit_transaction_count=int(row["debit_transaction_count"]),
            category_count=int(row["category_count"]),
            total_spend=float(row["total_spend"]),
            breakdown=breakdown,
            monthly_investments=await get_monthly_investments(
                db_path,
                timeframe=timeframe,
                bank=bank,
                current_day=current_day,
            ),
        )
    finally:
        await db.close()


def _row_to_ledger_transaction(row) -> LedgerTransaction:
    return LedgerTransaction(
        id=str(row["id"]),
        bank=row["bank"],
        date=date.fromisoformat(row["date"]),
        narration=str(row["narration"]),
        merchant_name=str(row["merchant_name"]),
        amount=float(row["amount"]),
        type=row["type"],
        category_name=str(row["category_name"]),
    )
