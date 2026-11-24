"""Demo from the terminal:  python -m app.cli "How many diabetic patients are over 60?" --tenant 1"""
import argparse
import json

from app.service import ask

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--tenant", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="print the raw response")
    args = parser.parse_args()

    r = ask(args.question, args.tenant)
    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(f"\nStatus : {r['status']}\nAnswer : {r['answer']}")
        if r["sql"]:
            print(f"SQL    : {r['sql']}")
        if r["rows_preview"]:
            print(f"Rows   : {r['columns']} -> {r['rows_preview'][:5]}")
        print("\nTrace:")
        for s in r["trace"]:
            print(f"  - {s['node']:<10} {s['ms']:>6} ms  {s['detail']}")
        u = r["usage"]
        print(f"\nLLM calls {u['llm_calls']} (+{u['cached_calls']} cached) | tokens {u['input_tokens']} in / {u['output_tokens']} out | "
              f"{r['latency_ms']} ms")
