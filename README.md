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
| `sites_near` | Finds sites within a radius of one or more points, with the same filters |
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

### Use real Weld County parcels

```bash
pip install -r requirements-data.txt
python scripts/build_weld.py
MAP_AGENT_DATA=data/weld.json uvicorn mapagent.api:app --reload
```

The build script downloads three public layers and keeps Weld County parcels of 80 acres or more:

- Parcels: [Colorado Public Parcel Composite](https://gis.colorado.gov/public/rest/services/Address_and_Parcel/Colorado_Public_Parcels/FeatureServer/0), maintained by the Colorado Office of Information Technology from county data
- Transmission lines: [HIFLD U.S. Transmission Lines](https://services3.arcgis.com/fwwoCWVtaahwlvxO/arcgis/rest/services/HIFLD_US_Transmission_Lines_with_ICF/FeatureServer/0)
- Highways: [CDOT Highways in Colorado](https://data.colorado.gov/Transportation/Highways-in-Colorado/phvc-rwei)

Acreage is measured from each parcel's geometry. Distances run from the parcel edge to the nearest mapped line. The output keeps the parcel ID, acreage, distances, and simplified geometry, and leaves out owner names and addresses. It is not committed to the repository.

Evals and tests always use the fixture, so their results do not change when the source data does.

### Use PostGIS

On a Mac with [Postgres.app](https://postgresapp.com), one script starts a private PostGIS server for this project. It keeps its data in `.pgdata`, which git ignores, listens on a local socket at port 5433, and leaves any other Postgres server alone. It prints the database URL to export.

```bash
eval "$(./scripts/local_postgis.sh)"
python scripts/load_data.py data/weld.json   # or no argument for the fixture
uvicorn mapagent.api:app --reload
```

With another PostGIS install, create the schema yourself and point `MAP_AGENT_DATABASE_URL` at it:

```bash
createdb map_agent && psql map_agent -c "CREATE EXTENSION postgis" -c "CREATE EXTENSION pg_trgm" -f sql/001_schema.sql
export MAP_AGENT_DATABASE_URL=postgresql://localhost/map_agent
python scripts/load_data.py
```

`load_data.py` replaces whatever the tables hold. It loads the 8,135 real Weld parcels in about a second. The store opens its connection read-only, so the agent cannot change data.

Searches by size and distance return the same sites as the in-memory store. Radius searches can differ near the edge of the radius: PostGIS measures to the parcel boundary, and the in-memory store measures to its centre point.

With the fixture loaded, `python -m pytest` also checks that every expected tool call in the evals returns the same sites from PostGIS as from the in-memory store. Those tests are skipped when no database is set or when it holds other data.

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

`evals/questions.json` holds 65 questions in three sets. A question passes when the agent makes the expected tool calls with the expected inputs and the map shows exactly the expected sites.

**Core (30 questions).** Each maps onto one tool or one two-step chain. Eight test restraint. Five ask for something the data lacks, such as price, zoning, or fiber service. Three have no matching place or site, and the correct answer says so. The run exits with an error when fewer than 85% of core questions pass.

**Hard (23 questions).** These are written to find the agent's limits: unit conversions, misspelled and abbreviated place names, boundary values, vague requests, prompt injection, and requests the tools cannot express yet, such as a radius combined with a size filter. The score is reported without gating the run. Each failure prints the calls the agent made, which shows what to build next.

**Fresh (12 questions).** The hard set guided the fixes, so a high score there mostly confirms those fixes. The fresh set uses wording and combinations that no fix was written against. It measures how well the agent handles requests it has not been tuned on. It is also reported without gating the run.

```bash
python -m evals.run --provider claude --set fresh --trials 3
```

Models give different answers to the same question from run to run. `--trials 3` asks each question three times, counts a question as passed only when every trial passes, and labels the rest `FLAKY` or `FAIL`.

After changing the data, run `python -m evals.run --freeze` to recompute the expected sites.

### Results

| Model | Version | Core | Hard | Fresh |
|---|---|---|---|---|
| claude-haiku-5-5 | First version | 30 of 30 | 15 of 23 | |
| claude-haiku-5-5 | Second version | 27 of 30 | 21 of 23 | |
| claude-haiku-5-5 | Third version | 30 of 30 | 23 of 23 | 7 of 12 |
| claude-haiku-5-5 | Fourth version | 30 of 30 | 23 of 23 | 12 of 12 |

The eight hard failures in the first version led to four changes: shared filters on `sites_near`, county exclusion, minimum distances, and tolerant place lookup. The system prompt also now tells the agent to ask about vague sizes and distances.

In the second run, all five misses showed the correct sites. The agent passed values such as 300.01 for "over 300 acres", which the size filter's description now recommends, and the scorer expected exactly 300. The expected inputs for "over" and "under" questions now accept either form, which the third run confirms.

Scores through the third version come from a single trial per question, except the fresh score, which uses three. The fourth version uses three trials throughout, and every question passed all three.

The fresh set scored 7 of 12 on its first run, against 100% on the two sets the agent had been tuned on. The five misses had five causes: exclusive wording on distances, a result limit the agent set on its own, a place name with a state attached, a request for sites near either of two places, and a rounding error in the scorer. Each is addressed in the fourth version.

All three sets have now guided fixes, so these scores confirm the fixes and say little about unseen wording. The next measure of that needs new questions written before any further tuning.

## Project status

The agent loop, tools, eval scoring, and fixture store are covered by tests. The Claude provider has passed a live eval run. The HTTP API and map page run end to end on the fixture and on real Weld County parcels. The PostGIS store matches the in-memory store on every eval query and answers live questions on the real parcels. The OpenAI provider is written and awaiting its first live run.

Only the 10 towns in the fixture can be geocoded, so questions about other Weld towns, such as Eaton, get a request for clarification.

## Roadmap

- Load Colorado towns into the geocoder
- Write fresh eval questions against the real Weld parcels
- Publish eval results for Claude and OpenAI models
- Add tools for point lookup, drawn areas, and context layers
- Add rate limiting before any public deployment
