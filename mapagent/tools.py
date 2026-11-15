"""The tools the model may call. Each one validates its input and returns plain data."""
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from .models import GeocodePlace, SearchSites, SitesNear

SITE_FIELDS = ("site_id", "county", "acres", "power_miles", "highway_miles", "distance_miles")


@dataclass(frozen=True)
class Tool:
    name: str
    model: type[BaseModel]
    run: Callable


def _sites(rows):
    """Split store rows into a compact list for the model and full features for the map."""
    summary = [{k: r[k] for k in SITE_FIELDS if k in r} for r in rows]
    features = [{"type": "Feature", "id": r["site_id"], "geometry": r["geometry"],
                 "properties": {k: r[k] for k in SITE_FIELDS if k in r}} for r in rows]
    return {"count": len(rows), "sites": summary}, features


def _search(store, args):
    return _sites(store.search(args))


def _near(store, args):
    return _sites(store.near(args))


def _geocode(store, args):
    matches = store.geocode(args.name)
    return {"count": len(matches), "places": matches}, None


TOOLS = {t.name: t for t in (
    Tool("search_sites", SearchSites, _search),
    Tool("sites_near", SitesNear, _near),
    Tool("geocode_place", GeocodePlace, _geocode),
)}


def specs():
    """Tool definitions in the shape model APIs expect."""
    return [{"name": t.name, "description": t.model.__doc__, "input_schema": t.model.model_json_schema()}
            for t in TOOLS.values()]


def call(store, name, args):
    """Run one tool. Returns (result for the model, map features or None).

    Bad input comes back as an error message so the model can correct itself.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}."}, None
    try:
        validated = tool.model.model_validate(args)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        return {"error": f"Invalid input. {problems}"}, None
    return tool.run(store, validated)
