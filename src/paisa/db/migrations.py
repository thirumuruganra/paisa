from __future__ import annotations

from pathlib import Path

from paisa.db.connection import connect


DEFAULT_CATEGORIES = [
    "Food",
    "Groceries",
    "Utilities",
    "Rent",
    "Travel",
    "Shopping",
    "Income",
    "Transfer",
    "Healthcare",
    "Entertainment",
    "Misc",
]


async def initialize_database(db_path: Path) -> None:
    db = await connect(db_path)
    try:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                bank TEXT NOT NULL CHECK (bank IN ('HDFC', 'ICICI')),
                date TEXT NOT NULL,
                narration TEXT NOT NULL,
                merchant_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount >= 0),
                type TEXT NOT NULL CHECK (type IN ('DEBIT', 'CREDIT')),
                running_balance REAL,
                category_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_date
                ON transactions(date);

            CREATE INDEX IF NOT EXISTS idx_transactions_merchant_name
                ON transactions(merchant_name);

            CREATE INDEX IF NOT EXISTS idx_transactions_category_id
                ON transactions(category_id);

            CREATE TABLE IF NOT EXISTS parsed_files (
                file_hash TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                bank TEXT NOT NULL CHECK (bank IN ('HDFC', 'ICICI')),
                import_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS merchant_category_rules (
                merchant_key TEXT PRIMARY KEY,
                category_id INTEGER NOT NULL,
                source_transaction_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id),
                FOREIGN KEY (source_transaction_id) REFERENCES transactions(id)
            );

            CREATE TABLE IF NOT EXISTS categorization_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_key TEXT NOT NULL,
                sample_text TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                source_transaction_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id),
                FOREIGN KEY (source_transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_merchant_category_rules_category_id
                ON merchant_category_rules(category_id);

            CREATE INDEX IF NOT EXISTS idx_categorization_examples_merchant_key
                ON categorization_examples(merchant_key);

            CREATE INDEX IF NOT EXISTS idx_categorization_examples_category_id
                ON categorization_examples(category_id);
            """
        )

        category_count_cursor = await db.execute("SELECT COUNT(*) FROM categories")
        category_count_row = await category_count_cursor.fetchone()
        if category_count_row is not None and int(category_count_row[0]) == 0:
            await db.executemany(
                "INSERT INTO categories (name) VALUES (?)",
                [(name,) for name in DEFAULT_CATEGORIES],
            )

        transaction_columns = await db.execute_fetchall("PRAGMA table_info(transactions)")
        transaction_column_names = {str(row[1]) for row in transaction_columns}
        if "running_balance" not in transaction_column_names:
            await db.execute("ALTER TABLE transactions ADD COLUMN running_balance REAL")

        await db.commit()
    finally:
        await db.close()
