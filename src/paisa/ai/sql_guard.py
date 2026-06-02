from __future__ import annotations

import re


class SqlGuardError(ValueError):
    pass


_CODE_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)
_LEADING_LABEL_RE = re.compile(r"^(sql|query|sqlquery)\s*[:\-]\s*", re.IGNORECASE)
_DISALLOWED_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|vacuum|reindex|analyze|grant|revoke)\b",
    re.IGNORECASE,
)


def normalize_sql_response(raw_response: str) -> str:
    sql = raw_response.strip()
    sql = _CODE_FENCE_RE.sub("", sql).strip()
    sql = _LEADING_LABEL_RE.sub("", sql).strip()
    if "```" in sql:
        sql = sql.replace("```", "").strip()
    return sql


def validate_select_sql(raw_response: str) -> str:
    sql = normalize_sql_response(raw_response)
    if not sql:
        raise SqlGuardError("Model did not return SQL.")

    if "--" in sql or "/*" in sql or "*/" in sql:
        raise SqlGuardError("SQL comments are not allowed.")

    sql_body = sql[:-1].strip() if sql.endswith(";") else sql
    if ";" in sql_body:
        raise SqlGuardError("Only one SQL statement is allowed.")

    lowered = sql_body.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise SqlGuardError("Only read-only SELECT queries are allowed.")

    if _DISALLOWED_RE.search(sql_body):
        raise SqlGuardError("Read-only SELECT queries only.")

    return sql_body