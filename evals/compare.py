"""Build a model comparison table from saved eval runs.

Each model needs two saved runs, one per data set:
    python -m evals.run --model claude-sonnet-5-5 --trials 3 --save evals/results/claude-sonnet-5-5-fixture.json
    python -m evals.run --model claude-sonnet-5-5 --data weld --trials 3 --save evals/results/claude-sonnet-5-5-weld.json
Then:
    python -m evals.compare            # prints a Markdown table
"""
import json
from pathlib import Path

from evals.run import SETS, passed, summary

RESULTS = Path(__file__).with_name("results")
ORDER = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def load():
    """Rows for each model, joined across its saved runs."""
    models = {}
    for path in sorted(RESULTS.glob("*.json")):
        run = json.loads(path.read_text())
        entry = models.setdefault(run["model"], {"rows": [], "trials": set()})
        entry["rows"] += run["rows"]
        entry["trials"].add(run["trials"])
    return dict(sorted(models.items(), key=lambda m: ORDER.index(m[0]) if m[0] in ORDER else len(ORDER)))


def table(models):
    header = ["Model", *(name.capitalize() for name in SETS), "All", "Mean time", "p90 time",
              "Tokens per answer (in / out)", "Cost per 1,000 answers"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for model, entry in models.items():
        rows = entry["rows"]
        scores = []
        for name in (*SETS, None):
            group = [r for r in rows if name is None or r["set"] == name]
            scores.append(f"{sum(map(passed, group))} of {len(group)}" if group else "")
        stats = summary(rows, model)
        price = f"${stats['cost_per_answer'] * 1000:.2f}" if stats["cost_per_answer"] is not None else "n/a"
        lines.append("| " + " | ".join([
            f"`{model}`", *scores, f"{stats['mean_seconds']:.1f}s", f"{stats['p90_seconds']:.1f}s",
            f"{stats['mean_input_tokens']:,.0f} / {stats['mean_output_tokens']:,.0f}", price]) + " |")
    trials = sorted({t for entry in models.values() for t in entry["trials"]})
    note = (f"\nA question counts as passed only when all {trials[0]} trials pass. Time and tokens are averaged "
            "over every trial, and time is wall-clock from this machine, including tool calls.")
    return "\n".join(lines) + (note if len(trials) == 1 else "")


if __name__ == "__main__":
    print(table(load()))
