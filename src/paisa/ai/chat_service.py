from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from paisa.ai.ollama_client import OllamaClient
from paisa.ai.sql_guard import validate_select_sql
from paisa.db.connection import connect
from paisa.db.queries import list_categories


class ChatServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[object, ...]]
    truncated: bool


@dataclass(frozen=True)
class ChatAnswer:
    sql: str
    answer: str
    result: QueryResult


_SCHEMA_PROMPT = """
You write SQLite SELECT queries for Paisa.
Return SQL only. No markdown. No commentary.
Rules:
- Single read-only statement.
- Use only SELECT or WITH ... SELECT.
- Never mutate schema or data.
- Prefer explicit column names.
- Default to ORDER BY date DESC when showing transactions.
- Keep result sets compact.
- Filter spending categories with a JOIN to categories and categories.name.
- transactions.type is only DEBIT or CREDIT, never a spending category.
- If the user uses an adjective or plural form, map it to the closest existing category name.

Schema:
categories(id, name)
transactions(id, bank, date, narration, merchant_name, amount, type, running_balance, category_id, created_at)
parsed_files(file_hash, filename, bank, import_date)

Useful joins:
transactions.category_id = categories.id
""".strip()

_ANSWER_PROMPT = """
You answer finance questions from Paisa query results.
Keep answers concise. Mention when no rows match.
Use rupee amounts exactly as given. Do not invent data.
""".strip()


class ChatService:
    def __init__(
        self,
        db_path: Path,
        *,
        model_client: OllamaClient | None = None,
        sql_row_limit: int = 50,
    ) -> None:
        self.db_path = db_path
        self.model_client = model_client or OllamaClient()
        self.sql_row_limit = sql_row_limit

    async def answer_question(self, question: str) -> ChatAnswer:
        normalized_question = " ".join(question.split()).strip()
        if not normalized_question:
            raise ValueError("Question cannot be empty.")

        sql = await self._generate_sql(normalized_question)
        result = await self._run_query(sql)
        answer = await self._generate_answer(normalized_question, sql, result)
        return ChatAnswer(sql=sql, answer=answer, result=result)

    async def _generate_sql(self, question: str) -> str:
        schema_prompt = await self._build_schema_prompt()
        response = await self.model_client.chat(
            [
                {"role": "system", "content": schema_prompt},
                {"role": "user", "content": question},
            ]
        )
        return validate_select_sql(response)

    async def _build_schema_prompt(self) -> str:
        try:
            categories = await list_categories(self.db_path)
        except sqlite3.OperationalError:
            return _SCHEMA_PROMPT
        if not categories:
            return _SCHEMA_PROMPT

        category_lines = "\n".join(f"- {name}" for _, name in categories)
        return f"{_SCHEMA_PROMPT}\n\nAvailable categories:\n{category_lines}"

    async def _generate_answer(self, question: str, sql: str, result: QueryResult) -> str:
        response = await self.model_client.chat(
            [
                {"role": "system", "content": _ANSWER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"SQL: {sql}\n"
                        f"Columns: {', '.join(result.columns) if result.columns else '(none)'}\n"
                        f"Rows: {result.rows}\n"
                        f"Truncated: {'yes' if result.truncated else 'no'}"
                    ),
                },
            ]
        )
        return response.strip()

    async def _run_query(self, sql: str) -> QueryResult:
        db = await connect(self.db_path)
        try:
            cursor = await db.execute(sql)
            rows = await cursor.fetchmany(self.sql_row_limit + 1)
            columns = [description[0] for description in cursor.description or []]
        finally:
            await db.close()

        truncated = len(rows) > self.sql_row_limit
        limited_rows = rows[: self.sql_row_limit]
        serialized_rows = [tuple(row[column] for column in columns) for row in limited_rows]
        return QueryResult(columns=columns, rows=serialized_rows, truncated=truncated)