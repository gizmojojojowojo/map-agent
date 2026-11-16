-- Places carry their county, so places that share a name and kind can be told apart
-- (Colorado has two Twin Lakes). Fixture places have no county and keep ''.
ALTER TABLE map_agent.places ADD COLUMN county text NOT NULL DEFAULT '';
ALTER TABLE map_agent.places DROP CONSTRAINT places_pkey;
ALTER TABLE map_agent.places ADD PRIMARY KEY (name, kind, county);
