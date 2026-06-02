from __future__ import annotations

from datetime import date

import httpx
import pytest

from paisa.ai.chat_service import ChatService
from paisa.ai.ollama_client import OllamaClient, OllamaRequestError, OllamaUnavailableError
from paisa.ai.sql_guard import SqlGuardError, validate_select_sql
from paisa.db.connection import connect as real_connect
from paisa.db.migrations import initialize_database
from paisa.db.queries import ensure_category, get_category_id, insert_imported_statement
from paisa.models import ImportedFile, TransactionInput


class FakeModelClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages, *, model=None):
        self.calls.append(list(messages))
        return self.responses.pop(0)


async def _seed_transactions(temp_db_path) -> None:
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")

    await insert_imported_statement(
        temp_db_path,
        ImportedFile(file_hash="phase4-hash", filename="phase4.pdf", bank="HDFC"),
        [
            TransactionInput(
                bank="HDFC",
                date=date(2026, 5, 1),
                narration="UPI/P2M/ZOMATO",
                merchant_name="Zomato",
                amount=350.0,
                type="DEBIT",
                category_id=food_id,
            )
        ],
    )


def test_sql_guard_rejects_non_select_statements() -> None:
    with pytest.raises(SqlGuardError):
        validate_select_sql("DELETE FROM transactions")

    assert validate_select_sql("```sql\nSELECT * FROM transactions;\n```") == "SELECT * FROM transactions"


async def test_chat_service_queries_only_local_sqlite_database(monkeypatch, temp_db_path) -> None:
    await _seed_transactions(temp_db_path)
    seen_paths = []

    async def tracked_connect(db_path):
        seen_paths.append(db_path)
        return await real_connect(db_path)

    monkeypatch.setattr("paisa.ai.chat_service.connect", tracked_connect)
    model_client = FakeModelClient(
        [
            "SELECT merchant_name, amount FROM transactions ORDER BY date DESC LIMIT 1",
            "Latest debit is Zomato for Rs 350.0.",
        ]
    )

    answer = await ChatService(temp_db_path, model_client=model_client).answer_question("latest debit")

    assert seen_paths == [temp_db_path]
    assert answer.result.columns == ["merchant_name", "amount"]
    assert answer.result.rows == [("Zomato", 350.0)]
    assert "Zomato" in answer.answer


async def test_chat_service_includes_live_categories_and_type_guidance_in_sql_prompt(temp_db_path) -> None:
    await initialize_database(temp_db_path)
    await ensure_category(temp_db_path, "Education")
    model_client = FakeModelClient(
        [
            "SELECT 1 AS total",
            "total is 1.",
        ]
    )

    await ChatService(temp_db_path, model_client=model_client).answer_question("educational expenses")

    system_prompt = model_client.calls[0][0]["content"]
    assert "Available categories:" in system_prompt
    assert "- Education" in system_prompt
    assert "transactions.type is only DEBIT or CREDIT" in system_prompt
    assert "categories.name" in system_prompt


async def test_chat_service_surfaces_ollama_unavailable(temp_db_path) -> None:
    class OfflineModel:
        async def chat(self, messages, *, model=None):
            raise OllamaUnavailableError("Ollama unavailable at http://127.0.0.1:11434. Start Ollama locally and retry.")

    with pytest.raises(OllamaUnavailableError):
        await ChatService(temp_db_path, model_client=OfflineModel()).answer_question("balance")


async def test_ollama_client_normalizes_chat_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"message": {"content": "ok"}}

    class FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, path: str, json: dict[str, object]) -> FakeResponse:
            captured["path"] = path
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("paisa.ai.ollama_client.httpx.AsyncClient", FakeAsyncClient)

    client = OllamaClient(base_url="http://127.0.0.1:11434/api/chat")
    answer = await client.chat([{"role": "user", "content": "ping"}])

    assert answer == "ok"
    assert captured["base_url"] == "http://127.0.0.1:11434"
    assert captured["path"] == "/api/chat"


def test_ollama_client_adds_wsl_host_candidate(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://172.26.48.1:11434/api/chat")

    client = OllamaClient()

    assert client.base_urls == ("http://172.26.48.1:11434",)


async def test_ollama_client_surfaces_timeout_as_request_error(monkeypatch) -> None:
    class SlowAsyncClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, path: str, json: dict[str, object]):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("paisa.ai.ollama_client.httpx.AsyncClient", SlowAsyncClient)

    with pytest.raises(OllamaRequestError, match="timed out after 15s"):
        await OllamaClient(base_url="http://172.26.48.1:11434", timeout=15).chat(
            [{"role": "user", "content": "ping"}]
        )