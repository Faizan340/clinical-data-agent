"""Deterministic answer verifier (no LLM call).

Every number in the analyst's answer must come from the query result (a cell value, a column total
or the row count) or from the question itself. This catches the most common LLM failure, a
hallucinated or mis-copied number, for free and reproducibly.
"""
import re

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _NUMBER.findall(text or "")]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.051, abs(b) * 0.001)  # allow rounding to 1 decimal


def allowed_numbers(question: str, columns: list, rows: list) -> list[float]:
    allowed = _numbers(question) + [float(len(rows))]
    for row in rows:
        for value in row:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                allowed.append(float(value))
            elif isinstance(value, str):
                allowed += _numbers(value)
    for i in range(len(columns)):
        col = [r[i] for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool)]
        if len(col) > 1:
            allowed.append(float(sum(col)))
    return allowed


_BREAKDOWN = re.compile(r"\b(by|per|each|every|breakdown|break down|distribution|which)\b", re.I)


def check_result_shape(question: str, columns: list, rows: list) -> str | None:
    """A 'How many ...' question (not a breakdown) must return exactly one row.
    Returns an error message for the planner, or None when the shape is fine."""
    if question.strip().lower().startswith("how many") and not _BREAKDOWN.search(question) and len(rows) > 1:
        return (f"The question asks for a single number but the query returned {len(rows)} rows. "
                "Aggregate into one row (e.g. count the rows of a grouped subquery).")
    return None


def verify_answer(answer: str, question: str, columns: list, rows: list) -> tuple[bool, str]:
    allowed = allowed_numbers(question, columns, rows)
    unsupported = [n for n in _numbers(answer) if not any(_close(n, a) for a in allowed)]
    if unsupported:
        return False, (f"These numbers are not in the query result: {unsupported}. "
                       "Use only numbers from the result rows.")
    return True, "ok"
