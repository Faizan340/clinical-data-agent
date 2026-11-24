import time

from app.graph import GRAPH
from app.llm import new_usage


def ask(question: str, tenant_id: int = 1) -> dict:
    started = time.perf_counter()
    state = GRAPH.invoke({"question": question, "tenant_id": tenant_id, "usage": new_usage(), "trace": []},
                         {"recursion_limit": 25})
    result = state.get("result") or {}
    return {
        "status": state.get("status", "failed"),
        "answer": state.get("answer") if state.get("status") == "answered" else state.get("message"),
        "sql": state.get("sql"),
        "columns": result.get("columns"),
        "rows_preview": (result.get("rows") or [])[:20],
        "verified": state.get("verified"),
        "sql_attempts": state.get("sql_attempts", 0),
        "trace": state.get("trace", []),
        "usage": state.get("usage", new_usage()),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
