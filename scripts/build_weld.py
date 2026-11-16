"""Build data/weld.json: real Weld County parcels with distances to transmission and highways.

Downloads three public layers, keeps parcels of at least --min-acres, measures each
parcel's distance to the nearest transmission line and highway, and writes a file in
the same shape as data/fixture.json. Owner names and addresses are not kept.

Sources:
- Parcels: Colorado Public Parcel Composite (Colorado OIT, from county data)
- Transmission: HIFLD U.S. Transmission Lines
- Highways: CDOT Highways in Colorado (Colorado Information Marketplace)

Usage:
    pip install -r requirements-data.txt
    python scripts/build_weld.py
    MAP_AGENT_DATA=data/weld.json uvicorn mapagent.api:app --reload
"""
import argparse
import json
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
WELD_BOUNDS = (-105.06, 39.90, -103.55, 41.01)  # west, south, east, north, padded slightly
PROJECTED = "EPSG:26913"  # UTM zone 13N, metres
ACRE_M2 = 4046.8564224
MILE_M = 1609.344
PAGE = 1000


def fetch_json(url, params=None, attempts=3):
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
            time.sleep(2 * attempt)


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
    lines = frame(arcgis_features(TRANSMISSION, "transmission segments", where="1=1", outFields="OBJECTID", **envelope),
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

    places = json.loads((ROOT / "data/fixture.json").read_text())["places"]
    output = {"synthetic": False, "built": time.strftime("%Y-%m-%d"), "min_acres": options.min_acres,
              "sources": {"parcels": PARCELS, "transmission": TRANSMISSION, "highways": HIGHWAYS},
              "sites": sites, "places": places}
    options.output.write_text(json.dumps(output, separators=(",", ":")) + "\n")
    print(f"wrote {len(sites):,} parcels to {options.output} "
          f"({options.output.stat().st_size / 1e6:.1f} MB; {len(lines):,} transmission segments, "
          f"{len(highways):,} highway segments)")


if __name__ == "__main__":
    main()
