from __future__ import annotations

from pathlib import Path

import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from paisa.parsers.base import InvalidPasswordError, extract_pdf_content


def test_pdf_password_error_is_reported_as_invalid_password(monkeypatch):
    def fake_open(path: Path, password: str | None = None):
        raise PdfminerException(PDFPasswordIncorrect())

    monkeypatch.setattr("paisa.parsers.base.pdfplumber.open", fake_open)

    with pytest.raises(InvalidPasswordError):
        extract_pdf_content(Path("statement.pdf"))
