"""Data access behind one small interface, so the agent never sees SQL.

MemoryStore runs on the synthetic fixture with no database; tests and evals use it.
PostgisStore runs the same three queries against PostGIS.
"""
import difflib
import json
import math
import re
from pathlib import Path
from typing import Protocol

from .models import SearchSites, SiteFilters, SitesNear

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixture.json"
SORTS = {"acres": ("acres", True), "power": ("power_miles", False), "highway": ("highway_miles", False)}
METERS_PER_MILE = 1609.344
ABBREVIATIONS = {"ft": "fort", "mt": "mount", "st": "saint"}
RANGES = (("acres", "min_acres", "max_acres"), ("power_miles", "min_power_miles", "max_power_miles"),
          ("highway_miles", "min_highway_miles", "max_highway_miles"))


class Store(Protocol):
    def search(self, criteria: SearchSites) -> list[dict]: ...
    def near(self, request: SitesNear) -> list[dict]: ...
    def geocode(self, name: str) -> list[dict]: ...


def miles_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3958.7613 * math.asin(math.sqrt(a))


def normalize_place(name):
    """Lower-case, drop punctuation and expand common abbreviations: 'Ft. Morgan, CO' -> 'fort morgan'.

    Anything after a comma is dropped, so a state or county qualifier is ignored.
    """
    words = re.sub(r"[^\w\s]", " ", name.split(",")[0].lower()).split()
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def place_keys(name):
    """Every normalised name a place answers to. 'Raymer (New Raymer)' -> raymer new raymer, raymer, new raymer."""
    keys = {normalize_place(name)}
    alternate = re.match(r"^(.*?)\s*\((.*)\)\s*$", name)
    if alternate:
        keys |= {normalize_place(alternate[1]), normalize_place(alternate[2])}
    return keys - {""}


def match_places(places, name):
    """Exact matches (up to 5), else close matches (up to 3). Both stores use this, so they agree.

    Several places can share a name, such as two Twin Lakes in different counties; all are returned.
    """
    wanted = normalize_place(name)
    places = sorted(places, key=lambda p: (p["name"], p.get("county", "")))
    exact = [p for p in places if wanted in place_keys(p["name"])]
    if exact:
        return [{**p, "match": "exact"} for p in exact[:5]]
    keys = sorted({key for p in places for key in place_keys(p["name"])})
    found = []
    for key in difflib.get_close_matches(wanted, keys, n=3, cutoff=0.8):
        found += [p for p in places if key in place_keys(p["name"]) and p not in found]
    return [{**p, "match": "close"} for p in found[:3]]


def matches(site, filters: SiteFilters):
    for column, low, high in RANGES:
        minimum, maximum = getattr(filters, low), getattr(filters, high)
        if minimum is not None and site[column] < minimum:
            return False
        if maximum is not None and site[column] > maximum:
            return False
    county = site["county"].lower()
    if filters.counties and county not in {c.lower() for c in filters.counties}:
        return False
    return county not in {c.lower() for c in filters.exclude_counties}


class MemoryStore:
    """Fixture-backed store. Distances are measured to the site's centre point."""

    def __init__(self, path=FIXTURE):
        data = json.loads(Path(path).read_text())
        self.sites, self.places = data["sites"], data["places"]

    def search(self, criteria):
        rows = [s for s in self.sites if matches(s, criteria)]
        key, descending = SORTS[criteria.sort_by]
        rows.sort(key=lambda s: (-s[key] if descending else s[key], s["site_id"]))
        return rows[:criteria.limit]

    def near(self, request):
        rows = []
        points = [(request.latitude, request.longitude), *((p.latitude, p.longitude) for p in request.more_points)]
        for s in self.sites:
            d = min(miles_between(lat, lon, s["latitude"], s["longitude"]) for lat, lon in points)
            if d <= request.radius_miles and matches(s, request):
                rows.append({**s, "distance_miles": round(d, 2)})
        rows.sort(key=lambda s: (s["distance_miles"], s["site_id"]))
        return rows[:request.limit]

    def geocode(self, name):
        return match_places(self.places, name)


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

    @staticmethod
    def _where(filters):
        """SQL conditions and parameters for the shared filters. Column names come from RANGES, never from input."""
        where, params = [], []
        for column, low, high in RANGES:
            for operator, value in ((">=", getattr(filters, low)), ("<=", getattr(filters, high))):
                if value is not None:
                    where.append(f"{column} {operator} %s")
                    params.append(value)
        if filters.counties:
            where.append("lower(county) = ANY(%s)")
            params.append([c.lower() for c in filters.counties])
        if filters.exclude_counties:
            where.append("NOT lower(county) = ANY(%s)")
            params.append([c.lower() for c in filters.exclude_counties])
        return where, params

    def search(self, criteria):
        where, params = self._where(criteria)
        query = (f"SELECT {self.COLUMNS} FROM map_agent.sites WHERE {' AND '.join(where) or 'true'} "
                 f"ORDER BY {self.ORDER[criteria.sort_by]}, site_id LIMIT %s")
        return self.connection.execute(query, [*params, criteria.limit]).fetchall()

    def near(self, request):
        point = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
        lonlats = [(request.longitude, request.latitude), *((p.longitude, p.latitude) for p in request.more_points)]
        distances = ", ".join(f"ST_Distance(geom::geography, {point})" for _ in lonlats)
        distance = f"LEAST({distances})" if len(lonlats) > 1 else distances
        within = " OR ".join(f"ST_DWithin(geom::geography, {point}, %s)" for _ in lonlats)
        radius = request.radius_miles * METERS_PER_MILE
        where, params = self._where(request)
        query = (f"SELECT {self.COLUMNS}, round(({distance} / {METERS_PER_MILE})::numeric, 2)::float AS distance_miles "
                 f"FROM map_agent.sites WHERE {' AND '.join([f'({within})', *where])} "
                 "ORDER BY distance_miles, site_id LIMIT %s")
        distance_params = [value for lonlat in lonlats for value in lonlat]
        within_params = [value for lonlat in lonlats for value in (*lonlat, radius)]
        return self.connection.execute(query, [*distance_params, *within_params, *params, request.limit]).fetchall()

    def geocode(self, name):
        """Match in Python, like MemoryStore. There are few places, and one matcher keeps the stores identical."""
        rows = self.connection.execute(
            "SELECT name, kind, county, ST_Y(geom) AS latitude, ST_X(geom) AS longitude FROM map_agent.places").fetchall()
        places = [{k: v for k, v in row.items() if k != "county" or v} for row in rows]  # fixture places have no county
        return match_places(places, name)
