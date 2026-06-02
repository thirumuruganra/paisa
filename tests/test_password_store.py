from __future__ import annotations

from pathlib import Path

import pytest

from paisa.importers.password_store import PasswordStore, PasswordStoreError


class FakeSecureBackend:
    priority = 1


class FakeFailBackend:
    priority = 0


def test_password_store_encrypts_before_keyring_and_decrypts_on_read(monkeypatch, tmp_path: Path):
    stored_values: dict[tuple[str, str], str] = {}

    monkeypatch.setattr("paisa.importers.password_store.keyring.get_keyring", lambda: FakeSecureBackend())
    monkeypatch.setattr(
        "paisa.importers.password_store.keyring.set_password",
        lambda service, username, password: stored_values.__setitem__((service, username), password),
    )
    monkeypatch.setattr(
        "paisa.importers.password_store.keyring.get_password",
        lambda service, username: stored_values.get((service, username)),
    )

    store = PasswordStore(key_file=tmp_path / "password-store.key")
    store.set_password("HDFC", "secret-pdf-password")

    encrypted_value = stored_values[("paisa", "pdf_password_hdfc")]
    assert encrypted_value != "secret-pdf-password"
    assert store.get_password("HDFC") == "secret-pdf-password"


def test_password_store_fails_loudly_without_secure_backend(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("paisa.importers.password_store.keyring.get_keyring", lambda: FakeFailBackend())

    store = PasswordStore(key_file=tmp_path / "password-store.key")

    with pytest.raises(PasswordStoreError, match="Secure keyring backend is unavailable"):
        store.set_password("ICICI", "secret")


async def test_import_pipeline_reports_secure_backend_failure(monkeypatch, temp_db_path, tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "statement.pdf").write_bytes(b"fake pdf")

    from paisa.importers.pipeline import import_statements
    from paisa.parsers.base import InvalidPasswordError
    from paisa.models import ParsedStatement, TransactionInput

    prompted_passwords: list[str] = []

    def fake_parse_statement(path, password=None, debug_pdf=False):
        if password is None:
            raise InvalidPasswordError("PDF password is required or invalid")
        prompted_passwords.append(password)
        if password != "secret":
            raise InvalidPasswordError("PDF password is required or invalid")
        return ParsedStatement(
            bank="ICICI",
            transactions=[
                TransactionInput(
                    bank="ICICI",
                    date=__import__("datetime").date(2026, 4, 30),
                    narration="Test",
                    merchant_name="Test",
                    amount=100.0,
                    type="CREDIT",
                )
            ],
        )

    class BrokenPasswordStore:
        def get_password(self, bank):
            raise PasswordStoreError(
                "Secure keyring backend is unavailable; configure one before importing password-protected PDFs"
            )

        def set_password(self, bank, password):
            raise PasswordStoreError(
                "Secure keyring backend is unavailable; configure one before importing password-protected PDFs"
            )

    monkeypatch.setattr("paisa.importers.pipeline.parse_statement", fake_parse_statement)

    result = await import_statements(
        temp_db_path,
        statements_dir,
        password_store=BrokenPasswordStore(),
        password_provider=lambda path: "secret",
    )

    assert prompted_passwords == ["secret"]
    assert result.imported_files == 1
    assert result.imported_transactions == 1
    assert result.failures == []