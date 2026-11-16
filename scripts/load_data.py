"""Load a data file (data/fixture.json by default) into PostGIS. Run the files in sql/ in order first.

Usage:
    MAP_AGENT_DATABASE_URL=postgresql://... python scripts/load_data.py
    MAP_AGENT_DATABASE_URL=postgresql://... python scripts/load_data.py data/weld.json

Replaces everything in map_agent.sites and map_agent.places.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mapagent.store import normalize_place  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("path", nargs="?", type=Path, default=ROOT / "data/fixture.json")
data = json.loads(parser.parse_args().path.read_text())

sites = [(site["site_id"], json.dumps({k: v for k, v in site.items() if k != "geometry"}), json.dumps(site["geometry"]))
         for site in data["sites"]]
places = [(p["name"], p["kind"], p.get("county", ""), normalize_place(p["name"]), p["longitude"], p["latitude"])
          for p in data["places"]]

with psycopg.connect(os.environ["MAP_AGENT_DATABASE_URL"]) as connection, connection.cursor() as cursor:
    cursor.execute("TRUNCATE map_agent.sites, map_agent.places")
    # Simplifying shapes for the map can leave small self-intersections; repair them and keep the polygon part.
    cursor.executemany(
        "INSERT INTO map_agent.sites (site_id, record, geom) VALUES (%s, %s, "
        "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)))", sites)
    cursor.executemany(
        "INSERT INTO map_agent.places (name, kind, county, search_name, geom) "
        "VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))", places)
    cursor.execute("ANALYZE map_agent.sites")
print(f"loaded {len(sites):,} sites and {len(places):,} places")
