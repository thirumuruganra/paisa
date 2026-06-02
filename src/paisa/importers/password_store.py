from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError

from paisa.config import default_data_dir
from paisa.models import Bank


SERVICE_NAME = "paisa"
KEY_FILE_NAME = "password-store.key"


class PasswordStoreError(RuntimeError):
    """Raised when secure password storage is unavailable or unusable."""


class PasswordStore:
    def __init__(self, key_file: Path | None = None) -> None:
        self.key_file = key_file or default_data_dir() / KEY_FILE_NAME

    def get_password(self, bank: Bank) -> str | None:
        self._require_secure_backend()

        try:
            encrypted_password = keyring.get_password(SERVICE_NAME, self._username(bank))
        except KeyringError as exc:
            raise PasswordStoreError("Could not read PDF password from secure keyring") from exc

        if encrypted_password is None:
            return None

        try:
            return self._get_cipher(generate=False).decrypt(encrypted_password.encode("utf-8")).decode(
                "utf-8"
            )
        except InvalidToken as exc:
            raise PasswordStoreError("Stored PDF password could not be decrypted") from exc

    def set_password(self, bank: Bank, password: str) -> None:
        self._require_secure_backend()
        encrypted_password = self._get_cipher(generate=True).encrypt(password.encode("utf-8")).decode(
            "utf-8"
        )

        try:
            keyring.set_password(SERVICE_NAME, self._username(bank), encrypted_password)
        except KeyringError as exc:
            raise PasswordStoreError("Could not write PDF password to secure keyring") from exc

    @staticmethod
    def _username(bank: Bank) -> Literal["pdf_password_hdfc", "pdf_password_icici"]:
        if bank == "HDFC":
            return "pdf_password_hdfc"
        return "pdf_password_icici"

    def _get_cipher(self, *, generate: bool) -> Fernet:
        key = self._load_or_create_key(generate=generate)
        if key is None:
            raise PasswordStoreError("Password encryption key is missing")
        return Fernet(key)

    def _load_or_create_key(self, *, generate: bool) -> bytes | None:
        if self.key_file.exists():
            try:
                return self.key_file.read_bytes().strip()
            except OSError as exc:
                raise PasswordStoreError("Could not read local password encryption key") from exc

        if not generate:
            return None

        try:
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            self.key_file.write_bytes(key)
            if os.name != "nt":
                self.key_file.chmod(0o600)
            return key
        except OSError as exc:
            raise PasswordStoreError("Could not create local password encryption key") from exc

    def _require_secure_backend(self) -> None:
        backend = keyring.get_keyring()
        backend_module = type(backend).__module__.lower()
        backend_name = type(backend).__name__.lower()
        backend_priority = getattr(backend, "priority", 1)

        is_unusable_backend = backend_priority <= 0 or backend_module.startswith("keyring.backends.fail")
        is_plaintext_backend = "plaintext" in backend_module or "plaintext" in backend_name

        if is_unusable_backend or is_plaintext_backend:
            raise PasswordStoreError(
                "Secure keyring backend is unavailable; configure one before importing password-protected PDFs"
            )
