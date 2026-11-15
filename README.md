# Map agent

Ask a question about land sites in plain language and see the answer on a map.

"Show me sites over 300 acres in Weld County" becomes a validated database query. The matching parcels are drawn on a Leaflet map with a short written answer.

## How it works

1. The question goes to a language model along with three tool definitions.
2. The model picks a tool and fills in its inputs. It never writes SQL.
3. Pydantic checks the inputs. A bad input goes back to the model as an error so it can correct itself.
4. The tool runs a fixed, read-only query and returns the matching sites.
5. The model writes a short answer. The response also carries the GeoJSON for the map and a trace of every tool call.

| Tool | What it does |
|---|---|
| `search_sites` | Filters sites by acreage, county, and distance to transmission lines and highways |
| `sites_near` | Finds sites within a radius of a point, with the same filters |
| `geocode_place` | Looks up the coordinates of a named town, tolerating misspellings and abbreviations |

The model can chain tools. "What's within 5 miles of Hudson?" calls `geocode_place`, then `sites_near`.

Questions the data cannot answer, such as available power capacity or ownership, get a plain statement that the data does not cover them.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
uvicorn mapagent.api:app --reload
```

Open http://127.0.0.1:8000 and ask a question.

With no database configured, the app uses `data/fixture.json`. The fixture holds 12 invented parcels near real Colorado towns, for demos and tests.

### Use PostGIS

```bash
createdb map_agent && psql map_agent -c "CREATE EXTENSION postgis" -c "CREATE EXTENSION pg_trgm" -f sql/001_schema.sql
export MAP_AGENT_DATABASE_URL=postgresql://localhost/map_agent
python scripts/load_fixture.py
```

## Tests

```bash
python -m pytest
```

The tests use a scripted model and the fixture. They need no database, network, or API key.

## Evals

```bash
python -m evals.run --provider claude
python -m evals.run --provider openai   # needs OPENAI_API_KEY and MAP_AGENT_OPENAI_MODEL
```

`evals/questions.json` holds 53 questions in two sets. A question passes when the agent makes the expected tool calls with the expected inputs and the map shows exactly the expected sites.

**Core (30 questions).** Each maps onto one tool or one two-step chain. Eight test restraint. Five ask for something the data lacks, such as price, zoning, or fiber service. Three have no matching place or site, and the correct answer says so. The run exits with an error when fewer than 85% of core questions pass.

**Hard (23 questions).** These are written to find the agent's limits: unit conversions, misspelled and abbreviated place names, boundary values, vague requests, prompt injection, and requests the tools cannot express yet, such as a radius combined with a size filter. The score is reported without gating the run. Each failure prints the calls the agent made, which shows what to build next.

```bash
python -m evals.run --provider claude --set hard
```

After changing the data, run `python -m evals.run --freeze` to recompute the expected sites.

### Results

| Model | Version | Core | Hard |
|---|---|---|---|
| claude-haiku-5-5 | First version | 30 of 30 | 15 of 23 |
| claude-haiku-5-5 | Second version | 27 of 30 | 21 of 23 |

The eight hard failures in the first version led to four changes: shared filters on `sites_near`, county exclusion, minimum distances, and tolerant place lookup. The system prompt also now tells the agent to ask about vague sizes and distances.

In the second run, all five misses showed the correct sites. The agent passed values such as 300.01 for "over 300 acres", which the size filter's description now recommends, and the scorer expected exactly 300. The expected inputs for "over" and "under" questions now accept either form.

## Project status

The agent loop, tools, eval scoring, and fixture store are covered by tests. The Claude provider has passed a live eval run. The OpenAI provider, HTTP API, PostGIS store, and map page are written and awaiting their first live run.

## Roadmap

- Load open county parcel data in place of the fixture
- Publish eval results for Claude and OpenAI models
- Add tools for point lookup, drawn areas, and context layers
- Add rate limiting before any public deployment
