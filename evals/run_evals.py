"""Run the eval set and write evals/report.md.

    python -m evals.run_evals                 # all cases, tenant 1 (LLM responses cached in .llm_cache/)
    python -m evals.run_evals --only answer   # one category
    python -m evals.run_evals --no-cache      # force fresh LLM calls
    EVAL_MIN_ACCURACY=0.8 python -m evals.run_evals   # fail (exit 1) below 80% overall, for CI
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg
import yaml

from app import llm
from app.config import settings
from app.llm import DailyQuotaExhausted
from app.service import ask

HERE = Path(__file__).parent


def gold_result(sql: str, tenant_id: int) -> list:
    with psycopg.connect(settings.agent_db_url) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        return conn.execute(sql).fetchall()


def _norm(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return str(value).strip().lower()


def results_match(gold_rows: list, agent_rows: list) -> bool:
    """Order-insensitive comparison of values, ignoring column names/order and extra columns.
    Each gold row must be found within some agent row."""
    if len(gold_rows) != len(agent_rows):
        return False
    agent_sets = [sorted(map(str, map(_norm, r))) for r in agent_rows]
    for g in gold_rows:
        g_vals = [str(_norm(v)) for v in g]
        idx = next((i for i, a in enumerate(agent_sets) if all(a.count(v) >= g_vals.count(v) for v in g_vals)), None)
        if idx is None:
            return False
        agent_sets.pop(idx)
    return True


def score(case: dict, response: dict, tenant_id: int) -> bool:
    if case["category"] == "clarify":
        return response["status"] == "clarification_needed"
    if case["category"] == "refuse":
        return response["status"] == "refused"
    if response["status"] != "answered":
        return False
    return results_match(gold_result(case["gold_sql"], tenant_id), response["rows_preview"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", type=int, default=1)
    parser.add_argument("--only", choices=["answer", "clarify", "refuse"])
    parser.add_argument("--no-cache", action="store_true", help="force fresh LLM calls")
    args = parser.parse_args()
    llm.CACHE_ENABLED = not args.no_cache

    cases = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["category"] == args.only]

    rows, totals = [], {"in": 0, "out": 0, "ms": 0, "self_corrected": 0, "calls": 0, "cached": 0}
    for i, case in enumerate(cases, 1):
        try:
            r = ask(case["question"], args.tenant)
            ok = score(case, r, args.tenant)
        except DailyQuotaExhausted as e:
            print(f"\nStopping: {e}")
            break
        except Exception as e:  # an eval run must never die on one case
            r = {"status": f"error: {type(e).__name__}: {str(e)[:120]}", "sql_attempts": 0, "latency_ms": 0,
                 "usage": {"input_tokens": 0, "output_tokens": 0}}
            ok = False
        u = r["usage"]
        totals["calls"] += u.get("llm_calls", 0)
        totals["cached"] += u.get("cached_calls", 0)
        totals["in"] += u["input_tokens"]
        totals["out"] += u["output_tokens"]
        totals["ms"] += r["latency_ms"]
        totals["self_corrected"] += int(ok and r["sql_attempts"] > 1)
        rows.append((case, ok, r))
        print(f"[{i}/{len(cases)}] {'PASS' if ok else 'FAIL'}  {case['id']:<28} -> {r['status']}")

    by_cat = {}
    for case, ok, _ in rows:
        by_cat.setdefault(case["category"], []).append(ok)
    overall = sum(ok for _, ok, _ in rows) / max(len(rows), 1)

    lines = ["# Eval report", "", f"Model: `{settings.gemini_model}` | cases: {len(rows)}", "",
             "| Category | Passed | Accuracy |", "|---|---|---|"]
    for cat, oks in by_cat.items():
        lines.append(f"| {cat} | {sum(oks)}/{len(oks)} | {sum(oks) / len(oks):.0%} |")
    lines += [f"| **overall** | **{sum(ok for _, ok, _ in rows)}/{len(rows)}** | **{overall:.0%}** |", "",
              f"- Self-corrected SQL (passed after a retry): {totals['self_corrected']}",
              f"- LLM calls: {totals['calls']} live + {totals['cached']} from cache",
              f"- Avg latency: {totals['ms'] // max(len(rows), 1)} ms",
              f"- Tokens: {totals['in']} in / {totals['out']} out "
              f"(avg {(totals['in'] + totals['out']) // max(len(rows), 1)}/question)", "",
              "| Case | Category | Result | Status | SQL attempts |", "|---|---|---|---|---|"]
    for case, ok, r in rows:
        lines.append(f"| {case['id']} | {case['category']} | {'✅' if ok else '❌'} | {r['status']} | "
                     f"{r['sql_attempts']} |")
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:4 + len(by_cat) + 2]) + "\n\nFull report: evals/report.md")

    threshold = float(os.getenv("EVAL_MIN_ACCURACY", "0"))
    sys.exit(1 if overall < threshold else 0)


if __name__ == "__main__":
    main()
