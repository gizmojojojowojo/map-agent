"""Data access behind one small interface, so the agent never sees SQL.

MemoryStore runs on the synthetic fixture with no database; tests and evals use it.
PostgisStore runs the same three queries against PostGIS.
"""
import json
import math
from pathlib import Path
from typing import Protocol

from .models import SearchSites, SitesNear

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixture.json"
SORTS = {"acres": ("acres", True), "power": ("power_miles", False), "highway": ("highway_miles", False)}
METERS_PER_MILE = 1609.344


class Store(Protocol):
    def search(self, criteria: SearchSites) -> list[dict]: ...
    def near(self, request: SitesNear) -> list[dict]: ...
    def geocode(self, name: str) -> list[dict]: ...


def miles_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3958.7613 * math.asin(math.sqrt(a))


class MemoryStore:
    """Fixture-backed store. Distances are measured to the site's centre point."""

    def __init__(self, path=FIXTURE):
        data = json.loads(Path(path).read_text())
        self.sites, self.places = data["sites"], data["places"]

    def search(self, criteria):
        counties = {c.lower() for c in criteria.counties}
        rows = [s for s in self.sites
                if s["acres"] >= criteria.min_acres
                and (criteria.max_acres is None or s["acres"] <= criteria.max_acres)
                and (criteria.max_power_miles is None or s["power_miles"] <= criteria.max_power_miles)
                and (criteria.max_highway_miles is None or s["highway_miles"] <= criteria.max_highway_miles)
                and (not counties or s["county"].lower() in counties)]
        key, descending = SORTS[criteria.sort_by]
        rows.sort(key=lambda s: (-s[key] if descending else s[key], s["site_id"]))
        return rows[:criteria.limit]

    def near(self, request):
        rows = []
        for s in self.sites:
            d = miles_between(request.latitude, request.longitude, s["latitude"], s["longitude"])
            if d <= request.radius_miles:
                rows.append({**s, "distance_miles": round(d, 2)})
        rows.sort(key=lambda s: (s["distance_miles"], s["site_id"]))
        return rows[:request.limit]

    def geocode(self, name):
        wanted = name.strip().lower()
        return [p for p in self.places if p["name"].lower() == wanted]


class PostgisStore:
    """PostGIS-backed store for the schema in sql/001_schema.sql.

    Distances are measured to the site polygon, so results near a radius edge can
    differ slightly from MemoryStore.
    """
    COLUMNS = ("site_id, county, acres, power_miles, highway_miles, "
               "ST_Y(ST_PointOnSurface(geom)) AS latitude, ST_X(ST_PointOnSurface(geom)) AS longitude, "
               "ST_AsGeoJSON(geom, 6)::jsonb AS geometry")
    ORDER = {"acres": "acres DESC", "power": "power_miles ASC", "highway": "highway_miles ASC"}

    def __init__(self, database_url):
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        self.connection.execute("SET default_transaction_read_only = on")

    def search(self, criteria):
        where, params = ["acres >= %s"], [criteria.min_acres]
        for column, value in (("acres", criteria.max_acres), ("power_miles", criteria.max_power_miles),
                              ("highway_miles", criteria.max_highway_miles)):
            if value is not None:
                where.append(f"{column} <= %s")
                params.append(value)
        if criteria.counties:
            where.append("lower(county) = ANY(%s)")
            params.append([c.lower() for c in criteria.counties])
        query = (f"SELECT {self.COLUMNS} FROM map_agent.sites WHERE {' AND '.join(where)} "
                 f"ORDER BY {self.ORDER[criteria.sort_by]}, site_id LIMIT %s")
        return self.connection.execute(query, [*params, criteria.limit]).fetchall()

    def near(self, request):
        point = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
        query = (f"SELECT {self.COLUMNS}, round((ST_Distance(geom::geography, {point}) / {METERS_PER_MILE})::numeric, 2)::float "
                 f"AS distance_miles FROM map_agent.sites WHERE ST_DWithin(geom::geography, {point}, %s) "
                 "ORDER BY distance_miles, site_id LIMIT %s")
        lonlat = (request.longitude, request.latitude)
        return self.connection.execute(
            query, [*lonlat, *lonlat, request.radius_miles * METERS_PER_MILE, request.limit]).fetchall()

    def geocode(self, name):
        return self.connection.execute(
            "SELECT name, kind, ST_Y(geom) AS latitude, ST_X(geom) AS longitude "
            "FROM map_agent.places WHERE lower(name) = lower(%s) ORDER BY name LIMIT 5", [name.strip()]).fetchall()
