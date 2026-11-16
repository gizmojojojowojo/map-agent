"""Score the agent on evals/questions.json.

A question passes when the agent made the expected tool calls with the expected
inputs and the map ends up showing exactly the expected sites.

Questions belong to a set. The "core" set gates the run. The "hard" and "fresh"
sets are reported only, and each failure prints the calls the agent made. Hard
questions were written to find limits and have since been used to guide fixes.
Fresh questions have not been used that way, so they measure how well the fixes
carry over to new wording.

Models are not deterministic. --trials 3 asks each question three times and
reports how many trials passed, which shows flaky questions.

A question may list "sites" explicitly. That marks a target the current tools
cannot reach with the listed calls, and the calls are not run when freezing.
An expected input written as {"approx": 1.24, "tolerance": 0.1} accepts a range,
and {"any_of": ["Ft. Morgan", "Fort Morgan"]} accepts any listed value.

Usage:
    python -m evals.run --provider claude     # needs ANTHROPIC_API_KEY
    python -m evals.run --provider claude --set fresh --trials 3
    python -m evals.run --freeze              # recompute expected site IDs after a data change
    python -m evals.run --data weld --trials 3  # real Weld parcels; see below

--data weld asks evals/weld_questions.json against real Weld County parcels: the
PostGIS database when MAP_AGENT_DATABASE_URL is set, otherwise data/weld.json.
Real data is rebuilt from live sources and is not committed, so expected sites are
not frozen. They are computed at run time by running the expected calls on the
same store the agent uses.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from mapagent import agent, providers, tools
from mapagent.store import MemoryStore, PostgisStore

QUESTIONS = Path(__file__).with_name("questions.json")
EXPECTED = Path(__file__).with_name("expected_sites.json")
WELD_QUESTIONS = Path(__file__).with_name("weld_questions.json")
WELD_DATA = Path(__file__).resolve().parents[1] / "data/weld.json"
THRESHOLD = 0.85
SETS = ("core", "hard", "fresh", "weld")
# US$ per million input and output tokens (Anthropic first-party rates, November 2026).
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}


def same(expected, actual):
    if isinstance(expected, dict) and "any_of" in expected:
        return any(same(option, actual) for option in expected["any_of"])
    if isinstance(expected, dict) and "approx" in expected:
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and abs(expected["approx"] - actual) <= expected["tolerance"] + 1e-9)
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and abs(expected - actual) < 1e-6
    if isinstance(expected, str):
        return isinstance(actual, str) and expected.strip().lower() == actual.strip().lower()
    if isinstance(expected, list) and all(isinstance(item, str) for item in expected):
        return (isinstance(actual, list) and all(isinstance(item, str) for item in actual)
                and sorted(map(str.lower, expected)) == sorted(map(str.lower, actual)))
    return expected == actual


def representative(value):
    """One concrete value for an expected input written as approx or any_of."""
    if isinstance(value, dict) and "approx" in value:
        return value["approx"]
    if isinstance(value, dict) and "any_of" in value:
        return value["any_of"][0]
    return value


def calls_match(expected, trace):
    """Successful calls must match the expected tools in order, with every expected input present."""
    made = [step for step in trace if not step["error"]]
    return len(made) == len(expected) and all(
        want["tool"] == got["tool"] and all(k in got["args"] and same(v, got["args"][k]) for k, v in want["args"].items())
        for want, got in zip(expected, made))


def expected_sites(store, calls):
    """Run the expected calls directly, feeding geocoded places into the next sites_near.

    Several geocoded places become one sites_near call: the first as its centre and
    the rest as more_points.
    """
    sites, places = [], []
    for c in calls:
        args = {k: representative(v) for k, v in c["args"].items()}
        if c["tool"] == "sites_near" and places:
            first, *rest = places
            args.update(latitude=first["latitude"], longitude=first["longitude"])
            if rest:
                args["more_points"] = [{"latitude": p["latitude"], "longitude": p["longitude"]} for p in rest]
        result, features = tools.call(store, c["tool"], args)
        if "error" in result:
            raise SystemExit(f"Expected call is invalid: {c} -> {result['error']}")
        if c["tool"] == "geocode_place":
            if result["count"] != 1:
                break
            places.append(result["places"][0])
        if features is not None:
            sites = [f["id"] for f in features]
    return sorted(sites)


def score(question, result, expected_ids):
    shown = sorted(f["id"] for f in result["features"]["features"])
    made = [{"tool": step["tool"], "args": step["args"]} for step in result["trace"] if not step["error"]]
    return {"id": question["id"], "set": question.get("set", "core"),
            "calls": calls_match(question["calls"], result["trace"]),
            "sites": shown == expected_ids, "complete": result["complete"], "made": made, "shown": shown}


def attempt(provider, store, question, expected_ids):
    started = time.perf_counter()
    try:
        result = agent.ask(question["question"], store, provider)
        row = {**score(question, result, expected_ids), "usage": result.get("usage", {})}
    except Exception as error:  # one failed request should not hide the other results
        row = {"id": question["id"], "set": question.get("set", "core"), "calls": False, "sites": False,
               "complete": False, "made": [], "shown": [], "usage": {}, "error": repr(error)}
    return {**row, "seconds": time.perf_counter() - started}


def run(provider, store, questions, expected, trials=1):
    """One row per question: the first failing trial if any failed, plus how many trials passed."""
    rows = []
    for number, q in enumerate(questions, 1):
        print(f"[{number}/{len(questions)}] {q['id']}", file=sys.stderr, flush=True)
        attempts = [attempt(provider, store, q, expected[q["id"]]) for _ in range(trials)]
        good = [a for a in attempts if ok(a)]
        shown = next((a for a in attempts if not ok(a)), attempts[0])
        totals = {key: sum(a["usage"].get(key, 0) for a in attempts)
                  for key in ("model_calls", "input_tokens", "output_tokens")}
        rows.append({**shown, "passes": len(good), "trials": trials, "seconds": [a["seconds"] for a in attempts],
                     "totals": totals})
    return rows


def ok(attempt_row):
    return attempt_row["calls"] and attempt_row["sites"] and attempt_row["complete"]


def passed(row):
    """A question passes only when every trial passed."""
    return row["passes"] == row["trials"]


def rate(rows):
    return sum(map(passed, rows)) / len(rows)


def cost(rows, model):
    """US$ for every trial in rows, or None for a model with no listed price."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return sum(r["totals"]["input_tokens"] * price_in + r["totals"]["output_tokens"] * price_out for r in rows) / 1e6


def summary(rows, model):
    """Per-answer averages over every trial: seconds, tokens and US$."""
    answers = sum(r["trials"] for r in rows)
    seconds = sorted(s for r in rows for s in r["seconds"])
    total = cost(rows, model)
    return {"answers": answers, "mean_seconds": sum(seconds) / answers, "p90_seconds": seconds[int(0.9 * (answers - 1))],
            "mean_input_tokens": sum(r["totals"]["input_tokens"] for r in rows) / answers,
            "mean_output_tokens": sum(r["totals"]["output_tokens"] for r in rows) / answers,
            "cost_per_answer": total / answers if total is not None else None, "total_cost": total}


def report(rows, provider, model=None):
    lines = []
    for r in rows:
        label = "PASS" if passed(r) else "FAIL" if r["passes"] == 0 else "FLAKY"
        tally = f" {r['passes']}/{r['trials']}" if r["trials"] > 1 else ""
        lines.append(f"{label:<5}{tally} {r['set']:<5} {r['id']:<26} calls={r['calls']} sites={r['sites']} {r.get('error', '')}")
        if not passed(r) and "error" not in r:
            lines.append(f"      made:  {json.dumps(r['made'])}")
            lines.append(f"      shown: {json.dumps(r['shown'])}")
    lines.append("")
    for name in SETS:
        group = [r for r in rows if r["set"] == name]
        if group:
            gate = f" (threshold {THRESHOLD:.0%})" if name == "core" else " (reported only)"
            flaky = sum(0 < r["passes"] < r["trials"] for r in group)
            lines.append(f"{provider} {name}: {sum(map(passed, group))} of {len(group)} passed, {rate(group):.0%}{gate}"
                         + (f", {flaky} flaky" if flaky else ""))
    stats = summary(rows, model)
    price = f", ${stats['cost_per_answer'] * 1000:.2f} per 1,000 answers, ${stats['total_cost']:.2f} this run" \
        if stats["cost_per_answer"] is not None else ""
    lines.append(f"{model or provider}: {stats['mean_seconds']:.1f}s mean, {stats['p90_seconds']:.1f}s p90, "
                 f"{stats['mean_input_tokens']:,.0f} input + {stats['mean_output_tokens']:,.0f} output tokens per answer{price}")
    return "\n".join(lines)


def finish(options, store, questions, expected):
    """Run the questions, print the report, and save the rows if asked."""
    provider = providers.get(options.provider, options.model)
    model = getattr(provider, "model", None)
    rows = run(provider, store, questions, expected, options.trials)
    print(report(rows, options.provider, model))
    if options.save:
        options.save.parent.mkdir(parents=True, exist_ok=True)
        options.save.write_text(json.dumps({"provider": options.provider, "model": model, "data": options.data,
                                            "trials": options.trials, "summary": summary(rows, model),
                                            "rows": rows}, indent=1) + "\n")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="claude")
    parser.add_argument("--model", help="model ID, e.g. claude-sonnet-5-5; defaults to the provider's default")
    parser.add_argument("--save", type=Path, help="write per-question rows and the summary as JSON")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--data", choices=("fixture", "weld"), default="fixture",
                        help="weld: ask the real-data questions against real Weld parcels")
    parser.add_argument("--set", choices=SETS, help="run one set only")
    parser.add_argument("--trials", type=int, default=1, choices=range(1, 11), metavar="1-10",
                        help="ask each question this many times")
    options = parser.parse_args()
    if options.data == "weld":
        url = os.environ.get("MAP_AGENT_DATABASE_URL")
        store = PostgisStore(url) if url else MemoryStore(WELD_DATA)
        questions = [{**q, "set": "weld"} for q in json.loads(WELD_QUESTIONS.read_text())]
        expected = {q["id"]: sorted(q["sites"]) if "sites" in q else expected_sites(store, q["calls"]) for q in questions}
        print(f"store: {'PostGIS' if url else WELD_DATA}", file=sys.stderr)
        rows = finish(options, store, questions, expected)
        return
    store, questions = MemoryStore(), json.loads(QUESTIONS.read_text())
    if options.freeze:
        frozen = {q["id"]: sorted(q["sites"]) if "sites" in q else expected_sites(store, q["calls"]) for q in questions}
        EXPECTED.write_text(json.dumps(frozen, indent=1) + "\n")
        print(f"wrote {EXPECTED}")
        return
    if options.set:
        questions = [q for q in questions if q.get("set", "core") == options.set]
    rows = finish(options, store, questions, json.loads(EXPECTED.read_text()))
    core = [r for r in rows if r["set"] == "core"]
    sys.exit(0 if not core or rate(core) >= THRESHOLD else 1)


if __name__ == "__main__":
    main()
