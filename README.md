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
- Places: the [Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html) list of all 482 Colorado cities, towns, and census-designated places, each tagged with its county from [Census TIGERweb](https://tigerweb.geo.census.gov/) county boundaries

Acreage is measured from each parcel's geometry. Distances run from the parcel edge to the nearest mapped line. The output keeps the parcel ID, acreage, distances, and simplified geometry, and leaves out owner names and addresses. It is not committed to the repository.

Tests and the main evals use the fixture, so their results do not change when the source data does. A separate eval set runs against the real parcels; see [Real Weld data](#real-weld-data-23-questions).

### Use PostGIS

On a Mac with [Postgres.app](https://postgresapp.com), one script starts a private PostGIS server for this project and applies any schema changes in `sql/` it has not seen. It keeps its data in `.pgdata`, which git ignores, listens on a local socket at port 5433, and leaves any other Postgres server alone. It prints the database URL to export.

```bash
eval "$(./scripts/local_postgis.sh)"
python scripts/load_data.py data/weld.json   # or no argument for the fixture
uvicorn mapagent.api:app --reload
```

With another PostGIS install, create the schema yourself and point `MAP_AGENT_DATABASE_URL` at it:

```bash
createdb map_agent && psql map_agent -c "CREATE EXTENSION postgis" -c "CREATE EXTENSION pg_trgm" -f sql/001_schema.sql -f sql/002_place_county.sql
export MAP_AGENT_DATABASE_URL=postgresql://localhost/map_agent
python scripts/load_data.py
```

`load_data.py` replaces whatever the tables hold. It loads the 8,135 real Weld parcels in about a second. The store opens its connection read-only, so the agent cannot change data.

Searches by size and distance return the same sites as the in-memory store. Radius searches can differ near the edge of the radius: PostGIS measures to the parcel boundary, and the in-memory store measures to its centre point.

With the fixture loaded, `python -m pytest` also checks that every expected tool call in the evals returns the same sites from PostGIS as from the in-memory store. Those tests are skipped when no database is set or when it holds other data.

### Live demo

https://map-agent-pgfg.onrender.com runs on Render's free plan. Each deploy builds `data/weld.json` from the live sources, so the demo serves real Weld County parcels from memory. The service sleeps when idle, so the first question after a quiet spell can take about a minute.

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt -r requirements-data.txt && python scripts/build_weld.py` |
| Start command | `uvicorn mapagent.api:app --host 0.0.0.0 --port $PORT` |
| Environment | `MAP_AGENT_DATA=data/weld.json`, `MAP_AGENT_TRUST_PROXY=1`, `MAP_AGENT_DATA_FALLBACK_URL` (the release asset below), `PYTHON_VERSION=3.14.6`, and `ANTHROPIC_API_KEY` set in the Render dashboard |

If a data source is down or has changed, the build uses the last good copy instead: the `weld.json` asset on the [`data-latest` release](https://github.com/gizmojojojowojo/map-agent/releases/tag/data-latest), named by `MAP_AGENT_DATA_FALLBACK_URL`. The build log says when it does. After a good local build, refresh that copy:

```bash
gh release upload data-latest data/weld.json --clobber
```

### Limits for a public deployment

Each question costs a model call, so the API limits how many it answers:

| Setting | Default | Meaning |
|---|---|---|
| `MAP_AGENT_LIMIT_PER_HOUR` | 20 | Questions per visitor in any rolling hour |
| `MAP_AGENT_LIMIT_PER_DAY` | 300 | Questions in total per UTC day, across all visitors |
| `MAP_AGENT_TRUST_PROXY` | unset | Set to `1` behind a proxy such as Render, so visitors are told apart by `X-Forwarded-For` |

A refused question gets a 429 with a plain message and a `Retry-After` header. A question the server fails to answer, such as when the model request fails, is not counted. Counts are kept in memory, so they assume one server process and reset on restart. The daily cap is what bounds spend; a visitor can dodge the hourly limit by faking `X-Forwarded-For`, but not the daily one. Set a monthly spend limit in the Anthropic console as well.

`GET /health` returns `{"ok": true}` once the data store is reachable.

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

### Real Weld data (23 questions)

```bash
eval "$(./scripts/local_postgis.sh)"   # or leave MAP_AGENT_DATABASE_URL unset to use data/weld.json
python -m evals.run --provider claude --data weld --trials 3
```

`evals/weld_questions.json` holds 23 questions written for the real parcels before any run against them. They cover:

- towns that are not in the fixture, such as Eaton, Nunn, Kersey, and Wellington, which is in Larimer County
- spellings that differ from the Census: Ft Lupton, LaSalle, New Raymer, and a misspelled Milliken
- a place name shared by two Colorado towns (Glendale)
- a county with no loaded parcels
- unit conversions: square miles, square kilometres, and feet
- top-N sorts over thousands of parcels
- questions the data cannot answer, such as capacity, ownership, and lookup by parcel ID

Real data is rebuilt from live sources and is not committed, so expected sites are not frozen. The runner computes them at run time by running each question's expected calls on the same store the agent queries. The score measures whether the agent asked the right question of the data, not whether the data is right.

| Model | Store | Run | Weld |
|---|---|---|---|
| claude-haiku-5-5 | PostGIS | First run | 19 of 23 |
| claude-haiku-5-5 | PostGIS | After two fixes | 22 of 23 |

The first run had four misses:

- **Three towns in one question.** The agent was right; the scorer was wrong. It geocoded the first town three times when computing the expected sites. Each lookup now starts from a different town.
- **"Milikin".** The PostGIS geocoder used trigram similarity, which scores Milikin against Milliken at 0.31, under its 0.5 cut-off. The in-memory geocoder found it. The in-memory geocoder in turn missed "New Raymer", which the Census lists as "Raymer (New Raymer)". Both stores now share one matcher in Python, and a name in parentheses counts as an alternate name.
- **Two flaky questions.** For "within 1 km of a power line", one trial added a sort by distance to power, which changes which 20 matching parcels are shown. For "within 1,500 feet", one trial repeated its search. Neither was changed, and both passed every trial on the second run.

On the second run the only miss was flaky: in one of three trials, "Who owns the biggest parcel near Greeley?" looked up Greeley before saying ownership is not in the data. It passed every trial on the first run. After the geocoder change, the fixture sets still pass 30 of 30, 23 of 23, and 12 of 12 on one trial each.

The geocoder fix came from this set, so the next unseen-wording measure needs new questions again.

### Model comparison

All 88 questions (65 fixture, 23 real Weld) on three Claude models, November 2026:

| Model | Trials per question | Trials passed | Questions passed every trial | Mean time per answer | Tokens per answer (in / out) | Cost per 1,000 answers |
|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 3 | 259 of 264 (98%) | 85 of 88 | 2.5s | 7,800 / 340 | $0.95 |
| `claude-sonnet-5-5` | 1 | 88 of 88 (100%) | 88 of 88 | 3.2s | 7,760 / 280 | $18.32 |
| `claude-opus-5-5` | 1 | 88 of 88 (100%) | 88 of 88 | 3.8s | 7,750 / 260 | $36.29 |

Cost uses Anthropic's first-party prices per million tokens: Haiku $0.10 in and $0.50 out, Sonnet $2 and $10, Opus $4 and $20. Time is wall-clock from a laptop, including tool calls. Each model ran with its default effort.

**Haiku stays the default.** It answers at about one nineteenth of Sonnet's cost and one thirty-eighth of Opus's, and it is the fastest. Its five failed trials share one habit: after a unit conversion (kilometres, hectares, feet), it sometimes runs the same search again with a slightly different number, such as 247.11 and then 247.12 acres. The map ends up showing the right sites, but the extra call fails the scorer's exact-calls check. A sixth question type, ownership, occasionally looks up the town before declining. Sonnet and Opus showed neither habit in their single trial each.

At the demo's cap of 300 questions a day, the worst-case model bill is about $0.29 a day on Haiku, $5.50 on Sonnet, or $11 on Opus.

Nearly all input tokens are the system prompt and tool definitions, sent on each of the two or three model calls per answer. Prompt caching could cut that input cost; it is not turned on yet.

Sonnet and Opus ran one trial each. The three-trial runs for them stopped partway when the account ran out of credit, and only complete runs are reported. To rerun the comparison:

```bash
python -m evals.run --model claude-sonnet-5-5 --trials 3 --save evals/results/claude-sonnet-5-5-fixture.json
python -m evals.run --model claude-sonnet-5-5 --data weld --trials 3 --save evals/results/claude-sonnet-5-5-weld.json
python -m evals.compare
```

Saved runs in `evals/results/` are not committed. The real-data rows hold parcel IDs, which are rebuilt with the data.

## Project status

The agent loop, tools, eval scoring, and fixture store are covered by tests. The Claude provider has passed a live eval run. The HTTP API and map page run end to end on the fixture and on real Weld County parcels. The PostGIS store matches the in-memory store on every eval query and answers live questions on the real parcels, where the agent passes 22 of 23 real-data eval questions. The OpenAI provider is written and awaiting its first live run.

With real data, the geocoder knows every Colorado city, town, and census-designated place. When two places share a name, such as the Twin Lakes in Adams and Lake counties, it returns both with their counties, and the agent asks which one is meant.

## Roadmap

- Load parcels for neighbouring counties, so towns near the Weld line return sites on both sides
- Publish eval results for Claude and OpenAI models
- Add tools for point lookup, drawn areas, and context layers
