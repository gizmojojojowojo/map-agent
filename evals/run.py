"""Score the agent on evals/questions.json.

A question passes when the agent made the expected tool calls with the expected
inputs and the map ends up showing exactly the expected sites.

Questions belong to a set. The "core" set gates the run. The "hard" set holds
questions the agent is expected to get wrong at first; its score is reported
separately and each failure prints the calls the agent made.

A question may list "sites" explicitly. That marks a target the current tools
cannot reach with the listed calls, and the calls are not run when freezing.
An expected input written as {"approx": 1.24, "tolerance": 0.1} accepts a range.

Usage:
    python -m evals.run --provider claude     # needs ANTHROPIC_API_KEY
    python -m evals.run --freeze              # recompute expected site IDs after a data change
"""
import argparse
import json
import sys
from pathlib import Path

from mapagent import agent, providers, tools
from mapagent.store import MemoryStore

QUESTIONS = Path(__file__).with_name("questions.json")
EXPECTED = Path(__file__).with_name("expected_sites.json")
THRESHOLD = 0.85


def same(expected, actual):
    if isinstance(expected, dict) and "approx" in expected:
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and abs(expected["approx"] - actual) <= expected["tolerance"])
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and abs(expected - actual) < 1e-6
    if isinstance(expected, str):
        return isinstance(actual, str) and expected.strip().lower() == actual.strip().lower()
    if isinstance(expected, list):
        return isinstance(actual, list) and sorted(map(str.lower, expected)) == sorted(map(str.lower, actual))
    return expected == actual


def calls_match(expected, trace):
    """Successful calls must match the expected tools in order, with every expected input present."""
    made = [step for step in trace if not step["error"]]
    return len(made) == len(expected) and all(
        want["tool"] == got["tool"] and all(k in got["args"] and same(v, got["args"][k]) for k, v in want["args"].items())
        for want, got in zip(expected, made))


def expected_sites(store, calls):
    """Run the expected calls directly, feeding a geocoded place into the next sites_near."""
    sites, place = [], None
    for c in calls:
        args = {k: v["approx"] if isinstance(v, dict) and "approx" in v else v for k, v in c["args"].items()}
        if c["tool"] == "sites_near" and place:
            args.update(latitude=place["latitude"], longitude=place["longitude"])
        result, features = tools.call(store, c["tool"], args)
        if "error" in result:
            raise SystemExit(f"Expected call is invalid: {c} -> {result['error']}")
        if c["tool"] == "geocode_place":
            place = result["places"][0] if result["count"] == 1 else None
            if place is None:
                break
        if features is not None:
            sites = [f["id"] for f in features]
    return sorted(sites)


def score(question, result, expected_ids):
    shown = sorted(f["id"] for f in result["features"]["features"])
    made = [{"tool": step["tool"], "args": step["args"]} for step in result["trace"] if not step["error"]]
    return {"id": question["id"], "set": question.get("set", "core"),
            "calls": calls_match(question["calls"], result["trace"]),
            "sites": shown == expected_ids, "complete": result["complete"], "made": made, "shown": shown}


def run(provider, store, questions, expected):
    rows = []
    for number, q in enumerate(questions, 1):
        print(f"[{number}/{len(questions)}] {q['id']}", file=sys.stderr, flush=True)
        try:
            rows.append(score(q, agent.ask(q["question"], store, provider), expected[q["id"]]))
        except Exception as error:  # one failed request should not hide the other results
            rows.append({"id": q["id"], "set": q.get("set", "core"), "calls": False, "sites": False,
                         "complete": False, "made": [], "shown": [], "error": repr(error)})
    return rows


def passed(row):
    return row["calls"] and row["sites"] and row["complete"]


def rate(rows):
    return sum(map(passed, rows)) / len(rows)


def report(rows, provider):
    lines = []
    for r in rows:
        lines.append(f"{'PASS' if passed(r) else 'FAIL'}  {r['set']:<4} {r['id']:<24} calls={r['calls']} sites={r['sites']} {r.get('error', '')}")
        if not passed(r) and "error" not in r:
            lines.append(f"      made:  {json.dumps(r['made'])}")
            lines.append(f"      shown: {json.dumps(r['shown'])}")
    lines.append("")
    for name in ("core", "hard"):
        group = [r for r in rows if r["set"] == name]
        if group:
            gate = f" (threshold {THRESHOLD:.0%})" if name == "core" else " (reported only)"
            lines.append(f"{provider} {name}: {sum(map(passed, group))} of {len(group)} passed, {rate(group):.0%}{gate}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="claude")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--set", choices=["core", "hard"], help="run one set only")
    options = parser.parse_args()
    store, questions = MemoryStore(), json.loads(QUESTIONS.read_text())
    if options.freeze:
        frozen = {q["id"]: sorted(q["sites"]) if "sites" in q else expected_sites(store, q["calls"]) for q in questions}
        EXPECTED.write_text(json.dumps(frozen, indent=1) + "\n")
        print(f"wrote {EXPECTED}")
        return
    if options.set:
        questions = [q for q in questions if q.get("set", "core") == options.set]
    rows = run(providers.get(options.provider), store, questions, json.loads(EXPECTED.read_text()))
    print(report(rows, options.provider))
    core = [r for r in rows if r["set"] == "core"]
    sys.exit(0 if not core or rate(core) >= THRESHOLD else 1)


if __name__ == "__main__":
    main()
