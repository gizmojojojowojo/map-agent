"""Tool inputs. The model fills these in; Pydantic rejects anything out of bounds."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Miles = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Acres = Annotated[float, Field(ge=0, le=2_000_000, allow_inf_nan=False)]
Limit = Annotated[int, Field(ge=1, le=100)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchSites(Strict):
    """Filter sites by size, county and distance to infrastructure."""
    min_acres: Acres = 0
    max_acres: Acres | None = None
    max_power_miles: Miles | None = Field(None, description="Maximum miles to the nearest mapped transmission line.")
    max_highway_miles: Miles | None = Field(None, description="Maximum miles to the nearest highway.")
    counties: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=20, description="County names without the word County. Empty means all.")
    sort_by: Literal["acres", "power", "highway"] = Field(
        "acres", description="acres sorts largest first; power and highway sort nearest first.")
    limit: Limit = 20


class SitesNear(Strict):
    """Find sites within a radius of a point. Use geocode_place first for a place name."""
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    radius_miles: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] = 5
    limit: Limit = 20


class GeocodePlace(Strict):
    """Look up the coordinates of a town or other named place."""
    name: Annotated[str, Field(min_length=1, max_length=80)]
