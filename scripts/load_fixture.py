"""Load data/fixture.json into PostGIS. Run sql/001_schema.sql first.

Usage: MAP_AGENT_DATABASE_URL=postgresql://... python scripts/load_fixture.py
"""
import json
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mapagent.store import normalize_place  # noqa: E402

data = json.loads((Path(__file__).resolve().parents[1] / "data/fixture.json").read_text())
with psycopg.connect(os.environ["MAP_AGENT_DATABASE_URL"]) as connection:
    connection.execute("TRUNCATE map_agent.sites, map_agent.places")
    for site in data["sites"]:
        record = {k: v for k, v in site.items() if k != "geometry"}
        connection.execute(
            "INSERT INTO map_agent.sites (site_id, record, geom) "
            "VALUES (%s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))",
            (site["site_id"], json.dumps(record), json.dumps(site["geometry"])))
    for place in data["places"]:
        connection.execute(
            "INSERT INTO map_agent.places (name, kind, search_name, geom) "
            "VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (place["name"], place["kind"], normalize_place(place["name"]), place["longitude"], place["latitude"]))
print(f"loaded {len(data['sites'])} sites and {len(data['places'])} places")
