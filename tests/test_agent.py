"""Tests run on the fixture with a scripted model: no database, network or API key."""
import json

import pytest

from evals import run as evals
from mapagent import agent, tools
from mapagent.agent import Call, Turn
from mapagent.providers import ClaudeProvider, OpenAIProvider, ScriptedProvider
from mapagent.store import MemoryStore

STORE = MemoryStore()


def near_from_geocode(radius):
    """A scripted step that reads the geocode result, as a model would."""
    def step(messages):
        place = json.loads(messages[-1]["results"][0]["content"])["places"][0]
        return Turn(calls=[Call("c2", "sites_near", {"latitude": place["latitude"],
                                                     "longitude": place["longitude"], "radius_miles": radius})])
    return step


def test_search_returns_answer_trace_and_features():
    provider = ScriptedProvider([
        Turn(calls=[Call("c1", "search_sites", {"min_acres": 300, "counties": ["Weld"]})]),
        Turn("Three Weld sites are over 300 acres."),
    ])
    result = agent.ask("Sites over 300 acres in Weld County", STORE, provider)
    assert result["complete"] and result["answer"].startswith("Three")
    assert result["trace"][0]["site_ids"] == ["W-003", "W-006", "W-001"]  # largest first
    assert [f["id"] for f in result["features"]["features"]] == ["W-003", "W-006", "W-001"]
    assert result["features"]["features"][0]["geometry"]["type"] == "Polygon"
    assert "Confirm power capacity" in result["evidence_boundary"]


def test_geocode_then_near_chains_two_tools():
    provider = ScriptedProvider([
        Turn(calls=[Call("c1", "geocode_place", {"name": "hudson"})]),
        near_from_geocode(5),
        Turn("Two sites are within 5 miles of Hudson."),
    ])
    result = agent.ask("What's within 5 miles of Hudson?", STORE, provider)
    assert [s["tool"] for s in result["trace"]] == ["geocode_place", "sites_near"]
    assert result["trace"][1]["site_ids"] == ["W-001", "W-002"]
    assert result["features"]["features"][0]["properties"]["distance_miles"] < 5


def test_invalid_input_is_returned_to_the_model_not_raised():
    provider = ScriptedProvider([
        Turn(calls=[Call("c1", "search_sites", {"min_acres": -5, "owner": "x"})]),
        Turn(calls=[Call("c2", "search_sites", {"min_acres": 1000})]),
        Turn("One site."),
    ])
    result = agent.ask("Sites over 1000 acres", STORE, provider)
    assert "Invalid input" in result["trace"][0]["error"]
    assert result["trace"][1]["site_ids"] == ["M-001"]


def test_unknown_tool_and_step_limit():
    result, features = tools.call(STORE, "drop_table", {})
    assert "Unknown tool" in result["error"] and features is None
    looping = ScriptedProvider([Turn(calls=[Call("c", "geocode_place", {"name": "Hudson"})])] * 3)
    stopped = agent.ask("loop", STORE, looping, max_steps=3)
    assert not stopped["complete"] and len(stopped["trace"]) == 3


def test_question_length_is_bounded():
    with pytest.raises(ValueError):
        agent.ask("x" * 401, STORE, ScriptedProvider([]))
    with pytest.raises(ValueError):
        agent.ask("   ", STORE, ScriptedProvider([]))


def test_tool_specs_are_closed_schemas():
    specs = {s["name"]: s for s in tools.specs()}
    assert set(specs) == {"search_sites", "sites_near", "geocode_place"}
    assert all(s["input_schema"]["additionalProperties"] is False and s["description"] for s in specs.values())
    assert specs["sites_near"]["input_schema"]["required"] == ["latitude", "longitude"]


def test_claude_message_conversion():
    convert = ClaudeProvider._convert
    assert convert({"role": "user", "text": "hi"}) == {"role": "user", "content": "hi"}
    assistant = convert({"role": "assistant", "text": "", "calls": [Call("c1", "geocode_place", {"name": "Hudson"})]})
    assert assistant["content"] == [{"type": "tool_use", "id": "c1", "name": "geocode_place", "input": {"name": "Hudson"}}]
    tool = convert({"role": "tool", "results": [{"id": "c1", "content": "{}"}]})
    assert tool == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}]}


def test_eval_scoring_passes_correct_calls_and_fails_wrong_ones():
    question = {"id": "q", "question": "Sites over 300 acres in Weld County",
                "calls": [{"tool": "search_sites", "args": {"min_acres": 300, "counties": ["Weld"]}}]}
    expected = evals.expected_sites(STORE, question["calls"])
    assert expected == ["W-001", "W-003", "W-006"]

    def answer(args):
        provider = ScriptedProvider([Turn(calls=[Call("c1", "search_sites", args)]), Turn("done")])
        return evals.score(question, agent.ask(question["question"], STORE, provider), expected)

    good = answer({"min_acres": 300.0, "counties": ["weld"], "limit": 50})
    assert good["calls"] and good["sites"]
    missing_filter = answer({"min_acres": 300})
    assert not missing_filter["calls"] and not missing_filter["sites"]
    extra_filter = answer({"min_acres": 300, "counties": ["Weld"], "max_power_miles": 1})
    assert extra_filter["calls"] and not extra_filter["sites"]


def test_frozen_expectations_match_the_fixture():
    questions = json.loads(evals.QUESTIONS.read_text())
    frozen = json.loads(evals.EXPECTED.read_text())
    assert {q["id"]: sorted(q["sites"]) if "sites" in q else evals.expected_sites(STORE, q["calls"])
            for q in questions} == frozen
    core = [q for q in questions if q.get("set", "core") == "core"]
    assert len(core) == 30 and [sum(q.get("set") == name for q in questions) for name in ("hard", "fresh")] == [23, 12]
    empty = {q["id"] for q in core if not frozen[q["id"]]}
    refusals = {q["id"] for q in core if not q["calls"]}
    assert len(refusals) == 5 and empty - refusals == {"unknown-place", "near-greeley-empty", "no-match"}


def test_near_accepts_the_shared_filters():
    hudson = STORE.geocode("Hudson")[0]
    args = {"latitude": hudson["latitude"], "longitude": hudson["longitude"], "radius_miles": 12}
    everything, _ = tools.call(STORE, "sites_near", args)
    large, _ = tools.call(STORE, "sites_near", {**args, "min_acres": 300})
    assert everything["count"] == 6 and [s["site_id"] for s in large["sites"]] == ["W-001", "W-003"]
    powered, _ = tools.call(STORE, "sites_near", {**args, "max_power_miles": 1, "exclude_counties": ["adams"]})
    assert [s["site_id"] for s in powered["sites"]] == ["W-001"]


def test_exclusion_and_minimum_distance():
    other, _ = tools.call(STORE, "search_sites", {"min_acres": 200.5, "exclude_counties": ["Weld"]})
    assert sorted(s["site_id"] for s in other["sites"]) == ["A-001", "A-002", "M-001", "M-002"]
    far, _ = tools.call(STORE, "search_sites", {"min_highway_miles": 2.5})
    assert [s["site_id"] for s in far["sites"]] == ["W-005"]
    above, _ = tools.call(STORE, "search_sites", {"min_acres": 400.01})
    assert "M-002" not in [s["site_id"] for s in above["sites"]] and above["count"] == 4


def test_geocode_tolerates_spelling_and_abbreviation():
    assert [(p["name"], p["match"]) for p in STORE.geocode("keenesburg")] == [("Keenesburg", "exact")]
    assert [(p["name"], p["match"]) for p in STORE.geocode("Keensburg")] == [("Keenesburg", "close")]
    assert [(p["name"], p["match"]) for p in STORE.geocode("Ft. Morgan")] == [("Fort Morgan", "exact")]
    assert STORE.geocode("Springfield") == [] and STORE.geocode("Fort") == []
    assert evals.same({"any_of": ["Ft. Morgan", "Fort Morgan"]}, "fort morgan")
    assert not evals.same({"any_of": ["Ft. Morgan", "Fort Morgan"]}, "Morgan")


def test_near_any_of_several_points():
    hudson, keenesburg = STORE.geocode("Hudson")[0], STORE.geocode("Keenesburg")[0]
    args = {"latitude": hudson["latitude"], "longitude": hudson["longitude"], "radius_miles": 5}
    one, _ = tools.call(STORE, "sites_near", args)
    both, features = tools.call(STORE, "sites_near", {**args, "more_points": [
        {"latitude": keenesburg["latitude"], "longitude": keenesburg["longitude"]}]})
    assert sorted(s["site_id"] for s in one["sites"]) == ["W-001", "W-002"]
    assert sorted(s["site_id"] for s in both["sites"]) == ["W-001", "W-002", "W-003", "W-004"]
    assert all(s["distance_miles"] <= 5 for s in both["sites"]) and len(features) == 4
    too_many, _ = tools.call(STORE, "sites_near", {**args, "more_points": [{"latitude": 40, "longitude": -104}] * 5})
    assert "Invalid input" in too_many["error"]


def test_geocode_ignores_a_state_qualifier():
    assert [(p["name"], p["match"]) for p in STORE.geocode("Hudsun, Colorado")] == [("Hudson", "close")]
    assert [(p["name"], p["match"]) for p in STORE.geocode("Fort Morgan, CO")] == [("Fort Morgan", "exact")]
    assert STORE.geocode("Springfield, Colorado") == []


def test_approximate_inputs_and_report():
    assert evals.same({"approx": 1.05, "tolerance": 0.05}, 1) and evals.same({"approx": 1.05, "tolerance": 0.05}, 1.1)
    assert not evals.same({"approx": 1.05, "tolerance": 0.05}, 0.99)
    assert evals.same({"approx": 1.243, "tolerance": 0.1}, 1.2)
    assert not evals.same({"approx": 1.243, "tolerance": 0.1}, 2)
    assert not evals.same({"approx": 1.243, "tolerance": 0.1}, "1.2")
    rows = [{"id": "a", "set": "core", "calls": True, "sites": True, "complete": True, "made": [], "shown": [],
             "passes": 1, "trials": 1},
            {"id": "b", "set": "hard", "calls": False, "sites": True, "complete": True,
             "made": [{"tool": "search_sites", "args": {}}], "shown": ["W-001"], "passes": 0, "trials": 1}]
    text = evals.report(rows, "claude")
    assert "claude core: 1 of 1 passed, 100%" in text and "claude hard: 0 of 1 passed, 0%" in text
    assert 'made:  [{"tool": "search_sites", "args": {}}]' in text


def test_trials_report_flaky_questions():
    question = {"id": "q", "question": "Sites over 300 acres in Weld County",
                "calls": [{"tool": "search_sites", "args": {"min_acres": 300, "counties": ["Weld"]}}]}
    right = [Turn(calls=[Call("c1", "search_sites", {"min_acres": 300, "counties": ["Weld"]})]), Turn("done")]
    wrong = [Turn(calls=[Call("c1", "search_sites", {"min_acres": 300})]), Turn("done")]
    rows = evals.run(ScriptedProvider(right + wrong + right), STORE, [question], {"q": ["W-001", "W-003", "W-006"]}, trials=3)
    assert rows[0]["passes"] == 2 and rows[0]["trials"] == 3 and not rows[0]["sites"]  # shows the failing trial
    text = evals.report(rows, "claude")
    assert text.startswith("FLAKY 2/3") and "0 of 1 passed, 0% (threshold 85%), 1 flaky" in text


def test_openai_message_conversion():
    convert = OpenAIProvider._convert
    assert convert({"role": "user", "text": "hi"}) == [{"role": "user", "content": "hi"}]
    assistant = convert({"role": "assistant", "text": "", "calls": [Call("c1", "geocode_place", {"name": "Hudson"})]})
    assert assistant == [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "geocode_place", "arguments": '{"name": "Hudson"}'}}]}]
    results = convert({"role": "tool", "results": [{"id": "c1", "content": "{}"}, {"id": "c2", "content": "[]"}]})
    assert results == [{"role": "tool", "tool_call_id": "c1", "content": "{}"},
                       {"role": "tool", "tool_call_id": "c2", "content": "[]"}]
    spec = OpenAIProvider.tools(tools.specs())[0]
    assert spec["type"] == "function" and spec["function"]["parameters"]["additionalProperties"] is False


def test_openai_unreadable_arguments_are_rejected_by_the_tool():
    assert OpenAIProvider._arguments('{"name": "Hudson"}') == {"name": "Hudson"}
    for raw in ("{not json", "[1, 2]", None):
        result, _ = tools.call(STORE, "geocode_place", OpenAIProvider._arguments(raw))
        assert "Invalid input" in result["error"]
