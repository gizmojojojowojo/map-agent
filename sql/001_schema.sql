-- Map agent schema. Requires the postgis and pg_trgm extensions:
--   CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Each site keeps its source record as JSON; the searchable columns are generated from it.
CREATE SCHEMA IF NOT EXISTS map_agent;

CREATE TABLE map_agent.sites (
    site_id text PRIMARY KEY,
    record jsonb NOT NULL,
    geom geometry(Geometry, 4326) NOT NULL,
    county text GENERATED ALWAYS AS (record->>'county') STORED,
    acres double precision GENERATED ALWAYS AS ((record->>'acres')::double precision) STORED,
    power_miles double precision GENERATED ALWAYS AS ((record->>'power_miles')::double precision) STORED,
    highway_miles double precision GENERATED ALWAYS AS ((record->>'highway_miles')::double precision) STORED,
    CHECK (record->>'site_id' = site_id),
    CHECK (ST_IsValid(geom) AND NOT ST_IsEmpty(geom)),
    CHECK (GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON'))
);

CREATE TABLE map_agent.places (
    name text NOT NULL,
    kind text NOT NULL,
    search_name text NOT NULL,  -- normalised by mapagent.store.normalize_place
    geom geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (name, kind)
);

CREATE INDEX sites_geom_idx ON map_agent.sites USING gist (geom);
CREATE INDEX sites_geography_idx ON map_agent.sites USING gist ((geom::geography));
CREATE INDEX sites_filters_idx ON map_agent.sites (county, acres);
CREATE INDEX places_search_name_idx ON map_agent.places USING gin (search_name gin_trgm_ops);
