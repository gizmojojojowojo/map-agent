"""Build data/weld.json: real Weld County parcels with distances to transmission and highways.

Downloads three public layers, keeps parcels of at least --min-acres, measures each
parcel's distance to the nearest transmission line and highway, and writes a file in
the same shape as data/fixture.json. Owner names and addresses are not kept.

Sources:
- Parcels: Colorado Public Parcel Composite (Colorado OIT, from county data)
- Transmission: HIFLD U.S. Transmission Lines
- Highways: CDOT Highways in Colorado (Colorado Information Marketplace)
- Places: U.S. Census Gazetteer, every Colorado city, town and census-designated place,
  with its county from Census TIGERweb county boundaries

Usage:
    pip install -r requirements-data.txt
    python scripts/build_weld.py
    MAP_AGENT_DATA=data/weld.json uvicorn mapagent.api:app --reload
"""
import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
PARCELS = "https://gis.colorado.gov/public/rest/services/Address_and_Parcel/Colorado_Public_Parcels/FeatureServer/0"
TRANSMISSION = ("https://services3.arcgis.com/fwwoCWVtaahwlvxO/arcgis/rest/services/"
                "HIFLD_US_Transmission_Lines_with_ICF/FeatureServer/0")
HIGHWAYS = "https://data.colorado.gov/resource/phvc-rwei.geojson?$limit=50000"
PLACES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_place_08.txt"
COUNTIES = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1"
PLACE_KINDS = {"city and county": "city", "city": "city", "town": "town", "CDP": "community"}
WELD_BOUNDS = (-105.06, 39.90, -103.55, 41.01)  # west, south, east, north, padded slightly
PROJECTED = "EPSG:26913"  # UTM zone 13N, metres
ACRE_M2 = 4046.8564224
MILE_M = 1609.344
PAGE = 1000


def fetch_json(url, params=None, attempts=5):
    """GET JSON, retrying for about two minutes; public GIS servers drop connections now and then."""
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "map-agent"}), timeout=120) as r:
                data = json.load(r)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"Service error: {data['error']}")
            return data
        except Exception as error:  # retry network hiccups, then report which source failed
            if attempt == attempts:
                raise SystemExit(f"Could not read {url}\n  {error}") from None
            wait = (5, 15, 30, 60)[min(attempt, 4) - 1]
            print(f"  retrying in {wait}s after: {error}", file=sys.stderr, flush=True)
            time.sleep(wait)


def arcgis_features(url, label, **query):
    """Page through an ArcGIS feature layer and return GeoJSON features."""
    features, offset = [], 0
    while True:
        page = fetch_json(f"{url}/query", {"f": "geojson", "outSR": 4326, "returnGeometry": "true",
                                           "resultOffset": offset, "resultRecordCount": PAGE, **query})
        batch = page.get("features", [])
        features += batch
        print(f"  {label}: {len(features):,}", file=sys.stderr, flush=True)
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if not batch or not more:
            return features
        offset += len(batch)


def colorado_places():
    """Every Colorado city, town and census-designated place, with its county."""
    with urllib.request.urlopen(urllib.request.Request(PLACES, headers={"User-Agent": "map-agent"}), timeout=120) as r:
        rows = [{key.strip(): value for key, value in row.items()}  # older files pad the last header
                for row in csv.DictReader(io.StringIO(r.read().decode("utf-8")), delimiter="|")]
    pattern = re.compile(r"^(.*) (%s)$" % "|".join(PLACE_KINDS))
    places = []
    for row in rows:
        match = pattern.match(row["NAME"].strip())
        if not match:
            raise SystemExit(f"Unexpected place name in the Census Gazetteer: {row['NAME']!r}")
        places.append({"name": match[1], "kind": PLACE_KINDS[match[2]],
                       "latitude": float(row["INTPTLAT"]), "longitude": float(row["INTPTLONG"].strip())})
    points = gpd.GeoDataFrame(places, geometry=gpd.points_from_xy([p["longitude"] for p in places],
                                                                  [p["latitude"] for p in places]), crs="EPSG:4326")
    counties = frame(arcgis_features(COUNTIES, "counties", where="STATE='08'", outFields="NAME"), "counties")
    joined = gpd.sjoin(points, counties[["NAME", "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated()]
    if joined["NAME"].isna().any():
        raise SystemExit(f"{joined['NAME'].isna().sum()} places fell outside every county; check the county source.")
    for place, county in zip(places, joined["NAME"]):
        place["county"] = county.removesuffix(" County")
    return places


def frame(features, label):
    if not features:
        raise SystemExit(f"No {label} came back. The source may have changed; check its URL in this script.")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def nearest_miles(sites, lines, column):
    joined = gpd.sjoin_nearest(sites[["site_id", "geometry"]], lines[["geometry"]], distance_col=column)
    return joined.groupby("site_id")[column].min() / MILE_M


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-acres", type=float, default=80)
    parser.add_argument("--output", type=Path, default=ROOT / "data/weld.json")
    options = parser.parse_args()
    west, south, east, north = WELD_BOUNDS
    envelope = {"geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope",
                "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"}

    print("Downloading parcels", file=sys.stderr)
    parcels = frame(arcgis_features(PARCELS, "parcels", where=f"countyName = 'Weld' AND landAcres >= {options.min_acres * 0.8:g}",
                                    outFields="parcel_id,landAcres"), "parcels")
    print("Downloading transmission lines", file=sys.stderr)
    lines = frame(arcgis_features(TRANSMISSION, "transmission segments", where="1=1", outFields="ID,VOLTAGE", **envelope),
                  "transmission lines")
    print("Downloading highways", file=sys.stderr)
    highways = frame(fetch_json(HIGHWAYS).get("features", []), "highways").cx[west:east, south:north]

    parcels, lines, highways = (layer.to_crs(PROJECTED) for layer in (parcels, lines, highways))
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty].copy()
    parcels["geometry"] = parcels.geometry.make_valid()
    parcels = parcels[parcels.geom_type.isin(["Polygon", "MultiPolygon"])]
    parcels["acres"] = parcels.geometry.area / ACRE_M2  # stated acreage varies; measure it
    parcels = parcels[parcels["acres"] >= options.min_acres]
    parcels["site_id"] = parcels["parcel_id"].astype(str).str.strip()
    parcels = parcels[parcels["site_id"] != ""].drop_duplicates("site_id")
    parcels["power_miles"] = parcels["site_id"].map(nearest_miles(parcels, lines, "d"))
    parcels["highway_miles"] = parcels["site_id"].map(nearest_miles(parcels, highways, "d"))

    centres = parcels.geometry.representative_point().to_crs(4326)
    parcels["geometry"] = parcels.geometry.simplify(5)
    shapes = parcels.to_crs(4326)
    sites = [{"site_id": row.site_id, "county": "Weld", "acres": round(row.acres, 1),
              "power_miles": round(row.power_miles, 2), "highway_miles": round(row.highway_miles, 2),
              "latitude": round(centre.y, 6), "longitude": round(centre.x, 6),
              "geometry": mapping(row.geometry)}
             for row, centre in zip(shapes.itertuples(), centres)]

    print("Downloading places", file=sys.stderr)
    places = colorado_places()
    output = {"synthetic": False, "built": time.strftime("%Y-%m-%d"), "min_acres": options.min_acres,
              "sources": {"parcels": PARCELS, "transmission": TRANSMISSION, "highways": HIGHWAYS,
                          "places": PLACES, "counties": COUNTIES},
              "sites": sites, "places": places}
    options.output.write_text(json.dumps(output, separators=(",", ":")) + "\n")
    print(f"wrote {len(sites):,} parcels to {options.output} "
          f"({options.output.stat().st_size / 1e6:.1f} MB; {len(lines):,} transmission segments, "
          f"{len(highways):,} highway segments, {len(places):,} places)")


if __name__ == "__main__":
    main()
