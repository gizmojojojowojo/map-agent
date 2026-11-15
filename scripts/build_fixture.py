"""Write data/fixture.json: synthetic square parcels near real Colorado towns.

The parcels are invented for tests and evals. Town coordinates are approximate.
"""
import json
import math
from pathlib import Path

SITES = [  # id, county, lat, lon, acres, power_miles, highway_miles
    ("W-001", "Weld", 40.09, -104.62, 320, 0.8, 1.2),
    ("W-002", "Weld", 40.05, -104.66, 85, 2.5, 0.4),
    ("W-003", "Weld", 40.12, -104.50, 640, 1.5, 0.9),
    ("W-004", "Weld", 40.10, -104.54, 210, 4.2, 2.0),
    ("W-005", "Weld", 40.40, -104.65, 150, 0.5, 3.1),
    ("W-006", "Weld", 40.22, -104.80, 480, 3.0, 0.7),
    ("W-007", "Weld", 40.08, -104.79, 60, 1.1, 0.3),
    ("A-001", "Adams", 39.97, -104.78, 240, 0.9, 1.4),
    ("A-002", "Adams", 39.76, -104.43, 900, 2.2, 0.6),
    ("A-003", "Adams", 39.75, -104.60, 130, 0.4, 0.2),
    ("M-001", "Morgan", 40.26, -103.78, 1200, 1.8, 1.0),
    ("M-002", "Morgan", 40.23, -104.07, 400, 5.5, 0.5),
]
PLACES = [
    ("Hudson", 40.0736, -104.6430), ("Keenesburg", 40.1083, -104.5197),
    ("Greeley", 40.4233, -104.7091), ("Fort Lupton", 40.0847, -104.8130),
    ("Platteville", 40.2150, -104.8227), ("Brighton", 39.9853, -104.8205),
    ("Bennett", 39.7589, -104.4275), ("Watkins", 39.7453, -104.6075),
    ("Fort Morgan", 40.2503, -103.8000), ("Wiggins", 40.2306, -104.0727),
]


def square(lat, lon, acres):
    half = math.sqrt(acres * 4046.8564224) / 2
    dlat = half / 111320
    dlon = half / (111320 * math.cos(math.radians(lat)))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat],
            [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]
    return {"type": "Polygon", "coordinates": [[[round(x, 6), round(y, 6)] for x, y in ring]]}


sites = [{"site_id": i, "county": c, "acres": a, "power_miles": p, "highway_miles": h,
          "latitude": lat, "longitude": lon, "geometry": square(lat, lon, a)}
         for i, c, lat, lon, a, p, h in SITES]
places = [{"name": n, "kind": "town", "latitude": lat, "longitude": lon} for n, lat, lon in PLACES]
out = Path(__file__).resolve().parents[1] / "data/fixture.json"
out.write_text(json.dumps({"synthetic": True, "sites": sites, "places": places}, indent=1) + "\n")
print(f"wrote {len(sites)} sites and {len(places)} places to {out}")
