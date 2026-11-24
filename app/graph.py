"""LangGraph agent flow (2 LLM calls for a typical question: planner + analyst).

    planner ──clarify / refuse──> END
       │ sql
       v
    execute ──SQL error / wrong result shape──> planner   (self-correction with the error message)
       │ ok          └──no attempts left──> give_up -> END
       v
    analyst ──> verify (code, no LLM) ──unsupported numbers (once)──> analyst
                   └──> END
"""
import time
from typing import Literal, Optional, TypedDict

import yaml
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app import prompts
from app.config import settings
from app.guardrails import GuardrailViolation
from app.llm import BudgetExceeded, generate
from app.tools import describe_schema, run_query
from app.verify import check_result_shape, verify_answer

SEMANTIC_LAYER = settings.semantic_layer_path.read_text(encoding="utf-8")
yaml.safe_load(SEMANTIC_LAYER)  # fail fast on a broken semantic layer


# ---------- structured outputs ----------
class Plan(BaseModel):
    route: Literal["sql", "clarify", "refuse"]
    reason: str
    clarifying_question: Optional[str] = None
    sql: Optional[str] = Field(default=None, description="One PostgreSQL SELECT statement when route is sql")


class Answer(BaseModel):
    answer: str


# ---------- state ----------
class AgentState(TypedDict, total=False):
    question: str
    tenant_id: int
    status: str            # answered | clarification_needed | refused | failed
    message: str
    sql: str
    sql_attempts: int
    error: Optional[str]
    result: dict
    answer: str
    verified: bool
    analyst_attempts: int
    verifier_issues: str
    usage: dict
    trace: list


def _step(state: AgentState, node: str, detail: str, started: float) -> list:
    return state.get("trace", []) + [{"node": node, "detail": detail,
                                      "ms": int((time.perf_counter() - started) * 1000)}]


def _budget_guard(fn):
    """Harness: if the per-question LLM budget runs out, stop cleanly instead of crashing."""
    def wrapper(state: AgentState):
        try:
            return fn(state)
        except BudgetExceeded as e:
            return {"status": "failed", "message": str(e)}
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------- nodes ----------
@_budget_guard
def planner(state: AgentState):
    t = time.perf_counter()
    attempt = state.get("sql_attempts", 0) + 1
    prompt = (f"Schema:\n{describe_schema(state['tenant_id'])}\n\nSemantic layer:\n{SEMANTIC_LAYER}\n\n"
              f"Question: {state['question']}")
    if state.get("error"):
        prompt += f"\n\nPrevious SQL:\n{state.get('sql')}\nIt failed with: {state['error']}\nFix it."
    plan, usage = generate(prompts.PLANNER, prompt, Plan, state["usage"])

    update = {"usage": usage, "trace": _step(state, "planner", f"{plan.route}: {plan.reason}", t)}
    if plan.route == "clarify":
        update.update(status="clarification_needed",
                      message=plan.clarifying_question or "Could you clarify your question?")
    elif plan.route == "refuse":
        update.update(status="refused", message=plan.reason)
    elif not plan.sql:
        update.update(status="failed", message="Planner chose 'sql' but returned no query.")
    else:
        update.update(sql=plan.sql, sql_attempts=attempt)
    return update


def execute(state: AgentState):
    t = time.perf_counter()
    try:
        result = run_query(state["sql"], state["tenant_id"])
        shape_issue = check_result_shape(state["question"], result["columns"], result["rows"])
        if shape_issue:  # wrong result shape -> back to the planner like any SQL error
            return {"error": shape_issue, "sql": result["sql"],
                    "trace": _step(state, "shape_check", shape_issue, t)}
        detail = f"{len(result['rows'])} row(s){' (cached)' if result['cached'] else ''}"
        return {"result": result, "sql": result["sql"], "error": None,
                "trace": _step(state, "execute", detail, t)}
    except GuardrailViolation as e:
        if e.kind in ("phi", "write"):  # never retry around a safety block
            return {"status": "refused", "message": f"Blocked by guardrail: {e}", "error": str(e),
                    "trace": _step(state, "guardrail", f"blocked ({e.kind})", t)}
        return {"error": f"Guardrail: {e}", "trace": _step(state, "guardrail", str(e), t)}
    except Exception as e:  # SQL error -> feed back to the planner
        return {"error": str(e).strip().splitlines()[0], "trace": _step(state, "execute", f"error: {e}", t)}


@_budget_guard
def analyst(state: AgentState):
    t = time.perf_counter()
    result = state["result"]
    prompt = (f"Question: {state['question']}\nSQL: {state['sql']}\nColumns: {result['columns']}\n"
              f"Rows (first 50): {result['rows'][:50]}")
    if state.get("verifier_issues"):
        prompt += f"\n\nYour previous answer was rejected: {state['verifier_issues']}"
    out, usage = generate(prompts.ANALYST, prompt, Answer, state["usage"])
    return {"answer": out.answer, "analyst_attempts": state.get("analyst_attempts", 0) + 1, "usage": usage,
            "trace": _step(state, "analyst", "drafted answer", t)}


def verify(state: AgentState):
    t = time.perf_counter()
    result = state["result"]
    ok, issues = verify_answer(state["answer"], state["question"], result["columns"], result["rows"])
    update = {"verified": ok, "verifier_issues": "" if ok else issues,
              "trace": _step(state, "verify", issues, t)}
    if ok or state.get("analyst_attempts", 0) >= 2:
        update["status"] = "answered"
    return update


def give_up(state: AgentState):
    return {"status": "failed",
            "message": f"Could not produce a working query after {state.get('sql_attempts')} attempts: "
                       f"{state.get('error')}"}


# ---------- edges ----------
def after_planner(state: AgentState):
    return END if state.get("status") else "execute"


def after_execute(state: AgentState):
    if state.get("status"):
        return END
    if state.get("error"):
        return "planner" if state.get("sql_attempts", 0) < settings.max_sql_attempts else "give_up"
    return "analyst"


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("planner", planner), ("execute", execute), ("analyst", analyst),
                     ("verify", verify), ("give_up", give_up)]:
        g.add_node(name, fn)
    g.set_entry_point("planner")
    g.add_conditional_edges("planner", after_planner, {"execute": "execute", END: END})
    g.add_conditional_edges("execute", after_execute,
                            {"planner": "planner", "analyst": "analyst", "give_up": "give_up", END: END})
    g.add_edge("give_up", END)
    g.add_conditional_edges("analyst", lambda s: END if s.get("status") else "verify",
                            {"verify": "verify", END: END})
    g.add_conditional_edges("verify", lambda s: END if s.get("status") else "analyst",
                            {"analyst": "analyst", END: END})
    return g.compile()


GRAPH = build_graph()
