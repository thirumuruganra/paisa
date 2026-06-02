from __future__ import annotations

from datetime import date

from paisa.importers.pipeline import import_statements
from paisa.models import ParsedStatement, TransactionInput


class DummyPasswordStore:
    def get_password(self, bank):
        return None

    def set_password(self, bank, password):
        return None


async def test_import_pipeline_skips_duplicate_files(monkeypatch, temp_db_path, tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    pdf_path = statements_dir / "statement.pdf"
    pdf_path.write_bytes(b"fake pdf")

    def fake_parse_statement(path, password=None, debug_pdf=False):
        return ParsedStatement(
            bank="HDFC",
            transactions=[
                TransactionInput(
                    bank="HDFC",
                    date=date(2026, 3, 1),
                    narration="ZOMATO",
                    merchant_name="Zomato",
                    amount=250,
                    type="DEBIT",
                )
            ],
        )

    monkeypatch.setattr("paisa.importers.pipeline.parse_statement", fake_parse_statement)

    first = await import_statements(
        temp_db_path,
        statements_dir,
        password_store=DummyPasswordStore(),
    )
    second = await import_statements(
        temp_db_path,
        statements_dir,
        password_store=DummyPasswordStore(),
    )

    assert first.imported_files == 1
    assert first.imported_transactions == 1
    assert second.imported_files == 0
    assert second.skipped_duplicates == 1
    assert second.imported_transactions == 0
