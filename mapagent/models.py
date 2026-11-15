"""Tool inputs. The model fills these in; Pydantic rejects anything out of bounds."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Miles = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Acres = Annotated[float, Field(ge=0, le=2_000_000, allow_inf_nan=False)]
Limit = Annotated[int, Field(ge=1, le=100)]
Counties = Annotated[list[Annotated[str, Field(min_length=1, max_length=40)]], Field(max_length=20)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteFilters(Strict):
    """Filters shared by every tool that returns sites. All bounds are inclusive."""
    min_acres: Acres = Field(0, description="Smallest size, inclusive. For 'more than X', pass a value just above X.")
    max_acres: Acres | None = Field(None, description="Largest size, inclusive. For 'less than X', pass a value just below X.")
    min_power_miles: Miles | None = Field(None, description="Minimum miles to the nearest mapped transmission line, inclusive.")
    max_power_miles: Miles | None = Field(None, description="Maximum miles to the nearest mapped transmission line, inclusive.")
    min_highway_miles: Miles | None = Field(None, description="Minimum miles to the nearest highway, inclusive.")
    max_highway_miles: Miles | None = Field(None, description="Maximum miles to the nearest highway, inclusive.")
    counties: Counties = Field(default_factory=list, description="Only these counties. Names without the word County. Empty means all.")
    exclude_counties: Counties = Field(default_factory=list, description="Leave out these counties. Use for 'not in X County'.")


class Point(Strict):
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class SearchSites(SiteFilters):
    """Filter sites by size, county and distance to infrastructure."""
    sort_by: Literal["acres", "power", "highway"] = Field(
        "acres", description="acres sorts largest first; power and highway sort nearest first.")
    limit: Limit = 20


class SitesNear(SiteFilters):
    """Find sites within a radius of a point, with the same optional filters as search_sites.

    Use geocode_place first for a place name. Results are sorted nearest first.
    """
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    radius_miles: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] = 5
    more_points: Annotated[list[Point], Field(max_length=4)] = Field(
        default_factory=list,
        description="Extra points for 'near A or near B'. A site matches when it is within the radius of any point.")
    limit: Limit = 20


class GeocodePlace(Strict):
    """Look up the coordinates of a town or other named place.

    Each result has match set to exact or close. A close match is a likely spelling
    or abbreviation of the name asked for.
    """
    name: Annotated[str, Field(min_length=1, max_length=80)]
